"""
Piece 5: resolver/assign.py's STATUS_AUTO/STATUS_ABSTAIN decision
(currently `score >= tau and margin >= delta`) is the same shape of
"confident enough, or escalate" decision Scope #1 already has --
should_escalate(score < tau, margin < delta) is its exact negation. This
test proves the equivalence, then confirms the real source was rewired.
"""
import itertools
from pathlib import Path

import pytest

from shared.confidence_gate import should_escalate

_ASSIGN_PY = (
    Path(__file__).resolve().parent.parent.parent
    / "components" / "scope2" / "resolver" / "assign.py"
)
_SOURCE = _ASSIGN_PY.read_text(encoding="utf-8")


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
