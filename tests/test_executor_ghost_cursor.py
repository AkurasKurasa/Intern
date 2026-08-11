"""
tests/test_executor_ghost_cursor.py
=====================================
Regression tests for agent/executor.py's ghost-cursor/ghost-caret wiring
-- direct user request ("We need a ghost cursor and ghost caret real
bad") to stop hijacking the real OS mouse cursor during a live run.

Two things matter most here, both covered explicitly:

1. Backward compatibility / test-suite safety: ghost_cursor defaults to
   OFF. Dozens of existing tests (and run_transformer.py's --dry_run path)
   construct ActionExecutor(dry_run=False) directly -- if this ever
   defaulted to spawning a real overlay window, every one of those would
   start silently popping up windows during `pytest tests/`. Only
   agent.py's and run_transformer.py's live-run construction sites pass
   ghost_cursor=True.

2. _try_uia_invoke() must actually skip the real pyautogui click when it
   succeeds (that's the entire point -- the real cursor must not move),
   and must fall back to the exact previous behavior when it doesn't.

GhostOverlay itself is never really constructed here -- always a
MagicMock passed in as `ghost=`, or the real class monkeypatched out at
the module level before construction. No test in this file spawns a real
overlay window (see tests/test_ghost_overlay.py for that).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import agent.executor as mod


def _executor_with_mocks(monkeypatch, uia_available=True, ghost_cursor=False):
    fake_pyautogui = MagicMock()
    monkeypatch.setattr(mod, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(mod, "_PYAUTOGUI_AVAILABLE", True)

    fake_uia = MagicMock()
    fake_uia.PatternId.InvokePattern = "InvokePattern"
    fake_uia.PatternId.TogglePattern = "TogglePattern"
    fake_uia.PatternId.SelectionItemPattern = "SelectionItemPattern"
    monkeypatch.setattr(mod, "_uia", fake_uia)
    monkeypatch.setattr(mod, "_UIA_AVAILABLE", uia_available)

    # Safety-critical: without this, _try_semantic_click's default falls
    # through to the REAL ctypes.windll.user32, which would run a real
    # WindowFromPoint/SendMessageW against whatever's actually on screen
    # at the test's coordinates every time a test below calls _click()
    # with a failed/absent UIA invoke. Defaulting the fake's return to a
    # falsy hwnd (0) keeps _try_semantic_click a clean no-op unless a
    # test explicitly configures otherwise.
    fake_user32 = MagicMock()
    fake_user32.WindowFromPoint.return_value = 0
    monkeypatch.setattr(mod, "_user32", fake_user32)

    executor = mod.ActionExecutor(dry_run=False, ghost_cursor=ghost_cursor)
    return executor, fake_pyautogui, fake_uia, fake_user32


class TestGhostCursorOptInDefaultsOff:
    def test_ghost_is_none_by_default_even_when_live(self, monkeypatch):
        """The critical regression guard: constructing a live executor
        the way dozens of existing tests already do must never spawn a
        real overlay window."""
        fake_ghost_cls = MagicMock()
        monkeypatch.setattr("agent.ghost_overlay.GhostOverlay", fake_ghost_cls, raising=False)
        executor, _, _, _ = _executor_with_mocks(monkeypatch, ghost_cursor=False)

        assert executor.ghost is None
        fake_ghost_cls.assert_not_called()

    def test_ghost_stays_none_in_dry_run_even_if_requested(self, monkeypatch):
        monkeypatch.setattr(mod, "pyautogui", MagicMock())
        monkeypatch.setattr(mod, "_PYAUTOGUI_AVAILABLE", True)
        executor = mod.ActionExecutor(dry_run=True, ghost_cursor=True)
        assert executor.ghost is None

    def test_ghost_is_created_and_started_when_explicitly_requested(self, monkeypatch):
        fake_overlay_instance = MagicMock()
        fake_ghost_cls = MagicMock(return_value=fake_overlay_instance)
        monkeypatch.setitem(sys.modules, "components.agent.ghost_overlay",
                             MagicMock(GhostOverlay=fake_ghost_cls))
        monkeypatch.setitem(sys.modules, "agent.ghost_overlay",
                             MagicMock(GhostOverlay=fake_ghost_cls))
        executor, _, _, _ = _executor_with_mocks(monkeypatch, ghost_cursor=True)

        fake_ghost_cls.assert_called_once()
        fake_overlay_instance.start.assert_called_once()
        assert executor.ghost is fake_overlay_instance

    def test_a_ghost_overlay_start_failure_is_caught_not_raised(self, monkeypatch):
        fake_ghost_cls = MagicMock(side_effect=RuntimeError("no display"))
        monkeypatch.setitem(sys.modules, "components.agent.ghost_overlay",
                             MagicMock(GhostOverlay=fake_ghost_cls))
        monkeypatch.setitem(sys.modules, "agent.ghost_overlay",
                             MagicMock(GhostOverlay=fake_ghost_cls))
        executor, _, _, _ = _executor_with_mocks(monkeypatch, ghost_cursor=True)  # must not raise

        assert executor.ghost is None


class TestTryUiaInvoke:
    def test_returns_false_immediately_when_uia_unavailable(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch, uia_available=False)
        assert executor._try_uia_invoke(10, 10) is False
        fake_uia.ControlFromPoint.assert_not_called()

    def test_returns_false_when_no_control_at_point(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch)
        fake_uia.ControlFromPoint.return_value = None
        assert executor._try_uia_invoke(10, 10) is False

    def test_invokes_invoke_pattern_when_available(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch)
        fake_ctrl = MagicMock()
        fake_invoke = MagicMock()
        fake_ctrl.GetPattern.side_effect = lambda pid: fake_invoke if pid == "InvokePattern" else None
        fake_uia.ControlFromPoint.return_value = fake_ctrl

        result = executor._try_uia_invoke(10, 10)

        assert result is True
        fake_invoke.Invoke.assert_called_once()

    def test_falls_through_to_toggle_pattern_when_no_invoke(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch)
        fake_ctrl = MagicMock()
        fake_toggle = MagicMock()
        fake_ctrl.GetPattern.side_effect = lambda pid: fake_toggle if pid == "TogglePattern" else None
        fake_uia.ControlFromPoint.return_value = fake_ctrl

        result = executor._try_uia_invoke(10, 10)

        assert result is True
        fake_toggle.Toggle.assert_called_once()

    def test_falls_through_to_selection_item_pattern_last(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch)
        fake_ctrl = MagicMock()
        fake_select = MagicMock()
        fake_ctrl.GetPattern.side_effect = (
            lambda pid: fake_select if pid == "SelectionItemPattern" else None
        )
        fake_uia.ControlFromPoint.return_value = fake_ctrl

        result = executor._try_uia_invoke(10, 10)

        assert result is True
        fake_select.Select.assert_called_once()

    def test_returns_false_when_control_supports_no_usable_pattern(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch)
        fake_ctrl = MagicMock()
        fake_ctrl.GetPattern.return_value = None
        fake_uia.ControlFromPoint.return_value = fake_ctrl

        assert executor._try_uia_invoke(10, 10) is False

    def test_an_exception_is_caught_and_treated_as_no_invoke(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch)
        fake_uia.ControlFromPoint.side_effect = RuntimeError("COM not initialized")

        assert executor._try_uia_invoke(10, 10) is False  # must not raise


class TestTrySemanticClick:
    """Regression tests for the BM_CLICK semantic-click tier, added
    2026-08-11 per explicit direction ("use semantic, not precise") as a
    middle tier between _try_uia_invoke() and the raw pyautogui click.
    """

    def test_returns_false_when_no_window_at_point(self, monkeypatch):
        executor, _, _, fake_user32 = _executor_with_mocks(monkeypatch)
        fake_user32.WindowFromPoint.return_value = 0

        assert executor._try_semantic_click(10, 10) is False
        fake_user32.SendMessageW.assert_not_called()

    def test_sends_bm_click_to_a_button_class_window(self, monkeypatch):
        executor, _, _, fake_user32 = _executor_with_mocks(monkeypatch)
        fake_user32.WindowFromPoint.return_value = 12345

        def fake_get_class_name(hwnd, buf, size):
            buf.value = "Button"
            return len("Button")
        fake_user32.GetClassNameW.side_effect = fake_get_class_name

        result = executor._try_semantic_click(10, 10)

        assert result is True
        fake_user32.SendMessageW.assert_called_once_with(12345, mod._BM_CLICK, 0, 0)

    def test_returns_false_for_a_non_button_window_class(self, monkeypatch):
        """Edit/ComboBox/custom controls have no semantic equivalent for
        a pixel click -- must fall through, not misfire BM_CLICK at a
        control that won't understand it."""
        executor, _, _, fake_user32 = _executor_with_mocks(monkeypatch)
        fake_user32.WindowFromPoint.return_value = 12345

        def fake_get_class_name(hwnd, buf, size):
            buf.value = "Edit"
            return len("Edit")
        fake_user32.GetClassNameW.side_effect = fake_get_class_name

        assert executor._try_semantic_click(10, 10) is False
        fake_user32.SendMessageW.assert_not_called()

    def test_an_exception_is_caught_and_treated_as_no_semantic_click(self, monkeypatch):
        executor, _, _, fake_user32 = _executor_with_mocks(monkeypatch)
        fake_user32.WindowFromPoint.side_effect = OSError("access violation")

        assert executor._try_semantic_click(10, 10) is False  # must not raise


