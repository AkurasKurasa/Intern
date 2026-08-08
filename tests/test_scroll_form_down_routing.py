"""
Regression test for agent.py's _scroll_form_down -- the top-level scroll
dispatcher's ROUTING ORDER, not any single route's own internals (those are
covered separately: test_focus_element_via_uia.py for ScrollIntoView,
test_scroll_form_down_uia.py for SetScrollPercent and the SmallIncrement
fallback).

Added 2026-08-08, live, direct user request ("scroll on it once and then
boom" + "don't you have the UI Accessibility Tree to just find what isn't
in focus"): _scroll_form_down originally tried THREE routes --
ScrollIntoView, then SmallIncrement, then mouse-wheel.

EXTENDED same night, same conversation: live evidence showed
ScrollIntoView (route 1, ScrollItemPattern-based) wasn't supported on this
form's controls -- always fell straight through to SmallIncrement, which
only ever reveals one field per call ("it only revealed one input, for
fuck sake"). Added a FOURTH route between the two: _scroll_form_down_uia_
percent (SetScrollPercent, computed from the pane's own self-reported
VerticalViewSize) -- ScrollPattern itself is already proven working on
this pane via SmallIncrement, so this is far more likely to actually work
than the optional ScrollItemPattern route was.

Current order: ScrollIntoView -> SetScrollPercent -> SmallIncrement ->
mouse-wheel. This test proves the ORDER and fallback conditions using
mocks for each route, not real UIA plumbing.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent import navigation_protocol


def _make_fake_self(scroll_into_view_result, percent_result, uia_result, wheel_result):
    from agent.agent import LLMAgent
    fake = MagicMock()
    fake._navproto = navigation_protocol
    fake._attempted_keys = set()
    fake._attempt_key = lambda e, elements=None: (e.get("label") or "").lower()
    fake._scroll_into_view_via_uia = MagicMock(return_value=scroll_into_view_result)
    fake._scroll_form_down_uia_percent = MagicMock(return_value=percent_result)
    fake._scroll_form_down_uia = MagicMock(return_value=uia_result)
    fake._scroll_form_down_wheel = MagicMock(return_value=wheel_result)
    # find_scroll_target_element is a real module-level function -- drive it
    # with real state instead of mocking it, so the test also proves the
    # target's label/bbox are passed through correctly.
    return fake, LLMAgent._scroll_form_down


def _field(label, bbox):
    return {"element_id": label, "type": "editcontrol", "label": label,
            "value": "", "bbox": list(bbox), "window_role": "active"}


class TestScrollFormDownTriesScrollIntoViewFirst:
    def test_scroll_into_view_success_skips_the_other_three_routes(self):
        fake_self, scroll_form_down = _make_fake_self(
            scroll_into_view_result=True, percent_result=True, uia_result=True, wheel_result=True)
        state = {"elements": [_field("Prior Expiry Date", (100, 2000, 300, 2030))]}

        result = scroll_form_down(fake_self, state)

        assert result is True
        fake_self._scroll_into_view_via_uia.assert_called_once_with("Prior Expiry Date")
        fake_self._scroll_form_down_uia_percent.assert_not_called()
        fake_self._scroll_form_down_uia.assert_not_called()
        fake_self._scroll_form_down_wheel.assert_not_called()

    def test_falls_back_to_scroll_percent_when_scroll_into_view_fails(self):
        fake_self, scroll_form_down = _make_fake_self(
            scroll_into_view_result=False, percent_result=True, uia_result=True, wheel_result=True)
        state = {"elements": [_field("Prior Expiry Date", (100, 2000, 300, 2030))]}

        result = scroll_form_down(fake_self, state)

        assert result is True
        fake_self._scroll_into_view_via_uia.assert_called_once()
        fake_self._scroll_form_down_uia_percent.assert_called_once_with(state, 2000)
        fake_self._scroll_form_down_uia.assert_not_called()
        fake_self._scroll_form_down_wheel.assert_not_called()

    def test_falls_back_to_small_increment_when_percent_also_fails(self):
        fake_self, scroll_form_down = _make_fake_self(
            scroll_into_view_result=False, percent_result=False, uia_result=True, wheel_result=True)
        state = {"elements": [_field("Prior Expiry Date", (100, 2000, 300, 2030))]}

        result = scroll_form_down(fake_self, state)

        assert result is True
        fake_self._scroll_form_down_uia_percent.assert_called_once()
        fake_self._scroll_form_down_uia.assert_called_once()
        fake_self._scroll_form_down_wheel.assert_not_called()

    def test_falls_back_to_wheel_when_all_uia_routes_fail(self):
        fake_self, scroll_form_down = _make_fake_self(
            scroll_into_view_result=False, percent_result=False, uia_result=False, wheel_result=True)
        state = {"elements": [_field("Prior Expiry Date", (100, 2000, 300, 2030))]}

        result = scroll_form_down(fake_self, state)

        assert result is True
        fake_self._scroll_into_view_via_uia.assert_called_once()
        fake_self._scroll_form_down_uia_percent.assert_called_once()
        fake_self._scroll_form_down_uia.assert_called_once()
        fake_self._scroll_form_down_wheel.assert_called_once()

    def test_no_computable_target_skips_straight_to_small_increment(self):
        """Nothing left to scroll to (e.g. tab genuinely exhausted) --
        find_scroll_target_element returns None, so neither ScrollIntoView
        nor SetScrollPercent (which needs a target bbox) is ever attempted."""
        fake_self, scroll_form_down = _make_fake_self(
            scroll_into_view_result=True, percent_result=True, uia_result=True, wheel_result=True)
        state = {"elements": []}   # nothing fillable at all

        result = scroll_form_down(fake_self, state)

        assert result is True
        fake_self._scroll_into_view_via_uia.assert_not_called()
        fake_self._scroll_form_down_uia_percent.assert_not_called()
        fake_self._scroll_form_down_uia.assert_called_once()
