"""
demo_recorder.py
================
Records a human filling the form as (UI state, action) trace pairs.
Output is identical to what _export_run_traces writes — train.py reads it directly.

Usage:
    python scripts/demo_recorder.py

Controls:
    F9  — start / stop recording (toggle)
    F10 — save current session and quit

Traces saved to: data/demos/human/session_<timestamp>/
Train with:      python scripts/train.py --trace_dir data/demos/human --epochs 50
"""

from __future__ import annotations

import ctypes
import datetime
import json
import logging
import os
import sys
import threading
import time

try:
    ctypes.windll.ole32.CoInitialize(None)
except Exception:
    pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMP = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("demo_recorder")

# ── imports ───────────────────────────────────────────────────────────────────
try:
    from pynput import mouse as _pm, keyboard as _pk
except ImportError:
    sys.exit("pynput not installed. Run: pip install pynput")

try:
    from observers.ui_observer.ui_observer import UIAutomationObserver
except ImportError:
    try:
        from components.observers.ui_observer.ui_observer import UIAutomationObserver
    except ImportError:
        sys.exit("UIAutomationObserver not found. Check components/observers/ui_observer/.")

# ── state ─────────────────────────────────────────────────────────────────────
_observer    = UIAutomationObserver()
_recording   = False
_steps: list = []
_lock        = threading.Lock()
_pending_text = ""           # accumulates typed characters between actions
_last_state: dict | None = None   # UI state captured before the action
_state_lock  = threading.Lock()
_CAPTURE_DELAY = 0.08        # seconds to wait after action before snapshotting UI


def _capture_state() -> dict:
    """Capture current UI state via UIA observer."""
    try:
        return _observer.snapshot()
    except Exception as exc:
        logger.warning("State capture failed: %s", exc)
        return {}


def _flush_pending_text(post_state: dict) -> None:
    """Save accumulated keystrokes as a keyboard trace step."""
    global _pending_text, _last_state
    if not _pending_text or _last_state is None:
        _pending_text = ""
        return
    text   = _pending_text
    before = _last_state
    _pending_text = ""
    _last_state   = None
    _save_step(before, post_state, action_type="keyboard", text=text)


def _save_step(
    state_before: dict,
    state_after:  dict,
    *,
    action_type:  str,
    click_pos:    list | None = None,
    text:         str         = "",
    keystrokes:   list        = [],
) -> None:
    """Append a (state, action) trace step."""
    res = state_before.get("screen_resolution", [1920, 1080])
    W   = float(res[0]) or 1920.0

    if action_type == "click" and click_pos:
        mouse_entry = {"actions": [{"position": [float(click_pos[0]), float(click_pos[1])],
                                    "type": "click",
                                    "timestamp": datetime.datetime.now().isoformat()}]}
        kb_entry    = {"actions": []}
    elif action_type == "keyboard" and text:
        mouse_entry = {"actions": []}
        kb_entry    = {"actions": [{"strokes": [{"pasted_text": text, "key": ""}]}]}
    elif action_type == "keyboard" and keystrokes:
        mouse_entry = {"actions": []}
        kb_entry    = {"actions": [{"strokes": [{"key": k, "pasted_text": ""} for k in keystrokes]}]}
    else:
        return

    idx   = len(_steps)
    trace = {
        "trace_id":   f"human_step_{idx:04d}",
        "timestamp":  datetime.datetime.now().isoformat(),
        "duration":   1.0,
        "type":       "form_filling",
        "state":      state_before,
        "mouse":      mouse_entry,
        "keyboard":   kb_entry,
        "next_state": state_after,
    }
    with _lock:
        _steps.append(trace)
    logger.info("  [%d] %s recorded", idx, action_type + (f" {text[:30]!r}" if text else ""))


# ── pynput listeners ──────────────────────────────────────────────────────────

def _on_click(x, y, button, pressed):
    global _last_state
    if not _recording or not pressed:
        return
    if button != _pm.Button.left:
        return

    # Flush any pending typed text before recording the click
    pre_click_state = _capture_state()
    with _lock:
        if _pending_text:
            _flush_pending_text(pre_click_state)

    _last_state = pre_click_state
    time.sleep(_CAPTURE_DELAY)
    post_state  = _capture_state()
    _save_step(pre_click_state, post_state, action_type="click", click_pos=[x, y])
    _last_state = None


