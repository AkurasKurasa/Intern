"""
components/workflow_builder/workflow_builder.py
================================================
Visual Node Workflow Builder — n8n / Zapier-style canvas built with Tkinter.

Nodes represent Intern pipeline steps. Edges connect node output ports to input
ports. The resulting workflow is serialisable to JSON and can be loaded back in.

Node types
----------
  trigger      — "Screen Observer" trigger (start of pipeline)
  translator   — TraceTranslator step
  model        — TransformerAgentNetwork inference
  executor     — ActionExecutor step
  condition    — Simple if/else branch
  logger       — Writes execution log to file
  custom       — User-defined label

UI interactions
---------------
  Drag canvas   — Pan (right-click drag or middle-click drag)
  Scroll wheel  — Zoom in / out
  Click node    — Select (shows properties panel)
  Drag node     — Move node around canvas
  Drag port     — Draw an edge to another port
  Delete key    — Delete selected node/edge
  Toolbar       — Add nodes, clear, save, load, run

Public API
----------
  WorkflowCanvas(parent)        — Tkinter widget (embed in any tk.Frame)
  WorkflowCanvas.get_workflow() — Returns dict with nodes + edges
  WorkflowCanvas.load_workflow(d) — Restore from dict
  WorkflowCanvas.to_json()      — Serialise to JSON string
  WorkflowCanvas.from_json(s)   — Restore from JSON string
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ── path setup (so standalone run works) ──────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))   # components/workflow_builder/
_COMP_DIR = os.path.dirname(_THIS_DIR)                   # components/
_ROOT     = os.path.dirname(_COMP_DIR)                   # Intern/
for _p in (_ROOT, _COMP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Library directory ─────────────────────────────────────────────────────────
_LIBRARY_DIR = os.path.join(_ROOT, "data", "workflow_library")

# ── Design tokens (match app/main.py palette) ─────────────────────────────────
BG          = "#0f1117"
BG_CARD     = "#1a1d27"
BG_HOVER    = "#22263a"
ACCENT      = "#6c63ff"
ACCENT_DIM  = "#4b44c2"
SUCCESS     = "#22c55e"
DANGER      = "#ef4444"
WARNING     = "#f59e0b"
TEXT        = "#e2e8f0"
TEXT_DIM    = "#64748b"
BORDER      = "#2d3148"
GRID_COLOR  = "#1e2235"

# Node colours by type
NODE_COLORS: Dict[str, str] = {
    "trigger":    "#7c3aed",   # purple
    "translator": "#0369a1",   # blue
    "trainer":    "#166534",   # green
    "model":      "#0f766e",   # teal
    "executor":   "#b45309",   # amber
    "condition":  "#be185d",   # pink
    "logger":     "#4b5563",   # gray
    "custom":     "#374151",   # dark gray
}

# Port colours
PORT_IN_COLOR  = "#38bdf8"
PORT_OUT_COLOR = "#4ade80"
PORT_RADIUS    = 7
PORT_HIT       = 14  # larger hit-test radius

NODE_W = 160
NODE_H = 56
CORNER = 10

FONT_NODE  = ("Segoe UI", 10, "bold")
FONT_TYPE  = ("Segoe UI", 8)
FONT_UI    = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)


# ══════════════════════════════════════════════════════════════════════════════
#  Data model
# ══════════════════════════════════════════════════════════════════════════════

class Port:
    """An input or output connection point on a node."""
    def __init__(self, node: "Node", kind: str, index: int = 0):
        self.node  = node
        self.kind  = kind    # "in" | "out"
        self.index = index   # position index (for multi-port nodes)
        self.edges: List["Edge"] = []

    @property
    def canvas_xy(self) -> Tuple[float, float]:
        """Absolute canvas position of this port."""
        nx, ny = self.node.x, self.node.y
        if self.kind == "in":
            return nx, ny + NODE_H / 2
        else:
            return nx + NODE_W, ny + NODE_H / 2


class Node:
    """A workflow node."""
    _id_counter = 0

    def __init__(
        self, x: float, y: float,
        node_type: str = "custom",
        label: str = "",
        config: Optional[Dict[str, Any]] = None,
    ):
        Node._id_counter += 1
        self.id        = f"node_{Node._id_counter:04d}"
        self.x, self.y = x, y
        self.node_type = node_type
        self.label     = label or _default_label(node_type)
        self.config    = config or {}
        self.selected  = False

        self.port_in  = Port(self, "in",  0)
        self.port_out = Port(self, "out", 0)

    def color(self) -> str:
        return NODE_COLORS.get(self.node_type, NODE_COLORS["custom"])

    def to_dict(self) -> dict:
        return {
            "id": self.id, "x": self.x, "y": self.y,
            "node_type": self.node_type, "label": self.label,
            "config": self.config,
        }

    @staticmethod
    def from_dict(d: dict) -> "Node":
        n = Node(d["x"], d["y"], d["node_type"], d["label"], d.get("config", {}))
        n.id = d["id"]
        return n


class Edge:
    """A directed connection from one node's output port to another's input."""
    _id_counter = 0

    def __init__(self, src: Node, dst: Node):
        Edge._id_counter += 1
        self.id  = f"edge_{Edge._id_counter:04d}"
        self.src = src
        self.dst = dst
        src.port_out.edges.append(self)
        dst.port_in.edges.append(self)

    def to_dict(self) -> dict:
        return {"id": self.id, "src": self.src.id, "dst": self.dst.id}

    def remove(self):
        if self in self.src.port_out.edges:
            self.src.port_out.edges.remove(self)
        if self in self.dst.port_in.edges:
            self.dst.port_in.edges.remove(self)


def _default_label(node_type: str) -> str:
    return {
        "trigger":    "Screen Observer",
        "translator": "Trace Translator",
        "trainer":    "BC Trainer",
        "model":      "AI Model",
        "executor":   "Action Executor",
        "condition":  "Condition",
        "logger":     "Logger",
        "custom":     "Custom Step",
    }.get(node_type, "Node")


# ══════════════════════════════════════════════════════════════════════════════
#  WorkflowCanvas  — the main draggable canvas widget
# ══════════════════════════════════════════════════════════════════════════════

class WorkflowCanvas(tk.Frame):
    """
    Embeddable workflow canvas widget.

    Drop this into any tk.Frame:
        canvas = WorkflowCanvas(parent_frame)
        canvas.pack(fill="both", expand=True)
    """

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)

        self._nodes: List[Node] = []
        self._edges: List[Edge] = []
        self._selected_node: Optional[Node] = None
        self._selected_edge: Optional[Edge] = None

        # Pan / zoom state
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._scale    = 1.0

        # Drag state
        self._drag_node: Optional[Node] = None
        self._drag_start_wx = 0.0
        self._drag_start_wy = 0.0
        self._drag_mouse_sx = 0
        self._drag_mouse_sy = 0

        # Edge drawing state
        self._edge_src:  Optional[Node] = None
        self._edge_line: Optional[int]  = None

        # Pan state
        self._pan_last_x = 0
        self._pan_last_y = 0

        # Highlight state: node_id → colour override
        self._highlights: Dict[str, str] = {}

        self._build()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        self._cv = tk.Canvas(
            self, bg=BG, highlightthickness=0, cursor="crosshair"
        )
        self._cv.pack(side="left", fill="both", expand=True)

        # Properties panel (right side)
        self._panel = tk.Frame(self, bg=BG_CARD, width=220)
        self._panel.pack(side="right", fill="y")
        self._panel.pack_propagate(False)
        self._build_panel()

        # Bindings
        self._cv.bind("<ButtonPress-1>",   self._on_press)
        self._cv.bind("<B1-Motion>",       self._on_drag)
        self._cv.bind("<ButtonRelease-1>", self._on_release)
        self._cv.bind("<ButtonPress-2>",   self._on_pan_start)
        self._cv.bind("<B2-Motion>",       self._on_pan)
        self._cv.bind("<ButtonPress-3>",   self._on_pan_start)
        self._cv.bind("<B3-Motion>",       self._on_pan)
        self._cv.bind("<MouseWheel>",      self._on_scroll)
        self._cv.bind("<Delete>",          self._on_delete)
        self._cv.bind("<Configure>",       lambda e: self._redraw())
        self._cv.bind("<Button-1>",        lambda e: self._cv.focus_set(), add="+")

        self._redraw()

    def _build_panel(self):
        tk.Label(self._panel, text="Properties", font=FONT_UI,
                 bg=BG_CARD, fg=ACCENT).pack(anchor="w", padx=12, pady=(14, 4))
        tk.Frame(self._panel, bg=BORDER, height=1).pack(fill="x", padx=10)

        self._prop_frame = tk.Frame(self._panel, bg=BG_CARD)
        self._prop_frame.pack(fill="both", expand=True, padx=10, pady=8)
        self._show_empty_props()

    def _show_empty_props(self):
        for w in self._prop_frame.winfo_children():
            w.destroy()
        tk.Label(self._prop_frame, text="Click a node\nto see properties.",
                 font=FONT_SMALL, bg=BG_CARD, fg=TEXT_DIM,
                 justify="center").pack(pady=20)

    def _show_node_props(self, node: Node):
        for w in self._prop_frame.winfo_children():
            w.destroy()

        def lbl(text):
            tk.Label(self._prop_frame, text=text, font=("Segoe UI", 8),
                     bg=BG_CARD, fg=TEXT_DIM, anchor="w").pack(fill="x", pady=(6, 1))

        def entry(var):
            e = tk.Entry(self._prop_frame, textvariable=var, font=FONT_SMALL,
                         bg=BG_HOVER, fg=TEXT, bd=0, insertbackground=TEXT,
                         relief="flat")
            e.pack(fill="x", ipady=3)
            return e

        lbl("Node ID")
        tk.Label(self._prop_frame, text=node.id, font=("Consolas", 8),
                 bg=BG_CARD, fg=TEXT_DIM, anchor="w").pack(fill="x")

        lbl("Label")
        label_var = tk.StringVar(value=node.label)
        entry(label_var)
        label_var.trace_add("write", lambda *_: self._on_label_change(node, label_var))

        lbl("Type")
        type_var = tk.StringVar(value=node.node_type)
        from tkinter import ttk
        cb = ttk.Combobox(self._prop_frame, textvariable=type_var,
                          values=list(NODE_COLORS.keys()),
                          font=FONT_SMALL, state="readonly")
        cb.pack(fill="x")
        type_var.trace_add("write", lambda *_: self._on_type_change(node, type_var))

        lbl("Config (JSON)")
        cfg_txt = tk.Text(self._prop_frame, height=5, font=("Consolas", 8),
                          bg=BG_HOVER, fg=TEXT, bd=0, insertbackground=TEXT,
                          wrap="word", relief="flat")
        cfg_txt.insert("1.0", json.dumps(node.config, indent=2))
        cfg_txt.pack(fill="x", pady=(0, 4))

        def save_cfg():
            try:
                node.config = json.loads(cfg_txt.get("1.0", "end").strip() or "{}")
                cfg_txt.config(bg=BG_HOVER)
            except json.JSONDecodeError:
                cfg_txt.config(bg="#3b0000")

        tk.Button(self._prop_frame, text="Apply Config", font=FONT_SMALL,
                  bg=ACCENT_DIM, fg="white", bd=0, relief="flat",
                  padx=6, pady=4, cursor="hand2", command=save_cfg
                  ).pack(fill="x", pady=(2, 8))

        tk.Button(self._prop_frame, text="🗑 Delete Node", font=FONT_SMALL,
                  bg=DANGER, fg="white", bd=0, relief="flat",
                  padx=6, pady=4, cursor="hand2",
                  command=lambda: self._delete_node(node)
                  ).pack(fill="x")

    def _on_label_change(self, node: Node, var: tk.StringVar):
        node.label = var.get()
        self._redraw()

    def _on_type_change(self, node: Node, var: tk.StringVar):
        node.node_type = var.get()
        self._redraw()

    # ── Coordinate transforms ─────────────────────────────────────────────────

    def _w2s(self, wx: float, wy: float) -> Tuple[float, float]:
        return (wx * self._scale + self._offset_x,
                wy * self._scale + self._offset_y)

    def _s2w(self, sx: float, sy: float) -> Tuple[float, float]:
        return ((sx - self._offset_x) / self._scale,
                (sy - self._offset_y) / self._scale)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _redraw(self):
        self._cv.delete("all")
        self._draw_grid()
        for edge in self._edges:
            self._draw_edge(edge)
        for node in self._nodes:
            self._draw_node(node)

    def _draw_grid(self):
        cw = self._cv.winfo_width()  or 800
        ch = self._cv.winfo_height() or 600
        spacing = max(30, 40 * self._scale)
        ox = self._offset_x % spacing
        oy = self._offset_y % spacing
        x = ox
        while x < cw:
            y = oy
            while y < ch:
                self._cv.create_oval(x - 1, y - 1, x + 1, y + 1,
                                     fill=GRID_COLOR, outline="")
                y += spacing
            x += spacing

    def _draw_node(self, node: Node):
        sx, sy  = self._w2s(node.x, node.y)
        sw      = NODE_W * self._scale
        sh      = NODE_H * self._scale
        r       = CORNER * self._scale

        # Highlight overrides node colour
        color   = self._highlights.get(node.id, node.color())

        if node.selected:
            outline, lw = ACCENT, 2.5
        elif node.id in self._highlights:
            outline, lw = self._highlights[node.id], 2.5
        else:
            outline, lw = BORDER, 1

        # Shadow
        self._cv.create_rectangle(
            sx + 3, sy + 4, sx + sw + 3, sy + sh + 4,
            fill="#0a0c12", outline="", tags="node_shadow",
        )
        # Body
        self._cv.create_rectangle(
            sx, sy, sx + sw, sy + sh,
            fill=color, outline=outline, width=lw,
        )
        # Top strip
        self._cv.create_rectangle(
            sx, sy, sx + sw, sy + r,
            fill=color, outline="",
        )

        # Label
        fs = max(7, int(10 * self._scale))
        self._cv.create_text(
            sx + sw / 2, sy + sh / 2 - 6 * self._scale,
            text=node.label, fill=TEXT, font=("Segoe UI", fs, "bold"),
            anchor="center", width=sw - 10,
        )
        fs2 = max(6, int(8 * self._scale))
        self._cv.create_text(
            sx + sw / 2, sy + sh / 2 + 8 * self._scale,
            text=node.node_type.upper(), fill=TEXT_DIM, font=("Segoe UI", fs2),
            anchor="center",
        )

        # Ports
        pr = PORT_RADIUS * self._scale
        in_x,  in_y  = self._w2s(*node.port_in.canvas_xy)
        out_x, out_y = self._w2s(*node.port_out.canvas_xy)

        self._cv.create_oval(
            in_x - pr, in_y - pr, in_x + pr, in_y + pr,
            fill=PORT_IN_COLOR, outline=BG, width=1.5,
        )
        self._cv.create_oval(
            out_x - pr, out_y - pr, out_x + pr, out_y + pr,
            fill=PORT_OUT_COLOR, outline=BG, width=1.5,
        )

    def _draw_edge(self, edge: Edge, color: str = ACCENT, width: float = 2.2,
                   dashed: bool = False):
        x1, y1 = self._w2s(*edge.src.port_out.canvas_xy)
        x2, y2 = self._w2s(*edge.dst.port_in.canvas_xy)
        self._draw_bezier(x1, y1, x2, y2, color, width, dashed)

    def _draw_bezier(self, x1, y1, x2, y2, color, width, dashed=False):
        dx   = abs(x2 - x1) * 0.5 + 40
        pts  = []
        steps = max(20, int(abs(x2 - x1 + y2 - y1) / 5))
        for i in range(steps + 1):
            t  = i / steps
            mt = 1 - t
            cx1, cy1 = x1 + dx, y1
            cx2, cy2 = x2 - dx, y2
            px = mt**3*x1 + 3*mt**2*t*cx1 + 3*mt*t**2*cx2 + t**3*x2
            py = mt**3*y1 + 3*mt**2*t*cy1 + 3*mt*t**2*cy2 + t**3*y2
            pts += [px, py]
        dash = (6, 4) if dashed else ()
        self._cv.create_line(*pts, fill=color, width=width, smooth=True, dash=dash)

    # ── Hit testing ───────────────────────────────────────────────────────────

    def _node_at(self, sx: float, sy: float) -> Optional[Node]:
        wx, wy = self._s2w(sx, sy)
        for node in reversed(self._nodes):
            if (node.x <= wx <= node.x + NODE_W and
                    node.y <= wy <= node.y + NODE_H):
                return node
        return None

    def _port_at(self, sx: float, sy: float) -> Optional[Tuple[Node, str]]:
        for node in reversed(self._nodes):
            for port in (node.port_in, node.port_out):
                px, py = self._w2s(*port.canvas_xy)
                hit = PORT_HIT * self._scale
                if math.hypot(sx - px, sy - py) <= hit:
                    return node, port.kind
        return None

    def _edge_at(self, sx: float, sy: float, tol: float = 6.0) -> Optional[Edge]:
        for edge in reversed(self._edges):
            x1, y1 = self._w2s(*edge.src.port_out.canvas_xy)
            x2, y2 = self._w2s(*edge.dst.port_in.canvas_xy)
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            if math.hypot(sx - mx, sy - my) < tol * 4:
                return edge
        return None

    # ── Mouse events ──────────────────────────────────────────────────────────

    def _on_press(self, event):
        self._cv.focus_set()
        sx, sy = event.x, event.y

        port_hit = self._port_at(sx, sy)
        if port_hit:
            node, kind = port_hit
            if kind == "out":
                self._edge_src  = node
                self._edge_line = self._cv.create_line(
                    sx, sy, sx, sy, fill=ACCENT, width=2, dash=(5, 3))
            return

        node = self._node_at(sx, sy)
        self._deselect_all()

        if node:
            node.selected       = True
            self._selected_node = node
            self._selected_edge = None
            self._drag_node     = node
            wx, wy              = self._s2w(sx, sy)
            self._drag_start_wx = wx - node.x
            self._drag_start_wy = wy - node.y
            self._show_node_props(node)
        else:
            edge = self._edge_at(sx, sy)
            if edge:
                self._selected_edge = edge
            self._show_empty_props()

        self._redraw()

    def _on_drag(self, event):
        sx, sy = event.x, event.y

        if self._edge_src and self._edge_line:
            ox, oy = self._w2s(*self._edge_src.port_out.canvas_xy)
            self._cv.coords(self._edge_line, ox, oy, sx, sy)
            return

        if self._drag_node:
            wx, wy = self._s2w(sx, sy)
            self._drag_node.x = wx - self._drag_start_wx
            self._drag_node.y = wy - self._drag_start_wy
            self._redraw()

    def _on_release(self, event):
        sx, sy = event.x, event.y

        if self._edge_src:
            port_hit = self._port_at(sx, sy)
            if port_hit:
                dst_node, kind = port_hit
                if kind == "in" and dst_node is not self._edge_src:
                    existing = {(e.src.id, e.dst.id) for e in self._edges}
                    pair = (self._edge_src.id, dst_node.id)
                    if pair not in existing:
                        self._edges.append(Edge(self._edge_src, dst_node))
            if self._edge_line:
                self._cv.delete(self._edge_line)
                self._edge_line = None
            self._edge_src = None

        self._drag_node = None
        self._redraw()

    def _on_pan_start(self, event):
        self._pan_last_x = event.x
        self._pan_last_y = event.y

    def _on_pan(self, event):
        dx = event.x - self._pan_last_x
        dy = event.y - self._pan_last_y
        self._offset_x += dx
        self._offset_y += dy
        self._pan_last_x = event.x
        self._pan_last_y = event.y
        self._redraw()

    def _on_scroll(self, event):
        factor = 1.1 if event.delta > 0 else 0.9
        mx, my = event.x, event.y
        self._offset_x = mx - (mx - self._offset_x) * factor
        self._offset_y = my - (my - self._offset_y) * factor
        self._scale    = max(0.2, min(3.0, self._scale * factor))
        self._redraw()

    def _on_delete(self, event):
        if self._selected_node:
            self._delete_node(self._selected_node)
        elif self._selected_edge:
            self._delete_edge(self._selected_edge)

    # ── Deselect / delete helpers ─────────────────────────────────────────────

    def _deselect_all(self):
        for n in self._nodes:
            n.selected = False
        self._selected_node = None
        self._selected_edge = None

    def _delete_node(self, node: Node):
        edges_to_remove = [e for e in self._edges
                           if e.src is node or e.dst is node]
        for edge in edges_to_remove:
            self._delete_edge(edge, redraw=False)
        self._nodes.remove(node)
        self._selected_node = None
        self._highlights.pop(node.id, None)
        self._show_empty_props()
        self._redraw()

    def _delete_edge(self, edge: Edge, redraw: bool = True):
        edge.remove()
        if edge in self._edges:
            self._edges.remove(edge)
        self._selected_edge = None
        if redraw:
            self._redraw()

    # ── Highlight API (used by execution engine) ──────────────────────────────

    def highlight_node(self, node_id: str, color: str):
        """Highlight a node with the given colour (thread-safe via after())."""
        self._highlights[node_id] = color
        self._redraw()

    def clear_highlights(self):
        self._highlights.clear()
        self._redraw()

    # ── Public API ────────────────────────────────────────────────────────────

    def add_node(
        self, node_type: str = "custom", label: str = "",
        x: Optional[float] = None, y: Optional[float] = None,
    ) -> Node:
        cw = self._cv.winfo_width()  or 600
        ch = self._cv.winfo_height() or 400
        if x is None:
            wx, wy = self._s2w(cw / 2, ch / 2)
            offset = len(self._nodes) * 20
            x, y = wx + offset - NODE_W / 2, wy + offset - NODE_H / 2
        node = Node(x, y, node_type, label)
        self._nodes.append(node)
        self._redraw()
        return node

    def clear(self):
        self._nodes.clear()
        self._edges.clear()
        self._selected_node = None
        self._selected_edge = None
        self._highlights.clear()
        Node._id_counter = 0
        Edge._id_counter = 0
        self._show_empty_props()
        self._redraw()

    def get_workflow(self) -> Dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self._nodes],
            "edges": [e.to_dict() for e in self._edges],
            "meta":  {"created": datetime.now().isoformat()},
        }

    def load_workflow(self, data: Dict[str, Any]):
        self.clear()
        node_map: Dict[str, Node] = {}
        for nd in data.get("nodes", []):
            n = Node.from_dict(nd)
            self._nodes.append(n)
            node_map[n.id] = n
        for ed in data.get("edges", []):
            src = node_map.get(ed["src"])
            dst = node_map.get(ed["dst"])
            if src and dst:
                self._edges.append(Edge(src, dst))
        self._redraw()

    def to_json(self) -> str:
        return json.dumps(self.get_workflow(), indent=2)

    def from_json(self, s: str):
        self.load_workflow(json.loads(s))

    def load_default_pipeline(self):
        """Populate the canvas with the standard Intern pipeline."""
        self.clear()
        positions = [
            ("trigger",    "Screen Observer",    60,  160),
            ("translator", "Trace Translator",  280,  160),
            ("model",      "AI Model",           500,  160),
            ("executor",   "Action Executor",    720,  160),
            ("logger",     "Logger",             940,  160),
        ]
        # Default configs for nodes that need configuration
        default_configs = {
            "trigger":  {"trace_type": "excel", "interval": 1.0,
                         "output_dir": "data/output/traces/live"},
            "trainer":  {"trace_dir":  "data/output/traces/live",
                         "save_path":  "data/models/transformer_bc.pt",
                         "epochs": 50, "batch_size": 16,
                         "continual": True},
            "model":    {"model_path": "data/models/transformer_bc.pt"},
            "executor": {"model_path": "data/models/transformer_bc.pt",
                         "max_steps": 20, "step_delay": 1.0,
                         "trace_type": "gui", "dry_run": False,
                         "goal": "", "provider": "none",
                         "api_key": "", "lmstudio_url": "http://localhost:1234/v1"},
            "logger":   {"log_path": "data/output/workflow_run.log"},
        }
        nodes = []
        for nt, lbl, x, y in positions:
            n = Node(x, y, nt, lbl)
            n.config = default_configs.get(nt, {})
            self._nodes.append(n)
            nodes.append(n)
        for i in range(len(nodes) - 1):
            self._edges.append(Edge(nodes[i], nodes[i + 1]))
        self._redraw()


# ══════════════════════════════════════════════════════════════════════════════
#  WorkflowArchivePanel  — left sidebar listing all library workflows
# ══════════════════════════════════════════════════════════════════════════════

class WorkflowArchivePanel(tk.Frame):
    """
    Scrollable left sidebar that lists every workflow produced by the learning
    layer, with ▶ Play  ⏸ Pause  🗑 Delete controls on each row.
    """

    def __init__(
        self,
        parent: tk.Widget,
        library_dir: str,
        canvas: "WorkflowCanvas",
        panel: "WorkflowBuilderPanel",
        **kw,
    ):
        super().__init__(parent, bg=BG_CARD, **kw)
        self._library_dir = library_dir
        self._canvas      = canvas
        self._panel       = panel

        self._entries:      List[dict]                    = []
        self._threads:      Dict[str, threading.Thread]   = {}
        self._pause_events: Dict[str, threading.Event]    = {}
        self._paused:       Dict[str, bool]               = {}
        self._row_widgets:  Dict[str, dict]               = {}

        self._build()
        self.refresh()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        # Header
        hdr = tk.Frame(self, bg=BG_CARD, pady=8)
        hdr.pack(fill="x")
        tk.Label(
            hdr, text="Workflow Archive",
            font=("Segoe UI", 10, "bold"), bg=BG_CARD, fg=ACCENT,
        ).pack(side="left", padx=12)
        tk.Button(
            hdr, text="↻", font=("Segoe UI", 11),
            bg=BG_CARD, fg=TEXT_DIM, bd=0, relief="flat", cursor="hand2",
            activebackground=BG_HOVER, activeforeground=TEXT,
            command=self.refresh,
        ).pack(side="right", padx=8)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # Scrollable list
        list_outer = tk.Frame(self, bg=BG_CARD)
        list_outer.pack(fill="both", expand=True)

        self._list_cv = tk.Canvas(list_outer, bg=BG_CARD, highlightthickness=0)
        _sb = tk.Scrollbar(list_outer, orient="vertical",
                           command=self._list_cv.yview)
        self._list_cv.configure(yscrollcommand=_sb.set)
        _sb.pack(side="right", fill="y")
        self._list_cv.pack(side="left", fill="both", expand=True)

        self._scroll_frame = tk.Frame(self._list_cv, bg=BG_CARD)
        self._scroll_win   = self._list_cv.create_window(
            (0, 0), window=self._scroll_frame, anchor="nw",
        )
        self._scroll_frame.bind("<Configure>", self._on_inner_configure)
        self._list_cv.bind("<Configure>",      self._on_outer_configure)
        self._list_cv.bind(
            "<MouseWheel>",
            lambda e: self._list_cv.yview_scroll(
                -1 if e.delta > 0 else 1, "units"),
        )

    def _on_inner_configure(self, _e):
        self._list_cv.configure(scrollregion=self._list_cv.bbox("all"))

    def _on_outer_configure(self, e):
        self._list_cv.itemconfig(self._scroll_win, width=e.width)

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        """Reload workflow entries from the library directory."""
        for w in self._scroll_frame.winfo_children():
            w.destroy()
        self._row_widgets.clear()

        try:
            from workflow_learner.workflow_learner import WorkflowLearner
            self._entries = WorkflowLearner(
                library_dir=self._library_dir).list_all()
        except Exception:
            self._entries = []

        if not self._entries:
            tk.Label(
                self._scroll_frame,
                text='No workflows yet.\nUse "🧠 Learn" to create one.',
                font=FONT_SMALL, bg=BG_CARD, fg=TEXT_DIM, justify="center",
            ).pack(pady=24, padx=10)
            return

        for entry in self._entries:
            self._build_row(entry)

    # ── Row builder ───────────────────────────────────────────────────────────

    def _build_row(self, entry: dict):
        name       = entry["name"]
        is_running = (name in self._threads and
                      self._threads[name].is_alive())
        is_paused  = self._paused.get(name, False)

        card = tk.Frame(self._scroll_frame, bg=BG_HOVER)
        card.pack(fill="x", padx=6, pady=3)

        # Info
        info = tk.Frame(card, bg=BG_HOVER)
        info.pack(fill="x", padx=8, pady=(6, 2))
        badge   = " 🧠" if entry.get("learned") else ""
        created = (entry.get("created") or "")[:10] or "—"
        tk.Label(
            info, text=f"{name}{badge}",
            font=("Segoe UI", 9, "bold"), bg=BG_HOVER, fg=TEXT, anchor="w",
        ).pack(fill="x")
        tk.Label(
            info, text=f"{entry['node_count']} nodes · {created}",
            font=("Segoe UI", 8), bg=BG_HOVER, fg=TEXT_DIM, anchor="w",
        ).pack(fill="x")

        # Buttons
        btns = tk.Frame(card, bg=BG_HOVER)
        btns.pack(fill="x", padx=8, pady=(2, 6))

        play_btn = tk.Button(
            btns, text="▶ Play",
            font=("Segoe UI", 8, "bold"),
            bg=TEXT_DIM if is_running else SUCCESS,
            fg="white", bd=0, relief="flat", padx=8, pady=3,
            cursor="hand2",
            state="disabled" if is_running else "normal",
            command=lambda e=entry: self._play(e),
        )
        play_btn.pack(side="left", padx=(0, 4))

        pause_text = "▶ Resume" if is_paused else "⏸ Pause"
        pause_bg   = ACCENT   if is_paused else WARNING
        pause_btn  = tk.Button(
            btns, text=pause_text,
            font=("Segoe UI", 8, "bold"),
            bg=TEXT_DIM if not is_running else pause_bg,
            fg="white", bd=0, relief="flat", padx=8, pady=3,
            cursor="hand2",
            state="normal" if is_running else "disabled",
            command=lambda n=name: self._toggle_pause(n),
        )
        pause_btn.pack(side="left", padx=(0, 4))

        delete_btn = tk.Button(
            btns, text="🗑",
            font=("Segoe UI", 8, "bold"),
            bg=DANGER, fg="white", bd=0, relief="flat", padx=8, pady=3,
            cursor="hand2",
            command=lambda e=entry: self._delete(e),
        )
        delete_btn.pack(side="left")

        self._row_widgets[name] = {
            "play_btn":  play_btn,
            "pause_btn": pause_btn,
        }

    # ── Actions ───────────────────────────────────────────────────────────────

    def _play(self, entry: dict):
        name = entry["name"]
        if name in self._threads and self._threads[name].is_alive():
            return

        try:
            from workflow_learner.workflow_learner import WorkflowLearner
            wf = WorkflowLearner(
                library_dir=self._library_dir).load(entry["path"])
            self._canvas.load_workflow(wf)
        except Exception as exc:
            messagebox.showerror("Load Error", str(exc))
            return

        pause_evt = threading.Event()
        pause_evt.set()                          # starts unpaused
        self._pause_events[name] = pause_evt
        self._paused[name]       = False

        order = _topo_sort(self._canvas.get_workflow())
        self._panel._clear_log()
        self._panel._canvas_widget.clear_highlights()
        self._panel._log(
            f"Archive: running '{name}' — {len(order)} node(s).", "bold")
        self._panel._set_status(f"Running '{name}'…", WARNING)

        widgets = self._row_widgets.get(name, {})
        if widgets.get("play_btn"):
            widgets["play_btn"].config(state="disabled", bg=TEXT_DIM)
        if widgets.get("pause_btn"):
            widgets["pause_btn"].config(
                state="normal", bg=WARNING, text="⏸ Pause")

        def run():
            try:
                self._panel._execute_workflow_pausable(order, pause_evt)
            finally:
                self.after(0, lambda: self._on_done(name))

        t = threading.Thread(target=run, daemon=True)
        self._threads[name] = t
        t.start()

    def _toggle_pause(self, name: str):
        evt     = self._pause_events.get(name)
        widgets = self._row_widgets.get(name, {})
        if not evt:
            return

        if self._paused.get(name):
            # Resume
            self._paused[name] = False
            evt.set()
            if widgets.get("pause_btn"):
                widgets["pause_btn"].config(text="⏸ Pause", bg=WARNING)
            self._panel._set_status(f"Resumed '{name}'.", SUCCESS)
            self._panel._log(f"Resumed '{name}'.", "ok")
        else:
            # Pause
            self._paused[name] = True
            evt.clear()
            if widgets.get("pause_btn"):
                widgets["pause_btn"].config(text="▶ Resume", bg=ACCENT)
            self._panel._set_status(f"Paused '{name}'.", WARNING)
            self._panel._log(f"Paused '{name}'.", "warn")

    def _delete(self, entry: dict):
        name = entry["name"]
        if not messagebox.askyesno(
            "Delete Workflow", f"Permanently delete '{name}'?"
        ):
            return
        try:
            from workflow_learner.workflow_learner import WorkflowLearner
            WorkflowLearner(
                library_dir=self._library_dir).delete(entry["path"])
            self._panel._log(f"Deleted workflow '{name}'.", "warn")
        except Exception as exc:
            messagebox.showerror("Delete Error", str(exc))
            return
        self.refresh()

    def _on_done(self, name: str):
        """Restore button states after a workflow thread finishes."""
        self._pause_events.pop(name, None)
        self._paused.pop(name, None)
        widgets = self._row_widgets.get(name, {})
        if widgets.get("play_btn"):
            widgets["play_btn"].config(state="normal", bg=SUCCESS)
        if widgets.get("pause_btn"):
            widgets["pause_btn"].config(
                state="disabled", bg=TEXT_DIM, text="⏸ Pause")
        self._panel._set_status("Workflow finished.", SUCCESS)


# ══════════════════════════════════════════════════════════════════════════════
#  WorkflowBuilderPanel  — toolbar + canvas + log composite widget
# ══════════════════════════════════════════════════════════════════════════════

class WorkflowBuilderPanel(tk.Frame):
    """
    Full workflow builder panel with toolbar, canvas, execution log, and status bar.
    Drop this into any tk container.
    """

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._running = False
        self._build()

    def _build(self):
        # Canvas must be created before toolbar (toolbar buttons reference it).
        # We place it inside a main_area frame so the archive sidebar can sit
        # beside it using side="left" packing.
        _main = tk.Frame(self, bg=BG)
        self._canvas_widget = WorkflowCanvas(_main)

        self._build_toolbar()          # packs into self → rendered above _main
        _main.pack(fill="both", expand=True)

        # Archive sidebar (left strip)
        _arch_frame = tk.Frame(_main, bg=BG_CARD, width=260)
        _arch_frame.pack(side="left", fill="y")
        _arch_frame.pack_propagate(False)

        tk.Frame(_main, bg=BORDER, width=1).pack(side="left", fill="y")

        # Canvas (right, fills remaining space)
        self._canvas_widget.pack(side="left", fill="both", expand=True)

        self._archive = WorkflowArchivePanel(
            _arch_frame, _LIBRARY_DIR, self._canvas_widget, self,
        )
        self._archive.pack(fill="both", expand=True)

        self._build_log_panel()
        self._build_statusbar()
        self._canvas_widget.load_default_pipeline()

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        bar = tk.Frame(self, bg=BG_CARD, pady=8)
        bar.pack(fill="x")

        def btn(text, color, cmd):
            b = tk.Button(bar, text=text, font=("Segoe UI", 9, "bold"),
                           bg=color, fg="white", bd=0, relief="flat",
                           padx=12, pady=5, cursor="hand2",
                           activebackground=ACCENT_DIM, activeforeground="white",
                           command=cmd)
            b.pack(side="left", padx=4)
            return b

        tk.Label(bar, text="Add:", font=("Segoe UI", 9), bg=BG_CARD,
                 fg=TEXT_DIM).pack(side="left", padx=(12, 6))

        for nt, label, color in [
            ("trigger",    "⚡ Trigger",    "#7c3aed"),
            ("translator", "🔄 Translator", "#0369a1"),
            ("trainer",    "🎓 Trainer",    "#166534"),
            ("model",      "🧠 Model",      "#0f766e"),
            ("executor",   "▶ Executor",    "#b45309"),
            ("condition",  "⑂ Condition",  "#be185d"),
            ("logger",     "📝 Logger",     "#4b5563"),
            ("custom",     "+ Custom",      "#374151"),
        ]:
            btn(label, color, lambda t=nt: self._canvas_widget.add_node(t))

        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", padx=8, pady=4)

        btn("💾 Save",    ACCENT,   self._save_workflow)
        btn("📂 Load",    BG_HOVER, self._load_workflow)
        btn("🔄 Default", BG_HOVER, self._canvas_widget.load_default_pipeline)
        btn("🗑 Clear",    DANGER,   self._clear_confirm)

        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", padx=8, pady=4)

        btn("🧠 Learn",   "#7c3aed", self._learn_workflow)
        btn("📚 Library", BG_HOVER,  self._open_library)

        self._run_btn = btn("▶ Run",    SUCCESS,  self._run_workflow)

    # ── Execution log panel ───────────────────────────────────────────────────

    def _build_log_panel(self):
        """Collapsible log area below the canvas."""
        self._log_visible = tk.BooleanVar(value=True)

        # Toggle bar
        toggle_bar = tk.Frame(self, bg=BG_CARD, pady=3)
        toggle_bar.pack(fill="x")
        tk.Button(
            toggle_bar, text="📋 Execution Log  ▾",
            font=("Segoe UI", 8, "bold"), bg=BG_CARD, fg=TEXT_DIM,
            bd=0, relief="flat", padx=10, cursor="hand2",
            activebackground=BG_HOVER, activeforeground=TEXT,
            command=self._toggle_log,
        ).pack(side="left")

        # Log frame
        self._log_frame = tk.Frame(self, bg=BG, height=120)
        self._log_frame.pack(fill="x")
        self._log_frame.pack_propagate(False)

        self._log_text = tk.Text(
            self._log_frame, bg=BG, fg=TEXT_DIM, bd=0, relief="flat",
            font=("Consolas", 8), state="disabled", wrap="word",
            insertbackground=TEXT,
        )
        self._log_text.pack(fill="both", expand=True, padx=8, pady=4)

        self._log_text.tag_configure("ok",      foreground=SUCCESS)
        self._log_text.tag_configure("err",     foreground=DANGER)
        self._log_text.tag_configure("warn",    foreground=WARNING)
        self._log_text.tag_configure("accent",  foreground=ACCENT)
        self._log_text.tag_configure("dim",     foreground=TEXT_DIM)
        self._log_text.tag_configure("bold",    foreground=TEXT, font=("Consolas", 8, "bold"))

    def _toggle_log(self):
        if self._log_visible.get():
            self._log_frame.pack_forget()
            self._log_visible.set(False)
        else:
            self._log_frame.pack(fill="x")
            self._log_visible.set(True)

    def _log(self, msg: str, tag: str = "dim"):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_text.config(state="normal")
        self._log_text.insert("end", f"[{ts}] ", "dim")
        self._log_text.insert("end", msg + "\n", tag)
        self._log_text.see("end")
        self._log_text.config(state="disabled")

    def _clear_log(self):
        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.config(state="disabled")

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_statusbar(self):
        bar = tk.Frame(self, bg=BG_CARD, pady=4)
        bar.pack(fill="x")
        self._status = tk.Label(
            bar, text="Drag and drop nodes · Right-click drag to pan · Scroll to zoom",
            font=("Segoe UI", 8), bg=BG_CARD, fg=TEXT_DIM,
        )
        self._status.pack(side="left", padx=12)
        tk.Label(bar, text="DEL = delete selected node/edge",
                 font=("Segoe UI", 8), bg=BG_CARD, fg=TEXT_DIM,
                 ).pack(side="right", padx=12)

    def _set_status(self, msg: str, color: str = TEXT_DIM):
        self._status.config(text=msg, fg=color)

    # ── File operations ───────────────────────────────────────────────────────

    def _save_workflow(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("Workflow JSON", "*.json"), ("All files", "*.*")],
            initialfile="workflow.json",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(self._canvas_widget.to_json())
        self._set_status(f"Saved → {os.path.basename(path)}", SUCCESS)
        self._log(f"Workflow saved to {path}", "ok")

    def _load_workflow(self):
        path = filedialog.askopenfilename(
            filetypes=[("Workflow JSON", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        with open(path, encoding="utf-8") as f:
            self._canvas_widget.from_json(f.read())
        self._set_status(f"Loaded ← {os.path.basename(path)}", ACCENT)
        self._log(f"Workflow loaded from {path}", "accent")

    def _clear_confirm(self):
        if messagebox.askyesno("Clear Workflow", "Remove all nodes and edges?"):
            self._canvas_widget.clear()
            self._set_status("Canvas cleared.", WARNING)
            self._log("Canvas cleared.", "warn")

    # ── Learn from traces ─────────────────────────────────────────────────────

    def _learn_workflow(self):
        """Pick a trace directory → auto-build a workflow → save to library."""
        trace_dir = filedialog.askdirectory(
            title="Select trace directory (folder of trace JSON files)",
            initialdir=os.path.join(_ROOT, "data", "output", "traces"),
        )
        if not trace_dir:
            return

        name = simpledialog.askstring(
            "Workflow Name",
            "Name for this learned workflow:",
            initialvalue=os.path.basename(trace_dir),
        )
        if not name:
            return

        try:
            from workflow_learner.workflow_learner import WorkflowLearner
            learner  = WorkflowLearner(library_dir=_LIBRARY_DIR)
            workflow = learner.learn_from_dir(trace_dir, name)
            path     = learner.save(workflow)

            n = len(workflow["nodes"])
            e = len(workflow["edges"])
            self._canvas_widget.load_workflow(workflow)
            self._set_status(f"Learned '{name}' — {n} nodes, {e} edges. Saved to library.", SUCCESS)
            self._log(f"Learned workflow '{name}' from {trace_dir}", "ok")
            self._log(f"  {n} nodes · {e} edges → {path}", "dim")
            if hasattr(self, "_archive"):
                self._archive.refresh()
        except Exception as exc:
            messagebox.showerror("Learn Error", str(exc))
            self._log(f"Learn failed: {exc}", "err")

    # ── Library ───────────────────────────────────────────────────────────────

    def _open_library(self):
        """Open the workflow library dialog."""
        LibraryDialog(self, _LIBRARY_DIR, self._canvas_widget, self)

    # ── Workflow execution ────────────────────────────────────────────────────

    def _run_workflow(self):
        if self._running:
            self._log("Already running — please wait.", "warn")
            return

        wf = self._canvas_widget.get_workflow()
        if not wf["nodes"]:
            messagebox.showinfo("Run Workflow", "No nodes on canvas.")
            return

        order = _topo_sort(wf)
        self._clear_log()
        self._canvas_widget.clear_highlights()
        self._running = True
        self._run_btn.config(state="disabled", text="⏳ Running…")
        self._set_status("Running workflow…", WARNING)
        self._log(f"Starting workflow — {len(order)} node(s) in order.", "bold")

        threading.Thread(
            target=self._execute_workflow,
            args=(order,),
            daemon=True,
        ).start()

    def _execute_workflow(self, order: List[dict]):
        """Run nodes in topological order (background thread)."""
        context: Dict[str, Any] = {}   # shared state passed between nodes

        try:
            for node_dict in order:
                nid       = node_dict["id"]
                ntype     = node_dict["node_type"]
                label     = node_dict["label"]
                config    = node_dict.get("config", {})

                # Highlight node as "running" (orange)
                self.after(0, lambda i=nid: self._canvas_widget.highlight_node(i, WARNING))
                self.after(0, lambda l=label: self._set_status(f"Running: {l}…", WARNING))
                self.after(0, lambda l=label, t=ntype:
                           self._log(f"▶  {l}  ({t.upper()})", "bold"))

                ok, detail = self._dispatch_node(ntype, config, context)

                if ok:
                    self.after(0, lambda i=nid: self._canvas_widget.highlight_node(i, SUCCESS))
                    self.after(0, lambda d=detail: self._log(f"   ✓ {d}", "ok"))
                else:
                    self.after(0, lambda i=nid: self._canvas_widget.highlight_node(i, DANGER))
                    self.after(0, lambda d=detail: self._log(f"   ✗ {d}", "err"))
                    self.after(0, lambda l=label:
                               self._set_status(f"Error in {l} — workflow stopped.", DANGER))
                    return

                time.sleep(0.4)   # brief pause so the user can see each node activate

            self.after(0, lambda: self._set_status(
                f"Workflow completed — {len(order)} node(s) executed.", SUCCESS))
            self.after(0, lambda: self._log("Workflow finished successfully.", "ok"))

        finally:
            self._running = False
            self.after(0, lambda: self._run_btn.config(state="normal", text="▶ Run"))

    def _execute_workflow_pausable(
        self,
        order: List[dict],
        pause_event: threading.Event,
    ):
        """
        Run nodes in topological order, blocking on *pause_event* between each
        node so that WorkflowArchivePanel can pause/resume execution.
        Does NOT touch self._running — archive runs are managed independently.
        """
        context: Dict[str, Any] = {}
        try:
            for node_dict in order:
                pause_event.wait()          # blocks here while paused

                nid    = node_dict["id"]
                ntype  = node_dict["node_type"]
                label  = node_dict["label"]
                config = node_dict.get("config", {})

                self.after(0, lambda i=nid:
                           self._canvas_widget.highlight_node(i, WARNING))
                self.after(0, lambda l=label:
                           self._set_status(f"Running: {l}…", WARNING))
                self.after(0, lambda l=label, t=ntype:
                           self._log(f"▶  {l}  ({t.upper()})", "bold"))

                ok, detail = self._dispatch_node(ntype, config, context)

                if ok:
                    self.after(0, lambda i=nid:
                               self._canvas_widget.highlight_node(i, SUCCESS))
                    self.after(0, lambda d=detail:
                               self._log(f"   ✓ {d}", "ok"))
                else:
                    self.after(0, lambda i=nid:
                               self._canvas_widget.highlight_node(i, DANGER))
                    self.after(0, lambda d=detail:
                               self._log(f"   ✗ {d}", "err"))
                    self.after(0, lambda l=label:
                               self._set_status(
                                   f"Error in {l} — workflow stopped.",
                                   DANGER))
                    return

                time.sleep(0.4)

            self.after(0, lambda: self._set_status(
                f"Workflow completed — {len(order)} node(s) executed.",
                SUCCESS))
            self.after(0, lambda: self._log(
                "Workflow finished successfully.", "ok"))
        except Exception as exc:
            self.after(0, lambda: self._log(f"Execution error: {exc}", "err"))

    def _dispatch_node(
        self, ntype: str, config: dict, context: dict
    ) -> Tuple[bool, str]:
        """
        Execute one node.  Returns (success, detail_message).
        context is shared between nodes so earlier nodes can pass data forward.
        """
        try:
            if ntype == "trigger":
                return self._exec_trigger(config, context)
            elif ntype == "translator":
                return self._exec_translator(config, context)
            elif ntype == "trainer":
                return self._exec_trainer(config, context)
            elif ntype == "model":
                return self._exec_model(config, context)
            elif ntype == "executor":
                return self._exec_executor(config, context)
            elif ntype == "logger":
                return self._exec_logger(config, context)
            elif ntype == "condition":
                return self._exec_condition(config, context)
            else:  # custom
                return True, f"Custom node — no-op (label={config.get('label', '')})"
        except Exception as exc:
            return False, str(exc)

    # ── Node handlers ─────────────────────────────────────────────────────────

    def _exec_trigger(self, config: dict, context: dict) -> Tuple[bool, str]:
        """Start ScreenObserver, capture one frame, store frames in context."""
        try:
            from recorder.recorder import ScreenObserver
        except ImportError:
            return False, "recorder not importable — check your path."

        output_dir  = config.get("output_dir", "data/output/traces/live")
        trace_type  = config.get("trace_type", "gui")
        interval    = float(config.get("interval", 1.0))

        os.makedirs(output_dir, exist_ok=True)
        # Pass ContinualLearner if the Trainer node already started one
        cl = context.get("continual_learner")
        observer = ScreenObserver(
            output_dir=output_dir,
            trace_type=trace_type,
            continual_learner=cl,
        )
        observer.start(interval_sec=interval)
        time.sleep(max(interval + 0.5, 1.5))   # capture at least one frame
        traces = observer.stop()

        context["output_dir"]   = output_dir
        context["trace_type"]   = trace_type
        context["trace_paths"]  = [
            t if isinstance(t, str) else getattr(t, "path", str(t))
            for t in (traces or [])
        ]
        context["frame_count"]  = len(observer._frames) if hasattr(observer, "_frames") else 0

        fc = context.get("frame_count", 0)
        tc = len(context.get("trace_paths", []))
        return True, f"Captured {fc} frame(s), produced {tc} trace(s) → {output_dir}"

    def _exec_translator(self, config: dict, context: dict) -> Tuple[bool, str]:
        """Load trace JSON files produced by ScreenObserver into context.

        ScreenObserver already performs OCR and saves fully-structured trace
        JSONs, so this step simply deserialises them and forwards them to the
        model node as 'translated'.
        """
        trace_paths = context.get("trace_paths", [])
        if not trace_paths:
            # Fall back to scanning output_dir
            out_dir = context.get("output_dir", "data/output/traces/live")
            if os.path.isdir(out_dir):
                trace_paths = [
                    os.path.join(out_dir, f)
                    for f in sorted(os.listdir(out_dir))
                    if f.endswith(".json")
                ]

        if not trace_paths:
            return False, "No trace files found to load."

        translated = []
        for path in trace_paths:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                translated.append(data)
            except Exception:
                pass  # skip unreadable files

        context["translated"] = translated
        return True, f"Loaded {len(translated)} / {len(trace_paths)} trace(s)."

    def _exec_trainer(self, config: dict, context: dict) -> Tuple[bool, str]:
        """
        Train (or retrain) the TransformerAgentNetwork via BCTrainer.

        Reads trace_dir for all session JSONs, trains for the configured number
        of epochs, and saves the checkpoint to save_path.  When continual=True
        a ContinualLearner is also started and injected into context so the
        trigger node can hand it to ScreenObserver on the next run.
        """
        try:
            from learning_models.intern_model.bc.behavioral_cloning import BCTrainer
        except ImportError:
            try:
                from components.learning_models.intern_model.bc.behavioral_cloning import BCTrainer
            except ImportError:
                return False, "BCTrainer not importable — check your path."

        trace_dir  = config.get("trace_dir",  "data/output/traces/live")
        save_path  = config.get("save_path",  "data/models/transformer_bc.pt")
        epochs     = int(config.get("epochs",     50))
        batch_size = int(config.get("batch_size", 16))
        continual  = bool(config.get("continual", True))

        if not os.path.isdir(trace_dir):
            return False, f"Trace directory not found: {trace_dir}"

        self.after(0, lambda: self._log(
            f"BCTrainer: training on {trace_dir}  epochs={epochs} …", "info"))

        trainer = BCTrainer(
            trace_dir=trace_dir,
            save_path=save_path,
            epochs=epochs,
            batch_size=batch_size,
        )
        try:
            trainer.train()
        except Exception as exc:
            return False, f"Training failed: {exc}"

        context["model_path"] = save_path

        # Optionally start a ContinualLearner for background retraining
        if continual:
            try:
                try:
                    from learning_models.intern_model.continual.learner import ContinualLearner
                except ImportError:
                    from components.learning_models.intern_model.continual.learner import ContinualLearner

                cl = ContinualLearner(
                    trace_dir=trace_dir,
                    bc_trainer=trainer,
                )
                cl.start()
                context["continual_learner"] = cl
                self.after(0, lambda: self._log(
                    "ContinualLearner started — background retraining active.", "ok"))
            except Exception as cl_exc:
                self.after(0, lambda: self._log(
                    f"ContinualLearner could not start: {cl_exc}", "warn"))

        return True, f"Training complete → {save_path}"

    def _exec_model(self, config: dict, context: dict) -> Tuple[bool, str]:
        """Run TransformerAgentNetwork.predict() on the latest translated trace."""
        try:
            from components.learning_models.transformer.transformer import predict
        except ImportError:
            try:
                from learning_models.transformer.transformer import predict
            except ImportError:
                return False, "Transformer model not importable — check your path."

        translated = context.get("translated", [])
        if not translated:
            return False, "No translated traces available — run Translator first."

        # Extract the state dict from the trace (traces have a "state" key wrapping elements)
        last = translated[-1] if isinstance(translated[-1], dict) else {}
        state = last.get("state", last)
        model_path = config.get("model_path", "data/models/transformer_bc.pt")
        device_str = config.get("device", "auto")

        if not os.path.isfile(model_path):
            return False, f"Model checkpoint not found: {model_path}"

        prediction = predict(
            state=state,
            history=context.get("history", []),
            model_path=model_path,
            device_str=device_str,
        )
        context["prediction"] = prediction
        atype = prediction.get("action_type", "no_op")
        return True, f"Predicted action: {atype}  (full → {prediction})"

    def _exec_executor(self, config: dict, context: dict) -> Tuple[bool, str]:
        """
        Live agentic loop: observe → model-predict → execute → repeat.

        Uses ExecutorAgent with a live observe_fn so the model always sees the
        real current screen/Excel state rather than a frozen snapshot.
        Falls back to the single model-prediction in context for one-shot use.
        """
        try:
            from agent.agent import LLMAgent as ExecutorAgent
        except ImportError:
            try:
                from components.agent.agent import LLMAgent as ExecutorAgent
            except ImportError:
                return False, "agent not importable — check your path."

        model_path  = config.get("model_path", "data/models/transformer_bc.pt")
        dry_run     = config.get("dry_run", False)
        max_steps   = int(config.get("max_steps", 20))
        step_delay  = float(config.get("step_delay", 1.0))
        device_str  = config.get("device", "auto")
        trace_type  = context.get("trace_type", config.get("trace_type", "gui"))

        if not os.path.isfile(model_path):
            return False, f"Model checkpoint not found: {model_path}"

        # ── Build live observe_fn ─────────────────────────────────────────────
        observe_fn = None
        if trace_type == "excel":
            try:
                try:
                    from excel_observer.excel_observer import ExcelObserver
                except ImportError:
                    from components.excel_observer.excel_observer import ExcelObserver
                _xl_obs = ExcelObserver()
                if _xl_obs.connect():
                    observe_fn = _xl_obs.snapshot
                    self.after(0, lambda: self._log("   Excel observer connected — live loop active.", "ok"))
                else:
                    self.after(0, lambda: self._log("   Excel observer could not connect — using frozen state.", "warn"))
            except ImportError:
                self.after(0, lambda: self._log("   excel_observer not available — using frozen state.", "warn"))

        # GUI / web: use UIAutomationObserver for live re-observation after each step
        if observe_fn is None:
            try:
                try:
                    from ui_observer.ui_observer import UIAutomationObserver
                except ImportError:
                    from components.ui_observer.ui_observer import UIAutomationObserver
                _uia_obs = UIAutomationObserver()
                if _uia_obs.available:
                    observe_fn = _uia_obs.snapshot
                    self.after(0, lambda: self._log("   UIAutomation observer active — live loop enabled.", "ok"))
                else:
                    self.after(0, lambda: self._log("   UIAutomation unavailable — using frozen state.", "warn"))
            except ImportError:
                self.after(0, lambda: self._log("   ui_observer not available — using frozen state.", "warn"))

        # ── Determine starting state ──────────────────────────────────────────
        # Prefer the translated state from the pipeline; fall back to last trace
        translated = context.get("translated", [])
        if translated:
            last = translated[-1] if isinstance(translated[-1], dict) else {}
            initial_state = last.get("state", last)
        else:
            # Last resort: scan trace output dir for most-recent JSON
            out_dir = context.get("output_dir", "data/output/traces/live")
            initial_state = {}
            if os.path.isdir(out_dir):
                jsons = sorted(
                    (f for f in os.listdir(out_dir) if f.endswith(".json")),
                    reverse=True,
                )
                if jsons:
                    try:
                        with open(os.path.join(out_dir, jsons[0]), encoding="utf-8") as fh:
                            initial_state = json.load(fh).get("state", {})
                    except Exception:
                        pass

        if observe_fn is not None:
            # Live mode: get a fresh state right now before the first step
            try:
                fresh = observe_fn()
                if fresh and fresh.get("elements") is not None:
                    initial_state = fresh
            except Exception:
                pass

        # ── Use LLMAgent when a goal is set and API key is available ─────────
        goal    = config.get("goal", "").strip()
        api_key = config.get("api_key", "").strip() or os.environ.get("ANTHROPIC_API_KEY", "")

        provider     = config.get("provider", "none").strip()
        lmstudio_url = config.get("lmstudio_url", "http://localhost:1234/v1")

        if goal and (provider != "none"):
            try:
                try:
                    from agent.agent import LLMAgent
                except ImportError:
                    from components.agent.agent import LLMAgent

                self.after(0, lambda: self._log(f"   LLMAgent active — provider={provider}  goal={goal!r}", "ok"))
                llm_agent = LLMAgent(
                    goal=goal,
                    provider=provider,
                    api_key=api_key,
                    lmstudio_url=lmstudio_url,
                    model_path=model_path,
                    dry_run=dry_run,
                    max_steps=max_steps,
                    step_delay=step_delay,
                    device_str=device_str,
                )
                step_results = llm_agent.run()
                context["execution_results"] = step_results
                n_tot = len(step_results)
                tag   = "[DRY-RUN] " if dry_run else ""
                return bool(step_results), (
                    f"{tag}LLMAgent completed {n_tot} step(s)  goal={goal!r}"
                )
            except Exception as llm_exc:
                self.after(0, lambda: self._log(f"   LLMAgent failed ({llm_exc}) — falling back to ExecutorAgent.", "warn"))

        # ── Fallback: ExecutorAgent (transformer-only, no LLM) ────────────────
        agent = ExecutorAgent(
            model_path=model_path,
            dry_run=dry_run,
            max_steps=max_steps,
            step_delay=step_delay,
            device_str=device_str,
        )

        results = agent.run(
            initial_state=initial_state,
            observe_fn=observe_fn,
        )

        context["execution_results"] = results
        n_ok  = sum(1 for r in results if r.success)
        n_tot = len(results)
        tag   = "[DRY-RUN] " if dry_run else ""
        live  = "live-observe" if observe_fn else "frozen-state"
        return bool(results), (
            f"{tag}{n_ok}/{n_tot} steps succeeded  ({live}  max_steps={max_steps})"
        )

    def _exec_logger(self, config: dict, context: dict) -> Tuple[bool, str]:
        """Write a structured log entry to a file."""
        log_path = config.get("log_path", "data/output/workflow_run.log")
        os.makedirs(os.path.dirname(log_path) if os.path.dirname(log_path) else ".", exist_ok=True)

        entry = {
            "timestamp":        datetime.now().isoformat(),
            "prediction":       context.get("prediction"),
            "execution_result": str(context.get("execution_result", "")),
            "frame_count":      context.get("frame_count", 0),
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return True, f"Log entry written → {log_path}"

    def _exec_condition(self, config: dict, context: dict) -> Tuple[bool, str]:
        """Evaluate a simple condition on the context (placeholder)."""
        field = config.get("field", "prediction.action_type")
        value = config.get("value", "no_op")

        # Simple dotted-key lookup in context
        parts = field.split(".")
        obj: Any = context
        for p in parts:
            if isinstance(obj, dict):
                obj = obj.get(p)
            else:
                obj = getattr(obj, p, None)
            if obj is None:
                break

        result = str(obj) == str(value)
        context["condition_result"] = result
        return True, f"Condition '{field} == {value}' → {result}"


# ══════════════════════════════════════════════════════════════════════════════
#  LibraryDialog  — browse, load, play, delete saved workflows
# ══════════════════════════════════════════════════════════════════════════════

class LibraryDialog:
    """
    Modal dialog that shows the workflow library.
    Buttons: Load into Canvas · Run · Delete · Close
    """

    def __init__(
        self,
        parent: tk.Widget,
        library_dir: str,
        canvas: "WorkflowCanvas",
        panel: "WorkflowBuilderPanel",
    ):
        self._canvas  = canvas
        self._panel   = panel
        self._library_dir = library_dir

        self._win = tk.Toplevel(parent)
        self._win.title("Workflow Library")
        self._win.geometry("680x440")
        self._win.configure(bg=BG)
        self._win.resizable(True, True)
        self._win.grab_set()   # modal

        self._entries: List[dict] = []
        self._build()
        self._refresh()

    def _build(self):
        # Header
        hdr = tk.Frame(self._win, bg=BG_CARD, pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text="📚  Workflow Library", font=("Segoe UI", 13, "bold"),
                 bg=BG_CARD, fg=TEXT).pack(side="left", padx=16)
        tk.Button(hdr, text="✕ Close", font=("Segoe UI", 9),
                  bg=BG_CARD, fg=TEXT_DIM, bd=0, relief="flat",
                  cursor="hand2", command=self._win.destroy
                  ).pack(side="right", padx=12)

        # List frame
        list_frame = tk.Frame(self._win, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(10, 0))

        # Scrollable listbox
        scroll = tk.Scrollbar(list_frame, bg=BG_CARD, troughcolor=BG)
        self._lb = tk.Listbox(
            list_frame, yscrollcommand=scroll.set,
            bg=BG_CARD, fg=TEXT, selectbackground=ACCENT, selectforeground="white",
            font=("Consolas", 9), bd=0, relief="flat",
            activestyle="none", height=14,
        )
        scroll.config(command=self._lb.yview)
        self._lb.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # Detail label
        self._detail = tk.Label(
            self._win, text="", font=("Segoe UI", 8),
            bg=BG, fg=TEXT_DIM, anchor="w", wraplength=640,
        )
        self._detail.pack(fill="x", padx=18, pady=(4, 0))
        self._lb.bind("<<ListboxSelect>>", self._on_select)

        # Button row
        btn_row = tk.Frame(self._win, bg=BG, pady=10)
        btn_row.pack(fill="x", padx=16)

        def btn(text, color, cmd):
            b = tk.Button(btn_row, text=text, font=("Segoe UI", 9, "bold"),
                          bg=color, fg="white", bd=0, relief="flat",
                          padx=14, pady=6, cursor="hand2",
                          activebackground=ACCENT_DIM, activeforeground="white",
                          command=cmd)
            b.pack(side="left", padx=(0, 8))
            return b

        btn("📂 Load into Canvas", ACCENT,   self._load_selected)
        btn("▶ Run",               SUCCESS,  self._run_selected)
        btn("🗑 Delete",            DANGER,   self._delete_selected)

        # Empty-library hint
        self._empty_lbl = tk.Label(
            self._win,
            text='No saved workflows yet.\nRecord traces, then click "🧠 Learn" in the toolbar.',
            font=("Segoe UI", 10), bg=BG, fg=TEXT_DIM, justify="center",
        )

    def _refresh(self):
        try:
            from workflow_learner.workflow_learner import WorkflowLearner
            learner = WorkflowLearner(library_dir=self._library_dir)
            self._entries = learner.list_all()
        except Exception:
            self._entries = []

        self._lb.delete(0, "end")

        if not self._entries:
            self._empty_lbl.pack(pady=20)
            return

        self._empty_lbl.pack_forget()
        for e in self._entries:
            badge   = " 🧠" if e.get("learned") else ""
            created = e["created"][:10] if e.get("created") else "—"
            line    = f"  {e['name']}{badge}   ·   {e['node_count']} nodes   ·   {created}"
            self._lb.insert("end", line)

    def _on_select(self, _event=None):
        idx = self._lb.curselection()
        if not idx:
            return
        e = self._entries[idx[0]]
        learned = "Learned from traces" if e.get("learned") else "Manually built"
        src     = e.get("source_dir", "—")
        self._detail.config(
            text=f"{learned}  ·  {e['node_count']} nodes  ·  {e['edge_count']} edges"
                 f"  ·  {e['step_count']} recorded steps  ·  source: {src}"
        )

    def _selected_entry(self) -> dict | None:
        idx = self._lb.curselection()
        if not idx:
            messagebox.showwarning("No selection", "Please select a workflow first.",
                                   parent=self._win)
            return None
        return self._entries[idx[0]]

    def _load_selected(self):
        e = self._selected_entry()
        if not e:
            return
        try:
            from workflow_learner.workflow_learner import WorkflowLearner
            wf = WorkflowLearner(library_dir=self._library_dir).load(e["path"])
            self._canvas.load_workflow(wf)
            self._panel._set_status(f"Loaded '{e['name']}' into canvas.", ACCENT)
            self._panel._log(f"Loaded workflow '{e['name']}' from library.", "accent")
            self._win.destroy()
        except Exception as exc:
            messagebox.showerror("Load Error", str(exc), parent=self._win)

    def _run_selected(self):
        e = self._selected_entry()
        if not e:
            return
        try:
            from workflow_learner.workflow_learner import WorkflowLearner
            wf = WorkflowLearner(library_dir=self._library_dir).load(e["path"])
            self._canvas.load_workflow(wf)
            self._win.destroy()
            self._panel._run_workflow()
        except Exception as exc:
            messagebox.showerror("Run Error", str(exc), parent=self._win)

    def _delete_selected(self):
        e = self._selected_entry()
        if not e:
            return
        if not messagebox.askyesno(
            "Delete Workflow",
            f"Permanently delete '{e['name']}'?",
            parent=self._win,
        ):
            return
        try:
            from workflow_learner.workflow_learner import WorkflowLearner
            WorkflowLearner(library_dir=self._library_dir).delete(e["path"])
            self._panel._log(f"Deleted workflow '{e['name']}' from library.", "warn")
            self._refresh()
            if hasattr(self._panel, "_archive"):
                self._panel._archive.refresh()
        except Exception as exc:
            messagebox.showerror("Delete Error", str(exc), parent=self._win)


# ══════════════════════════════════════════════════════════════════════════════
#  Topological sort
# ══════════════════════════════════════════════════════════════════════════════

def _topo_sort(workflow: dict) -> List[dict]:
    """Return nodes in topological order using Kahn's algorithm."""
    nodes = {n["id"]: n for n in workflow["nodes"]}
    in_deg = {nid: 0 for nid in nodes}
    out_edges: Dict[str, List[str]] = {nid: [] for nid in nodes}
    for e in workflow["edges"]:
        if e["src"] in out_edges:
            out_edges[e["src"]].append(e["dst"])
        if e["dst"] in in_deg:
            in_deg[e["dst"]] += 1
    queue = [nid for nid, d in in_deg.items() if d == 0]
    order = []
    while queue:
        nid = queue.pop(0)
        order.append(nodes[nid])
        for dst in out_edges.get(nid, []):
            in_deg[dst] -= 1
            if in_deg[dst] == 0:
                queue.append(dst)
    # Append any remaining (cycle nodes)
    for nid in nodes:
        if nodes[nid] not in order:
            order.append(nodes[nid])
    return order


# ══════════════════════════════════════════════════════════════════════════════
#  Standalone run
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Intern — Workflow Builder")
    root.geometry("1280x800")
    root.configure(bg=BG)
    WorkflowBuilderPanel(root).pack(fill="both", expand=True)
    root.mainloop()
