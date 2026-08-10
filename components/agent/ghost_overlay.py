"""
agent/ghost_overlay.py
=======================
GhostOverlay -- a transparent, always-on-top window that draws a custom
"Intern"-branded cursor and text caret over whatever the agent is acting
on, decoupled from the real OS mouse cursor.

Why this exists
----------------
executor.py's click path now tries a real UIA action (InvokePattern /
TogglePattern / SelectionItemPattern via ControlFromPoint) before ever
falling back to a simulated mouse click -- see _try_uia_invoke() -- so a
live run increasingly never touches the user's real cursor at all. This
overlay is the visual replacement: it shows WHERE the agent just acted
without ever taking over the real pointer, so a live run doesn't hijack
the user's mouse the way naive pyautogui-driven automation normally does.

The one thing this MUST get right
-----------------------------------
A naive transparent overlay (Tkinter's `-transparentcolor` trick alone)
only makes color-keyed pixels invisible -- the OPAQUE pixels we actually
draw (the cursor shape, the caret bar) are still normal, clickable window
content. Since we deliberately draw those shapes exactly where a real
click is about to land, an overlay window without extra care would sit
directly in front of the target control and silently swallow the click
meant for it -- turning a cosmetic feature into a correctness bug in the
one thing this project can least afford to regress (see DEVELOPERS.md's
history on executor.py reliability). Fixed by adding the Win32
WS_EX_TRANSPARENT extended style to the window after creation: it makes
the ENTIRE window, drawn pixels included, invisible to mouse hit-testing,
while still rendering normally on screen. WS_EX_LAYERED (needed for
`-transparentcolor` anyway) and WS_EX_TRANSPARENT are independent
concerns -- one controls what's visually see-through, the other controls
what receives input -- so combining them is exactly the intended,
supported combination, not a hack stacked on a hack.

Threading
---------
Tkinter's mainloop must own one dedicated thread for its whole life. The
agent thread (and executor.py, which runs on it) never touches Tkinter
directly -- it only pushes position/visibility updates into a
thread-safe queue, which the Tkinter thread drains on its own recurring
`after()` timer. Safe to call show_cursor()/show_caret() from any thread.
"""
from __future__ import annotations

import ctypes
import logging
import queue
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_ORANGE      = "#F97316"
_ORANGE_DARK = "#C2410C"
_WHITE       = "#FFFFFF"
# Arbitrary, deliberately garish magic color key -- anything drawn in this
# exact color becomes both invisible and (via WS_EX_TRANSPARENT below,
# belt-and-suspenders) a mouse-input hole.
_TRANSPARENT = "#FE00FE"
_LABEL_TEXT  = "Intern"

_GWL_EXSTYLE        = -20
_WS_EX_LAYERED      = 0x00080000
_WS_EX_TRANSPARENT  = 0x00000020


