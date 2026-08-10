"""
tests/test_emergency_stop_hotkey.py
=====================================
Regression test for agent/emergency_stop.py -- the global Ctrl+Alt+K
killswitch added after a real, severe incident: agent.py's
_reassert_form_window() fought for foreground every single step,
including deliberately defeating Windows' own anti-focus-stealing
protection to do it. A user trying to click over to the Electron Stop
button had focus yanked back before they could ever act -- they had to
hard-shutdown their laptop to regain control of their own mouse.

_reassert_form_window() is fixed at its source too (backs off after one
recurrence of the same foreign window -- see agent.py), but this listener
exists specifically to NOT depend on any of that application logic
behaving correctly. So this test verifies it the same way: spawn a real
child process, actually press Ctrl+Alt+K via synthetic input (pyautogui),
and confirm the process dies immediately. A mocked RegisterHotKey call
would prove nothing here -- the entire point is that the OS-level hook
actually works end to end, independent of anything this codebase controls.

The hotkey combo is consumed entirely by RegisterHotKey -- it never types
visible characters into any window, so this is safe to actually press.

Known limitation, accepted rather than worked around: Win32's
RegisterHotKey(hWnd=NULL, ...) scopes a combo to whichever thread
registers it first system-wide -- a second registration of the same
combo fails outright while the first is still held. If this test somehow
ran WHILE a real live agent run were active (this project's own rule is
that only the user ever starts those), the keypress would go to whichever
process registered first, which could be the live run instead of this
test's own child. Not mocked away because the entire point of this test
is verifying the real OS-level mechanism end to end, not a stand-in for it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

_CHILD_SCRIPT = f"""
import sys
sys.path.insert(0, {str(_ROOT)!r})
sys.path.insert(0, {str(_ROOT / "components")!r})
from agent.emergency_stop import start_emergency_stop_listener, HOTKEY_LABEL
start_emergency_stop_listener()
print("ARMED", flush=True)
import time
time.sleep(30)
print("SHOULD_NEVER_REACH_HERE", flush=True)
"""


def _pyautogui_available() -> bool:
    try:
        import pyautogui  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey/CTRL hotkeys are Windows-only")
@pytest.mark.skipif(not _pyautogui_available(), reason="pyautogui not installed")
def test_ctrl_alt_k_force_kills_the_process_immediately():
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", _CHILD_SCRIPT],
        cwd=str(_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        # Wait for confirmation the listener thread is armed before pressing
        # the hotkey, and give RegisterHotKey's message loop a moment to
        # actually be pumping (armed != the GetMessageW loop is live yet).
        deadline = time.time() + 15
        saw_armed = False
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    pytest.fail(f"child exited early (code {proc.returncode}) before arming")
                continue
            if "ARMED" in line:
                saw_armed = True
                break
        assert saw_armed, "listener never reported ARMED"
        time.sleep(1.5)

        import pyautogui
        pyautogui.hotkey("ctrl", "alt", "k")

        t0 = time.time()
        code = proc.wait(timeout=5)
        elapsed = time.time() - t0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    remaining = proc.stdout.read()
    assert code == 1, f"expected force-kill exit code 1, got {code}\noutput:\n{remaining}"
    assert elapsed < 5, f"took {elapsed:.1f}s to die -- should be near-instant"
    assert "SHOULD_NEVER_REACH_HERE" not in remaining
    assert "EMERGENCY STOP" in remaining
