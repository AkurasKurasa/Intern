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

Found 2026-08-09, live regression during the Driver-3-loop fix: this file
never restored sys.modules['uiautomation']/['win32gui'] after installing
fakes, so on a machine where the real packages ARE importable (this repo's
own dev sandbox has both installed), whichever fake root this file
installed LAST silently leaked into any test file that ran afterward and
did its own bare `import uiautomation` -- caught when
test_focus_first_empty_field_active_tab.py (a completely unrelated file,
with its own separate inline UIA lookup) started failing ONLY when run
after this file, never in isolation. The autouse fixture below snapshots
and restores both module-registry entries around every test in this file.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


@pytest.fixture(autouse=True)
def _restore_uia_modules():
    """Every test in this file replaces sys.modules['uiautomation'] and
    ['win32gui'] with fakes via _install_fake_uia_modules -- without this,
    that replacement is permanent for the rest of the pytest process,
    corrupting any later, unrelated test that does its own real import of
    either module."""
    _had_uia = "uiautomation" in sys.modules
    _orig_uia = sys.modules.get("uiautomation")
    _had_w32g = "win32gui" in sys.modules
    _orig_w32g = sys.modules.get("win32gui")
    yield
    if _had_uia:
        sys.modules["uiautomation"] = _orig_uia
    else:
        sys.modules.pop("uiautomation", None)
    if _had_w32g:
        sys.modules["win32gui"] = _orig_w32g
    else:
        sys.modules.pop("win32gui", None)


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


class _FakeRect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class _FakeControl:
    def __init__(self, exists=True, scroll_item_pattern=None, bbox=None):
        self._exists = exists
        self.SetFocus = MagicMock()
        self._scroll_item_pattern = scroll_item_pattern
        if bbox is not None:
            self.BoundingRectangle = _FakeRect(*bbox)

    def Exists(self, maxSearchSeconds=0.3):
        return self._exists

    def GetScrollItemPattern(self):
        return self._scroll_item_pattern


class _FakeRoot:
    """Minimal stand-in for the window's root UIA control -- exposes the
    three finder methods _focus_element_via_uia tries, in order.

    `edit`/`combo`/`checkbox` accept either a single control or a LIST of
    controls sharing the same Name -- the list form (used by
    TestFindUiaControlByNameDisambiguatesRepeatedLabels below) simulates a
    repeated-section form where 'foundIndex' is the only way UIA itself
    tells same-named controls apart."""

    def __init__(self, edit=None, combo=None, checkbox=None):
        self._edit = self._as_list(edit)
        self._combo = self._as_list(combo)
        self._checkbox = self._as_list(checkbox)
        self.seen_calls = []

    @staticmethod
    def _as_list(value):
        if value is None:
            return [_FakeControl(exists=False)]
        if isinstance(value, list):
            return value
        return [value]

    def _pick(self, kind, controls, Name, foundIndex):
        self.seen_calls.append((kind, Name, foundIndex))
        idx = foundIndex - 1
        if 0 <= idx < len(controls):
            return controls[idx]
        return _FakeControl(exists=False)

    def EditControl(self, searchDepth=25, Name="", foundIndex=1):
        return self._pick("edit", self._edit, Name, foundIndex)

    def ComboBoxControl(self, searchDepth=25, Name="", foundIndex=1):
        return self._pick("combo", self._combo, Name, foundIndex)

    def CheckBoxControl(self, searchDepth=25, Name="", foundIndex=1):
        return self._pick("checkbox", self._checkbox, Name, foundIndex)

    @property
    def seen_names(self):
        """Backward-compat view for existing tests: (kind, Name) pairs."""
        return [(kind, name) for kind, name, _idx in self.seen_calls]


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


