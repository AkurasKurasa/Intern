"""
Regression test for agent.py's _record_attempt -- for CLICK actions, it now
verifies which element ACTUALLY ended up focused (via state_after) instead
of trusting that the click landed on the element its own position matched
in the PRE-click state.

Found 2026-08-09, live, direct user report ("Check most recent logs, skipped
most fields in Vehicle after Current Mileage, what the fuck.") -- confirmed
on a second run via the new [FOCUS-DIAG] diagnostic added for that report.
A plain navigate click aimed at 'Annual Miles Est.' (its own bbox matched the
click position in the state used to build the prediction) resulted in real
focus landing on the 'Submit' button instead -- yet _record_attempt was
always called with that same stale pre-click state, so its position-based
lookup still matched 'Annual Miles Est.' and marked THAT field attempted,
permanently excluding it from ever being offered as a target again even
though it was never actually focused or typed into. 'Annual Miles Est.'
never appeared anywhere else in either affected log -- not as a fill
attempt, not as a blank-field lookup -- confirming it was silently and
permanently skipped by this exact mechanism.

The caller (agent.py's run()) already takes a fresh state_after observation
for validation a few lines above the _record_attempt call -- it simply
wasn't being passed through. Fixed by preferring state_after's own
focused_element_id (the verified post-action reality) for CLICK actions
only; keyboard/type actions are deliberately left on the pre-action state
(see the real function's own docstring for why: a trailing Tab/commit
keystroke within the same type action could leave state_after focused on
the NEXT field instead of the one just typed into).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _attempt_key(elem, elements=None):
    lbl = (elem.get("label") or elem.get("text") or "").strip().lower()
    if not lbl:
        b = elem.get("bbox") or [0, 0, 0, 0]
        return ("@", round((b[0] + b[2]) / 2 / 20) * 20, round((b[1] + b[3]) / 2 / 20) * 20)
    return lbl


def _record_attempt(attempted_keys, state, prediction, state_after=None):
    """Mirrors the CURRENT (2026-08-09) Agent._record_attempt: for click
    actions, prefers state_after's verified focused element over the
    pre-click position match, falling back to the position match only when
    state_after isn't available or has nothing focused."""
    at = prediction.get("action_type")
    elements = state.get("elements", [])
    elem = None
    if at == "keyboard":
        fid = state.get("focused_element_id")
        elem = next((e for e in elements if e.get("element_id") == fid), None)
    elif at == "click":
        after_fid = state_after.get("focused_element_id") if state_after else None
        if after_fid:
            after_elements = state_after.get("elements", [])
            elem = next((e for e in after_elements if e.get("element_id") == after_fid), None)
            if elem is not None:
                elements = after_elements
        if elem is None:
            px, py = (prediction.get("click_position") or [None, None])
            elem = next((e for e in elements
                         if e.get("bbox") and e["bbox"][0] <= px <= e["bbox"][2]
                         and e["bbox"][1] <= py <= e["bbox"][3]), None) if px is not None else None
    if elem is not None:
        attempted_keys.add(_attempt_key(elem, elements=elements))
    return elem


class TestClickAttemptTrustsVerifiedPostActionFocus:
    def test_marks_the_actually_focused_element_not_the_intended_click_target(self):
        """The actual live regression: click aimed at 'Annual Miles Est.'
        (matches its bbox in the pre-click state), but real focus landed on
        'Submit' -- must mark 'Submit' attempted, NOT 'Annual Miles Est.',
        so the real target stays available for a future retry."""
        annual_miles = {"element_id": "e1", "type": "editcontrol",
                         "label": "Annual Miles Est.", "value": "",
                         "bbox": [1400, 810, 1600, 840]}
        submit_btn = {"element_id": "e2", "type": "buttoncontrol",
                      "label": "Submit", "value": "", "bbox": [1350, 820, 1450, 860]}
        state = {"elements": [annual_miles, submit_btn]}
        state_after = {"elements": [annual_miles, submit_btn], "focused_element_id": "e2"}

        attempted = set()
        elem = _record_attempt(
            attempted, state, {"action_type": "click", "click_position": [1455, 824]},
            state_after=state_after)

        assert elem is submit_btn
        assert _attempt_key(annual_miles) not in attempted
        assert _attempt_key(submit_btn) in attempted

    def test_click_that_lands_correctly_still_marks_the_intended_field(self):
        """The normal, common case must be unaffected: when the click DOES
        land where intended, state_after's focused element agrees with the
        position match, so the same field gets marked either way."""
        field = {"element_id": "e1", "type": "editcontrol", "label": "First Name",
                  "value": "", "bbox": [100, 100, 300, 130]}
        state = {"elements": [field]}
        state_after = {"elements": [field], "focused_element_id": "e1"}

        attempted = set()
        elem = _record_attempt(
            attempted, state, {"action_type": "click", "click_position": [200, 115]},
            state_after=state_after)

        assert elem is field
        assert _attempt_key(field) in attempted

    def test_falls_back_to_position_match_when_state_after_has_nothing_focused(self):
        """Defensive fallback: state_after provided but focused_element_id
        is empty/missing (e.g. focus briefly null after a dialog closed) --
        must not silently mark nothing attempted, falls back to the old
        position-based lookup."""
        field = {"element_id": "e1", "type": "editcontrol", "label": "First Name",
                  "value": "", "bbox": [100, 100, 300, 130]}
        state = {"elements": [field]}
        state_after = {"elements": [field], "focused_element_id": None}

        attempted = set()
        elem = _record_attempt(
            attempted, state, {"action_type": "click", "click_position": [200, 115]},
            state_after=state_after)

        assert elem is field
        assert _attempt_key(field) in attempted

    def test_falls_back_to_position_match_when_state_after_not_provided(self):
        """Backward compatibility: any caller that doesn't pass state_after
        (none currently, but the parameter is optional) behaves exactly as
        before."""
        field = {"element_id": "e1", "type": "editcontrol", "label": "First Name",
                  "value": "", "bbox": [100, 100, 300, 130]}
        state = {"elements": [field]}

        attempted = set()
        elem = _record_attempt(
            attempted, state, {"action_type": "click", "click_position": [200, 115]})

        assert elem is field
        assert _attempt_key(field) in attempted


class TestKeyboardActionsAreUnaffected:
    """Deliberately NOT changed -- a type action can legitimately trail a
    commit keystroke within the same step, which could leave state_after
    focused on the NEXT field. The pre-action state's focused element stays
    correct for keyboard/type regardless of what state_after is passed."""

    def test_keyboard_action_still_uses_the_pre_action_focused_element(self):
        typed_field = {"element_id": "e1", "type": "editcontrol",
                        "label": "Years Continuously Insured", "value": ""}
        next_field = {"element_id": "e2", "type": "editcontrol",
                      "label": "Email Address", "value": ""}
        state = {"elements": [typed_field, next_field], "focused_element_id": "e1"}
        # Simulates a trailing commit keystroke having already moved focus
        # onward by the time state_after was observed.
        state_after = {"elements": [typed_field, next_field], "focused_element_id": "e2"}

        attempted = set()
        elem = _record_attempt(
            attempted, state, {"action_type": "keyboard", "text": "9"},
            state_after=state_after)

        assert elem is typed_field
        assert _attempt_key(typed_field) in attempted
        assert _attempt_key(next_field) not in attempted
