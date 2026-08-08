"""
Regression test for agent.py's _scroll_form_down_uia -- the revived native
UIA ScrollPattern scroll route (see navigation_protocol.py's optimal-view
redesign and DEVELOPERS.md -> execution_scroll_reveals_only_one_field).

Found 2026-08-08, live, immediately after reviving this route: it used
ScrollAmount.LargeIncrement -- a full native "page jump," much bigger than
the old wheel-fallback's small -5/-15/-25 unit nudges. Combined with the new
optimal-view decide() (which already loops, re-scrolling as many times as
needed until cur==best), one LargeIncrement call overshot so far that the
very first field on the tab ('First Name') vanished from the post-scroll
snapshot entirely, confirmed by [SCROLL-DIAG] in the log -- and since this
protocol only scrolls DOWN, never back up, that content was permanently lost
for the rest of the run (task completion 2.3% that run). Fixed by switching
to ScrollAmount.SmallIncrement, since decide()'s own loop is what makes
several small scrolls the correct shape now, not one large one.

Real UI Automation isn't available in a test environment, so `uiautomation`
and `win32gui` are faked via sys.modules injection -- this test's job is
narrowly to prove the SCROLL AMOUNT CHOICE, not to test real UIA plumbing.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _fake_self():
    """A stand-in for `self` that binds the REAL _find_scrollable_pane_uia
    method -- agent.py refactored _scroll_form_down_uia to share pane-
    finding with _scroll_form_down_uia_percent. A bare MagicMock would
    auto-generate a mock for that attribute instead of running the real
    lookup logic under test, silently making tests pass for the wrong
    reason (caught live -- see test_focus_element_via_uia.py's identical
    fix for the same class of bug in a sibling refactor)."""
    from agent.agent import LLMAgent
    fake = MagicMock()
    fake._find_scrollable_pane_uia = types.MethodType(LLMAgent._find_scrollable_pane_uia, fake)
    fake._locked_hwnd = 12345
    return fake


def _install_fake_uia_modules(anchor_control):
    """Injects fake `uiautomation`/`win32gui` modules into sys.modules so
    agent.py's local `import uiautomation as _uia` / `import win32gui as
    _w32g` inside _scroll_form_down_uia pick these up instead of erroring
    (or touching a real window)."""
    fake_uia = types.ModuleType("uiautomation")

    class _ScrollAmount:
        NoAmount = "NoAmount"
        SmallIncrement = "SmallIncrement"
        LargeIncrement = "LargeIncrement"
        SmallDecrement = "SmallDecrement"
        LargeDecrement = "LargeDecrement"

    class _ScrollPattern:
        NoScrollValue = -1

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
    """Minimal stand-in for a uiautomation Control -- just enough surface
    for _scroll_form_down_uia's anchor-walk and scroll-pattern lookup."""

    def __init__(self, name="", scroll_pattern=None, parent=None, bounding_rect=None):
        self.Name = name
        self._scroll_pattern = scroll_pattern
        self._parent = parent
        self.owner_root = self  # EditControl(...) is called on the root
        if bounding_rect is None:
            bounding_rect = MagicMock()
            bounding_rect.top = 100
        self._bounding_rect = bounding_rect

    def EditControl(self, searchDepth=25, Name=""):
        return self  # pretend the anchor field was found directly

    def Exists(self, maxSearchSeconds=0.2):
        return True

    def GetScrollPattern(self):
        return self._scroll_pattern

    def GetParentControl(self):
        return self._parent

    @property
    def BoundingRectangle(self):
        return self._bounding_rect


class TestScrollFormDownUiaUsesSmallIncrement:
    def test_scroll_call_uses_small_increment_not_large_increment(self):
        """The actual regression: SmallIncrement must be the amount passed
        to ScrollPattern.Scroll(), not LargeIncrement."""
        from agent.agent import LLMAgent

        scroll_pattern = MagicMock()
        scroll_pattern.VerticallyScrollable = True
        panel = _FakeControl(name="tab_policyholder", scroll_pattern=scroll_pattern)
        anchor = _FakeControl(name="First Name", parent=panel)
        anchor.owner_root = anchor

        _install_fake_uia_modules(anchor)

        fake_self = _fake_self()
        state = {"elements": [
            {"type": "editcontrol", "label": "First Name", "window_role": "active"},
        ]}

        result = LLMAgent._scroll_form_down_uia(fake_self, state)

        assert result is True
        scroll_pattern.Scroll.assert_called_once()
        args = scroll_pattern.Scroll.call_args[0]
        assert args[1] == "SmallIncrement"
        assert args[1] != "LargeIncrement"

    def test_returns_false_when_no_scrollable_pane_is_found(self):
        """No ancestor exposes ScrollPattern.VerticallyScrollable -- caller
        must fall back to the mouse-wheel route, not silently no-op."""
        from agent.agent import LLMAgent

        anchor = _FakeControl(name="First Name", parent=None)
        anchor.owner_root = anchor
        _install_fake_uia_modules(anchor)

        fake_self = _fake_self()
        state = {"elements": [
            {"type": "editcontrol", "label": "First Name", "window_role": "active"},
        ]}

        result = LLMAgent._scroll_form_down_uia(fake_self, state)
        assert result is False

    def test_returns_false_when_uiautomation_is_unavailable(self):
        """UIA import failure must fall back cleanly, not raise."""
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
        state = {"elements": []}

        orig = builtins.__import__
        builtins.__import__ = _blocked_import
        try:
            result = LLMAgent._scroll_form_down_uia(fake_self, state)
        finally:
            builtins.__import__ = orig

        assert result is False


class TestScrollFormDownUiaPercent:
    """Regression test for _scroll_form_down_uia_percent -- jumps the pane
    directly via ScrollPattern.SetScrollPercent(), computed from the
    pane's own self-reported VerticalViewSize (one read, no calibration
    nudge, no measured pixels-per-scroll-increment ratio).

    Added 2026-08-08, live, immediately after _scroll_into_view_via_uia
    (ScrollItemPattern-based) turned out to not be supported on this
    form's controls, falling back to SmallIncrement every time -- which
    only ever reveals one field per call ("it only revealed one input").
    ScrollPattern itself (used here) is already proven working on this
    pane via SmallIncrement, so SetScrollPercent is far more likely to
    actually be implemented."""

    def _make_panel(self, view_size, cur_percent, panel_top=0, panel_bottom=1000):
        scroll_pattern = MagicMock()
        scroll_pattern.VerticallyScrollable = True
        scroll_pattern.VerticalViewSize = view_size
        scroll_pattern.VerticalScrollPercent = cur_percent
        rect = MagicMock()
        rect.top = panel_top
        rect.bottom = panel_bottom
        panel = _FakeControl(name="tab_policyholder", scroll_pattern=scroll_pattern, bounding_rect=rect)
        return panel, scroll_pattern

    def test_computes_and_issues_an_absolute_scroll_percent_jump(self):
        """Panel is 1000px tall and currently shows 20% of the content ->
        total content = 1000 / 0.20 = 5000px, so 1% = 50px. Currently at
        10%. Target is 500px below the current top -> +10% -> 20%."""
        from agent.agent import LLMAgent

        panel, scroll_pattern = self._make_panel(view_size=20, cur_percent=10, panel_bottom=1000)
        anchor = _FakeControl(name="First Name", parent=panel)
        anchor.owner_root = anchor
        _install_fake_uia_modules(anchor)

        fake_self = _fake_self()
        state = {"elements": [
            {"type": "editcontrol", "label": "First Name", "window_role": "active"},
        ]}

        result = LLMAgent._scroll_form_down_uia_percent(fake_self, state, 500.0)

        assert result is True
        scroll_pattern.SetScrollPercent.assert_called_once()
        args = scroll_pattern.SetScrollPercent.call_args[0]
        assert args[0] == -1   # NoScrollValue -- horizontal untouched
        assert args[1] == 20.0

    def test_clamps_the_target_percent_to_100(self):
        """A target far below the remaining content must not request a
        percentage above 100."""
        from agent.agent import LLMAgent

        panel, scroll_pattern = self._make_panel(view_size=20, cur_percent=90, panel_bottom=1000)
        anchor = _FakeControl(name="First Name", parent=panel)
        anchor.owner_root = anchor
        _install_fake_uia_modules(anchor)

        fake_self = _fake_self()
        state = {"elements": [
            {"type": "editcontrol", "label": "First Name", "window_role": "active"},
        ]}

        result = LLMAgent._scroll_form_down_uia_percent(fake_self, state, 5000.0)

        assert result is True
        args = scroll_pattern.SetScrollPercent.call_args[0]
        assert args[1] == 100.0

    def test_returns_false_when_view_size_is_unreported(self):
        """VerticalViewSize <= 0 means the ratio can't be computed -- must
        fail cleanly, not divide by zero."""
        from agent.agent import LLMAgent

        panel, scroll_pattern = self._make_panel(view_size=0, cur_percent=10)
        anchor = _FakeControl(name="First Name", parent=panel)
        anchor.owner_root = anchor
        _install_fake_uia_modules(anchor)

        fake_self = _fake_self()
        state = {"elements": [
            {"type": "editcontrol", "label": "First Name", "window_role": "active"},
        ]}

        result = LLMAgent._scroll_form_down_uia_percent(fake_self, state, 500.0)

        assert result is False
        scroll_pattern.SetScrollPercent.assert_not_called()

    def test_returns_false_when_no_scrollable_pane_is_found(self):
        from agent.agent import LLMAgent

        anchor = _FakeControl(name="First Name", parent=None)
        anchor.owner_root = anchor
        _install_fake_uia_modules(anchor)

        fake_self = _fake_self()
        state = {"elements": [
            {"type": "editcontrol", "label": "First Name", "window_role": "active"},
        ]}

        result = LLMAgent._scroll_form_down_uia_percent(fake_self, state, 500.0)
        assert result is False
