"""
Piece 5 of docs/superpowers/specs/2026-08-21-scope-unification-design.md:
the "is this confidence signal good enough, or should I escalate" step,
previously written out three times (twice in agent.py, once in
resolver/assign.py) as slightly different boolean expressions. This module
gives it one shared, tested home. Each caller still computes its OWN
confidence signal its own way -- this function only combines
already-evaluated booleans.
"""
from shared.confidence_gate import should_escalate


def test_no_signals_means_no_escalation():
    assert should_escalate() is False


def test_all_false_means_no_escalation():
    assert should_escalate(False, False, False) is False


def test_one_true_signal_escalates():
    assert should_escalate(False, True, False) is True


def test_all_true_escalates():
    assert should_escalate(True, True, True) is True


def test_single_false_signal_does_not_escalate():
    assert should_escalate(False) is False


def test_single_true_signal_escalates():
    assert should_escalate(True) is True


def test_works_with_expressions_not_just_literals():
    # Mirrors real call-site usage: callers pass already-evaluated
    # comparisons, not bare booleans.
    confidence = 0.37
    threshold = 0.50
    streak = 0
    assert should_escalate(confidence < threshold, streak > 0) is True


def test_two_signal_case_matches_scope2_resolver_shape():
    # Scope #2's resolver/assign.py escalates (abstains) when score is too
    # low OR margin is too small -- the exact two-signal shape this task
    # will wire in during Task 3.
    score, margin = 0.55, 0.10
    tau, delta = 0.6, 0.15
    assert should_escalate(score < tau, margin < delta) is True
    score, margin = 0.9, 0.5
    assert should_escalate(score < tau, margin < delta) is False
