"""
Regression test for agent.py's low-confidence-fallback redirect -- a THIRD
sibling guard (alongside the reclick-streak guard and the combobox-
attempted-blank guard) that shares the exact same "find a target, SetFocus,
verify landing" shape, but never received either of their "complete an
accidental partial reveal" or "refocus to the shallowest revealed field"
fixes.

Found 2026-08-09, live, direct user report ("it did it again in the
Navigation Protocol, one input field at a time. What the fuck is wrong with
you genuinely?"). Log evidence: this exact branch fired repeatedly on the
Vehicle tab (Annual Miles Est., Primary Use, Purchase Date, Purchase
Price...), each redirect occasionally growing the element count ("3 new
interactive element(s) appeared", "1 new interactive element(s) appeared")
with nothing ever noticing or exploiting it -- one field revealed and
filled per redirect, never a cluster, even though the OTHER two sibling
guards already had this exact fix from the previous round.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import find_visible_empty_target, find_scroll_target_element

VIEWPORT_BOTTOM = 1000.0


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol"):
    return {"element_id": label, "type": ftype, "label": label, "value": value,
            "bbox": list(bbox), "window_role": "active"}


def _attempt_key(elem, elements=None):
    return (elem.get("label") or elem.get("text") or "").strip().lower()


def _lc_complete_reveal_then_refocus(state_before_land, state_after_land, t_key, focus_via_uia_fn):
    """Mirrors the CURRENT (2026-08-09) low-confidence-fallback landed
    branch in agent.py's run(): completes an accidental partial reveal via
    find_scroll_target_element, then refocuses to the shallowest revealed
    target -- the same shape as the two sibling guards."""
    steps = []
    state = state_after_land
    if len(state.get("elements", [])) > len(state_before_land.get("elements", [])):
        deep = find_scroll_target_element(state, VIEWPORT_BOTTOM)
        if deep and deep.get("bbox"):
            deep_key = _attempt_key(deep)
            deep_label = (deep.get("label") or deep.get("text") or "").strip()
            if deep_key != t_key and deep_label and focus_via_uia_fn(deep_label):
                steps.append(("complete_reveal", deep_label))

    shallow = find_visible_empty_target(state, VIEWPORT_BOTTOM)
    if shallow and shallow.get("bbox"):
        shallow_key = _attempt_key(shallow)
        if shallow_key != t_key:
            shallow_label = (shallow.get("label") or shallow.get("text") or "").strip()
            if shallow_label and focus_via_uia_fn(shallow_label):
                steps.append(("refocus_shallow", shallow_label))
    return steps


class TestLowConfFallbackCompletesAnAccidentalPartialReveal:
    def test_growing_element_count_triggers_completion_then_shallow_refocus(self):
        """The actual live regression: the low-confidence fallback landed
        on 'Primary Use' (the field it happened to find first), and that
        landing revealed BOTH a shallower field ('Annual Miles Est.') and
        deeper ones ('Garaging Location', 'Purchase Date') that weren't
        visible before -- must complete the reveal toward the new deepest
        field, then refocus to the new shallowest, exactly like the
        sibling guards, instead of leaving it at one field."""
        before = {"elements": [
            _field("Current Mileage", value="38450", bbox=(100, 100, 300, 130)),
            _field("Primary Use", value="", bbox=(100, 180, 300, 210)),
        ]}
        after = {"elements": [
            _field("Current Mileage", value="38450", bbox=(100, 100, 300, 130)),
            _field("Annual Miles Est.", value="", bbox=(100, 140, 300, 170)),
            _field("Primary Use", value="", bbox=(100, 180, 300, 210)),
            _field("Garaging Location", value="", bbox=(100, 220, 300, 250)),
            _field("Purchase Date", value="", bbox=(100, 260, 300, 290)),
        ]}
        focus_via_uia = MagicMock(return_value=True)

        steps = _lc_complete_reveal_then_refocus(
            before, after, t_key="primary use", focus_via_uia_fn=focus_via_uia)

        kinds = [s[0] for s in steps]
        assert "complete_reveal" in kinds
        assert "refocus_shallow" in kinds
        refocus_steps = [s for s in steps if s[0] == "refocus_shallow"]
        assert refocus_steps[0][1] == "Annual Miles Est."

    def test_no_growth_skips_completion_entirely(self):
        before = {"elements": [_field("Current Mileage", value="38450", bbox=(100, 100, 300, 130)),
                                _field("Annual Miles Est.", value="", bbox=(100, 140, 300, 170))]}
        after = {"elements": [_field("Current Mileage", value="38450", bbox=(100, 100, 300, 130)),
                               _field("Annual Miles Est.", value="", bbox=(100, 140, 300, 170))]}
        focus_via_uia = MagicMock(return_value=True)

        steps = _lc_complete_reveal_then_refocus(
            before, after, t_key="annual miles est.", focus_via_uia_fn=focus_via_uia)

        assert [s for s in steps if s[0] == "complete_reveal"] == []
