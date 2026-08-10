"""
Regression tests for LLMAgent._select_combobox_value_via_keyboard() --
a real keyboard-navigation fallback for comboboxes whose dropdown never
renders at all when clicked, added 2026-08-10.

Verified live, directly, before writing this: clicking this project's own
car insurance form's 'Policy Status' combobox, then walking the entire
real UIA tree, found ZERO list-type elements anywhere on screen -- the
existing click-and-poll path's "still empty after N tries" warnings were
completely accurate, not a timing race (two earlier same-day fixes both
assumed a timing race and were both wrong -- see agent.py's own history
comments on the poll constants). This control exposes no Invoke/Toggle/
SelectionItem/ExpandCollapse pattern, and its ValuePattern.SetValue()
raises a raw COM error despite claiming writable. What DOES work,
confirmed directly against the real control: type-ahead (pressing the
target's first letter) and Up/Down arrow navigation, both of which
visibly change ValuePattern.Value in real time.

These tests fake uiautomation entirely via sys.modules injection (same
pattern test_modal_dialog_dismiss.py already uses for win32gui) and a
small in-memory combobox model that mirrors the REAL observed behavior:
a linear, non-wrapping option list, Down/Up move through it, a letter
jumps to the first option starting with that letter. No real screen or
window is ever touched.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _make_agent():
    return LLMAgent(goal="test goal", dry_run=True, max_steps=1)


class _FakeComboboxModel:
    """Mirrors the real, live-observed behavior: a linear, non-wrapping
    list. index=-1 means blank/unset."""
    def __init__(self, options):
        self.options = options
        self.index = -1

    @property
    def value(self):
        return "" if self.index < 0 else self.options[self.index]

    def press(self, key):
        if key == "down":
            self.index = min(self.index + 1, len(self.options) - 1)
        elif key == "up":
            self.index = max(self.index - 1, -1)
        elif len(key) == 1:
            for i, opt in enumerate(self.options):
                if opt.lower().startswith(key.lower()):
                    self.index = i
                    return


class _FakeValuePattern:
    """`.Value` must be read fresh each call (a live property backed by
    `model`, mutated by the fake executor below) -- not a snapshot taken
    once at setup time."""
    def __init__(self, model: _FakeComboboxModel):
        self._model = model

    @property
    def Value(self):
        return self._model.value


def _install_fake_uia(monkeypatch, model: _FakeComboboxModel):
    fake_ctrl = MagicMock()
    fake_ctrl.GetPattern.return_value = _FakeValuePattern(model)

    fake_uia = types.SimpleNamespace(
        ControlFromPoint=MagicMock(return_value=fake_ctrl),
        PatternId=types.SimpleNamespace(ValuePattern="ValuePattern"),
    )
    monkeypatch.setitem(sys.modules, "uiautomation", fake_uia)
    return fake_uia


def _make_executor_mock(model: _FakeComboboxModel):
    executor = MagicMock()

    def _exec(prediction):
        if prediction.get("action_type") == "keyboard":
            for key in prediction.get("keystrokes", []):
                model.press(key)
    executor.execute.side_effect = _exec
    return executor


class TestTypeAheadFastPath:
    def test_matching_first_letter_succeeds_in_one_keystroke(self, monkeypatch):
        model = _FakeComboboxModel(["Active", "Inactive", "Cancelled"])
        _install_fake_uia(monkeypatch, model)
        agent = _make_agent()
        agent._executor = _make_executor_mock(model)

        result = agent._select_combobox_value_via_keyboard("Policy Status", "Active", 100, 200)

        assert result is True
        assert model.value == "Active"
        keyboard_calls = [c.args[0] for c in agent._executor.execute.call_args_list]
        assert keyboard_calls == [{"action_type": "keyboard", "key_count": 1, "keystrokes": ["a"]}]


class TestDownArrowFallback:
    def test_falls_through_to_down_arrow_when_type_ahead_misses(self, monkeypatch):
        # Two options share the same first letter -- type-ahead lands on
        # the WRONG one first (real Windows combobox behavior), Down
        # should still reach the actual target.
        model = _FakeComboboxModel(["Active", "Archived", "Cancelled"])
        _install_fake_uia(monkeypatch, model)
        agent = _make_agent()
        agent._executor = _make_executor_mock(model)

        result = agent._select_combobox_value_via_keyboard("Policy Status", "Archived", 100, 200)

        assert result is True
        assert model.value == "Archived"

    def test_pure_down_navigation_from_blank(self, monkeypatch):
        model = _FakeComboboxModel(["Inactive", "Cancelled", "Expired", "Pending", "Lapsed"])
        _install_fake_uia(monkeypatch, model)
        agent = _make_agent()
        agent._executor = _make_executor_mock(model)

        result = agent._select_combobox_value_via_keyboard("Policy Status", "Pending", 100, 200)

        assert result is True
        assert model.value == "Pending"

    def test_stops_pressing_down_early_once_the_value_stops_changing(self, monkeypatch):
        """Found live 2026-08-10: a real run pressed Down 18 times in a
        row with the value stuck at the list's last item after press #5,
        wasting real seconds before ever trying Up -- direct user report
        right after: "so fucking slow." The list is non-wrapping, so a
        keypress that doesn't move the value means the boundary is
        reached and every further press in that direction is guaranteed
        wasted, not just unlucky -- must stop immediately, not burn the
        full max_steps budget."""
        model = _FakeComboboxModel(["Inactive", "Cancelled", "Expired"])  # only 3 real options
        _install_fake_uia(monkeypatch, model)
        agent = _make_agent()
        agent._executor = _make_executor_mock(model)

        result = agent._select_combobox_value_via_keyboard(
            "Policy Status", "Nonexistent", 100, 200, max_steps=20)

        assert result is False
        down_presses = [
            c.args[0] for c in agent._executor.execute.call_args_list
            if c.args[0].get("keystrokes") == ["down"]
        ]
        # 3 real options -> at most 3 presses to reach the end, plus the
        # one that confirms no-change -- nowhere near the 20-step budget.
        assert len(down_presses) <= 4, f"expected an early stop, got {len(down_presses)} Down presses"


class TestUpArrowFallback:
    def test_walks_back_up_when_target_is_before_the_current_position(self, monkeypatch):
        """The real list doesn't wrap -- confirmed live. If Down alone
        would overshoot past the target (starting position already past
        it), Up must still find it."""
        model = _FakeComboboxModel(["Active", "Inactive", "Cancelled", "Expired"])
        model.index = 3  # starts at "Expired", the very end
        _install_fake_uia(monkeypatch, model)
        agent = _make_agent()
        agent._executor = _make_executor_mock(model)

        result = agent._select_combobox_value_via_keyboard("Policy Status", "Active", 100, 200)

        assert result is True
        assert model.value == "Active"


class TestGenuineFailure:
    def test_returns_false_when_the_value_never_exists_in_the_list(self, monkeypatch):
        model = _FakeComboboxModel(["Active", "Inactive"])
        _install_fake_uia(monkeypatch, model)
        agent = _make_agent()
        agent._executor = _make_executor_mock(model)

        result = agent._select_combobox_value_via_keyboard(
            "Policy Status", "Nonexistent", 100, 200, max_steps=5)

        assert result is False


class TestGracefulDegradation:
    def test_returns_false_immediately_when_uiautomation_unavailable(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "uiautomation":
                raise ImportError("simulated missing uiautomation")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        agent = _make_agent()
        agent._executor = MagicMock()

        result = agent._select_combobox_value_via_keyboard("Policy Status", "Active", 100, 200)

        assert result is False
        agent._executor.execute.assert_not_called()

    def test_a_live_read_exception_does_not_crash_the_search(self, monkeypatch):
        fake_ctrl = MagicMock()
        fake_ctrl.GetPattern.side_effect = RuntimeError("COM error")
        fake_uia = types.SimpleNamespace(
            ControlFromPoint=MagicMock(return_value=fake_ctrl),
            PatternId=types.SimpleNamespace(ValuePattern="ValuePattern"),
        )
        monkeypatch.setitem(sys.modules, "uiautomation", fake_uia)
        agent = _make_agent()
        agent._executor = MagicMock()

        result = agent._select_combobox_value_via_keyboard(
            "Policy Status", "Active", 100, 200, max_steps=2)

        assert result is False  # must not raise


class TestGhostOverlayStoppedForTheWholeCall:
    """Found live 2026-08-10: every _live_value() read in this function
    was silently resolving to the ghost overlay's own window instead of
    the real combobox, because that overlay covers the entire screen for
    its whole lifetime. The only mechanism confirmed (live, directly) to
    actually fix this is stopping the overlay entirely -- but that has
    real overhead (thread teardown + a fresh Tk window), so it must
    happen ONCE for the whole keyboard-fallback call, not once per
    keystroke read (which would multiply that cost by up to ~40x across
    the type-ahead + Down walk + Up walk)."""

    def test_hide_for_uia_read_called_exactly_once_regardless_of_keystroke_count(self, monkeypatch):
        # Long, non-matching list -- burns the full Down AND Up budget,
        # i.e. many _live_value() calls, to prove the hide/restore isn't
        # happening per-read.
        model = _FakeComboboxModel([f"Option{i}" for i in range(10)])
        _install_fake_uia(monkeypatch, model)
        agent = _make_agent()
        agent._executor = _make_executor_mock(model)
        agent._executor.ghost = MagicMock()
        agent._executor.ghost.hide_for_uia_read.return_value = True

        agent._select_combobox_value_via_keyboard(
            "Policy Status", "Nonexistent", 100, 200, max_steps=10)

        assert agent._executor.ghost.hide_for_uia_read.call_count == 1
        assert agent._executor.ghost.restore_after_uia_read.call_count == 1

    def test_restore_not_called_if_hide_reports_nothing_was_stopped(self, monkeypatch):
        """hide_for_uia_read() returns False when the overlay was never
        running (e.g. tests, or a dry_run with no real overlay started) --
        restore_after_uia_read() must not be called in that case, since
        that would start a brand-new overlay that was never meant to
        exist in the first place."""
        model = _FakeComboboxModel(["Active", "Inactive"])
        _install_fake_uia(monkeypatch, model)
        agent = _make_agent()
        agent._executor = _make_executor_mock(model)
        agent._executor.ghost = MagicMock()
        agent._executor.ghost.hide_for_uia_read.return_value = False

        agent._select_combobox_value_via_keyboard("Policy Status", "Active", 100, 200)

        agent._executor.ghost.restore_after_uia_read.assert_not_called()

    def test_restore_still_happens_after_an_exception(self, monkeypatch):
        """The overlay must never be left permanently stopped just because
        something else in the search loop raised."""
        fake_ctrl = MagicMock()
        fake_ctrl.GetPattern.side_effect = RuntimeError("COM error")
        fake_uia = types.SimpleNamespace(
            ControlFromPoint=MagicMock(return_value=fake_ctrl),
            PatternId=types.SimpleNamespace(ValuePattern="ValuePattern"),
        )
        monkeypatch.setitem(sys.modules, "uiautomation", fake_uia)
        agent = _make_agent()
        agent._executor = MagicMock()
        agent._executor.ghost = MagicMock()
        agent._executor.ghost.hide_for_uia_read.return_value = True

        agent._select_combobox_value_via_keyboard(
            "Policy Status", "Active", 100, 200, max_steps=2)

        agent._executor.ghost.restore_after_uia_read.assert_called_once()

    def test_no_ghost_attribute_does_not_raise(self, monkeypatch):
        """dry_run executors and some test doubles have no .ghost at all --
        getattr(..., None) must handle that instead of an AttributeError."""
        model = _FakeComboboxModel(["Active", "Inactive"])
        _install_fake_uia(monkeypatch, model)
        agent = _make_agent()
        agent._executor = _make_executor_mock(model)
        del agent._executor.ghost  # MagicMock auto-creates it; remove to simulate a plain object without one

        result = agent._select_combobox_value_via_keyboard("Policy Status", "Active", 100, 200)

        assert result is True
