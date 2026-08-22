# Shared Confidence Gate (Piece 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the "is this confidence signal good enough, or should I escalate"
comparison — currently written out three separate times across the codebase
(twice in Scope #1's `agent.py`, once implicitly in Scope #2's `resolver/
assign.py`) — into one small, shared, well-tested function both scopes call.

**Architecture:** One new pure function, `should_escalate(*signals: bool) -> bool`,
in `components/shared/confidence_gate.py` (same package Piece 2 created for
`run_recorder.py`). Each caller keeps computing its OWN confidence signal its
own way — Scope #1's Transformer click-confidence and struggle-streak
counters, Scope #2's matcher score/margin thresholds — and passes the
already-evaluated booleans in. The function itself is `any(signals)`: escalate
if any signal says "not confident." Nothing about HOW confidence is measured
is shared, only the "combine and decide" step.

**Tech Stack:** Python 3.12, `pytest`, stdlib only — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-21-scope-unification-design.md`
(Piece 5)

## Global Constraints

- Scope #1's decision-making, click/type mechanics, and speed must not
  change at all. Task 2 touches `agent.py`, but ONLY to replace an existing
  boolean expression with a call that evaluates to the exact same value for
  every possible input — proven by a truth-table test before the expression
  is ever touched in `agent.py` itself. If the truth-table test cannot prove
  exact equivalence, this plan is wrong and must not proceed with Task 2.
- Recording/utility failures must never break a run — `should_escalate`
  takes plain booleans and returns a plain boolean; there's no I/O, so
  nothing here can raise for a caller passing valid arguments.
- Full existing test suite (1313 passed, 9 skipped as of this branch's last
  commit, 88f19048) must stay green after every task.
- No task-specific hardcoding — `should_escalate` must not know it's being
  used for click confidence or match confidence; it only combines booleans.

---

### Task 1: Shared confidence-gate module

**Files:**
- Create: `components/shared/confidence_gate.py`
- Test: `tests/test_shared_confidence_gate.py`

**Interfaces:**
- Produces: `should_escalate(*signals: bool) -> bool` — returns `True` if any
  signal is truthy (i.e., any reason to distrust the fast/cheap answer),
  `False` only if every signal is falsy. `should_escalate()` (zero signals)
  returns `False` — no reason given, no escalation.

This function is deliberately trivial (`any(signals)`). The value isn't
clever logic — it's giving the "combine confidence signals into an
escalate/don't-escalate decision" step ONE tested, named home instead of
three separately-written copies of the same shape of expression that could
drift out of sync with each other.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_shared_confidence_gate.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_shared_confidence_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.confidence_gate'`

- [ ] **Step 3: Write the implementation**

```python
# components/shared/confidence_gate.py
"""
Shared "should I trust the fast answer, or escalate" decision, used by both
Scope #1 (components/agent/agent.py) and Scope #2
(components/scope2/resolver/assign.py).

Both scopes already compute a confidence signal before this point --
Scope #1's Transformer click-confidence plus two struggle-streak counters,
Scope #2's matcher score and margin from resolver/assign.py -- this module
does not touch or know about either. It only combines already-evaluated
booleans into one escalate/don't-escalate decision, replacing three
separately-written copies of the same shape of expression (two in agent.py,
one implicit in resolver/assign.py's `score >= tau and margin >= delta`)
with one shared, tested function.

See docs/superpowers/specs/2026-08-21-scope-unification-design.md, Piece 5.
"""


def should_escalate(*signals: bool) -> bool:
    """True if any signal indicates the fast/cheap answer shouldn't be
    trusted as-is. No signals given -> no reason to escalate -> False."""
    return any(signals)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_shared_confidence_gate.py -v`
Expected: PASS, all 8 tests

- [ ] **Step 5: Commit**

```bash
git add components/shared/confidence_gate.py tests/test_shared_confidence_gate.py
git commit -m "Add shared confidence gate, replacing 3 independently-written escalate checks"
```

---

### Task 2: Wire Scope #1 (`agent.py`) to the shared gate

**Files:**
- Modify: `components/agent/agent.py:3907` (the `_cb_defer` combobox-deferral trigger)
- Modify: `components/agent/agent.py:4369-4373` (the `_deep_reason` LLM-branch trigger)
- Test: `tests/test_agent_confidence_gate_wiring.py`

**Interfaces:**
- Consumes: `should_escalate(*signals: bool) -> bool` from Task 1.

**This is the one task in this plan that touches Scope #1's decision-making
file.** It is allowed ONLY because the change is a pure, proven-equivalent
refactor — the exact same three-condition OR expression, just called through
one shared function instead of written out twice. Nothing about WHEN Scope
#1 escalates changes. If Step 1's truth-table test cannot prove the old
expression and the new call always agree, stop and do not proceed to Step 3
— that would mean this task is unsafe as designed, not just as implemented.

Both existing call sites use the identical three-condition expression
today:

```python
# agent.py:3907 (inside the combobox-deferral branch)
_cb_defer = t_conf < _MED_CONF or _lowconf_fallback_streak > 0 or _reclick_streak > 0
```

```python
# agent.py:4369-4373 (the general LLM-branch trigger)
_deep_reason = (
    t_conf < _MED_CONF
    or _lowconf_fallback_streak > 0
    or _reclick_streak > 0
)
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agent_confidence_gate_wiring.py
"""
Piece 5: agent.py's two escalate-trigger call sites (_cb_defer at ~L3907,
_deep_reason at ~L4369-4373) both currently write out the identical
three-condition OR expression by hand. This test first PROVES the shared
should_escalate() function is behaviorally identical to that expression for
every possible input (a truth table over the 3 boolean conditions), then
confirms both call sites in the real agent.py source were rewired to use it
-- source-level, matching this project's established pattern for verifying
agent.py changes without needing to construct a full LLMAgent instance.
"""
import itertools
import re
from pathlib import Path

from shared.confidence_gate import should_escalate

_AGENT_PY = Path(__file__).resolve().parent.parent / "components" / "agent" / "agent.py"
_SOURCE = _AGENT_PY.read_text(encoding="utf-8")


def _old_expression(t_conf_low: bool, streak1: bool, streak2: bool) -> bool:
    """The exact expression both call sites used before this task, as a
    function of its three boolean conditions (t_conf < _MED_CONF,
    _lowconf_fallback_streak > 0, _reclick_streak > 0)."""
    return t_conf_low or streak1 or streak2


def test_should_escalate_matches_old_expression_for_every_combination():
    for t_conf_low, streak1, streak2 in itertools.product([False, True], repeat=3):
        expected = _old_expression(t_conf_low, streak1, streak2)
        actual = should_escalate(t_conf_low, streak1, streak2)
        assert actual == expected, (t_conf_low, streak1, streak2)


def test_cb_defer_call_site_uses_should_escalate():
    idx = _SOURCE.index("_cb_defer = ")
    line_end = _SOURCE.index("\n", idx)
    line = _SOURCE[idx:line_end]
    assert "should_escalate(" in line
    assert "t_conf < _MED_CONF" in line
    assert "_lowconf_fallback_streak > 0" in line
    assert "_reclick_streak > 0" in line


def test_deep_reason_call_site_uses_should_escalate():
    idx = _SOURCE.index("_deep_reason = ")
    window = _SOURCE[idx:idx + 200]
    assert "should_escalate(" in window
    assert "t_conf < _MED_CONF" in window
    assert "_lowconf_fallback_streak > 0" in window
    assert "_reclick_streak > 0" in window


def test_shared_confidence_gate_import_present():
    assert "from shared.confidence_gate import should_escalate" in _SOURCE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent_confidence_gate_wiring.py -v`
Expected: `test_should_escalate_matches_old_expression_for_every_combination` PASSES
(it only tests Task 1's already-built function against the old expression's
logic, not the real source yet) — but `test_cb_defer_call_site_uses_
should_escalate`, `test_deep_reason_call_site_uses_should_escalate`, and
`test_shared_confidence_gate_import_present` FAIL, since `agent.py` hasn't
been rewired yet.

- [ ] **Step 3: Implement — add the import, rewire both call sites**

Add the import near `agent.py`'s other same-package imports (right after
the existing `from data_sources.notepad_source import ...` line, ~L162):

