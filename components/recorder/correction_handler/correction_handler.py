"""
recorder/correction_handler/correction_handler.py
=========================================================
Captures user corrections during agent execution and saves them as
training data so the agent learns from its mistakes immediately.

How it works
------------
1.  The agent calls  handler.start_monitoring()  before each action.
2.  The executor fires the action.
3.  If the agent detects a validation failure (StateValidator returns
    no_change / unexpected / error), it calls  handler.watch(seconds=3).
4.  During the watch window, any real user mouse/keyboard input is
    intercepted by pynput listeners.
5.  Each user correction step is captured as a trace-compatible dict.
6.  At the end of the watch window (or when the user stops),
    handler.save(task_name)  writes the steps to a new JSON trace file
    in the task's trace directory so the next training run picks it up.

Dependency
----------
    pip install pynput

If pynput is not installed, CorrectionHandler silently degrades to
a no-op so the rest of the pipeline keeps running.

Usage
-----
    handler   = CorrectionHandler(trace_base="data/output/traces")
    validator = StateValidator()

    # Inside the agent loop:
    state_before = observer.snapshot()
    executor.execute(action)
    state_after  = observer.snapshot()

    result = validator.validate(state_before, state_after, action)
    if result.status in ("no_change", "unexpected", "error"):
        logger.info("Validation failed — watching for user correction …")
        correction_steps = handler.watch(observer, seconds=4)
        if correction_steps:
            handler.save(task_name="fill_insurance", steps=correction_steps)
            logger.info("Saved %d correction step(s).", len(correction_steps))
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── pynput (optional) ─────────────────────────────────────────────────────────
try:
    from pynput import keyboard as _kb, mouse as _ms
    _PYNPUT_OK = True
except ImportError:
    _PYNPUT_OK = False
    logger.debug("CorrectionHandler: pynput not installed — correction capture disabled.")


# ── Defaults ──────────────────────────────────────────────────────────────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_COMP      = os.path.dirname(os.path.dirname(_HERE))
_ROOT      = os.path.dirname(_COMP)
_TRACE_BASE = os.path.join(_ROOT, "data", "output", "traces")


# ══════════════════════════════════════════════════════════════════════════════

class CorrectionHandler:
    """
    Watches for user corrections after agent failures and saves them
    as new trace files.

    Parameters
    ----------
    trace_base  : Root directory for traces.  Task-specific sub-dirs are
                  created automatically under  trace_base/<task_name>/.
    idle_cutoff : Seconds of inactivity before the watch window closes early.
    """

    def __init__(
        self,
        trace_base:  str   = _TRACE_BASE,
        idle_cutoff: float = 2.5,
    ):
        self.trace_base  = trace_base
        self.idle_cutoff = idle_cutoff

        self._steps:     List[dict]       = []
        self._last_event: float           = 0.0
        self._recording:  bool            = False
        self._kb_listener: Optional[object] = None
        self._ms_listener: Optional[object] = None
        self._typed_buf:   List[str]      = []

    # ── Public API ─────────────────────────────────────────────────────────────

    def watch(self, observer, seconds: float = 4.0) -> List[dict]:
        """
        Open a watch window.  Any user mouse/keyboard input captured during
        `seconds` seconds is recorded and returned as trace-compatible steps.

        Parameters
        ----------
        observer : UIAutomationObserver (or compatible) — used to capture
                   screen state for each correction step.
        seconds  : Maximum watch duration.

        Returns
        -------
        List of trace step dicts.  Empty list if pynput is unavailable or the
        user didn't intervene.
        """
        if not _PYNPUT_OK:
            return []

        self._steps.clear()
        self._typed_buf.clear()
        self._recording = True
        self._last_event = time.monotonic()

        self._start_listeners(observer)

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            idle = time.monotonic() - self._last_event
            if idle >= self.idle_cutoff and self._steps:
                break
            time.sleep(0.05)

        self._stop_listeners()
        self._recording = False

        logger.info(
            "CorrectionHandler: captured %d correction step(s).", len(self._steps)
        )
        return list(self._steps)

    def save(self, task_name: str, steps: List[dict]) -> Optional[str]:
        """
        Write correction steps to a new trace JSON file.

        Parameters
        ----------
        task_name : Used to resolve the trace sub-directory.
        steps     : Steps returned by watch().

        Returns
        -------
        Path of the saved file, or None if nothing was saved.
        """
        if not steps:
            return None

        trace_dir = os.path.join(self.trace_base, task_name)
        os.makedirs(trace_dir, exist_ok=True)

        ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(trace_dir, f"correction_{ts}.json")

        payload = {
            "source":     "correction",
            "task":       task_name,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "steps":      steps,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

        logger.info("CorrectionHandler: saved correction → %s", path)
        return path

    # ── Listener management ────────────────────────────────────────────────────

    def _start_listeners(self, observer):
        if not _PYNPUT_OK:
            return

        def on_click(x, y, button, pressed):
            if not self._recording or not pressed:
                return
            self._last_event = time.monotonic()
            self._flush_typed(observer)
            state = self._safe_snapshot(observer)
            self._steps.append({
                "action_type":    "click",
                "click_position": [float(x), float(y)],
                "state":          state,
                "timestamp":      time.time(),
                "source":         "correction",
            })
            logger.debug("CorrectionHandler: click @ (%d, %d)", x, y)

        def on_press(key):
            if not self._recording:
                return
            self._last_event = time.monotonic()
            try:
                char = key.char
                if char and char.isprintable():
                    self._typed_buf.append(char)
            except AttributeError:
                # Special key — flush whatever was typed so far
                self._flush_typed(observer)

        self._ms_listener = _ms.Listener(on_click=on_click)
        self._kb_listener = _kb.Listener(on_press=on_press)
        self._ms_listener.start()
        self._kb_listener.start()

    def _stop_listeners(self):
        if self._ms_listener:
            self._ms_listener.stop()
            self._ms_listener = None
        if self._kb_listener:
            self._kb_listener.stop()
            self._kb_listener = None

    def _flush_typed(self, observer):
        """Save accumulated keystrokes as a keyboard action step."""
        if not self._typed_buf:
            return
        text  = "".join(self._typed_buf)
        self._typed_buf.clear()
        state = self._safe_snapshot(observer)
        self._steps.append({
            "action_type": "keyboard",
            "text":        text,
            "state":       state,
            "timestamp":   time.time(),
            "source":      "correction",
        })
        logger.debug("CorrectionHandler: keyboard %r", text)

    @staticmethod
    def _safe_snapshot(observer) -> dict:
        try:
            return observer.snapshot()
        except Exception:
            return {"elements": [], "screen_resolution": [1920, 1080]}
