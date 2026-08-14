"""
tests/test_executor_direct_fill.py
====================================
Regression tests for agent/executor.py's WM_SETTEXT direct-fill fast path.

Built 2026-08-14, following a real live investigation on the actual
running practice form (read-only/reversible tests, not assumed):

- UIA's own ValuePattern.SetValue() was tried first and fails outright
  (COM error) on this form's controls -- not used here.
- Sending a raw Win32 WM_SETTEXT directly to a text field's native window
  handle, via ctypes.windll.user32.SendMessageW, reliably sets an
  EditControl's value in ONE call -- confirmed correct two independent
  ways (a raw ctypes WM_GETTEXTLENGTH/WM_GETTEXT readback, and UIA's own
  readback) that agree with each other. win32gui.SendMessage's own
  GetWindowText wrapper was independently found to be BUGGY for reading
  this exact control back (disagreed with two other correct readbacks) --
  that's why verification here is the raw ctypes readback, not that
  wrapper.
- Tested against a ComboBoxControl and found to be a silent no-op (value
  unchanged, no error) -- comboboxes are excluded via the class-name
  check and keep their existing click-based handling untouched.

This is deliberately NOT a new top-level action_type (a full audit of
agent.py's fill path found seven separate "== keyboard" gates between
decision and execution -- introducing a second action-type string would
mean finding and correctly widening all seven). Instead, _keyboard()
gained an optional `direct_fill_hwnd` parameter: when set, it tries
WM_SETTEXT first and falls back to the existing, completely unmodified
clipboard-paste path in the same call on any failure -- transparent to
the caller, same ExecutionResult shape either way.

Also a direct regression guard against a real, separately-found gap: the
checkbox BM_SETCHECK mechanism elsewhere in this codebase calls real
win32api.SendMessage with NO dry_run check at all. This feature must not
repeat that -- see test_dry_run_never_touches_real_sendmessagew below.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import agent.executor as mod


def _executor_with_mocks(monkeypatch, dry_run=False):
    fake_pyautogui = MagicMock()
    monkeypatch.setattr(mod, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(mod, "_PYAUTOGUI_AVAILABLE", True)

    fake_user32 = MagicMock()
    monkeypatch.setattr(mod, "_user32", fake_user32)

    fake_pyperclip = MagicMock()
    monkeypatch.setitem(sys.modules, "pyperclip", fake_pyperclip)

    executor = mod.ActionExecutor(dry_run=dry_run, ghost_cursor=False)
    return executor, fake_pyautogui, fake_user32, fake_pyperclip


def _configure_edit_class(fake_user32, class_name="Edit"):
    def fake_get_class_name(hwnd, buf, size):
        buf.value = class_name
        return len(class_name)
    fake_user32.GetClassNameW.side_effect = fake_get_class_name


def _configure_readback(fake_user32, text):
    """Makes WM_GETTEXTLENGTH/WM_GETTEXT report back `text`, mirroring
    what a real successful WM_SETTEXT would leave behind."""
    def fake_send_message(hwnd, msg, wparam, lparam):
        if msg == mod._WM_GETTEXTLENGTH:
            return len(text)
        if msg == mod._WM_GETTEXT:
            lparam.value = text
            return len(text)
        return 0
    fake_user32.SendMessageW.side_effect = fake_send_message


class TestKeyboardDirectSuccess:
    def test_successful_direct_fill_sends_wm_settext(self, monkeypatch):
        executor, _, fake_user32, _ = _executor_with_mocks(monkeypatch)
        _configure_edit_class(fake_user32)
        _configure_readback(fake_user32, "374.84")

        result = executor._keyboard_direct(12345, "374.84")

        assert result is True
        calls = [c for c in fake_user32.SendMessageW.call_args_list
                 if c.args[1] == mod._WM_SETTEXT]
        assert calls == [((12345, mod._WM_SETTEXT, 0, "374.84"), {})]

    def test_full_keyboard_call_skips_paste_on_successful_direct_fill(self, monkeypatch):
        """The point of the whole feature: when direct-fill works, the
        clipboard/select-all/paste path must never run at all."""
        executor, fake_pyautogui, fake_user32, fake_pyperclip = _executor_with_mocks(monkeypatch)
        _configure_edit_class(fake_user32)
        _configure_readback(fake_user32, "374.84")

        issued = executor._keyboard(6, list("374.84"), "374.84", direct_fill_hwnd=12345)

        assert issued == list("374.84")
        fake_pyperclip.copy.assert_not_called()
        fake_pyautogui.hotkey.assert_not_called()


class TestKeyboardDirectFallback:
    def test_class_name_mismatch_falls_back_to_paste(self, monkeypatch):
        """A ComboBoxControl (or anything not a plain Edit) must never
        get WM_SETTEXT sent to it -- live-tested to be a silent no-op."""
        executor, fake_pyautogui, fake_user32, _ = _executor_with_mocks(monkeypatch)
        _configure_edit_class(fake_user32, class_name="ComboBox")

        result = executor._keyboard_direct(12345, "374.84")

        assert result is False
        assert not any(c.args[1] == mod._WM_SETTEXT
                        for c in fake_user32.SendMessageW.call_args_list)

    def test_full_keyboard_call_falls_back_to_paste_on_class_mismatch(self, monkeypatch):
        executor, fake_pyautogui, fake_user32, fake_pyperclip = _executor_with_mocks(monkeypatch)
        _configure_edit_class(fake_user32, class_name="ComboBox")

        issued = executor._keyboard(6, list("374.84"), "374.84", direct_fill_hwnd=12345)

        assert issued == list("374.84")
        fake_pyperclip.copy.assert_called_once_with("374.84")
        fake_pyautogui.hotkey.assert_any_call("ctrl", "a")
        fake_pyautogui.hotkey.assert_any_call("ctrl", "v")

    def test_readback_mismatch_falls_back_to_paste(self, monkeypatch):
        """WM_SETTEXT was sent without raising, but the value read back
        afterward doesn't match -- must not be trusted as a success."""
        executor, _, fake_user32, _ = _executor_with_mocks(monkeypatch)
        _configure_edit_class(fake_user32)
        _configure_readback(fake_user32, "SOMETHING ELSE")

        assert executor._keyboard_direct(12345, "374.84") is False

    def test_full_keyboard_call_falls_back_to_paste_on_readback_mismatch(self, monkeypatch):
        executor, fake_pyautogui, fake_user32, fake_pyperclip = _executor_with_mocks(monkeypatch)
        _configure_edit_class(fake_user32)
        _configure_readback(fake_user32, "SOMETHING ELSE")

        issued = executor._keyboard(6, list("374.84"), "374.84", direct_fill_hwnd=12345)

        assert issued == list("374.84")
        fake_pyperclip.copy.assert_called_once_with("374.84")

    def test_exception_during_resolution_falls_back_without_raising(self, monkeypatch):
        executor, _, fake_user32, _ = _executor_with_mocks(monkeypatch)
        fake_user32.GetClassNameW.side_effect = OSError("invalid handle")

        assert executor._keyboard_direct(999999, "x") is False  # must not raise

    def test_no_hwnd_behaves_exactly_like_before_this_feature(self, monkeypatch):
        """Backward-compat guard: direct_fill_hwnd defaults to None, and
        the paste path must be byte-for-byte the same as before this
        feature existed."""
        executor, fake_pyautogui, fake_user32, fake_pyperclip = _executor_with_mocks(monkeypatch)

        issued = executor._keyboard(6, list("374.84"), "374.84")

        assert issued == list("374.84")
        fake_pyperclip.copy.assert_called_once_with("374.84")
        assert not any(c.args[1] == mod._WM_SETTEXT
                        for c in fake_user32.SendMessageW.call_args_list)


