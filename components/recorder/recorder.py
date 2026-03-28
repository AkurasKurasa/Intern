"""
ScreenObserver - Screen Recording + Input Tracking + Trace Pipeline

Records the screen at a fixed interval while simultaneously tracking all mouse
and keyboard events using passive pynput listeners. The built-in pipeline
translates the captured frames and inputs into trace JSON files via
TraceTranslator.

CLASSES:
    MouseInput        - Records mouse clicks/drags passively
    KeyboardInput     - Records keyboard strokes passively
    ScreenObserver    - Owns inputs + screen capture, runs the pipeline

QUICK START (run record_trace.py instead of importing directly):
    python record_trace.py

PROGRAMMATIC USAGE:
    observer = ScreenObserver(output_dir="data/output/traces/live")
    observer.start(interval_sec=1.0)
    # ... interact with your screen ...
    traces = observer.stop()          # stops recording, translates, saves JSONs
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image as _PILImage
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    _PILImage = None

# ── resolve project root ────────────────────────────────────────────────────
_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))   # components/recorder/
_COMP_DIR   = os.path.dirname(_THIS_DIR)                   # components/
_INTERN_DIR = os.path.dirname(_COMP_DIR)                   # Intern/
for _p in (_INTERN_DIR, _COMP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── optional dependencies ─────────────────────────────────────────────────────

try:
    from pynput import mouse as _pynput_mouse, keyboard as _pynput_keyboard
    _PYNPUT_AVAILABLE = True
except ImportError:
    _PYNPUT_AVAILABLE = False

try:
    import mss
    _MSS_AVAILABLE = True
except ImportError:
    _MSS_AVAILABLE = False

try:
    import pyperclip
    _PYPERCLIP_AVAILABLE = True
except ImportError:
    _PYPERCLIP_AVAILABLE = False

try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "excel_observer"))
    from excel_observer import ExcelObserver as _ExcelObserver
    _EXCEL_OBSERVER_AVAILABLE = True
except ImportError:
    _EXCEL_OBSERVER_AVAILABLE = False

try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "ui_observer"))
    from ui_observer import UIAutomationObserver as _UIAObserver
    _UIA_OBSERVER_AVAILABLE = True
except ImportError:
    _UIA_OBSERVER_AVAILABLE = False


# =============================================================================
# MOUSE INPUT
# =============================================================================

class MouseInput:
    """
    Passively listens to mouse events and records them as structured actions.

    Each action:
        {
            "id":        "mouse_action_NNNN",
            "position":  [x, y],
            "type":      "click" | "double_click" | "drag" | "highlight",
            "timestamp": "<ISO-8601>"
        }

    Usage:
        inp = MouseInput()
        inp.start()
        inp.stop()
        actions = inp.get_actions()
    """

    DOUBLE_CLICK_THRESHOLD = 0.35   # seconds between two clicks = double-click
    DRAG_THRESHOLD = 5              # pixel distance before press+move = drag

    def __init__(self):
        self._actions: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._listener: Optional[Any] = None
        self._action_counter = 0
        self._press_pos: Optional[Tuple[int, int]] = None
        self._press_time: Optional[float] = None
        self._last_click_time: float = 0.0
        self._last_click_pos: Optional[Tuple[int, int]] = None
        self._dragging: bool = False

    def start(self):
        if not _PYNPUT_AVAILABLE:
            print("Warning: pynput not installed — mouse events will not be recorded.")
            return
        if self._listener is not None:
            return
        self._listener = _pynput_mouse.Listener(
            on_click=self._on_click, on_move=self._on_move)
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()
            self._listener = None

    def get_actions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._actions)

    def clear(self):
        with self._lock:
            self._actions.clear()
            self._action_counter = 0

    # ── internal ──────────────────────────────────────────────────────────────

    def _on_move(self, x: int, y: int):
        if self._press_pos is not None and not self._dragging:
            px, py = self._press_pos
            if ((x - px) ** 2 + (y - py) ** 2) ** 0.5 >= self.DRAG_THRESHOLD:
                self._dragging = True

    def _on_click(self, x: int, y: int, button, pressed: bool):
        if pressed:
            self._press_pos = (x, y)
            self._dragging = False
        else:
            if self._press_pos is None:
                return
            now = time.time()
            if self._dragging:
                action_type = "drag"
            elif (
                self._last_click_pos is not None
                and (now - self._last_click_time) <= self.DOUBLE_CLICK_THRESHOLD
                and abs(x - self._last_click_pos[0]) <= 5
                and abs(y - self._last_click_pos[1]) <= 5
            ):
                action_type = "double_click"
                with self._lock:
                    if self._actions and self._actions[-1]["type"] == "click":
                        self._actions.pop()
            else:
                action_type = "click"

            self._record(action_type, x, y)
            self._last_click_time = now
            self._last_click_pos = (x, y)
            self._press_pos = None
            self._dragging = False

    def _record(self, action_type: str, x: int, y: int):
        with self._lock:
            self._actions.append({
                "id": f"mouse_action_{self._action_counter:04d}",
                "position": [x, y],
                "type": action_type,
                "timestamp": datetime.now().isoformat(),
            })
            self._action_counter += 1


# =============================================================================
# KEYBOARD INPUT
# =============================================================================

class KeyboardInput:
    """
    Passively listens to keyboard events and groups them into stroke sequences.

    Each action:
        { "strokes": ["a", "b", "Key.enter", ...] }

    A new group is started after GROUP_TIMEOUT seconds of inactivity.

    Usage:
        inp = KeyboardInput()
        inp.start()
        inp.stop()
        actions = inp.get_actions()
    """

    GROUP_TIMEOUT = 1.0  # seconds of inactivity before opening a new group

    def __init__(self, clipboard: Optional["ClipboardMonitor"] = None):
        self._actions: List[Dict[str, Any]] = []
        self._current_strokes: List[Dict[str, str]] = []
        self._lock = threading.Lock()
        self._listener: Optional[Any] = None
        self._last_key_time: float = 0.0
        self._clipboard = clipboard

    def start(self):
        if not _PYNPUT_AVAILABLE:
            print("Warning: pynput not installed — keyboard events will not be recorded.")
            return
        if self._listener is not None:
            return
        self._listener = _pynput_keyboard.Listener(on_press=self._on_press)
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()
            self._listener = None
        self._flush()

    def get_actions(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._flush_locked()
            return list(self._actions)

    def clear(self):
        with self._lock:
            self._actions.clear()
            self._current_strokes.clear()

    # ── internal ──────────────────────────────────────────────────────────────

    def _on_press(self, key):
        now = time.time()
        key_name = self._key_name(key)
        with self._lock:
            if self._current_strokes and (now - self._last_key_time) > self.GROUP_TIMEOUT:
                self._flush_locked()

            stroke: Dict[str, Any] = {
                "key":       key_name,
                "timestamp": datetime.now().isoformat(),
            }

            # Ctrl+C (\x03) — snapshot clipboard in background so we don't block
            if key_name == "\x03" and self._clipboard:
                threading.Thread(
                    target=self._clipboard.record_copy, daemon=True
                ).start()

            # Ctrl+V (\x16) — attach pasted text to this stroke record
            if key_name == "\x16" and self._clipboard:
                pasted = self._clipboard.record_paste()
                if pasted:
                    stroke["pasted_text"] = pasted

            self._current_strokes.append(stroke)
            self._last_key_time = now

    def _flush(self):
        with self._lock:
            self._flush_locked()

    def _flush_locked(self):
        if self._current_strokes:
            self._actions.append({"strokes": list(self._current_strokes)})
            self._current_strokes.clear()

    @staticmethod
    def _key_name(key) -> str:
        try:
            return key.char
        except AttributeError:
            return str(key)


# =============================================================================
# CLIPBOARD MONITOR
# =============================================================================

class ClipboardMonitor:
    """
    Captures clipboard text content at copy and paste events.

    Integrates with KeyboardInput — when Ctrl+C (\x03) is detected the
    clipboard is snapshotted in a background thread; when Ctrl+V (\x16) is
    detected the last known content is attached to the stroke record so the
    trace carries the full pasted text.

    Each event:
        {
            "event":     "copy" | "paste",
            "content":   "<clipboard text>",
            "timestamp": "<ISO-8601>"
        }
    """

    def __init__(self):
        self._last_content: str = ""
        self._events: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def snapshot(self) -> str:
        """Read the current clipboard text. Returns empty string on failure."""
        if _PYPERCLIP_AVAILABLE:
            try:
                return pyperclip.paste() or ""
            except Exception:
                pass
        try:
            import tkinter as tk
            r = tk.Tk()
            r.withdraw()
            content = r.clipboard_get()
            r.destroy()
            return content
        except Exception:
            return ""

    def record_copy(self) -> str:
        """
        Called after Ctrl+C. Waits briefly for the OS to finish copying,
        then snapshots and stores the clipboard content.
        """
        time.sleep(0.08)   # let the OS complete the copy operation
        content = self.snapshot()
        with self._lock:
            self._last_content = content
            self._events.append({
                "event":     "copy",
                "content":   content,
                "timestamp": datetime.now().isoformat(),
            })
        return content

    def record_paste(self) -> str:
        """
        Called on Ctrl+V. Records a paste event using the last known
        clipboard content and returns it so it can be attached to the stroke.
        """
        with self._lock:
            content = self._last_content
            self._events.append({
                "event":     "paste",
                "content":   content,
                "timestamp": datetime.now().isoformat(),
            })
        return content

    def get_last(self) -> str:
        with self._lock:
            return self._last_content

    def get_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def clear(self):
        with self._lock:
            self._events.clear()
            self._last_content = ""

    def prime(self):
        """Snapshot clipboard at recording start to catch pre-existing content."""
        content = self.snapshot()
        with self._lock:
            self._last_content = content


# =============================================================================
# SCREEN OBSERVER  (+ built-in pipeline)
# =============================================================================

class ScreenObserver:
    """
    Orchestrates screen capture, input tracking, and trace translation.

    Captures full-screen frames at a fixed interval using ``mss`` while running
    ``MouseInput`` and ``KeyboardInput`` listeners in the background.  When
    stopped, it automatically feeds the captured frames into ``TraceTranslator``
    (CV/OCR) and writes one trace JSON per consecutive frame pair.

    Args:
        output_dir:   Where trace JSONs are saved (created if needed).
        trace_type:   "web" | "excel" | "gui"  (written into every trace).
        application:  Optional app-name tag passed to TraceTranslator.
        monitor:      mss monitor index (1 = primary screen).

    Usage:
        observer = ScreenObserver(output_dir="data/output/traces/live")
        observer.start(interval_sec=1.0)
        # ... interact with your screen ...
        traces = observer.stop()        # blocks briefly while translating
    """

    def __init__(
        self,
        output_dir: str = "data/output/traces/live",
        trace_type: str = "gui",
        application: Optional[str] = None,
        monitor: int = 1,
        continual_learner: Optional[Any] = None,
    ):
        if not _MSS_AVAILABLE:
            raise ImportError(
                "mss is required for screen capture. Install with: pip install mss"
            )

        # Each recording session gets its own timestamped subfolder
        # e.g. data/output/traces/live/session_20260321_143012
        from datetime import datetime as _dt
        _session_ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = os.path.join(output_dir, f"session_{_session_ts}")
        self.base_traces_dir = output_dir   # root dir, used for gitignore placement
        self.trace_type = trace_type
        self.application = application
        self.monitor_index = monitor

        self.clipboard = ClipboardMonitor()
        self.mouse    = MouseInput()
        self.keyboard = KeyboardInput(clipboard=self.clipboard)

        # Priority 1: ExcelObserver (semantic COM state) when trace_type="excel"
        self._excel_observer: Optional[Any] = None
        if trace_type == "excel" and _EXCEL_OBSERVER_AVAILABLE:
            self._excel_observer = _ExcelObserver()
            if self._excel_observer.connect():
                print("[ScreenObserver] ExcelObserver connected — Excel semantic mode active.")
            else:
                print("[ScreenObserver] ExcelObserver could not connect — trying UIAutomation.")
                self._excel_observer = None

        # Priority 2: UIAutomationObserver — works for all apps, no OCR needed
        self._uia_observer: Optional[Any] = None
        if self._excel_observer is None and _UIA_OBSERVER_AVAILABLE:
            obs = _UIAObserver()
            if obs.available:
                self._uia_observer = obs
                print("[ScreenObserver] UIAutomationObserver active — semantic state enabled for all apps.")
            else:
                print("[ScreenObserver] UIAutomation unavailable — falling back to OCR.")

        # Priority 3: OCR via TraceTranslator (fallback only)
        if self._excel_observer is None and self._uia_observer is None:
            print("[ScreenObserver] Using OCR fallback (TraceTranslator).")

        self._continual_learner = continual_learner

        self._frames: List[Any] = []   # (ts, img) or (ts, img, semantic_state)
        self._capture_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._interval_sec: float = 1.0  # default; overwritten by start()

    # ── public API ────────────────────────────────────────────────────────────

    def start(self, interval_sec: float = 2.0):
        """Begin recording (non-blocking)."""
        if self._capture_thread is not None and self._capture_thread.is_alive():
            print("ScreenObserver is already running.")
            return

        self._interval_sec = interval_sec
        self._frames.clear()
        self._stop_event.clear()
        self.mouse.clear()
        self.keyboard.clear()
        self.clipboard.clear()
        self.clipboard.prime()   # snapshot any pre-existing clipboard content

        self.mouse.start()
        self.keyboard.start()

        self._capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="ScreenObserver-capture")
        self._capture_thread.start()
        print(f"ScreenObserver started  [interval={interval_sec}s | output={self.output_dir}]")
        print("Press Ctrl+C (or call observer.stop()) to finish recording.\n")

    def stop(self) -> List[Dict[str, Any]]:
        """Stop recording, translate frames to traces, save JSONs, return traces."""
        self._stop_event.set()
        if self._capture_thread:
            self._capture_thread.join(timeout=5)
            self._capture_thread = None

        self.mouse.stop()
        self.keyboard.stop()

        mouse_actions      = self.mouse.get_actions()
        keyboard_actions   = self.keyboard.get_actions()
        clipboard_events   = self.clipboard.get_events()
        frames             = list(self._frames)   # list of (timestamp_str, PIL_image)

        print(
            f"\nRecording stopped — {len(frames)} frames | "
            f"{len(mouse_actions)} mouse actions | "
            f"{len(keyboard_actions)} keyboard groups | "
            f"{len(clipboard_events)} clipboard events"
        )

        return self._translate_and_save(
            frames=frames,
            mouse_actions=mouse_actions,
            keyboard_actions=keyboard_actions,
            clipboard_events=clipboard_events,
        )

    def record(self, duration_sec: float, interval_sec: float = 1.0) -> List[Dict[str, Any]]:
        """Blocking convenience: start → wait → stop."""
        self.start(interval_sec=interval_sec)
        time.sleep(duration_sec)
        return self.stop()

    # ── pipeline (private) ────────────────────────────────────────────────────

    def _translate_and_save(
        self,
        frames: List[Any],
        mouse_actions: List[Dict],
        keyboard_actions: List[Dict],
        clipboard_events: Optional[List[Dict]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Convert frame pairs + inputs into trace JSONs via TraceTranslator.

        Each trace covers exactly one State1 -> State2 step.  Mouse and
        keyboard actions are filtered to only those that occurred during the
        time window between the two frames, so every trace gets its own
        independent input snapshot rather than the full-session dump.
        """
        if len(frames) < 2:
            print("Need at least 2 frames to build a trace — nothing saved.")
            return []

        # Frames are always 3-tuples (ts, img, semantic_state|None)
        frame_ts        = [f[0] for f in frames]
        frame_imgs      = [f[1] for f in frames]
        raw_states      = [f[2] for f in frames]
        _semantic_mode  = any(s is not None for s in raw_states)
        semantic_states = [s if s is not None else {} for s in raw_states]

        if _semantic_mode:
            source_label = semantic_states[0].get("source", "semantic") if semantic_states else "semantic"
            print(f"\nSemantic mode ({source_label}) — {len(frame_imgs)} frames (no OCR needed).")
            states = semantic_states
        else:
            # OCR fallback via TraceTranslator
            from trace_translator.trace_translator import TraceTranslator
            translator = TraceTranslator(use_cv=True, use_html=False)

            print(f"\nOCR fallback — translating {len(frame_imgs)} frames ...")
            states: List[Dict[str, Any]] = []
            for i, img in enumerate(frame_imgs):
                print(f"  [{i+1}/{len(frame_imgs)}] ", end="", flush=True)
                state = translator._state_from_pil(
                    img,
                    source_label=f"frame_{i:04d}",
                    application=self.application,
                )
                states.append(state)
                print(f"{len(state['elements'])} elements")

        os.makedirs(self.output_dir, exist_ok=True)
        traces: List[Dict[str, Any]] = []

        # Each session folder starts from step 0 (folder name provides uniqueness)
        _step_offset = 0

        _clipboard_events = clipboard_events or []

        for i in range(len(states) - 1):
            t_start = frame_ts[i]
            t_end   = frame_ts[i + 1]

            # Filter mouse actions that occurred in this frame's window
            step_mouse = [
                a for a in mouse_actions
                if t_start <= a["timestamp"] < t_end
            ]

            # Filter keyboard strokes that occurred in this frame's window
            step_strokes = [
                stroke
                for group in keyboard_actions
                for stroke in group["strokes"]
                if t_start <= stroke["timestamp"] < t_end
            ]
            step_kb = [{"strokes": step_strokes}] if step_strokes else []

            # Filter clipboard events that occurred in this frame's window
            step_clipboard = [
                ev for ev in _clipboard_events
                if t_start <= ev["timestamp"] < t_end
            ]

            # Compute real duration from frame timestamps
            try:
                from datetime import datetime as _dt
                duration = (
                    _dt.fromisoformat(t_end) - _dt.fromisoformat(t_start)
                ).total_seconds()
            except Exception:
                duration = self._interval_sec

            step_idx = _step_offset + i
            diff = {}
            if not _semantic_mode:
                raw = translator.states_to_trace(states[i], states[i + 1],
                                                 trace_id=f"live_step_{step_idx:04d}")
                diff = raw.get("diff", {})
            trace = {
                "trace_id":   f"live_step_{step_idx:04d}",
                "timestamp":  t_start,
                "duration":   duration,
                "type":       self.trace_type,
                "state":      _fmt_state(states[i]),
                "next_state": _fmt_state(states[i + 1]),
                "mouse":      {"actions": step_mouse},
                "keyboard":   {"actions": step_kb},
                "clipboard":  {"events": step_clipboard},
                "diff":       diff,
            }
            out_path = os.path.join(self.output_dir, f"live_step_{step_idx:04d}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(trace, f, indent=2, ensure_ascii=False)
            traces.append(trace)

            # Notify ContinualLearner so it can trigger background retraining
            if self._continual_learner is not None:
                try:
                    self._continual_learner.add_trace(out_path)
                except Exception:
                    pass

        print(f"\nSaved {len(traces)} trace(s) -> {self.output_dir}")
        return traces

    # ── capture loop ──────────────────────────────────────────────────────────

    def _capture_loop(self):
        with mss.mss() as sct:
            monitor = sct.monitors[self.monitor_index]
            while not self._stop_event.is_set():
                ts   = datetime.now().isoformat()
                shot = sct.grab(monitor)
                img  = _PILImage.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

                if self._excel_observer is not None:
                    semantic_state = self._excel_observer.snapshot()
                    self._frames.append((ts, img, semantic_state))
                elif self._uia_observer is not None:
                    semantic_state = self._uia_observer.snapshot()
                    # If UIA returned very few active elements (≤8) the
                    # foreground app is likely a Tkinter / non-UIA window.
                    # Drop the UIA state so the frame falls through to OCR.
                    active_count = sum(
                        1 for e in semantic_state.get("elements", [])
                        if e.get("window_role") == "active"
                        and e.get("type") not in ("windowcontrol", "titlebarcontrol",
                                                   "menubarcontrol", "menuitemcontrol",
                                                   "panecontrol", "buttoncontrol")
                    )
                    if active_count >= 1:
                        self._frames.append((ts, img, semantic_state))
                    else:
                        # Tkinter or other UIA-opaque app — use OCR for this frame
                        self._frames.append((ts, img, None))
                else:
                    self._frames.append((ts, img, None))

                self._stop_event.wait(timeout=self._interval_sec)


# =============================================================================
# HELPERS
# =============================================================================

def _fmt_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return trace-format fields from a raw state dict.

    Updated trace format fields
    ---------------------------
    application        : str   — process name (e.g. "chrome.exe", "EXCEL.EXE")
    window_title       : str   — foreground window title
    process_id         : int|None — OS process ID
    screen_resolution  : [w, h]
    focused_element_id : str|None — element_id of the focused element
    source             : str   — "uia" | "excel_com" | "ocr" | "uia_unavailable"
    elements           : list  — see element schema below

    Element schema
    --------------
    element_id   : str            — unique id within this state
    type         : str            — simplified control type (button, input, label…)
    control_type : str            — raw UIA ControlTypeName (Button, Edit, Text…)
    bbox         : [x1,y1,x2,y2] — screen coordinates
    text         : str            — visible text / name
    value        : str            — current value (edit controls)
    label        : str            — same as text (kept for model compat)
    automation_id: str            — UIA AutomationId
    class_name   : str            — Windows class name
    enabled      : bool
    visible      : bool
    focused      : bool           — True for the currently focused element
    confidence   : float          — 1.0 for UIA/COM; 0–1 for OCR
    source       : str            — "uia" | "excel_com" | "ocr"
    metadata     : dict           — extra per-source data
    """
    out = {
        "application":        state.get("application", "Unknown"),
        "window_title":       state.get("window_title", ""),
        "process_id":         state.get("process_id"),
        "screen_resolution":  state.get("screen_resolution", [0, 0]),
        "focused_element_id": state.get("focused_element_id"),
        "source":             state.get("source", "ocr"),
        "elements":           state.get("elements", []),
    }
    # Carry through Excel-specific semantic context when present
    if "excel_context" in state:
        out["excel_context"] = state["excel_context"]
    return out


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = ["ScreenObserver", "MouseInput", "KeyboardInput", "ClipboardMonitor"]
