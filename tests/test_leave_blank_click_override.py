"""
Regression test for a confirmed live infinite loop on combobox fields.

Found 2026-08-07 from a real run stuck on "Trim / Sub-model" (a combobox):
the log showed the LLM/lookup correctly resolving 'Sport 2.0T', then
_merge() overriding the "type" decision into a "click" (comboboxes need a
click to open their dropdown before a value can be selected — see
_merge()'s TRANSFORMER_TYPE_OVERRIDE_THRESHOLD branch), then the
leave-blank guard reading the click prediction's missing "text" key as
"leave this blank" and Tab-skipping away before the dropdown could even be
used — then the transformer clicked straight back onto the same combobox
the very next step. Confirmed via the log: "[MERGE] TRANSFORMER overrides
LLM type→click" immediately followed by "[OPT2] ... LLM value for 'Trim /
Sub-model' -> ''" and "leave-blank/empty — Tab past (skip)", repeating for
dozens of steps with 0 additional fields ever getting filled.

Fix: _is_leave_blank_prediction() only returns True for an ACTUAL keyboard/
type prediction — a click override (no "text" key at all) can never be
mistaken for a deliberate leave-blank decision.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import _is_leave_blank_prediction


class TestIsLeaveBlankPrediction:
    def test_true_for_genuinely_blank_type_action(self):
        assert _is_leave_blank_prediction({"action_type": "keyboard", "text": ""}) is True

    def test_true_for_none_marker(self):
        assert _is_leave_blank_prediction({"action_type": "keyboard", "text": "None"}) is True

    def test_true_for_na_marker(self):
        assert _is_leave_blank_prediction({"action_type": "keyboard", "text": "n/a"}) is True

    def test_true_for_leave_blank_phrase_with_note(self):
        assert _is_leave_blank_prediction(
            {"action_type": "keyboard", "text": "(leave blank - optional field)"}) is True

    def test_false_for_real_value_type_action(self):
        assert _is_leave_blank_prediction({"action_type": "keyboard", "text": "Sport 2.0T"}) is False

    def test_false_for_click_override_even_with_no_text_key(self):
        """The actual bug: _merge() overriding a combobox type-decision into a
        click has no "text" key at all — this must NEVER be treated as a
        deliberate leave-blank, or the dropdown click gets Tab-skipped away
        before it can be used, causing an infinite click-then-skip loop."""
        assert _is_leave_blank_prediction(
            {"action_type": "click", "click_position": [1456, 356]}) is False

    def test_false_for_click_override_with_stray_empty_text_key(self):
        """Belt-and-suspenders: even if a click prediction somehow carried an
        empty "text" key, action_type must still gate it out."""
        assert _is_leave_blank_prediction(
            {"action_type": "click", "click_position": [1456, 356], "text": ""}) is False
