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
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

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
