"""
Regression tests for components/recorder/action_labeler.py's handling of
Tab/Shift+Tab and Backspace/Delete keyboard events.

Found 2026-08-13, investigating why the semantic-mode checkpoint's action-
TYPE (verb) accuracy was only 51.8% despite click_elem/source_elem pointer
accuracy being 91.6%/90.9%. A real per-verb confusion breakdown (against the
trained checkpoint's own val split) showed focus (24.1% acc) and set_value
(63.3% acc) -- the two most important verbs for this task -- both dominated
by misclassification AS hotkey.

translate_step() was labeling ALL non-printable keyboard events (Tab,
Backspace, Escape, ...) as the generic HOTKEY verb, regardless of what they
actually did. Two of those are not "window-level key combos" (HOTKEY's own
docstring) at all:
  - Tab/Shift+Tab moves keyboard focus to the next/previous field with no
    value change -- semantically identical to clicking that field directly
    (already labeled FOCUS), just via keyboard.
  - Backspace/Delete mid-edit is a value CORRECTION on the field currently
    being typed into -- part of SET_VALUE, not a generic hotkey.

The Tab fix was implemented first but verified (2026-08-13, against all 5
real eight_Tabs sessions) to have ZERO effect on this project's actual
dataset -- 0 of 3987 real steps ever used a literal Tab press, every
field-to-field move was a direct click. Backspace was the real driver:
455 of 470 real hotkey-labeled steps (97%) across all 5 sessions. Kept the
Tab fix anyway (harmless, correct in principle, may matter for future
recordings) and added the Backspace/Delete fix, which measurably shifted
the real histogram (hotkey 491->36, set_value 1182->1637 across the whole
dataset) -- verified with real data before spending a retrain on it, unlike
the Tab fix's first (unverified) attempt.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from recorder.action_labeler import translate_step
from agent.semantic_action import Verb


def _entry(eid, label, bbox=(100, 100, 300, 130)):
    return {"element_id": eid, "type": "editcontrol", "window_role": "active",
            "label": label, "text": label, "value": "", "bbox": list(bbox),
            "confidence": 1.0}


def _hotkey_step(hotkey_name, focused_id="e1", elements=None, next_focused_id=None):
    elements = elements if elements is not None else [_entry("e1", "First Name"),
                                                        _entry("e2", "Last Name", bbox=(100, 150, 300, 180))]
    state = {
        "screen_resolution": [1920, 1080],
        "focused_element_id": focused_id,
        "elements": elements,
    }
    next_state = dict(state)
    if next_focused_id is not None:
        next_state = {**state, "focused_element_id": next_focused_id}
    return {
        "state": state,
        "next_state": next_state,
        "mouse": {"actions": []},
        "keyboard": {"actions": [{"hotkey": hotkey_name,
                                   "strokes": [{"key": hotkey_name, "pasted_text": ""}]}]},
    }


class TestTabAndShiftTabBecomeFocus:
    def test_lone_tab_is_focus_not_hotkey(self):
        trace = _hotkey_step("tab", focused_id="e1", next_focused_id="e2")
        action = translate_step(trace)
        assert action.verb is Verb.FOCUS

    def test_shift_tab_is_focus_not_hotkey(self):
        trace = _hotkey_step("shift+tab", focused_id="e2", next_focused_id="e1")
        action = translate_step(trace)
        assert action.verb is Verb.FOCUS

    def test_tab_target_idx_resolves_to_the_newly_focused_element(self):
        trace = _hotkey_step("tab", focused_id="e1", next_focused_id="e2")
        action = translate_step(trace)
        assert action.target_idx == 1   # e2 is elements[1]

    def test_tab_target_idx_is_none_when_next_focus_unresolvable(self):
        """next_state's focused element isn't in the CURRENT elements list --
        must not crash, just leave target_idx unset."""
        trace = _hotkey_step("tab", focused_id="e1", next_focused_id="not_in_list")
        action = translate_step(trace)
        assert action.target_idx is None


class TestBackspaceAndDeleteBecomeSetValue:
    def test_lone_backspace_is_set_value_not_hotkey(self):
        trace = _hotkey_step("backspace", focused_id="e1")
        action = translate_step(trace)
        assert action.verb is Verb.SET_VALUE

    def test_lone_delete_is_set_value_not_hotkey(self):
        trace = _hotkey_step("delete", focused_id="e1")
        action = translate_step(trace)
        assert action.verb is Verb.SET_VALUE

    def test_backspace_target_idx_is_the_currently_focused_field(self):
        trace = _hotkey_step("backspace", focused_id="e2")
        action = translate_step(trace)
        assert action.target_idx == 1   # e2 is elements[1]

    def test_backspace_carries_no_text_value(self):
        """A correction keystroke deletes, it doesn't add new text this step --
        matches how a single-character SET_VALUE step already only carries
        that step's own text, not the field's running total."""
        trace = _hotkey_step("backspace", focused_id="e1")
        action = translate_step(trace)
        assert action.value == ""


class TestOtherHotkeysAreUnaffected:
    """Regression: only tab/shift+tab/backspace/delete change classification.
    Real, genuine window-level commands stay HOTKEY."""

    def test_escape_is_still_hotkey(self):
        trace = _hotkey_step("escape", focused_id="e1")
        assert translate_step(trace).verb is Verb.HOTKEY

    def test_enter_is_still_hotkey(self):
        trace = _hotkey_step("enter", focused_id="e1")
        assert translate_step(trace).verb is Verb.HOTKEY

    def test_ctrl_c_is_still_hotkey(self):
        trace = _hotkey_step("ctrl+c", focused_id="e1")
        assert translate_step(trace).verb is Verb.HOTKEY

    def test_arrow_left_is_still_hotkey(self):
        trace = _hotkey_step("arrow_left", focused_id="e1")
        assert translate_step(trace).verb is Verb.HOTKEY


class TestTypedTextStillSetValue:
    """Regression: real typed/pasted text is unaffected -- still classified
    via the typed_text branch, not the hotkey branch at all."""

    def test_pasted_text_is_set_value(self):
        trace = {
            "state": {
                "screen_resolution": [1920, 1080],
                "focused_element_id": "e1",
                "elements": [_entry("e1", "First Name")],
            },
            "mouse": {"actions": []},
            "keyboard": {"actions": [{"strokes": [{"pasted_text": "James", "key": ""}]}]},
        }
        action = translate_step(trace)
        assert action.verb is Verb.SET_VALUE
        assert action.value == "James"