```python
from shared.confidence_gate import should_escalate
```

Replace `agent.py:3907`:

```python
                            _cb_defer = should_escalate(
                                t_conf < _MED_CONF,
                                _lowconf_fallback_streak > 0,
                                _reclick_streak > 0,
                            )
```

Replace `agent.py:4369-4373`:

```python
                _deep_reason = should_escalate(
                    t_conf < _MED_CONF,
                    _lowconf_fallback_streak > 0,
                    _reclick_streak > 0,
                )
```

Preserve each site's original indentation exactly (the two call sites sit
at different nesting depths in the real file — match whatever the
surrounding lines around L3907 and L4369 already use, not the plan's own
formatting).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agent_confidence_gate_wiring.py -v`
Expected: PASS, all 4 tests

- [ ] **Step 5: Run the full existing suite to confirm no regression**

Run: `pytest -q`
Expected: same pass count as before this task, plus this task's 4 new tests
and Task 1's 8 — no existing test's outcome changes. `agent.py`'s behavior
is unchanged (proven by Step 1's truth-table test), so no agent-related test
should flip.

- [ ] **Step 6: Commit**

```bash
git add components/agent/agent.py tests/test_agent_confidence_gate_wiring.py
git commit -m "Route agent.py's two escalate triggers through the shared confidence gate"
```

---

### Task 3: Wire Scope #2 (`resolver/assign.py`) to the shared gate

**Files:**
- Modify: `components/scope2/resolver/assign.py:167` (the `status = STATUS_AUTO if (...) else STATUS_ABSTAIN` line)
- Test: `tests/scope2/test_resolver_confidence_gate_wiring.py`

**Interfaces:**
- Consumes: `should_escalate(*signals: bool) -> bool` from Task 1.

Today, `resolve()` in `components/scope2/resolver/assign.py` decides
`STATUS_AUTO` vs `STATUS_ABSTAIN` with:

```python
status = STATUS_AUTO if (score >= tau and margin >= delta) else STATUS_ABSTAIN
```

`score >= tau and margin >= delta` being true means "confident" (`STATUS_
AUTO`); its negation — `score < tau or margin < delta` — means "not
confident, abstain," which is exactly `should_escalate(score < tau, margin
< delta)`. This task rewrites the line to go through the shared gate,
proving equivalence the same way Task 2 did.

- [ ] **Step 1: Write the failing tests**

```python
# tests/scope2/test_resolver_confidence_gate_wiring.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/scope2/test_resolver_confidence_gate_wiring.py -v`
Expected: the 5 parametrized equivalence tests PASS (they only test Task 1's
function against the math, not the real source yet); `test_resolve_source_
uses_should_escalate` and `test_shared_confidence_gate_import_present_in_
assign_py` FAIL, since `resolver/assign.py` hasn't been rewired yet.

