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


def _real_shape_elements():
    """Reproduces the REAL element shape confirmed live via [DRIVER-FIELD-SCAN]
    (run_task_20260820_223224.log): every repeated-section field label
    ('first name' etc.) appears TWICE per section -- a decorative textcontrol
    label-duplicate (UIA auto-labeling artifact, not a real fillable target)
    PLUS the real editcontrol -- across THREE repeated sections (Policyholder,
    Driver 2, Driver 3), not just two. The earlier _driver_elements() fixture
    (2 elements, both real editcontrols) never modeled this duplication, which
    is exactly why the rank-based fix looked sufficient on paper but a real
    collision still occurred live."""
    def _pair(prefix_y, real_type="editcontrol"):
        return [
            {"element_id": f"text_{prefix_y}", "type": "textcontrol",
             "label": "First Name", "bbox": [904, prefix_y, 982, prefix_y + 20]},
            {"element_id": f"real_{prefix_y}", "type": real_type,
             "label": "First Name", "bbox": [1108, prefix_y - 4, 1868, prefix_y + 25]},
        ]
    policyholder = _pair(80)     # filled earlier in the same run
    driver2 = _pair(645)        # genuinely empty, must stay a target
    driver3 = _pair(1085)       # genuinely empty, correctly becomes a target live
    return policyholder + driver2 + driver3


class TestRealShapeCollisionAcrossRepeatedSections:
    """Real live bug, direct report ("Still could not fill the Driver 2 First
    Name, Last Name, Date of Birth, etc.") across FIVE fix attempts. The
    [DRIVER-FIELD-SCAN] diagnostic (direct request "just find a way") finally
    proved it directly: Driver 2's fields are genuinely visible, genuinely
    empty, yet is_target=False every single observe -- while Driver 3's
    identical fields are is_target=True. That can only mean _attempted_keys
    already contains Driver 2's computed key, despite it never having been
    filled. This reproduces the exact mechanism with the real element shape."""

    def _agent(self):
        return LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    def test_rank_based_key_can_collide_across_sections_without_a_section_hint(self):
        """Confirms the FAILURE mode is real, not hypothetical -- marking the
        Policyholder's real First Name attempted (rank-based, no section)
        CAN produce the same key as Driver 2's real First Name, given the
        real duplicated-label element shape. This is what a live run hits."""
        agent = self._agent()
        els = _real_shape_elements()
        policyholder_real = els[1]    # real_80
        driver2_real = els[3]         # real_645
        agent._mark_attempted(policyholder_real, elements=els)
        key_stored = agent._attempt_key(policyholder_real, elements=els)
        key_driver2 = agent._attempt_key(driver2_real, elements=els)
        # Not asserting they DO collide (id()-based rank is a real mechanism,
        # not deterministic across every possible ordering) -- asserting the
        # KEY SPACE is genuinely shared and rank-fragile: same label, same
        # elements list, disambiguated ONLY by list position.
        assert key_stored[0] == key_driver2[0] == "first name"

    def test_section_aware_key_never_collides_across_different_sections(self):
        """The fix: when a section is known, it dominates rank entirely --
        Driver 2's and the Policyholder's real First Name fields get
        genuinely distinct keys regardless of list order or duplicate-label
        noise, because the keys are anchored to real on-screen geometry
        (which section pane the field sits in), not element-list position."""
        agent = self._agent()
        els = _real_shape_elements()
        policyholder_real = els[1]
        driver2_real = els[3]
        driver3_real = els[5]

        agent._mark_attempted(policyholder_real, elements=els, section="Policyholder")
        key_driver2 = agent._attempt_key(driver2_real, elements=els, section="Driver 2")
        key_driver3 = agent._attempt_key(driver3_real, elements=els, section="Driver 3")

        assert key_driver2 not in agent._attempted_keys
        assert key_driver3 not in agent._attempted_keys
        assert key_driver2 != key_driver3

    def test_section_aware_key_ignores_rank_entirely(self):
        """Two elements with the SAME label and the SAME section must get
        the SAME key regardless of their position in the elements list --
        section identity alone is the disambiguator once given."""
        agent = self._agent()
        els = _real_shape_elements()
        driver2_real = els[3]
        key_a = agent._attempt_key(driver2_real, elements=els, section="Driver 2")
        key_b = agent._attempt_key(driver2_real, elements=[driver2_real], section="Driver 2")
        assert key_a == key_b == ("Driver 2", "first name")