class TestDryRunNeverTouchesRealSendMessage:
    """Direct regression guard against a real, separately-found gap: the
    checkbox BM_SETCHECK sites elsewhere in this codebase call real
    win32api.SendMessage with NO dry_run check at all. This feature must
    not repeat that mistake."""

    def test_dry_run_never_touches_real_sendmessagew(self, monkeypatch):
        executor, fake_pyautogui, fake_user32, fake_pyperclip = _executor_with_mocks(
            monkeypatch, dry_run=True)

        issued = executor._keyboard(6, list("374.84"), "374.84", direct_fill_hwnd=12345)

        assert issued == list("374.84")
        fake_user32.SendMessageW.assert_not_called()
        fake_user32.GetClassNameW.assert_not_called()
        fake_pyperclip.copy.assert_not_called()
        fake_pyautogui.hotkey.assert_not_called()

    def test_dry_run_with_no_hwnd_behaves_exactly_like_before(self, monkeypatch):
        executor, fake_pyautogui, fake_user32, fake_pyperclip = _executor_with_mocks(
            monkeypatch, dry_run=True)

        issued = executor._keyboard(6, list("374.84"), "374.84")

        assert issued == list("374.84")
        fake_user32.SendMessageW.assert_not_called()
        fake_pyperclip.copy.assert_not_called()


class TestExecuteEndToEnd:
    def test_execute_wires_direct_fill_hwnd_through_from_prediction(self, monkeypatch):
        executor, _, fake_user32, fake_pyperclip = _executor_with_mocks(monkeypatch)
        _configure_edit_class(fake_user32)
        _configure_readback(fake_user32, "374.84")

        result = executor.execute({
            "action_type": "keyboard", "key_count": 6, "keystrokes": list("374.84"),
            "text": "374.84", "direct_fill_hwnd": 12345,
        })

        assert result.action_type == "keyboard"
        assert result.success is True
        fake_pyperclip.copy.assert_not_called()

    def test_execute_without_direct_fill_hwnd_is_unchanged(self, monkeypatch):
        """Regression guard: a plain "keyboard" prediction with no
        direct_fill_hwnd key at all (the shape every existing caller
        already uses) must behave exactly as before."""
        executor, fake_pyautogui, fake_user32, fake_pyperclip = _executor_with_mocks(monkeypatch)

        result = executor.execute({
            "action_type": "keyboard", "key_count": 6, "keystrokes": list("374.84"),
            "text": "374.84",
        })

        assert result.action_type == "keyboard"
        assert result.success is True
        fake_pyperclip.copy.assert_called_once_with("374.84")
