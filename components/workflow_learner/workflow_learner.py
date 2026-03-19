"""
components/workflow_learner/workflow_learner.py
===============================================
Learns a visual workflow graph from a recorded sequence of trace JSON files
(produced by ScreenObserver + TraceTranslator) and manages a persistent
library of saved workflows.

Each trace step  →  one or more workflow nodes:
  mouse click      →  executor node  (action_type=click, position=[x,y])
  keyboard group   →  executor node  (action_type=keyboard, keystrokes=[...])
  passive step     →  skipped (no visible user action)
  first trace      →  trigger node prepended automatically
  optional         →  translator + model nodes inserted after trigger
  always appended  →  logger node at the end

Library layout
--------------
  data/workflow_library/
    my_workflow.json       ← individual workflow file
    fill_form.json
    ...

Each library file is a workflow dict with:
  {
    "name":  "fill_form",
    "nodes": [...],        ← WorkflowCanvas-compatible node dicts
    "edges": [...],        ← WorkflowCanvas-compatible edge dicts
    "meta":  {
      "created":    "...",
      "source_dir": "...",
      "step_count": N,
      "learned":    true
    }
  }

Public API
----------
  learner = WorkflowLearner()
  workflow = learner.learn_from_dir("data/output/traces/live", "my_workflow")
  path     = learner.save(workflow)
  entries  = learner.list_all()   # [{name, path, created, node_count, learned}, ...]
  wf       = learner.load(path)
  learner.delete(path)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ── path setup ────────────────────────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_COMP_DIR = os.path.dirname(_THIS_DIR)
_ROOT     = os.path.dirname(_COMP_DIR)
for _p in (_ROOT, _COMP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Default library directory (relative to project root)
_DEFAULT_LIBRARY = os.path.join(_ROOT, "data", "workflow_library")


# ══════════════════════════════════════════════════════════════════════════════
#  WorkflowLearner
# ══════════════════════════════════════════════════════════════════════════════

class WorkflowLearner:
    """
    Builds workflow graphs from recorded trace sequences and manages the
    persistent workflow library.
    """

    NODE_SPACING_X = 220   # horizontal gap between nodes on canvas
    NODE_Y         = 160   # fixed y position for the main pipeline row

    def __init__(self, library_dir: str = _DEFAULT_LIBRARY):
        self.library_dir = library_dir
        os.makedirs(library_dir, exist_ok=True)

    # ── Learning ──────────────────────────────────────────────────────────────

    def learn_from_dir(
        self,
        trace_dir: str,
        name: str,
        include_pipeline_nodes: bool = True,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """
        Read all trace JSON files from *trace_dir* in sorted order and convert
        them into a workflow dict compatible with WorkflowCanvas.load_workflow().

        Parameters
        ----------
        trace_dir               : Directory containing trace JSON files.
        name                    : Human-readable name for this workflow.
        include_pipeline_nodes  : If True, inserts Translator node after Trigger.
        dry_run                 : Embed dry_run=True in every executor node config.

        Returns
        -------
        dict  with keys: name, nodes, edges, meta
        """
        traces = self._load_traces(trace_dir)
        if not traces:
            raise ValueError(f"No trace JSON files found in: {trace_dir}")

        nodes, edges = self._build_graph(traces, include_pipeline_nodes, dry_run)

        return {
            "name":  name,
            "nodes": nodes,
            "edges": edges,
            "meta":  {
                "created":    datetime.now().isoformat(),
                "source_dir": trace_dir,
                "step_count": len(traces),
                "learned":    True,
            },
        }

    # ── Library CRUD ──────────────────────────────────────────────────────────

    def save(self, workflow: Dict[str, Any]) -> str:
        """Persist workflow to library. Returns the saved file path."""
        name      = workflow.get("name", f"workflow_{int(time.time())}")
        safe_name = _safe_filename(name)
        path      = os.path.join(self.library_dir, f"{safe_name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(workflow, f, indent=2, ensure_ascii=False)
        return path

    def list_all(self) -> List[Dict[str, Any]]:
        """
        Return a list of metadata dicts for every workflow in the library,
        sorted newest-first.
        """
        entries = []
        for fname in sorted(os.listdir(self.library_dir), reverse=True):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.library_dir, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    wf = json.load(f)
                meta = wf.get("meta", {})
                entries.append({
                    "name":        wf.get("name", fname[:-5]),
                    "path":        path,
                    "created":     meta.get("created", ""),
                    "node_count":  len(wf.get("nodes", [])),
                    "edge_count":  len(wf.get("edges", [])),
                    "step_count":  meta.get("step_count", 0),
                    "source_dir":  meta.get("source_dir", ""),
                    "learned":     meta.get("learned", False),
                })
            except Exception:
                pass
        return entries

    def load(self, path: str) -> Dict[str, Any]:
        """Load and return a workflow dict from a library file path."""
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def delete(self, path: str) -> None:
        """Remove a workflow file from the library."""
        if os.path.isfile(path):
            os.remove(path)

    # ── Internal — trace loading ───────────────────────────────────────────────

    def _load_traces(self, trace_dir: str) -> List[Dict[str, Any]]:
        if not os.path.isdir(trace_dir):
            return []
        files = sorted(
            f for f in os.listdir(trace_dir) if f.endswith(".json")
        )
        traces = []
        for fname in files:
            try:
                with open(os.path.join(trace_dir, fname), encoding="utf-8") as f:
                    traces.append(json.load(f))
            except Exception:
                pass
        return traces

    # ── Internal — graph construction ─────────────────────────────────────────

    def _build_graph(
        self,
        traces: List[Dict[str, Any]],
        include_pipeline: bool,
        dry_run: bool,
    ) -> Tuple[List[dict], List[dict]]:
        nodes:  List[dict] = []
        edges:  List[dict] = []
        _nc = [0]
        _ec = [0]
        _x  = [60]

        def push_node(ntype: str, label: str, config: dict = None) -> str:
            nid = f"node_{_nc[0]:04d}"
            _nc[0] += 1
            nodes.append({
                "id":        nid,
                "x":         _x[0],
                "y":         self.NODE_Y,
                "node_type": ntype,
                "label":     label,
                "config":    config or {},
            })
            _x[0] += self.NODE_SPACING_X
            return nid

        def connect(src: str, dst: str):
            eid = f"edge_{_ec[0]:04d}"
            _ec[0] += 1
            edges.append({"id": eid, "src": src, "dst": dst})

        # ── Trigger ─────────────────────────────────────────────────────────
        first = traces[0]
        prev = push_node("trigger", "Screen Observer", {
            "trace_type": first.get("type", "gui"),
            "output_dir": "data/output/traces/live",
        })

        # ── Optional pipeline nodes ──────────────────────────────────────────
        if include_pipeline:
            tid = push_node("translator", "Trace Translator", {})
            connect(prev, tid)
            prev = tid

        # ── Action nodes from each trace step ────────────────────────────────
        action_count = 0

        for trace in traces:
            mouse_actions    = trace.get("mouse", {}).get("actions", [])
            keyboard_actions = trace.get("keyboard", {}).get("actions", [])

            for action in mouse_actions:
                atype = action.get("type", "click")
                pos   = action.get("position", [0, 0])
                label = _mouse_label(atype, pos)
                nid = push_node("executor", label, {
                    "action_type":    "click",
                    "click_position": pos,
                    "dry_run":        dry_run,
                })
                connect(prev, nid)
                prev = nid
                action_count += 1

            for kb_group in keyboard_actions:
                strokes = kb_group.get("strokes", [])
                keys    = [
                    s.get("key", s) if isinstance(s, dict) else s
                    for s in strokes
                ]
                label = _keyboard_label(keys)
                nid = push_node("executor", label, {
                    "action_type": "keyboard",
                    "keystrokes":  keys,
                    "dry_run":     dry_run,
                })
                connect(prev, nid)
                prev = nid
                action_count += 1

        # ── Logger ───────────────────────────────────────────────────────────
        lid = push_node("logger", "Logger", {
            "log_path": "data/output/workflow_run.log"
        })
        connect(prev, lid)

        return nodes, edges


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _safe_filename(name: str) -> str:
    """Convert an arbitrary name to a safe filename stem."""
    s = name.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s]+", "_", s)
    return s[:64] or "workflow"


def _mouse_label(atype: str, pos: List) -> str:
    x, y = int(pos[0]), int(pos[1])
    return {
        "click":        f"Click ({x}, {y})",
        "double_click": f"Double-click ({x}, {y})",
        "drag":         f"Drag from ({x}, {y})",
    }.get(atype, f"Mouse {atype} ({x}, {y})")


def _keyboard_label(keys: List[str]) -> str:
    text = "".join(k for k in keys if len(k) == 1)
    if text:
        preview = text[:14] + ("…" if len(text) > 14 else "")
        return f"Type '{preview}'"
    specials = [k for k in keys if len(k) > 1]
    if specials:
        return f"Keys: {', '.join(specials[:3])}"
    return "Keyboard"