class TestClickTriesSemanticClickBeforeRealCursor:
    def test_semantic_click_used_when_invoke_fails_but_button_found(self, monkeypatch):
        """When UIA invoke can't handle it but the target is a real
        Button-class control, the semantic tier must be used instead of
        a real pyautogui click -- the real cursor still never moves."""
        executor, fake_pyautogui, fake_uia, fake_user32 = _executor_with_mocks(monkeypatch)
        fake_uia.ControlFromPoint.return_value = None  # UIA invoke fails
        fake_user32.WindowFromPoint.return_value = 999

        def fake_get_class_name(hwnd, buf, size):
            buf.value = "Button"
            return len("Button")
        fake_user32.GetClassNameW.side_effect = fake_get_class_name

        executor._click(15, 25)

        fake_user32.SendMessageW.assert_called_once_with(999, mod._BM_CLICK, 0, 0)
        fake_pyautogui.moveTo.assert_not_called()
        fake_pyautogui.click.assert_not_called()

    def test_falls_back_to_pyautogui_when_neither_tier_applies(self, monkeypatch):
        """Full regression chain: UIA invoke fails, semantic click finds
        nothing usable, so the original real-click behavior still fires
        exactly as before either tier existed."""
        executor, fake_pyautogui, fake_uia, fake_user32 = _executor_with_mocks(monkeypatch)
        fake_uia.ControlFromPoint.return_value = None
        fake_user32.WindowFromPoint.return_value = 0

        executor._click(15, 25)

        fake_pyautogui.moveTo.assert_called_once_with(15, 25, duration=0.15)
        fake_pyautogui.click.assert_called_once_with(15, 25)


