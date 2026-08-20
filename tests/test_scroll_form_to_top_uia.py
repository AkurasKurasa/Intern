"""
Regression test for agent.py's _scroll_form_to_top_uia_percent -- a safe,
UIA-only replacement for _scroll_form_to_top when resetting scroll position
on an ordinary tab switch.

Real live bug, two rounds: (1) direct report ("Driver 2 was still not
filled") traced to a tab switch never resetting scroll position, so a long
tab (Coverage) scrolled deep down could leave the newly-active Drivers tab
starting mid-page, silently scrolling Driver 2's topmost fields out of the
initial viewport. (2) Fixed by wiring the EXISTING _scroll_form_to_top into
the ordinary tab-click path -- but that function does a REAL physical mouse
click (to give the panel keyboard focus), Ctrl+Home, and several scroll-
wheel events. Safe as a rare, near-never-hit fallback (its only prior use),
but running it on every ordinary tab switch caused a real regression,
direct report: "it got slower... so many wrong things got filled" -- a
stray click landing wrong, or a scroll-wheel event catching a combobox and
changing its value. Reverted.

This is the safer replacement: jumps the scrollable pane directly to 0%
via ScrollPattern.SetScrollPercent() -- ONE native UIA call, no mouse
movement, no click, no keyboard hotkey, no scroll-wheel simulation at all.
Reuses the exact same ScrollPattern mechanism already proven live for
_scroll_form_down_uia_percent (see test_scroll_form_down_uia.py), just
targeting the top instead of a computed downward position.

Real UI Automation isn't available in a test environment, so
`uiautomation`/`win32gui` are faked via sys.modules injection, matching
test_scroll_form_down_uia.py's own established pattern exactly.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _fake_self():
    from agent.agent import LLMAgent
    fake = MagicMock()
    fake._find_scrollable_pane_uia = types.MethodType(LLMAgent._find_scrollable_pane_uia, fake)
    fake._locked_hwnd = 12345
    return fake


class _ScrollAmount:
    NoAmount = "NoAmount"
    SmallIncrement = "SmallIncrement"


class _ScrollPattern:
    NoScrollValue = -1


def _install_fake_uia_modules(anchor_control):
    fake_uia = types.ModuleType("uiautomation")
    fake_uia.ScrollAmount = _ScrollAmount
    fake_uia.ScrollPattern = _ScrollPattern
    fake_uia.ControlFromHandle = MagicMock(return_value=anchor_control.owner_root)
    fake_uia.GetFocusedControl = MagicMock(return_value=anchor_control)

    fake_w32g = types.ModuleType("win32gui")
    fake_w32g.GetForegroundWindow = MagicMock(return_value=12345)

    sys.modules["uiautomation"] = fake_uia
    sys.modules["win32gui"] = fake_w32g
    return fake_uia, fake_w32g


class _FakeControl:
    def __init__(self, name="", scroll_pattern=None, parent=None, bounding_rect=None):
        self.Name = name
        self._scroll_pattern = scroll_pattern
        self._parent = parent
        self.owner_root = self
        if bounding_rect is None:
            bounding_rect = MagicMock()
            bounding_rect.top = 100
        self._bounding_rect = bounding_rect

    def EditControl(self, searchDepth=25, Name=""):
        return self

    def Exists(self, maxSearchSeconds=0.2):
        return True

    def GetScrollPattern(self):
        return self._scroll_pattern

    def GetParentControl(self):
        return self._parent

    @property
    def BoundingRectangle(self):
        return self._bounding_rect


def _panel_and_anchor(cur_percent):
    scroll_pattern = MagicMock()
    scroll_pattern.VerticallyScrollable = True
    scroll_pattern.VerticalScrollPercent = cur_percent
    panel = _FakeControl(name="tab_drivers", scroll_pattern=scroll_pattern)
    anchor = _FakeControl(name="First Name", parent=panel)
    anchor.owner_root = anchor
    return panel, anchor, scroll_pattern


def _state():
    return {"elements": [
        {"type": "editcontrol", "label": "First Name", "window_role": "active"},
    ]}


class TestScrollFormToTopUiaPercent:
    def test_jumps_to_zero_percent_when_not_already_at_top(self):
        from agent.agent import LLMAgent

        panel, anchor, scroll_pattern = _panel_and_anchor(cur_percent=45.0)
        _install_fake_uia_modules(anchor)
        fake_self = _fake_self()

        result = LLMAgent._scroll_form_to_top_uia_percent(fake_self, _state())

        assert result is True
        scroll_pattern.SetScrollPercent.assert_called_once()
        args = scroll_pattern.SetScrollPercent.call_args[0]
        assert args[0] == -1     # NoScrollValue -- horizontal untouched
        assert args[1] == 0.0    # jumps straight to the top

    def test_already_at_top_returns_true_without_calling_setscrollpercent(self):
        """Nothing to reset -- must not issue a needless call."""
        from agent.agent import LLMAgent

        panel, anchor, scroll_pattern = _panel_and_anchor(cur_percent=0.0)
        _install_fake_uia_modules(anchor)
        fake_self = _fake_self()

        result = LLMAgent._scroll_form_to_top_uia_percent(fake_self, _state())

        assert result is True
        scroll_pattern.SetScrollPercent.assert_not_called()

    def test_no_mouse_or_keyboard_module_is_ever_touched(self):
        """The whole point of this replacement -- confirm no pyautogui
        IMPORT anywhere in its source (the docstring legitimately mentions
        the word while explaining why the OLD function was risky), the
        exact mechanism that caused the prior regression."""
        import inspect
        from agent.agent import LLMAgent
        src = inspect.getsource(LLMAgent._scroll_form_to_top_uia_percent)
        assert "import pyautogui" not in src
        assert ".click(" not in src
        assert "hotkey(" not in src
        assert ".scroll(" not in src

    def test_returns_false_when_no_scrollable_pane_is_found(self):
        from agent.agent import LLMAgent

        anchor = _FakeControl(name="First Name", parent=None)
        anchor.owner_root = anchor
        _install_fake_uia_modules(anchor)
        fake_self = _fake_self()

        result = LLMAgent._scroll_form_to_top_uia_percent(fake_self, _state())
        assert result is False

    def test_returns_false_when_not_vertically_scrollable(self):
        from agent.agent import LLMAgent

        scroll_pattern = MagicMock()
        scroll_pattern.VerticallyScrollable = False
        panel = _FakeControl(name="tab_drivers", scroll_pattern=scroll_pattern)
        anchor = _FakeControl(name="First Name", parent=panel)
        anchor.owner_root = anchor
        _install_fake_uia_modules(anchor)
        fake_self = _fake_self()

        result = LLMAgent._scroll_form_to_top_uia_percent(fake_self, _state())
        assert result is False
        scroll_pattern.SetScrollPercent.assert_not_called()

    def test_returns_false_when_uiautomation_is_unavailable(self):
        from agent.agent import LLMAgent

        sys.modules.pop("uiautomation", None)
        sys.modules.pop("win32gui", None)
        real_import = __import__

        def _blocked_import(name, *a, **kw):
            if name in ("uiautomation", "win32gui"):
                raise ImportError(f"no {name} in this test env")
            return real_import(name, *a, **kw)

        import builtins
        fake_self = _fake_self()

        orig = builtins.__import__
        builtins.__import__ = _blocked_import
        try:
            result = LLMAgent._scroll_form_to_top_uia_percent(fake_self, {"elements": []})
        finally:
            builtins.__import__ = orig

        assert result is False

    def test_never_raises_on_a_scroll_pattern_exception(self):
        from agent.agent import LLMAgent

        panel, anchor, scroll_pattern = _panel_and_anchor(cur_percent=45.0)
        scroll_pattern.SetScrollPercent.side_effect = Exception("boom")
        _install_fake_uia_modules(anchor)
        fake_self = _fake_self()

        result = LLMAgent._scroll_form_to_top_uia_percent(fake_self, _state())
        assert result is False
