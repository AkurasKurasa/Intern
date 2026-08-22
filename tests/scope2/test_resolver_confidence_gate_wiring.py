"""
Piece 5: resolver/assign.py's STATUS_AUTO/STATUS_ABSTAIN decision
(currently `score >= tau and margin >= delta`) is the same shape of
"confident enough, or escalate" decision Scope #1 already has --
should_escalate(score < tau, margin < delta) is its exact negation. This
test proves the equivalence, then confirms the real source was rewired.
"""
import sys
from pathlib import Path

import pytest

from shared.confidence_gate import should_escalate

_ASSIGN_PY = (
    Path(__file__).resolve().parent.parent.parent
    / "components" / "scope2" / "resolver" / "assign.py"
)
_SOURCE = _ASSIGN_PY.read_text(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "components" / "scope2"))

from descriptors import FieldDescriptor, KIND_INPUT, SourceColumn  # noqa: E402
from resolver.assign import STATUS_ABSTAIN, STATUS_AUTO, resolve  # noqa: E402


def _field(label, key, dom_order, input_type="text"):
    return FieldDescriptor(
        label=label, label_rule=3, kind=KIND_INPUT, input_type=input_type,
        column_key=f"t:col{dom_order}", column_index=dom_order,
        header_text=label, dom_order=dom_order, truth_key=key,
    )


def _column(header, index):
    return SourceColumn(header=header, index=index, inferred_type="text",
                        samples=["x"], non_null=50, total=50)


@pytest.mark.parametrize("score,margin,tau,delta", [
    (0.9, 0.5, 0.6, 0.15),   # confident: score high, margin wide
    (0.55, 0.5, 0.6, 0.15),  # score too low
    (0.9, 0.10, 0.6, 0.15),  # margin too small
    (0.55, 0.10, 0.6, 0.15), # both too low
    (0.6, 0.15, 0.6, 0.15),  # exactly at both thresholds -- confident (>=)
])
def test_should_escalate_negation_matches_old_auto_condition(score, margin, tau, delta):
    old_is_auto = score >= tau and margin >= delta
    new_is_abstain = should_escalate(score < tau, margin < delta)
    assert new_is_abstain == (not old_is_auto)


def test_resolve_source_uses_should_escalate():
    idx = _SOURCE.index("status = ")
    line_end = _SOURCE.index("\n", idx)
    line = _SOURCE[idx:line_end]
    assert "should_escalate(" in line
    assert "score < tau" in line
    assert "margin < delta" in line


def test_shared_confidence_gate_import_present_in_assign_py():
    assert "from shared.confidence_gate import should_escalate" in _SOURCE


# ------------------------------------------------- real resolve() end-to-end
#
# The tests above prove the math (should_escalate's negation matches the old
# `score >= tau and margin >= delta` condition) and the wiring (the source
# text mentions should_escalate()) separately. Neither actually calls the
# real resolve() function, so neither would catch a bug like tau/delta being
# swapped, or the matrix being misindexed, inside resolve() itself. These
# tests close that gap by calling resolve() directly, the same way
# test_matcher_resolver.py does.


def test_resolve_is_auto_at_the_exact_tau_delta_boundary():
    """score == tau and margin == delta must be STATUS_AUTO: should_escalate
    is a >= (inclusive) comparison, not a strict one.

    tau/delta are set to 0.3/0.15 (rather than the module defaults of
    0.6/0.15) because 0.3 - 0.15 == 0.15 is exact in IEEE-754 double
    arithmetic -- verified: no float pair summing to the default tau=0.6
    produces a bit-exact 0.15 margin, so this is the boundary case that is
    actually reachable through real subtraction, not a rounded stand-in."""
    columns = [_column("FINAL GRADE", 0)]
    fields = [_field("Grade", "grade", 0, "number"),
              _field("Grade (Recomputed)", "grade_recomputed", 1, "number")]
    # scores[0] = 0.30 (== tau), margin = 0.30 - 0.15 = 0.15 (== delta), exact.
    mapping = resolve(columns, fields, [[0.30, 0.15]], tau=0.3, delta=0.15)

    assignment = mapping.assignments[0]
    assert assignment.score == 0.3
    assert assignment.margin == 0.15
    assert assignment.status == STATUS_AUTO
    assert mapping.as_truth() == {"Grade": "FINAL GRADE"}


def test_resolve_is_auto_when_clearly_confident():
    columns = [_column("PROGRAM", 0)]
    fields = [_field("Course", "course", 0)]
    mapping = resolve(columns, fields, [[0.95]], tau=0.6, delta=0.15)

    assert mapping.assignments[0].status == STATUS_AUTO
    assert mapping.as_truth() == {"Course": "PROGRAM"}


def test_resolve_abstains_just_below_the_boundary():
    """Just under tau and delta must flip to STATUS_ABSTAIN -- the mirror
    image of the inclusive-boundary AUTO case above (same 0.3/0.15
    thresholds, so the two tests bracket the same boundary from both sides)."""
    columns = [_column("FINAL GRADE", 0)]
    fields = [_field("Grade", "grade", 0, "number"),
              _field("Grade (Recomputed)", "grade_recomputed", 1, "number")]
    # scores[0] = 0.29 (< tau); margin = 0.29 - 0.16 = 0.13 (< delta)
    mapping = resolve(columns, fields, [[0.29, 0.16]], tau=0.3, delta=0.15)

    assignment = mapping.assignments[0]
    assert assignment.status == STATUS_ABSTAIN
    assert mapping.as_truth() == {}