class TestClickSkipsRealCursorOnSuccessfulInvoke:
    def test_successful_invoke_skips_pyautogui_entirely(self, monkeypatch):
        """The entire point of this feature: when UIA invoke succeeds,
        the real OS cursor must never move."""
        executor, fake_pyautogui, fake_uia, _ = _executor_with_mocks(monkeypatch)
        fake_ctrl = MagicMock()
        fake_invoke = MagicMock()
        fake_ctrl.GetPattern.side_effect = lambda pid: fake_invoke if pid == "InvokePattern" else None
        fake_uia.ControlFromPoint.return_value = fake_ctrl

        executor._click(15, 25)

        fake_pyautogui.moveTo.assert_not_called()
        fake_pyautogui.click.assert_not_called()

    def test_failed_invoke_falls_back_to_a_real_coordinate_click(self, monkeypatch):
        """Regression check: the previous, always-real-click behavior
        must still work exactly as before when invoke isn't usable."""
        executor, fake_pyautogui, fake_uia, _ = _executor_with_mocks(monkeypatch)
        fake_uia.ControlFromPoint.return_value = None

        executor._click(15, 25)

        fake_pyautogui.moveTo.assert_called_once_with(15, 25, duration=0.15)
        fake_pyautogui.click.assert_called_once_with(15, 25)

    def test_ghost_show_cursor_called_regardless_of_invoke_outcome(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch, ghost_cursor=False)
        fake_ghost = MagicMock()
        executor.ghost = fake_ghost  # simulate a successfully-started overlay
        fake_uia.ControlFromPoint.return_value = None  # invoke fails -> falls back

        executor._click(30, 40)

        fake_ghost.show_cursor.assert_called_once_with(30, 40)

    def test_a_ghost_show_cursor_exception_does_not_break_the_click(self, monkeypatch):
        executor, fake_pyautogui, fake_uia, _ = _executor_with_mocks(monkeypatch)
        fake_ghost = MagicMock()
        fake_ghost.show_cursor.side_effect = RuntimeError("queue broken")
        executor.ghost = fake_ghost
        fake_uia.ControlFromPoint.return_value = None

        executor._click(1, 2)  # must not raise

        fake_pyautogui.click.assert_called_once_with(1, 2)

    def test_dry_run_never_touches_uia_or_ghost(self, monkeypatch):
        monkeypatch.setattr(mod, "pyautogui", MagicMock())
        monkeypatch.setattr(mod, "_PYAUTOGUI_AVAILABLE", True)
        fake_uia = MagicMock()
        monkeypatch.setattr(mod, "_uia", fake_uia)
        monkeypatch.setattr(mod, "_UIA_AVAILABLE", True)
        executor = mod.ActionExecutor(dry_run=True)
        executor.ghost = MagicMock()

        executor._click(5, 5)

        fake_uia.ControlFromPoint.assert_not_called()
        executor.ghost.show_cursor.assert_not_called()


