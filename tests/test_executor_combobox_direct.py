"""
tests/test_executor_combobox_direct.py
=========================================
Regression tests for agent/executor.py's CB_SETCURSEL direct-select fast
path -- the combobox counterpart to _keyboard_direct's WM_SETTEXT (text
fields) and the existing BM_SETCHECK mechanism (checkboxes).

Built 2026-08-14, same night as the text-field direct-fill work, direct
request ("still using the mouse click" for dropdowns). Live-tested
against a real combobox on the running practice form (read-only/
reversible): its real options were read directly via
CB_GETCOUNT/CB_GETLBTEXT (no dropdown ever opened), CB_SETCURSEL was sent
to select one, and the selection was confirmed two independent ways
(CB_GETCURSEL's own readback, and UIA's ValuePattern) that agreed with
each other, then restored.

This is a genuinely new action_type ("combobox_select") -- unlike the
text-field case, today's click-based combobox filling never sets
action_type == "keyboard" at all (it's two separate "click" actions in
agent.py), so there was no existing gate to widen or risk missing.
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

    executor = mod.ActionExecutor(dry_run=dry_run, ghost_cursor=False)
    return executor, fake_user32


def _configure_combobox(fake_user32, class_name="ComboBox", options=(), selected_after=None):
    """Wires fake_user32.SendMessageW to behave like a real Win32
    combobox: CB_GETCOUNT/CB_GETLBTEXTLEN/CB_GETLBTEXT enumerate
    `options`; CB_SETCURSEL "selects" (recorded in a mutable cell so
    CB_GETCURSEL can read it back); `selected_after` overrides what
    CB_GETCURSEL reports post-select, for mismatch tests."""
    def fake_get_class_name(hwnd, buf, size):
        buf.value = class_name
        return len(class_name)
    fake_user32.GetClassNameW.side_effect = fake_get_class_name

    state = {"cursel": -1}

    def fake_send_message(hwnd, msg, wparam, lparam):
        if msg == mod._CB_GETCOUNT:
            return len(options)
        if msg == mod._CB_GETLBTEXTLEN:
            return len(options[wparam])
        if msg == mod._CB_GETLBTEXT:
            lparam.value = options[wparam]
            return len(options[wparam])
        if msg == mod._CB_SETCURSEL:
            state["cursel"] = wparam
            return wparam
        if msg == mod._CB_GETCURSEL:
            return selected_after if selected_after is not None else state["cursel"]
        return 0

    fake_user32.SendMessageW.side_effect = fake_send_message
    return state


class TestComboboxDirectSuccess:
    def test_selects_matching_option_via_cb_setcursel(self, monkeypatch):
        executor, fake_user32 = _executor_with_mocks(monkeypatch)
        _configure_combobox(fake_user32, options=["", "Monthly", "Quarterly", "Annual"])

        result = executor._combobox_direct(12345, "Monthly")

        assert result is True
        setcursel_calls = [c for c in fake_user32.SendMessageW.call_args_list
                            if c.args[1] == mod._CB_SETCURSEL]
        assert setcursel_calls == [((12345, mod._CB_SETCURSEL, 1, 0), {})]

    def test_match_is_case_and_whitespace_insensitive(self, monkeypatch):
        executor, fake_user32 = _executor_with_mocks(monkeypatch)
        _configure_combobox(fake_user32, options=["", "Monthly", "Quarterly"])

        assert executor._combobox_direct(12345, "  monthly  ") is True

    def test_full_execute_wires_combobox_hwnd_through(self, monkeypatch):
        executor, fake_user32 = _executor_with_mocks(monkeypatch)
        _configure_combobox(fake_user32, options=["", "Active", "Lapsed"])

        result = executor.execute({
            "action_type": "combobox_select", "text": "Active",
            "combobox_hwnd": 999,
        })

        assert result.action_type == "combobox_select"
        assert result.success is True


class TestComboboxDirectFallback:
    def test_class_name_mismatch_returns_false(self, monkeypatch):
        """An EditControl or anything else must never get CB_ messages
        sent to it."""
        executor, fake_user32 = _executor_with_mocks(monkeypatch)
        _configure_combobox(fake_user32, class_name="Edit", options=["Monthly"])

        assert executor._combobox_direct(12345, "Monthly") is False

    def test_no_matching_option_returns_false(self, monkeypatch):
        """The known value isn't one of the real options -- must fall
        back to the existing click-based open+select, not guess."""
        executor, fake_user32 = _executor_with_mocks(monkeypatch)
        _configure_combobox(fake_user32, options=["", "Monthly", "Quarterly"])

        assert executor._combobox_direct(12345, "Biweekly") is False
        assert not any(c.args[1] == mod._CB_SETCURSEL
                        for c in fake_user32.SendMessageW.call_args_list)

    def test_readback_mismatch_returns_false(self, monkeypatch):
        executor, fake_user32 = _executor_with_mocks(monkeypatch)
        _configure_combobox(fake_user32, options=["", "Monthly", "Quarterly"], selected_after=0)

        assert executor._combobox_direct(12345, "Monthly") is False

    def test_exception_falls_back_without_raising(self, monkeypatch):
        executor, fake_user32 = _executor_with_mocks(monkeypatch)
        fake_user32.GetClassNameW.side_effect = OSError("invalid handle")

        assert executor._combobox_direct(999999, "Monthly") is False  # must not raise

    def test_full_execute_reports_failure_cleanly(self, monkeypatch):
        executor, fake_user32 = _executor_with_mocks(monkeypatch)
        _configure_combobox(fake_user32, options=["", "Monthly"])

        result = executor.execute({
            "action_type": "combobox_select", "text": "Nonexistent",
            "combobox_hwnd": 999,
        })

        assert result.action_type == "combobox_select"
        assert result.success is False

    def test_no_hwnd_fails_cleanly_without_touching_sendmessage(self, monkeypatch):
        executor, fake_user32 = _executor_with_mocks(monkeypatch)

        result = executor.execute({"action_type": "combobox_select", "text": "Monthly"})

        assert result.success is False
        fake_user32.SendMessageW.assert_not_called()


class TestComboboxDirectDryRun:
    def test_dry_run_never_touches_real_sendmessagew(self, monkeypatch):
        executor, fake_user32 = _executor_with_mocks(monkeypatch, dry_run=True)

        result = executor.execute({
            "action_type": "combobox_select", "text": "Monthly",
            "combobox_hwnd": 12345,
        })

        assert result.success is True  # dry-run "would have" succeeded, logged only
        fake_user32.SendMessageW.assert_not_called()
        fake_user32.GetClassNameW.assert_not_called()
