"""
agent/emergency_stop.py
========================
A global, OS-level keyboard killswitch for a live agent run -- Ctrl+Alt+K
force-terminates the process immediately (os._exit), regardless of which
window has focus or what the agent's own logic is doing at the time.

Why this exists
----------------
A real, severe incident: agent.py's _reassert_form_window() fought for
foreground every single step (~1-2s cadence), including deliberately
defeating Windows' own anti-focus-stealing protection (a fake Alt
keypress) to do it. A user trying to click over to the Electron Stop
button had focus yanked back before they could ever act on it, over and
over -- they had to hard-shutdown their laptop to regain control of their
own mouse. That's fixed at its source too (agent.py now backs off after
one recurrence), but a live GUI-automation agent needs an escape hatch
that does NOT depend on any of its own application logic behaving
correctly. This listens completely independently, on its own OS-level
hook, and the only thing it does on trigger is the single most forceful,
unconditional stop the OS offers -- nothing else needs to cooperate.

Design
------
Raw ctypes + RegisterHotKey, not a third-party hotkey library (pynput/
keyboard) -- specifically so there is nothing else that could itself hang
or misbehave between "key pressed" and "process is dead." Win32's own
global hotkey API, via ctypes, is already how this project talks to the
OS elsewhere (ghost_overlay.py's click-through window style).

On trigger this calls os._exit() directly from the listener thread --
never sys.exit(), never raising KeyboardInterrupt. Both of those require
the interpreter to reach a bytecode-level checkpoint, and would depend on
whatever the main thread happens to be doing at that instant (e.g.
blocked inside a win32 SendInput/SetForegroundWindow call) to ever
actually take effect. os._exit() terminates the whole process at the OS
level immediately, with zero cooperation required from any other thread.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
import sys
import threading

logger = logging.getLogger(__name__)

_MOD_ALT     = 0x0001
_MOD_CONTROL = 0x0002
_VK_K        = 0x4B
_HOTKEY_ID   = 0xE5C1   # arbitrary, process-local id
_WM_HOTKEY   = 0x0312

HOTKEY_LABEL = "Ctrl+Alt+K"


def start_emergency_stop_listener() -> None:
    """Fire-and-forget -- starts a daemon thread that force-kills this
    process the instant Ctrl+Alt+K is pressed, anywhere, regardless of
    which window has focus. Safe to call more than once (only ever starts
    the listener thread the first time)."""
    if getattr(start_emergency_stop_listener, "_started", False):
        return
    start_emergency_stop_listener._started = True
    threading.Thread(target=_listen, name="EmergencyStop", daemon=True).start()


def _listen() -> None:
    # use_last_error=True, NOT ctypes.windll.user32 -- found 2026-08-11
    # while investigating a real live failure that logged "err=0" for a
    # RegisterHotKey call that had definitely failed (confirmed separately:
    # a different, still-running process already held Ctrl+Alt+K).
    # ctypes.get_last_error() only reflects the real Win32 last-error code
    # when the DLL handle was constructed with use_last_error=True -- the
    # plain ctypes.windll.user32 cached instance never updates it, so this
    # branch was ALWAYS going to log a misleading "err=0" on any real
    # failure, not just this one. Every future diagnosis of a failed arm
    # needs the real error code (e.g. 1409/ERROR_HOTKEY_ALREADY_REGISTERED)
    # to actually be useful.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if not user32.RegisterHotKey(None, _HOTKEY_ID, _MOD_ALT | _MOD_CONTROL, _VK_K):
        logger.warning(
            "EmergencyStop: RegisterHotKey(%s) failed (err=%s) -- the "
            "keyboard failsafe is NOT active this run.",
            HOTKEY_LABEL, ctypes.get_last_error())
        return
    logger.info("EmergencyStop: armed -- press %s at any time to force-kill this run.", HOTKEY_LABEL)
    try:
        msg = ctypes.wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            if msg.message == _WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                _kill_now()
    finally:
        try:
            user32.UnregisterHotKey(None, _HOTKEY_ID)
        except Exception:
            pass


def _kill_now() -> None:
    # Best-effort visibility only -- must never block or fail in a way
    # that delays the actual exit below.
    try:
        print(f"\n[EMERGENCY STOP] {HOTKEY_LABEL} pressed -- force-killing this run NOW.")
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(1)
