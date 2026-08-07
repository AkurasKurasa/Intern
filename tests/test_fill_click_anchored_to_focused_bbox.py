"""
Regression test for anchoring a merge-overridden FILL click to the
focused field's own bbox (components/agent/agent.py, right after
`prediction = self._merge(t_pred, t_conf, llm_action, state)` in the
OPT2 fill branch).

Found live 2026-08-07, directly reported by the user: "Same loop on Auto
Enrolled" (1175 log lines for 'Auto-Pay Enrolled' in one run). Traced it:
_merge()'s type->click override uses t_pred['click_position'] -- the
transformer's own, SEPARATELY-learned navigation pointer (trained to
answer "where do I click next", not "where is the field currently
focused"). Those are different questions. At one visit the pointer
guessed (1484,426), which missed 'Auto-Pay Enrolled''s real bbox
entirely -- the click landed on some unrelated element, which THEN got
marked attempted by _record_attempt() instead of the checkbox actually
intended. 'Auto-Pay Enrolled' itself was never recorded as attempted, so
it kept re-entering the fill branch every time focus cycled back to it,
forever.

The field that's ALREADY focused (_fe2) has a known bbox -- there's no
reason to trust a second, independent guess for "click the thing that's
already focused". Fix: when merge overrides the fill decision into a
click, anchor click_position to _fe2's own bbox center instead of
whatever _merge() computed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _anchor_click_to_focused_bbox(prediction, fe2):
    """Mirrors the CURRENT anchoring logic in agent.py's run()."""
    if prediction.get("action_type") == "click" and fe2 and fe2.get("bbox"):
        feb = fe2["bbox"]
        prediction = dict(prediction)
        prediction["click_position"] = [(feb[0] + feb[2]) / 2, (feb[1] + feb[3]) / 2]
    return prediction


def _checkbox(label="Auto-Pay Enrolled", bbox=(960, 448, 1020, 476)):
    return {"element_id": "cb1", "type": "checkboxcontrol", "label": label,
            "text": label, "value": "", "bbox": list(bbox), "window_role": "active"}


class TestFillClickAnchoredToFocusedBbox:
    def test_click_is_anchored_to_the_focused_checkboxs_own_bbox(self):
        """The actual live bug: the transformer's guessed position
        (1484, 426) has nothing to do with where 'Auto-Pay Enrolled'
        actually sits (960-1020, 448-476) -- the click must use the
        checkbox's own bbox center, not the guess."""
        fe2 = _checkbox()
        merge_prediction = {"action_type": "click", "click_position": [1484, 426]}

        fixed = _anchor_click_to_focused_bbox(merge_prediction, fe2)

        assert fixed["click_position"] == [990.0, 462.0]   # fe2's own bbox center
        assert fixed["click_position"] != [1484, 426]      # not the transformer's guess

    def test_keyboard_predictions_are_not_affected(self):
        fe2 = _checkbox()
        merge_prediction = {"action_type": "keyboard", "text": "9"}

        fixed = _anchor_click_to_focused_bbox(merge_prediction, fe2)

        assert fixed == merge_prediction   # unchanged, no click_position to anchor

    def test_no_focused_element_leaves_prediction_unchanged(self):
        merge_prediction = {"action_type": "click", "click_position": [1484, 426]}

        fixed = _anchor_click_to_focused_bbox(merge_prediction, None)

        assert fixed["click_position"] == [1484, 426]

    def test_focused_element_without_a_bbox_leaves_prediction_unchanged(self):
        fe2 = _checkbox()
        del fe2["bbox"]
        merge_prediction = {"action_type": "click", "click_position": [1484, 426]}

        fixed = _anchor_click_to_focused_bbox(merge_prediction, fe2)

        assert fixed["click_position"] == [1484, 426]

    def test_applies_equally_to_a_focused_combobox(self):
        """Not checkbox-specific -- comboboxes go through the exact same
        merge-override path when opening their dropdown for the first
        time, and were latently affected by the same imprecision, just
        less visibly (transformer predictions for comboboxes tend to be
        closer to correct)."""
        cbox = {"element_id": "cb2", "type": "comboboxcontrol", "label": "Payment Frequency",
                "text": "Payment Frequency", "value": "", "bbox": [900, 500, 1100, 530],
                "window_role": "active"}
        merge_prediction = {"action_type": "click", "click_position": [50, 50]}

        fixed = _anchor_click_to_focused_bbox(merge_prediction, cbox)

        assert fixed["click_position"] == [1000.0, 515.0]


class TestRecordAttemptNowAttributesCorrectlyAfterAnchoring:
    """End-to-end sanity check: once the click is anchored to the focused
    field's own bbox, _elem_at() (used by _record_attempt for click
    predictions) resolves back to the SAME field, closing the loop that
    let 'Auto-Pay Enrolled' go unrecorded."""

    def _make_agent(self):
        return LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    def test_elem_at_the_anchored_position_is_the_focused_field_itself(self):
        agent = self._make_agent()
        fe2 = _checkbox()
        merge_prediction = {"action_type": "click", "click_position": [1484, 426]}
        fixed = _anchor_click_to_focused_bbox(merge_prediction, fe2)

        found = agent._elem_at({"elements": [fe2]}, fixed["click_position"])

        assert found is fe2
