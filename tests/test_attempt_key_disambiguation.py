"""
Regression test for _attempt_key disambiguating repeated same-label fields.

Found 2026-08-07 from a direct live report: Driver 1's and Driver 2's "First
Name" fields were "indistinguishable" and got "mixed up." Confirmed in real
trace data — every driver block labels its name field just "First Name" with
no driver-number qualifier. _attempt_key used the label alone as the field's
identity, so filling Driver 1's First Name marked the shared key "first name"
as attempted — Driver 2's First Name (a different, still-empty element) then
silently read as already-done and got skipped.

Fix: when the full elements list from the same state is supplied, disambiguate
by rank among same-labeled elements in list order (stable under scroll, since
list order reflects accessibility-tree traversal order, not bbox).

There are two copies of this logic that must stay behaviorally identical:
transformer.py's module-level _attempt_key (train-time) and agent.py's
LLMAgent._attempt_key (live-inference-time).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from intelligence.model.transformer import _attempt_key as transformer_attempt_key
from agent.agent import LLMAgent


def _driver_elements():
    """Two repeated-section fields sharing the identical label 'First Name',
    plus one uniquely-labeled field for contrast."""
    return [
        {"element_id": "e0", "label": "First Name", "bbox": [100, 200, 300, 230]},
        {"element_id": "e1", "label": "First Name", "bbox": [100, 420, 300, 450]},
        {"element_id": "e2", "label": "Policy Number", "bbox": [100, 50, 300, 80]},
    ]


class TestTransformerAttemptKey:
    def test_without_elements_context_same_label_collapses_to_one_key(self):
        """Old behavior preserved for callers that can't supply the list."""
        els = _driver_elements()
        assert transformer_attempt_key(els[0]) == transformer_attempt_key(els[1])

    def test_with_elements_context_same_label_fields_get_distinct_keys(self):
        els = _driver_elements()
        k0 = transformer_attempt_key(els[0], elements=els)
        k1 = transformer_attempt_key(els[1], elements=els)
        assert k0 != k1

    def test_uniquely_labeled_field_key_is_unaffected(self):
        els = _driver_elements()
        assert transformer_attempt_key(els[2], elements=els) == "policy number"

    def test_disambiguated_key_is_stable_across_separate_state_snapshots(self):
        """The whole point: a key computed for Driver 2's field in a LATER
        snapshot (a different dict object, same relative list position) must
        match the key recorded from an EARLIER snapshot, so the 'already
        attempted' lookup actually works across steps."""
        snapshot_1 = _driver_elements()
        snapshot_2 = _driver_elements()  # fresh objects, same order — like a later step
        k_driver2_early = transformer_attempt_key(snapshot_1[1], elements=snapshot_1)
        k_driver2_later = transformer_attempt_key(snapshot_2[1], elements=snapshot_2)
        assert k_driver2_early == k_driver2_later

    def test_unlabeled_element_falls_back_to_bbox_bucket_unchanged(self):
        elem = {"element_id": "e9", "bbox": [100, 100, 140, 120]}
        key = transformer_attempt_key(elem, elements=[elem])
        assert key[0] == "@"


class TestAgentAttemptKey:
    def _agent(self):
        return LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    def test_matches_transformer_behavior_without_elements(self):
        agent = self._agent()
        els = _driver_elements()
        assert agent._attempt_key(els[0]) == transformer_attempt_key(els[0])

    def test_matches_transformer_behavior_with_elements(self):
        agent = self._agent()
        els = _driver_elements()
        assert (agent._attempt_key(els[0], elements=els)
                == transformer_attempt_key(els[0], elements=els))
        assert (agent._attempt_key(els[1], elements=els)
                == transformer_attempt_key(els[1], elements=els))

    def test_marking_driver1_attempted_does_not_mark_driver2_attempted(self):
        """The actual bug, end to end through the agent's own tracking."""
        agent = self._agent()
        els = _driver_elements()
        agent._mark_attempted(els[0], elements=els)   # Driver 1's First Name filled
        assert agent._attempt_key(els[0], elements=els) in agent._attempted_keys
        assert agent._attempt_key(els[1], elements=els) not in agent._attempted_keys