_SPECIAL_KEYS = {
    _pk.Key.tab:    "tab",
    _pk.Key.enter:  "return",
    _pk.Key.space:  " ",
    _pk.Key.backspace: "backspace",
    _pk.Key.delete: "delete",
    _pk.Key.esc:    "escape",
    _pk.Key.up:     "Key.up",
    _pk.Key.down:   "Key.down",
    _pk.Key.left:   "Key.left",
    _pk.Key.right:  "Key.right",
    _pk.Key.home:   "Key.home",
    _pk.Key.end:    "Key.end",
}

_FLUSH_KEYS = {_pk.Key.tab, _pk.Key.enter, _pk.Key.esc}


def _on_key_press(key):
    global _pending_text, _last_state

    if not _recording:
        return

    # F9 = toggle, F10 = save+quit (handled in main)
    if key in (_pk.Key.f9, _pk.Key.f10):
        return

    pre_state = _capture_state()

    if key in _FLUSH_KEYS:
        # Flush pending text first, then record the navigation key
        if _pending_text and _last_state:
            _flush_pending_text(pre_state)
        _last_state = pre_state
        time.sleep(_CAPTURE_DELAY)
        post_state  = _capture_state()
        mapped = _SPECIAL_KEYS.get(key, str(key))
        _save_step(pre_state, post_state, action_type="keyboard", keystrokes=[mapped])
        _last_state = None
        return

    if key in _SPECIAL_KEYS:
        mapped = _SPECIAL_KEYS[key]
        if _pending_text and _last_state:
            _flush_pending_text(pre_state)
        _last_state = pre_state
        time.sleep(_CAPTURE_DELAY)
        post_state  = _capture_state()
        _save_step(pre_state, post_state, action_type="keyboard", keystrokes=[mapped])
        _last_state = None
        return

    # Regular character — accumulate
    try:
        char = key.char
        if char:
            if _last_state is None:
                _last_state = pre_state
            _pending_text += char
    except AttributeError:
        pass


def _on_key_release(key):
    pass


# ── session save ──────────────────────────────────────────────────────────────

def _save_session() -> str:
    ts      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(_ROOT, "data", "demos", "human", f"session_{ts}")
    os.makedirs(out_dir, exist_ok=True)
    with _lock:
        steps = list(_steps)
    if not steps:
        logger.warning("No steps recorded — nothing saved.")
        return out_dir
    for i, step in enumerate(steps):
        path = os.path.join(out_dir, f"human_step_{i:04d}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(step, f, indent=2)
    logger.info("Saved %d steps → %s", len(steps), out_dir)
    return out_dir


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    global _recording

    print("\n" + "="*60)
    print("  DEMO RECORDER")
    print("  F9  — toggle record on/off")
    print("  F10 — save session and quit")
    print("="*60)
    print("\nOpen the car insurance form + Notepad, then press F9 to begin.\n")

    mouse_listener    = _pm.Listener(on_click=_on_click)
    keyboard_listener = _pk.Listener(on_press=_on_key_press, on_release=_on_key_release)
    mouse_listener.start()
    keyboard_listener.start()

    def _hotkey_handler(key):
        global _recording, _steps
        if key == _pk.Key.f9:
            _recording = not _recording
            state = "RECORDING" if _recording else "PAUSED"
            print(f"\n  [{state}]  {len(_steps)} steps so far\n")
        elif key == _pk.Key.f10:
            _recording = False
            print("\n  Saving and quitting...")
            out = _save_session()
            print(f"\n  Done. {len(_steps)} steps saved to:\n  {out}")
            print(f"\n  Train with:\n  python scripts/train.py --trace_dir data/demos/human --epochs 50\n")
            mouse_listener.stop()
            keyboard_listener.stop()

    hotkey_listener = _pk.Listener(on_press=_hotkey_handler)
    hotkey_listener.start()

    try:
        hotkey_listener.join()
    except KeyboardInterrupt:
        _recording = False
        _save_session()

    mouse_listener.stop()
    keyboard_listener.stop()


if __name__ == "__main__":
    main()
