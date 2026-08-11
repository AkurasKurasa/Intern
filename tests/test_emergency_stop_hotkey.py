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

import ctypes
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


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey is Windows-only")
class TestRegisterHotKeyFailureIsDiagnosable:
    """Found live 2026-08-11: a real RegisterHotKey failure (a different,
    still-running process already held Ctrl+Alt+K) logged 'err=0' --
    completely uninformative, because ctypes.get_last_error() only reflects
    the real Win32 last-error code when the DLL handle was constructed with
    use_last_error=True. The plain ctypes.windll.user32 cached instance
    never updates it -- this was ALWAYS going to misreport 'err=0' on any
    real failure, not just this one. These tests reproduce a genuine,
    deterministic RegisterHotKey failure (registering the identical combo
    twice in this same process) and confirm the two ways of reading the
    error code actually differ -- proving the fix is real, not cosmetic.
    """

    _MOD_ALT_CONTROL = 0x0001 | 0x0002
    _VK_L = 0x4C  # an arbitrary key unlikely to collide with a real hotkey
    _ERROR_HOTKEY_ALREADY_REGISTERED = 1409

    def test_plain_windll_user32_never_reports_the_real_error(self):
        """Locks down the OLD (buggy) behavior this fix moved away from --
        if ctypes ever starts tracking this correctly by default, this test
        failing would be worth noticing, not a silent behavior change."""
        user32 = ctypes.windll.user32
        first_id, second_id = 0xE5D1, 0xE5D2
        assert user32.RegisterHotKey(None, first_id, self._MOD_ALT_CONTROL, self._VK_L)
        try:
            ok = user32.RegisterHotKey(None, second_id, self._MOD_ALT_CONTROL, self._VK_L)
            assert not ok, "registering the identical combo twice should fail"
            assert ctypes.get_last_error() == 0, (
                "documents the bug this fix moved away from -- plain "
                "ctypes.windll.user32 never updates get_last_error()"
            )
        finally:
            user32.UnregisterHotKey(None, first_id)

    def test_windll_with_use_last_error_reports_the_real_error(self):
        """The actual fix: emergency_stop.py now constructs its DLL handle
        this way, so a real failure reports a real, diagnosable code."""
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        first_id, second_id = 0xE5D3, 0xE5D4
        assert user32.RegisterHotKey(None, first_id, self._MOD_ALT_CONTROL, self._VK_L)
        try:
            ok = user32.RegisterHotKey(None, second_id, self._MOD_ALT_CONTROL, self._VK_L)
            assert not ok, "registering the identical combo twice should fail"
            assert ctypes.get_last_error() == self._ERROR_HOTKEY_ALREADY_REGISTERED
        finally:
            user32.UnregisterHotKey(None, first_id)

    def test_emergency_stop_module_uses_use_last_error(self):
        """Direct guard on the actual source line -- catches a future
        refactor silently reverting to plain ctypes.windll.user32."""
        sys.path.insert(0, str(_ROOT / "components"))
        import inspect
        from agent import emergency_stop
        src = inspect.getsource(emergency_stop._listen)
        assert 'use_last_error=True' in src