class GhostOverlay:
    """Background-thread overlay. start()/stop() once; show_cursor(),
    hide_cursor(), show_caret(), hide_caret() are safe from any thread."""

    def __init__(self):
        self._queue: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    # ── public, thread-safe API ─────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="GhostOverlay", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)

    def stop(self) -> None:
        if self._thread is None:
            return
        self._queue.put(("_quit", None))
        self._thread.join(timeout=2.0)
        self._thread = None

    def show_cursor(self, x: int, y: int) -> None:
        self._queue.put(("cursor_show", (x, y)))

    def hide_cursor(self) -> None:
        self._queue.put(("cursor_hide", None))

    def show_caret(self, x: int, y: int, height: int) -> None:
        self._queue.put(("caret_show", (x, y, height)))

    def hide_caret(self) -> None:
        self._queue.put(("caret_hide", None))

    # ── Tkinter thread ──────────────────────────────────────────────────
    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception as exc:
            logger.warning("GhostOverlay: tkinter unavailable, overlay disabled (%s)", exc)
            self._ready.set()
            return

        try:
            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            try:
                root.attributes("-transparentcolor", _TRANSPARENT)
            except Exception:
                logger.warning("GhostOverlay: -transparentcolor unsupported here (non-Windows?).")

            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            root.geometry(f"{sw}x{sh}+0+0")
            canvas = tk.Canvas(root, width=sw, height=sh, bg=_TRANSPARENT, highlightthickness=0)
            canvas.pack(fill="both", expand=True)

            self._make_click_through(root)
        except Exception as exc:
            logger.warning("GhostOverlay: window setup failed, overlay disabled (%s)", exc)
            self._ready.set()
            return

        state = {"cursor": None, "caret": None, "blink": True}

        def draw_cursor(x: int, y: int) -> None:
            canvas.delete("ghost_cursor")
            # Slightly-large arrow silhouette, tip at the actual target
            # point. White is drawn first and wider as an "overtone" halo
            # so the orange reads clearly against any background.
            pts = [x, y, x, y + 24, x + 6, y + 18, x + 10, y + 27,
                   x + 14, y + 25, x + 10, y + 16, x + 18, y + 16]
            canvas.create_polygon(*pts, fill=_WHITE, outline=_WHITE, width=6, tags="ghost_cursor")
            canvas.create_polygon(*pts, fill=_ORANGE, outline=_ORANGE_DARK, width=1, tags="ghost_cursor")
            lx, ly = x + 20, y + 12
            canvas.create_rectangle(lx, ly, lx + 46, ly + 17,
                                     fill=_ORANGE, outline=_WHITE, width=1, tags="ghost_cursor")
            canvas.create_text(lx + 23, ly + 8, text=_LABEL_TEXT, fill=_WHITE,
                                font=("Segoe UI", 8, "bold"), tags="ghost_cursor")

        def draw_caret(x: int, y: int, h: int) -> None:
            canvas.delete("ghost_caret")
            if not state["blink"]:
                return
            canvas.create_line(x, y, x, y + h, fill=_WHITE, width=5, tags="ghost_caret")
            canvas.create_line(x, y, x, y + h, fill=_ORANGE, width=2, tags="ghost_caret")
            lx, ly = x + 6, y - 2
            canvas.create_rectangle(lx, ly, lx + 42, ly + 15,
                                     fill=_ORANGE, outline=_WHITE, width=1, tags="ghost_caret")
            canvas.create_text(lx + 21, ly + 7, text=_LABEL_TEXT, fill=_WHITE,
                                font=("Segoe UI", 7, "bold"), tags="ghost_caret")

        def blink() -> None:
            if state["caret"] is not None:
                state["blink"] = not state["blink"]
                draw_caret(*state["caret"])
            root.after(500, blink)

        def poll() -> None:
            try:
                while True:
                    kind, payload = self._queue.get_nowait()
                    if kind == "_quit":
                        root.destroy()
                        return
                    elif kind == "cursor_show":
                        state["cursor"] = payload
                        draw_cursor(*payload)
                    elif kind == "cursor_hide":
                        state["cursor"] = None
                        canvas.delete("ghost_cursor")
                    elif kind == "caret_show":
                        state["caret"] = payload
                        state["blink"] = True
                        draw_caret(*payload)
                    elif kind == "caret_hide":
                        state["caret"] = None
                        canvas.delete("ghost_caret")
            except queue.Empty:
                pass
            root.after(30, poll)

        root.after(30, poll)
        root.after(500, blink)
        self._ready.set()
        try:
            root.mainloop()
        except Exception as exc:
            logger.warning("GhostOverlay: mainloop ended unexpectedly: %s", exc)

    @staticmethod
    def _make_click_through(root, user32=None) -> None:
        """WS_EX_TRANSPARENT -- see module docstring. Without this, every
        pixel we actually draw would be normal, clickable window content
        sitting on top of the exact spot a real click is about to land.

        `user32` is injectable specifically so tests never touch the real
        `ctypes.windll` singleton -- it's a live, C-backed, process-wide
        object, and patching it directly (tried once while writing this)
        triggers a genuine low-level crash (a real access-violation-class
        fault, not a Python exception), not just a flaky test."""
        if user32 is None:
            user32 = ctypes.windll.user32
        try:
            hwnd = root.winfo_id()
            ex_style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex_style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT)
        except Exception as exc:
            logger.warning(
                "GhostOverlay: couldn't set click-through window style (%s) -- "
                "the overlay may intercept real clicks near the cursor.", exc)
