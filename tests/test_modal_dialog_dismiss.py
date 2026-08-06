"""
Regression test for LLMAgent._reassert_form_window()'s modal-dialog dismissal.

Bug this locks down: clicking the form's "Submit" button pops a blocking
native wx.MessageBox (missing-fields warning or a success confirmation) —
a MODAL dialog owned by the same process as the form. Windows will not let
SetForegroundWindow bring an owner window back to front while its modal
child is open, so the pre-existing _reassert_form_window() looped forever
("Re-asserted form foreground" every step, never actually recovering) once
Submit was clicked even slightly prematurely — a live run couldn't
progress past it. Found 2026-08-06.

Fix: detect a foreign foreground window belonging to the SAME process as
the locked form (generic — no hardcoded dialog titles) and dismiss it with
Escape before attempting to reassert.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _make_agent():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1)
    agent._locked_hwnd = 111  # pretend the form was locked to this hwnd
    return agent


def _install_fake_win32(monkeypatch, foreground_hwnd, form_pid, fg_pid):
    fake_win32gui = types.SimpleNamespace(
        GetForegroundWindow=lambda: foreground_hwnd,
        IsWindow=lambda hwnd: True,
        SetForegroundWindow=MagicMock(),
    )

    def _get_thread_pid(hwnd):
        pid = form_pid if hwnd == 111 else fg_pid
        return (0, pid)

    fake_win32process = types.SimpleNamespace(GetWindowThreadProcessId=_get_thread_pid)
    fake_pyautogui = types.SimpleNamespace(press=MagicMock())
    fake_win32com_client = types.SimpleNamespace(Dispatch=lambda name: MagicMock())
    fake_win32com = types.SimpleNamespace(client=fake_win32com_client)

    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(sys.modules, "win32process", fake_win32process)
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_win32com_client)
    return fake_pyautogui, fake_win32gui


def test_dismisses_a_same_process_modal_dialog(monkeypatch):
    # Foreground is hwnd 222, owned by the SAME pid (1234) as the locked form
    # (111) — a modal dialog spawned by the form's own app.
    fake_pyautogui, fake_win32gui = _install_fake_win32(
        monkeypatch, foreground_hwnd=222, form_pid=1234, fg_pid=1234)

    agent = _make_agent()
    agent._reassert_form_window()

    fake_pyautogui.press.assert_called_once_with("escape")
    fake_win32gui.SetForegroundWindow.assert_called_once_with(111)


def test_does_not_press_escape_for_an_unrelated_window(monkeypatch):
    # Foreground is hwnd 333, owned by a DIFFERENT pid (9999) — some other
    # app stole focus, not a dialog belonging to the form. Should reassert
    # without blindly pressing Escape (that would be a stray keystroke into
    # an unrelated app).
    fake_pyautogui, fake_win32gui = _install_fake_win32(
        monkeypatch, foreground_hwnd=333, form_pid=1234, fg_pid=9999)

    agent = _make_agent()
    agent._reassert_form_window()

    fake_pyautogui.press.assert_not_called()
    fake_win32gui.SetForegroundWindow.assert_called_once_with(111)


def test_noop_when_form_already_foreground(monkeypatch):
    fake_pyautogui, fake_win32gui = _install_fake_win32(
        monkeypatch, foreground_hwnd=111, form_pid=1234, fg_pid=1234)

    agent = _make_agent()
    agent._reassert_form_window()

    fake_pyautogui.press.assert_not_called()
    fake_win32gui.SetForegroundWindow.assert_not_called()
