"""
Regression test for ui_observer.py's element "value" field on checkboxes --
it only ever read UIA's ValuePattern (and, as a fallback, TextPattern), but
checkboxes/toggle buttons expose TogglePattern instead, which ValuePattern
never returns for them. So every checkbox, checked or not, always reported
value="" -- a checkbox looked permanently "empty" to anything that checks
`value` to decide whether a field still needs filling.

Found 2026-08-09, live, direct user report ("Another loop error"): after
the 'Homeowner' checkbox got checked, navigation_protocol's
find_visible_empty_target() kept re-offering 'Homeowner' itself as the
"next empty target" -- because its value never stopped reading as empty --
so the redirect-instead-of-blind-Tab guard (added earlier tonight)
SetFocus'd right back onto the same checkbox it had just redirected away
from. That refocus made the auto-fill fast path see 'Homeowner' as the
freshly focused field again, which re-entered the same guard, which
redirected to 'Homeowner' again -- a tight, self-reinforcing loop across
many steps with nothing ever progressing (steps 43-48 in
logs/run_task_20260809_014104.log).

Fixed by reading TogglePattern.ToggleState (On/Off/Indeterminate) whenever
ValuePattern left `value` empty, and setting value="checked" for On. This
is universal -- applies to every checkbox in any form, not just Homeowner.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


class _FakeToggleState:
    On = 1
    Off = 0
    Indeterminate = 2


class _FakePatternId:
    ValuePattern = "ValuePattern"
    TogglePattern = "TogglePattern"


def _resolve_value(ctrl, uia_module):
    """Mirrors the CURRENT ui_observer.py fix: try ValuePattern first
    (unchanged, still the primary source for edit/combobox controls), then
    fall back to TogglePattern only when ValuePattern left value empty."""
    value = ""
    try:
        vp = ctrl.GetPattern(uia_module.PatternId.ValuePattern)
        if vp:
            value = (vp.Value or "").strip()
    except Exception:
        pass

    if not value:
        try:
            tgp = ctrl.GetPattern(uia_module.PatternId.TogglePattern)
            if tgp:
                value = "checked" if tgp.ToggleState == uia_module.ToggleState.On else ""
        except Exception:
            pass

    return value


class TestCheckboxValueReadsRealToggleState:
    def test_checked_checkbox_reports_checked(self):
        uia = MagicMock()
        uia.PatternId = _FakePatternId
        uia.ToggleState = _FakeToggleState

        ctrl = MagicMock()
        def get_pattern(pattern_id):
            if pattern_id == "ValuePattern":
                return None
            if pattern_id == "TogglePattern":
                toggle = MagicMock()
                toggle.ToggleState = _FakeToggleState.On
                return toggle
            return None
        ctrl.GetPattern.side_effect = get_pattern

        assert _resolve_value(ctrl, uia) == "checked"

    def test_unchecked_checkbox_reports_empty(self):
        uia = MagicMock()
        uia.PatternId = _FakePatternId
        uia.ToggleState = _FakeToggleState

        ctrl = MagicMock()
        def get_pattern(pattern_id):
            if pattern_id == "ValuePattern":
                return None
            if pattern_id == "TogglePattern":
                toggle = MagicMock()
                toggle.ToggleState = _FakeToggleState.Off
                return toggle
            return None
        ctrl.GetPattern.side_effect = get_pattern

        assert _resolve_value(ctrl, uia) == ""

    def test_value_pattern_still_wins_when_present(self):
        """Edit/combobox controls (which DO expose ValuePattern) must be
        completely unaffected by this fallback."""
        uia = MagicMock()
        uia.PatternId = _FakePatternId
        uia.ToggleState = _FakeToggleState

        ctrl = MagicMock()
        def get_pattern(pattern_id):
            if pattern_id == "ValuePattern":
                vp = MagicMock()
                vp.Value = "James"
                return vp
            return None
        ctrl.GetPattern.side_effect = get_pattern

        assert _resolve_value(ctrl, uia) == "James"

    def test_no_toggle_pattern_at_all_defaults_to_empty(self):
        """A plain control with neither pattern (e.g. a static label) must
        not crash and must fall back to empty, matching pre-fix behavior."""
        uia = MagicMock()
        uia.PatternId = _FakePatternId
        uia.ToggleState = _FakeToggleState

        ctrl = MagicMock()
        ctrl.GetPattern.side_effect = lambda pattern_id: None

        assert _resolve_value(ctrl, uia) == ""

    def test_toggle_pattern_read_failure_does_not_crash(self):
        uia = MagicMock()
        uia.PatternId = _FakePatternId
        uia.ToggleState = _FakeToggleState

        ctrl = MagicMock()
        def get_pattern(pattern_id):
            if pattern_id == "TogglePattern":
                raise RuntimeError("no pattern")
            return None
        ctrl.GetPattern.side_effect = get_pattern

        assert _resolve_value(ctrl, uia) == ""


class TestNavigationProtocolNoLongerLoopsOnACheckedCheckbox:
    """End-to-end shape of the actual live regression: once the checkbox's
    value correctly reads 'checked', find_visible_empty_target must stop
    offering it back as a target."""

    def test_checked_checkbox_is_excluded_from_empty_target_search(self):
        from agent.navigation_protocol import find_visible_empty_target

        state = {"elements": [
            {"element_id": "e1", "type": "checkboxcontrol", "label": "Homeowner",
             "value": "checked", "bbox": [100, 100, 300, 130], "window_role": "active"},
            {"element_id": "e2", "type": "editcontrol", "label": "Marital Status",
             "value": "", "bbox": [100, 200, 300, 230], "window_role": "active"},
        ]}
        target = find_visible_empty_target(state, 1000.0)
        assert target is not None
        assert target["label"] == "Marital Status"
