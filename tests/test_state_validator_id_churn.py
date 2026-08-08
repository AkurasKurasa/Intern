"""
Tests for StateValidator.validate()'s resistance to element_id churn.

Found live 2026-08-07: a run ended itself after only 9 steps (6 of 176
fields filled), claiming the task was "done." Root cause: a permanent status
label (wx.StaticText, created once at form startup — only its text ever
changes via .SetLabel()) had shown "Submitted #9 -- Ready for next record"
since an EARLIER submission, long before this run started. element_id is
assigned purely by scan position ("elem_{offset+count}" in ui_observer.py)
-- self-consistent within one observation, but not stable across two
separate ones. Some other element on screen shifted the label's id between
state_before and state_after, so StateValidator's id-based new-element diff
read the label as freshly appeared and matched it against _DONE_KEYWORDS --
a false "done" that stopped the run at 3.4% completion.

Fix: an element only counts as a genuinely NEW completion/error signal if
its exact text wasn't already present (under any id) in state_before.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.state_validator.state_validator import StateValidator


def _elem(element_id, text, etype="statictext"):
    return {"element_id": element_id, "type": etype, "text": text,
            "window_role": "active"}


class TestIgnoresChurnedIdSameContent:
    def test_pre_existing_status_label_with_a_new_id_is_not_done(self):
        """The exact bug: same text, different id between snapshots."""
        validator = StateValidator()
        state_before = {"elements": [
            _elem("elem_12", "  Submitted #9 — Ready for next record"),
            _elem("elem_5", "First Name", etype="editcontrol"),
        ]}
        state_after = {"elements": [
            _elem("elem_13", "  Submitted #9 — Ready for next record"),  # id shifted
            _elem("elem_5", "First Name", etype="editcontrol"),
        ]}
        result = validator.validate(state_before, state_after, {"action_type": "click"})
        assert result.status != "done"

    def test_pre_existing_error_text_with_a_new_id_is_not_error(self):
        validator = StateValidator()
        state_before = {"elements": [_elem("elem_1", "Invalid input in a prior field")]}
        state_after  = {"elements": [_elem("elem_2", "Invalid input in a prior field")]}
        result = validator.validate(state_before, state_after, {"action_type": "click"})
        assert result.status != "error"

    def test_genuinely_new_done_text_still_detected(self):
        """Must not become blind to real completions — only pre-existing
        content (by text) should be ignored."""
        validator = StateValidator()
        state_before = {"elements": [_elem("elem_1", "Ready")]}
        state_after  = {"elements": [
            _elem("elem_1", "Ready"),
            _elem("elem_2", "Record saved successfully"),
        ]}
        result = validator.validate(state_before, state_after, {"action_type": "click"})
        assert result.status == "done"

    def test_genuinely_new_error_text_still_detected(self):
        validator = StateValidator()
        state_before = {"elements": [_elem("elem_1", "Ready")]}
        state_after  = {"elements": [
            _elem("elem_1", "Ready"),
            _elem("elem_2", "Error: invalid date format"),
        ]}
        result = validator.validate(state_before, state_after, {"action_type": "click"})
        assert result.status == "error"


def _field(element_id, label, value, etype="editcontrol"):
    return {"element_id": element_id, "type": etype, "label": label, "text": label,
            "value": value, "window_role": "active"}


class TestKeyboardValueChangeIgnoresChurnedIds:
    """Found live 2026-08-09, direct user report ("it didn't finish actually
    filling in necessary tabs that were already present"): the SAME
    element_id-churn bug the class above already fixed for done/error
    detection also existed, unfixed, in the "did a value change" check --
    it matched elems_before[eid] against elems_after[eid] by the same
    unstable, scan-position id. Live evidence: right after checking the
    'Homeowner' checkbox (which revealed new elements, shifting every
    later id), the log reported "Field value changed: 'Male' -> ''" --
    'Male' was the ALREADY-FILLED 'Gender' combobox's value, several
    fields away from Homeowner and never touched by this action at all.
    The id slot Gender used to occupy now held a different, empty field,
    and the validator compared them as if they were the same field.

    Fixed by matching on (label, type) instead of element_id -- the same
    stable-identity principle already used by agent.py's own
    _attempt_key."""

    def test_an_unrelated_field_landing_in_the_old_ids_slot_is_not_reported_as_changed(self):
        """The actual live bug, reproduced: Gender's id shifts (163 elements
        appeared after Homeowner's checkbox reveal), and the field that now
        occupies Gender's OLD id slot is a different, empty field. Must NOT
        be reported as "Gender changed" -- matching by label proves they're
        different fields entirely."""
        validator = StateValidator()
        state_before = {"elements": [
            _field("elem_20", "Gender", "Male", etype="comboboxcontrol"),
            _field("elem_21", "Homeowner", "", etype="checkboxcontrol"),
        ]}
        state_after = {"elements": [
            # Gender's real field, same label, id shifted by newly-revealed
            # elements elsewhere -- but its OWN value is unchanged.
            _field("elem_24", "Gender", "Male", etype="comboboxcontrol"),
            _field("elem_21", "Homeowner", "Checked", etype="checkboxcontrol"),
            # A field that happens to have landed at Gender's OLD slot
            # ("elem_20") in this new snapshot -- unrelated, empty.
            _field("elem_20", "Prior Insurer", "", etype="editcontrol"),
        ]}
        result = validator.validate(state_before, state_after, {"action_type": "keyboard"})
        assert result.reason != "Field value changed: 'Male' → ''"

    def test_a_genuine_value_change_is_still_detected_despite_id_churn(self):
        """Must not become blind to real changes -- only matching by the
        WRONG identity should stop, not detection itself. Same field
        (by label+type), id shifted, value genuinely changed."""
        validator = StateValidator()
        state_before = {"elements": [
            _field("elem_5", "Years Continuously Insured", ""),
        ]}
        state_after = {"elements": [
            _field("elem_9", "Years Continuously Insured", "9"),  # id shifted, real change
        ]}
        result = validator.validate(state_before, state_after, {"action_type": "keyboard"})
        assert result.status == "ok"
        assert result.reason == "Field value changed: '' → '9'"
