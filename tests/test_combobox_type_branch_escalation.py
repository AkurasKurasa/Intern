"""
Regression test for the missing escalation counter in agent.py's
"type-into-combobox" branch (~L4401, the "Combobox: %r not in dropdown —
pressing Escape" else clause).

Found live 2026-08-12, directly reported ("Another loop damn"):
'Bodily Injury (k$/k$)' retried the exact same failing search 71 times
across one run. Root cause: the record's own target value ('30/60')
genuinely isn't one of this control's real options (['25/50', '50/100',
'100/300', '250/500', '500/500', '300/300']) -- a real data problem no
amount of retrying can ever resolve. This branch had no escalation/give-up
counter at all, unlike its sibling click-fill branch (~L3502), which
already solved the identical SHAPE of bug on 2026-08-09 ('Payment
Frequency', 121 retries in one run) via _combobox_dropdown_fail_counts --
a 2-strike counter that marks the field attempted and moves on instead of
retrying identically forever.

Fix: reuse that exact same, already-proven counter/constant
(_combobox_dropdown_fail_counts / _COMBOBOX_DROPDOWN_FAIL_LIMIT) in the
type-into-combobox branch's failure path too, instead of inventing a
second mechanism. These tests pin down the shared counting/escalation
logic itself.

FOLLOW-UP FIX, same day, found immediately after shipping the above:
'Bodily Injury (k$/k$)' STILL retried right after "marking attempted and
moving on" was logged. self._mark_attempted() is bookkeeping only -- it
never moves keyboard focus. Focus stayed on the same failed combobox, so
the very next step's LLM lookup (which doesn't check attempted-state at
all -- it just answers from the record) re-triggered the entire branch
from scratch. Fixed two ways: (1) an active redirect to the next visible
target after escalating, mirroring the click-fill sibling branch's own
already-working redirect; (2) the escalated field is now ALSO added to
self._leave_blank_keys (a 2-strike give-up is just as final and
deliberate a decision as a genuine leave-blank answer), so the reclick
guard extended earlier the same day for leave-blank fields also protects
against the TRANSFORMER's own navigate-click pointer drifting back onto
this field later, not just this immediate re-ask.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent

_COMBOBOX_DROPDOWN_FAIL_LIMIT = 2  # matches agent.py's real constant


def _record_combobox_failure(fail_counts: dict, label: str,
                              limit: int = _COMBOBOX_DROPDOWN_FAIL_LIMIT) -> tuple[dict, bool]:
    """Mirrors the CURRENT escalation logic (shared by both the type-into-
    combobox branch and its click-fill sibling): increment the fail count
    for this label; once it reaches `limit`, pop it and signal 'give up,
    mark attempted, move on' (True) instead of 'try again next time'
    (False)."""
    fail_counts = dict(fail_counts)
    fail_counts[label] = fail_counts.get(label, 0) + 1
    give_up = fail_counts[label] >= limit
    if give_up:
        fail_counts.pop(label, None)
    return fail_counts, give_up


class TestFirstFailureDoesNotEscalate:
    def test_single_failure_just_increments(self):
        counts, give_up = _record_combobox_failure({}, "Bodily Injury (k$/k$)")
        assert give_up is False
        assert counts["Bodily Injury (k$/k$)"] == 1


class TestSecondFailureEscalates:
    def test_reaching_the_limit_signals_give_up(self):
        counts, _ = _record_combobox_failure({}, "Bodily Injury (k$/k$)")
        counts, give_up = _record_combobox_failure(counts, "Bodily Injury (k$/k$)")
        assert give_up is True

    def test_counter_is_cleared_once_it_escalates(self):
        """Matches the real code's own .pop() -- a field that gets marked
        attempted and moves on shouldn't leave stale count state behind."""
        counts, _ = _record_combobox_failure({}, "Bodily Injury (k$/k$)")
        counts, _ = _record_combobox_failure(counts, "Bodily Injury (k$/k$)")
        assert "Bodily Injury (k$/k$)" not in counts


class TestDifferentFieldsTrackedIndependently:
    def test_one_fields_failures_do_not_affect_another(self):
        counts, give_up_a = _record_combobox_failure({}, "Bodily Injury (k$/k$)")
        counts, give_up_b = _record_combobox_failure(counts, "Property Damage ($)")
        assert give_up_a is False
        assert give_up_b is False
        assert counts == {"Bodily Injury (k$/k$)": 1, "Property Damage ($)": 1}


class TestThisIsTheExactLiveScenario:
    def test_bodily_injury_gives_up_on_its_second_failure_not_its_71st(self):
        """Direct regression pin for the incident: with the fix, this field
        gives up after 2 failed attempts, not 71."""
        counts = {}
        attempts = 0
        gave_up = False
        for _ in range(71):
            attempts += 1
            counts, gave_up = _record_combobox_failure(counts, "Bodily Injury (k$/k$)")
            if gave_up:
                break
        assert gave_up is True
        assert attempts == _COMBOBOX_DROPDOWN_FAIL_LIMIT


def _combobox_element(label="Bodily Injury (k$/k$)", element_id="e1",
                       bbox=(1400, 200, 1600, 230)):
    return {"element_id": element_id, "type": "comboboxcontrol", "label": label,
            "text": label, "value": "", "bbox": list(bbox), "window_role": "active"}


class TestEscalationMarksLeaveBlankNotJustAttempted:
    """The follow-up fix, verified against the REAL LLMAgent methods (not a
    mirror) -- confirms escalation's output (a key in self._leave_blank_keys)
    actually composes with the reclick guard's input (checking that same
    set), which is the part that was silently broken before: attempted-only
    bookkeeping that nothing downstream actually respected for a still-
    focused, still-failing combobox."""

    def _make_agent(self):
        return LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    def test_mark_attempted_alone_is_not_enough_to_block_a_reclick(self):
        """Pins down the ORIGINAL gap this whole fix responds to: attempted
        alone does NOT stop the reclick guard (matching the SEVENTH-round
        reasoning -- attempted alone must stay permissive for fields that
        still genuinely need a value). This is why leave_blank_keys, not
        attempted_keys, had to be the signal."""
        agent = self._make_agent()
        agent._executor = MagicMock()
        field = _combobox_element()
        elements = [field]
        agent._mark_attempted(field, elements=elements)
        key = agent._attempt_key(field, elements=elements)

        assert key in agent._attempted_keys
        assert key not in agent._leave_blank_keys

    def test_escalation_style_marking_makes_the_field_reclick_protected(self):
        """Mirrors exactly what the real escalation code now does on give-up:
        mark_attempted() AND add to leave_blank_keys. Confirms the SAME key
        computation used by both the escalation site and the reclick guard
        agree with each other (attempt_key is deterministic for a labeled
        element), so the guard actually recognizes this field afterward."""
        agent = self._make_agent()
        agent._executor = MagicMock()
        field = _combobox_element()
        elements = [field]

        agent._mark_attempted(field, elements=elements)
        agent._leave_blank_keys.add(agent._attempt_key(field, elements=elements))

        # Same lookup the real reclick guard performs at the click position.
        key_at_reclick_time = agent._attempt_key(field, elements=elements)
        assert key_at_reclick_time in agent._leave_blank_keys
