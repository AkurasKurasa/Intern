"""
Regression test for agent.py's _focus_element_via_uia -- focuses a control
directly via UIA's SetFocus(), bypassing simulated mouse clicks at computed
screen coordinates entirely.

Found 2026-08-08, live, direct user instruction ("Don't use semantic
positioning for God's sake") immediately after two different fields --
'Street Address 1' and then 'Street Address 2', click coordinates only 10px
apart -- both failed to receive focus via a simulated click at their bbox
center, twice each, confirmed by re-observing afterward (see
tests/test_reclick_streak_redirect.py for the surrounding stall/escalation
fix). A pixel-coordinate click can miss for reasons that have nothing to do
with WHICH field is targeted (edge clipping, visual occlusion, a few pixels
of bbox drift); SetFocus() sidesteps that class of failure entirely since it
never goes through screen coordinates, just the control object UIA already
knows about.

Real UI Automation isn't available in a test environment, so `uiautomation`
and `win32gui` are faked via sys.modules injection.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _fake_self():
    """A stand-in for `self` that binds the REAL _find_uia_control_by_name
    method (agent.py refactored _focus_element_via_uia and
    _scroll_into_view_via_uia to share it) -- a bare MagicMock would auto-
    generate a mock for that attribute instead of running the real lookup
    logic under test, silently making every test pass for the wrong reason."""
    from agent.agent import LLMAgent
    fake = MagicMock()
    fake._find_uia_control_by_name = types.MethodType(LLMAgent._find_uia_control_by_name, fake)
    fake._locked_hwnd = 12345
    return fake


class _FakeControl:
    def __init__(self, exists=True, scroll_item_pattern=None):
        self._exists = exists
        self.SetFocus = MagicMock()
        self._scroll_item_pattern = scroll_item_pattern

    def Exists(self, maxSearchSeconds=0.3):
        return self._exists

    def GetScrollItemPattern(self):
        return self._scroll_item_pattern


class _FakeRoot:
    """Minimal stand-in for the window's root UIA control -- exposes the
    three finder methods _focus_element_via_uia tries, in order."""

    def __init__(self, edit=None, combo=None, checkbox=None):
        self._edit = edit or _FakeControl(exists=False)
        self._combo = combo or _FakeControl(exists=False)
        self._checkbox = checkbox or _FakeControl(exists=False)
        self.seen_names = []

    def EditControl(self, searchDepth=25, Name=""):
        self.seen_names.append(("edit", Name))
        return self._edit

    def ComboBoxControl(self, searchDepth=25, Name=""):
        self.seen_names.append(("combo", Name))
        return self._combo

    def CheckBoxControl(self, searchDepth=25, Name=""):
        self.seen_names.append(("checkbox", Name))
        return self._checkbox


def _install_fake_uia_modules(root):
    fake_uia = types.ModuleType("uiautomation")
    fake_uia.ControlFromHandle = MagicMock(return_value=root)

    fake_w32g = types.ModuleType("win32gui")
    fake_w32g.GetForegroundWindow = MagicMock(return_value=12345)

    sys.modules["uiautomation"] = fake_uia
    sys.modules["win32gui"] = fake_w32g
    return fake_uia, fake_w32g


class TestFocusElementViaUia:
    def test_finds_and_focuses_a_matching_edit_control(self):
        from agent.agent import LLMAgent

        edit = _FakeControl(exists=True)
        root = _FakeRoot(edit=edit)
        _install_fake_uia_modules(root)

        fake_self = _fake_self()

        result = LLMAgent._focus_element_via_uia(fake_self, "Street Address 1")

        assert result is True
        edit.SetFocus.assert_called_once()
        assert ("edit", "Street Address 1") in root.seen_names

    def test_falls_through_to_combobox_when_no_matching_edit_control(self):
        from agent.agent import LLMAgent

        combo = _FakeControl(exists=True)
        root = _FakeRoot(edit=_FakeControl(exists=False), combo=combo)
        _install_fake_uia_modules(root)

        fake_self = _fake_self()

        result = LLMAgent._focus_element_via_uia(fake_self, "Marital Status")

        assert result is True
        combo.SetFocus.assert_called_once()

    def test_returns_false_when_no_control_matches_anywhere(self):
        from agent.agent import LLMAgent

        root = _FakeRoot()  # all three finders report not-exists
        _install_fake_uia_modules(root)

        fake_self = _fake_self()

        result = LLMAgent._focus_element_via_uia(fake_self, "Nonexistent Field")

        assert result is False

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
            result = LLMAgent._focus_element_via_uia(fake_self, "First Name")
        finally:
            builtins.__import__ = orig

        assert result is False


class TestScrollIntoViewViaUia:
    """Regression test for _scroll_into_view_via_uia -- brings a control
    into view via UIA's ScrollItemPattern.ScrollIntoView(), the actual
    "scroll once and boom" primitive. Added 2026-08-08, live, direct user
    request, after rejecting an earlier plan that computed a
    pixels-per-scroll-increment ratio and fired a calculated number of
    increments (still fundamentally a guess). The user's own question --
    "don't you have the UI Accessibility Tree to just find what isn't in
    focus" -- pointed at this primitive directly: ask UIA to bring a
    specific control into view, no pixel math involved at all."""

    def test_calls_scroll_into_view_on_the_matching_control(self):
        from agent.agent import LLMAgent

        sip = MagicMock()
        edit = _FakeControl(exists=True, scroll_item_pattern=sip)
        root = _FakeRoot(edit=edit)
        _install_fake_uia_modules(root)

        fake_self = _fake_self()

        result = LLMAgent._scroll_into_view_via_uia(fake_self, "Prior Expiry Date")

        assert result is True
        sip.ScrollIntoView.assert_called_once()

    def test_returns_false_when_control_has_no_scroll_item_pattern(self):
        """Some controls (e.g. checkboxes on certain custom panes) may not
        expose ScrollItemPattern at all -- must fail cleanly, not raise, so
        the caller falls back to the increment-based route."""
        from agent.agent import LLMAgent

        edit = _FakeControl(exists=True, scroll_item_pattern=None)
        root = _FakeRoot(edit=edit)
        _install_fake_uia_modules(root)

        fake_self = _fake_self()

        result = LLMAgent._scroll_into_view_via_uia(fake_self, "Prior Expiry Date")

        assert result is False

    def test_returns_false_when_no_control_matches(self):
        from agent.agent import LLMAgent

        root = _FakeRoot()  # nothing exists
        _install_fake_uia_modules(root)

        fake_self = _fake_self()

        result = LLMAgent._scroll_into_view_via_uia(fake_self, "Nonexistent Field")

        assert result is False
