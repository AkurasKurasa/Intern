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
