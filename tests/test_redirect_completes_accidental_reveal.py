"""
Regression test for agent.py's redirect guards -- when landing on a
"nearest already-visible" target still turns out to require a real reveal
(element count grows), the redirect now completes that reveal via the
deep-cluster search before refocusing to the shallowest field, instead of
leaving the reveal half-done.

Found 2026-08-09, live, direct user report ("You're revealing one input
field one-by-one again. We've been here before."). Log evidence: element
count crept 162 -> 169 -> 170 across separate redirects, with zero
"revealed cluster via" or "Scroll-form" lines anywhere in the run -- the
deliberate big-reveal strategy essentially never fired. Root cause: the
same night's earlier "prefer an already-visible target" fix (added to stop
a redirect from skipping past genuinely-visible fields for a distant
cluster) means find_visible_empty_target now finds SOMETHING almost every
time, via an imprecise geometric viewport estimate -- but "matches the
geometric bound" isn't the same as "genuinely on screen, zero scroll
needed." When the chosen near target still required a real reveal (proof:
element count grew after landing), the redirect was leaving that reveal
half-exploited, one field at a time.

Fixed by checking, right after landing, whether the element count grew --
if so, immediately aim the SAME deep-cluster search (find_scroll_target_
element) at the new state to complete the reveal, before doing the
existing refocus-to-shallowest step.
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


def _complete_reveal_then_refocus_shallow(state_before_land, state_after_land, rc_key,
                                           focus_via_uia_fn):
    """Mirrors the CURRENT (2026-08-09) post-landing logic in agent.py's
    redirect guards: if landing grew the element count, complete the
    reveal via find_scroll_target_element before refocusing shallow."""
    steps = []
    state = state_after_land
    if len(state.get("elements", [])) > len(state_before_land.get("elements", [])):
        deep = find_scroll_target_element(state, VIEWPORT_BOTTOM)
        if deep and deep.get("bbox"):
            deep_key = _attempt_key(deep)
            deep_label = (deep.get("label") or deep.get("text") or "").strip()
            if deep_key != rc_key and deep_label and focus_via_uia_fn(deep_label):
                steps.append(("complete_reveal", deep_label))

    shallow = find_visible_empty_target(state, VIEWPORT_BOTTOM)
    if shallow and shallow.get("bbox"):
        shallow_key = _attempt_key(shallow)
        if shallow_key != rc_key:
            shallow_label = (shallow.get("label") or shallow.get("text") or "").strip()
            if shallow_label and focus_via_uia_fn(shallow_label):
                steps.append(("refocus_shallow", shallow_label))
    return steps


class TestCompletesAnAccidentalPartialReveal:
    def test_growing_element_count_triggers_a_completing_deep_focus(self):
        """The actual live regression: landing on a 'near' target (here,
        'City' -- the field the redirect actually picked before anything
        shallower was visible) still grew the element count (a real
        reveal happened, bringing 'Street Address 1' into view for the
        first time) -- must complete the reveal via the deep-cluster
        search, then refocus to the newly-revealed SHALLOWER field."""
        before = {"elements": [
            _field("SSN", value="512-88-4401", bbox=(100, 100, 300, 130)),
            _field("City", value="", bbox=(100, 480, 300, 510)),
        ]}
        after = {"elements": [
            _field("SSN", value="512-88-4401", bbox=(100, 100, 300, 130)),
            _field("Street Address 1", value="", bbox=(100, 400, 300, 430)),
            _field("City", value="", bbox=(100, 480, 300, 510)),
            _field("State", value="", bbox=(100, 520, 300, 550)),
        ]}
        focus_via_uia = MagicMock(return_value=True)

        steps = _complete_reveal_then_refocus_shallow(
            before, after, rc_key="city", focus_via_uia_fn=focus_via_uia)

        kinds = [s[0] for s in steps]
        assert "complete_reveal" in kinds
        assert "refocus_shallow" in kinds
        # complete_reveal must happen before refocus_shallow
        assert kinds.index("complete_reveal") < kinds.index("refocus_shallow")
        # the shallow refocus must land on the newly-revealed shallower
        # field, not stay on the original 'city' target.
        refocus_steps = [s for s in steps if s[0] == "refocus_shallow"]
        assert refocus_steps[0][1] == "Street Address 1"

    def test_no_growth_skips_the_completing_step(self):
        """The common, correct case: landing on a target that was
        genuinely already visible (no reveal at all) must not trigger an
        unnecessary extra SetFocus hop."""
        before = {"elements": [
            _field("First Name", value="James", bbox=(100, 100, 300, 130)),
            _field("Middle Name", value="", bbox=(100, 200, 300, 230)),
        ]}
        after = {"elements": [
            _field("First Name", value="James", bbox=(100, 100, 300, 130)),
            _field("Middle Name", value="", bbox=(100, 200, 300, 230)),
        ]}
        focus_via_uia = MagicMock(return_value=True)

        steps = _complete_reveal_then_refocus_shallow(
            before, after, rc_key="middle name", focus_via_uia_fn=focus_via_uia)

        kinds = [s[0] for s in steps]
        assert "complete_reveal" not in kinds

    def test_landing_already_on_the_shallowest_field_skips_the_redundant_refocus(self):
        """If the redirect's own landing spot turns out to already be the
        shallowest field even after the reveal, no redundant SetFocus hop
        is needed -- already correctly positioned."""
        before = {"elements": [_field("SSN", value="512-88-4401", bbox=(100, 100, 300, 130))]}
        after = {"elements": [
            _field("SSN", value="512-88-4401", bbox=(100, 100, 300, 130)),
            _field("Street Address 1", value="", bbox=(100, 400, 300, 430)),
            _field("City", value="", bbox=(100, 480, 300, 510)),
        ]}
        focus_via_uia = MagicMock(return_value=True)

        steps = _complete_reveal_then_refocus_shallow(
            before, after, rc_key="street address 1", focus_via_uia_fn=focus_via_uia)

        assert [s for s in steps if s[0] == "refocus_shallow"] == []