- [ ] **Step 3: Implement**

`resolver/assign.py` already inserts the repo root (`components/scope2`)
onto `sys.path` near its own top (`REPO = Path(__file__).resolve().parents[1]`
/ `sys.path.insert(0, str(REPO))`) — `components/shared` needs `components/`
(i.e. `REPO.parent`) on the path too, the same pattern Piece 2's Task 3 used
for `automate.py`. Add, near that existing block:

```python
sys.path.insert(0, str(REPO.parent))
from shared.confidence_gate import should_escalate  # noqa: E402
```

Replace the line at `resolver/assign.py:167`:

```python
        status = STATUS_ABSTAIN if should_escalate(score < tau, margin < delta) else STATUS_AUTO
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/scope2/test_resolver_confidence_gate_wiring.py -v`
Expected: PASS, all 7 tests (5 parametrized + 2 source checks)

- [ ] **Step 5: Run the full existing suite to confirm no regression**

Run: `pytest -q`
Expected: same pass count as after Task 2, plus this task's 7 new tests —
no existing test's outcome changes, including Scope #2's own existing
resolver tests (the STATUS_AUTO/STATUS_ABSTAIN boundary behavior is
unchanged, only how it's computed).

- [ ] **Step 6: Commit**

```bash
git add components/scope2/resolver/assign.py tests/scope2/test_resolver_confidence_gate_wiring.py
git commit -m "Route Scope #2's resolver through the shared confidence gate"
```

---

### Task 4: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the complete test suite one more time**

Run: `pytest -q`
Expected: all tests pass, 0 failures.

- [ ] **Step 2: `git status` check**

Run: `git status`
Expected: clean working tree, all changes committed across Tasks 1–3.

- [ ] **Step 3: Push the branch**

```bash
git push origin experiment/scope-unification
```