class TestGhostCaretPlacement:
    def test_no_op_when_ghost_is_none(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch)
        executor.ghost = None
        executor._show_ghost_caret()  # must not raise
        fake_uia.GetFocusedControl.assert_not_called()

    def test_no_op_when_uia_unavailable(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch, uia_available=False)
        executor.ghost = MagicMock()
        executor._show_ghost_caret()
        executor.ghost.show_caret.assert_not_called()

    def test_shows_caret_at_focused_control_bounding_rect(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch)
        executor.ghost = MagicMock()
        fake_rect = MagicMock(left=100, top=200, right=260, bottom=224)
        fake_focused = MagicMock(BoundingRectangle=fake_rect)
        fake_uia.GetFocusedControl.return_value = fake_focused

        executor._show_ghost_caret()

        executor.ghost.show_caret.assert_called_once_with(104, 203, 18)

    def test_no_op_when_nothing_focused(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch)
        executor.ghost = MagicMock()
        fake_uia.GetFocusedControl.return_value = None

        executor._show_ghost_caret()

        executor.ghost.show_caret.assert_not_called()

    def test_no_op_when_rect_is_degenerate(self, monkeypatch):
        """A zero-size rect (e.g. a hidden/collapsed element) must not
        produce a visible caret sitting at a meaningless size."""
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch)
        executor.ghost = MagicMock()
        fake_rect = MagicMock(left=50, top=50, right=50, bottom=50)
        fake_uia.GetFocusedControl.return_value = MagicMock(BoundingRectangle=fake_rect)

        executor._show_ghost_caret()

        executor.ghost.show_caret.assert_not_called()

    def test_an_exception_reading_focus_is_caught(self, monkeypatch):
        executor, _, fake_uia, _ = _executor_with_mocks(monkeypatch)
        executor.ghost = MagicMock()
        fake_uia.GetFocusedControl.side_effect = RuntimeError("COM not initialized")

        executor._show_ghost_caret()  # must not raise

    def test_keyboard_action_shows_caret_when_live(self, monkeypatch):
        executor, fake_pyautogui, fake_uia, _ = _executor_with_mocks(monkeypatch)
        executor.ghost = MagicMock()
        fake_uia.GetFocusedControl.return_value = None  # no-op inside, but must be called

        executor._keyboard(1, ["tab"], "")

        fake_uia.GetFocusedControl.assert_called_once()

    def test_keyboard_action_does_not_show_caret_in_dry_run(self, monkeypatch):
        monkeypatch.setattr(mod, "pyautogui", MagicMock())
        monkeypatch.setattr(mod, "_PYAUTOGUI_AVAILABLE", True)
        fake_uia = MagicMock()
        monkeypatch.setattr(mod, "_uia", fake_uia)
        monkeypatch.setattr(mod, "_UIA_AVAILABLE", True)
        executor = mod.ActionExecutor(dry_run=True)
        executor.ghost = MagicMock()

        executor._keyboard(1, ["tab"], "")

        fake_uia.GetFocusedControl.assert_not_called()