class TestFindUiaControlByNameDisambiguatesRepeatedLabels:
    """Regression test for _find_uia_control_by_name's `expected_bbox`
    parameter.

    Found 2026-08-09, live, direct user report ("another loop in the
    Drivers, why do you hate me?"): logs/latest.log showed a redirect
    repeatedly picking Driver 3's 'First Name' as the correct next target,
    calling SetFocus() on it, then the very next step's fresh observation
    showing the transformer's pointer already back on an unrelated,
    already-filled field -- as if the SetFocus call never actually landed
    where it was supposed to. Root cause: the accessible Name 'First Name'
    (no section prefix baked into the raw UIA Name property) is repeated
    identically across Driver 1/2/3 -- the exact repeated-section collision
    class already fixed twice elsewhere in this project (state_validator.py,
    agent.py's own _attempt_key), just never carried into this UIA-level
    lookup. `EditControl(Name="First Name")` with no disambiguation always
    returns the FIRST match in the UIA tree (Driver 1's, already filled) --
    regardless of which driver's cluster the caller actually meant, no
    matter how many times SetFocus is retried.

    Fixed by walking every control sharing that Name (via UIA's own
    foundIndex) and picking whichever one's own on-screen position is
    closest to the caller's already-known target bbox, instead of blindly
    trusting tree order.
    """

    def test_picks_the_occurrence_closest_to_the_expected_position(self):
        from agent.agent import LLMAgent

        driver1_first_name = _FakeControl(exists=True, bbox=(1449, 100, 1600, 130))
        driver2_first_name = _FakeControl(exists=True, bbox=(1449, 400, 1600, 430))
        driver3_first_name = _FakeControl(exists=True, bbox=(1449, 700, 1600, 730))
        root = _FakeRoot(edit=[driver1_first_name, driver2_first_name, driver3_first_name])
        _install_fake_uia_modules(root)

        fake_self = _fake_self()

        # Navigation Protocol picked Driver 3's on-screen empty First Name --
        # its own bbox is what gets passed through as expected_bbox.
        ctrl = LLMAgent._find_uia_control_by_name(
            fake_self, "First Name", expected_bbox=[1449, 705, 1600, 735])

        assert ctrl is driver3_first_name

    def test_without_expected_bbox_keeps_old_first_match_behavior(self):
        """Callers that don't have a specific position (none currently, but
        the parameter is optional) must be completely unaffected."""
        from agent.agent import LLMAgent

        driver1_first_name = _FakeControl(exists=True, bbox=(1449, 100, 1600, 130))
        driver2_first_name = _FakeControl(exists=True, bbox=(1449, 400, 1600, 430))
        root = _FakeRoot(edit=[driver1_first_name, driver2_first_name])
        _install_fake_uia_modules(root)

        fake_self = _fake_self()

        ctrl = LLMAgent._find_uia_control_by_name(fake_self, "First Name")

        assert ctrl is driver1_first_name

    def test_single_matching_occurrence_still_works_with_expected_bbox(self):
        """The common case (a uniquely-labeled field like 'Street Address
        1') must still work once expected_bbox is threaded through."""
        from agent.agent import LLMAgent

        only = _FakeControl(exists=True, bbox=(100, 800, 400, 830))
        root = _FakeRoot(edit=only)
        _install_fake_uia_modules(root)

        fake_self = _fake_self()

        ctrl = LLMAgent._find_uia_control_by_name(
            fake_self, "Street Address 1", expected_bbox=[100, 800, 400, 830])

        assert ctrl is only

    def test_focus_element_via_uia_threads_expected_bbox_through(self):
        """End-to-end: _focus_element_via_uia's own new parameter must
        actually reach the disambiguation logic, not just exist."""
        from agent.agent import LLMAgent

        driver1_first_name = _FakeControl(exists=True, bbox=(1449, 100, 1600, 130))
        driver3_first_name = _FakeControl(exists=True, bbox=(1449, 700, 1600, 730))
        root = _FakeRoot(edit=[driver1_first_name, driver3_first_name])
        _install_fake_uia_modules(root)

        fake_self = _fake_self()

        result = LLMAgent._focus_element_via_uia(
            fake_self, "First Name", expected_bbox=[1449, 705, 1600, 735])

        assert result is True
        driver3_first_name.SetFocus.assert_called_once()
        driver1_first_name.SetFocus.assert_not_called()
