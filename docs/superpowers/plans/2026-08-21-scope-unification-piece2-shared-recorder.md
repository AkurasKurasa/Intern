# Shared Success Recording (Piece 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Scope #1's and Scope #2's two separate, independently-unreliable
run-result recording paths with one shared, hardened recorder both scopes call —
so a fix to "why didn't this run get recorded" benefits both scopes at once.

**Architecture:** One new module (`components/shared/run_recorder.py`) with a
single `record_run_result(scope, row, path=None)` function that appends one
JSON row to a shared trend log (`data/output/run_metrics.jsonl`, tagged with a
`"scope"` field). Each scope keeps its own rich, scope-specific result
computation exactly as it is today — only the unreliable persistence step is
replaced. Failures are logged at `ERROR` with a full traceback, not silently
swallowed at `DEBUG` (the confirmed root cause of the current gap — see Task 1).

**Tech Stack:** Python 3.12, `pytest`, stdlib `json`/`logging`/`pathlib` only —
no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-21-scope-unification-design.md`
(Piece 2, and its Error Handling section)

## Global Constraints

- Scope #1's decision-making, click/type mechanics, and speed must not change
  at all — this plan only touches the `finally:` block of `run_task.py`, never
  `agent.py`'s live-execution code.
- Recording a run's result must never fail the run itself — every persistence
  call in this plan catches its own exceptions internally.
- Full existing test suite (1293 passed, 9 skipped as of 2026-08-21) must stay
  green after every task.
- No task-specific hardcoding — the shared recorder must work for either
  scope's row shape without knowing anything about form fields or portals.

---

### Task 1: Shared run recorder module

**Files:**
- Create: `components/shared/__init__.py` (empty — makes `shared` a package)
- Create: `components/shared/run_recorder.py`
- Test: `tests/test_shared_run_recorder.py`

**Interfaces:**
- Produces: `record_run_result(scope: str, row: Mapping[str, Any], path:
  Optional[Path] = None) -> bool` — appends one JSONL row (`{"scope": ...,
  "timestamp": ..., **row}`) to `path` (default `data/output/run_metrics.
  jsonl`, resolved from the repo root regardless of caller's cwd). Returns
  `True` on success, `False` on failure. Never raises.
- Produces: `DEFAULT_PATH: Path` — the shared trend log's default location,
  importable so tests and callers can both point at it without hardcoding the
  string twice.

Root cause note (found while writing this plan, not guessed): the existing
persist block in `run_task.py` already wraps its file write in `try/except
Exception as _me: logger.debug(...)` — that swallows any real failure
(disk full, permission error, a bad path) at a log level nothing in this
project reads. This module fixes that specific pattern, the same class of
bug already found and fixed this session for Driver 2's silent
control-resolution failures (promoted `debug` → `warning` there too).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_shared_run_recorder.py
import json
import logging

import pytest

from shared.run_recorder import DEFAULT_PATH, record_run_result


def test_record_run_result_appends_a_jsonl_line(tmp_path):
    target = tmp_path / "run_metrics.jsonl"
    ok = record_run_result("scope1", {"task_completion_rate": 1.0}, path=target)
    assert ok is True
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["scope"] == "scope1"
    assert row["task_completion_rate"] == 1.0
    assert "timestamp" in row


def test_record_run_result_appends_not_overwrites(tmp_path):
    target = tmp_path / "run_metrics.jsonl"
    record_run_result("scope1", {"n": 1}, path=target)
    record_run_result("scope2", {"n": 2}, path=target)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["scope"] == "scope1"
    assert json.loads(lines[1])["scope"] == "scope2"


def test_record_run_result_creates_missing_parent_dirs(tmp_path):
    target = tmp_path / "nested" / "dir" / "run_metrics.jsonl"
    ok = record_run_result("scope1", {"n": 1}, path=target)
    assert ok is True
    assert target.exists()


def test_record_run_result_never_raises_on_write_failure(tmp_path, monkeypatch):
    target = tmp_path / "run_metrics.jsonl"

    def _boom(*a, **kw):
        raise OSError("disk is on fire")

    monkeypatch.setattr("builtins.open", _boom)
    ok = record_run_result("scope1", {"n": 1}, path=target)
    assert ok is False  # never raises -- caller must be able to tell it failed


def test_record_run_result_logs_at_error_not_debug_on_failure(tmp_path, monkeypatch, caplog):
    target = tmp_path / "run_metrics.jsonl"

    def _boom(*a, **kw):
        raise OSError("disk is on fire")

    monkeypatch.setattr("builtins.open", _boom)
    with caplog.at_level(logging.ERROR, logger="shared.run_recorder"):
        record_run_result("scope1", {"n": 1}, path=target)
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_default_path_is_the_shared_run_metrics_jsonl():
    assert DEFAULT_PATH.name == "run_metrics.jsonl"
    assert DEFAULT_PATH.parent.name == "output"
    assert DEFAULT_PATH.parent.parent.name == "data"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_shared_run_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared'`

- [ ] **Step 3: Write the implementation**

```python
# components/shared/__init__.py
```//empty file

```python
# components/shared/run_recorder.py
"""
Shared, reliability-hardened run-result recorder used by both Scope #1
(components/agent/, via run_task.py) and Scope #2 (components/scope2/,
via automate.py). One appended JSONL row per run, in one shared trend
log, so a fix to "why didn't this run get recorded" benefits both scopes
at once instead of needing to be found and fixed twice.

Each scope keeps its own rich, scope-specific result computation --
Scope #1's eval_metrics.evaluate_run(), Scope #2's per-run JSON log via
automate.py's log.write() -- this module only unifies the reliability of
the *persistence* step, not what a "result" means for each scope. See
docs/superpowers/specs/2026-08-21-scope-unification-design.md, Piece 2.

Confirmed root cause of the pre-unification gap: the old inline persist
block in run_task.py caught its own write failures at logger.debug(),
a level nothing in this project actually reads -- so a real failure
there was indistinguishable from "nothing went wrong." This module fixes
that by logging at ERROR with a full traceback, and by returning a
plain bool so callers/tests can assert on success without needing to
inspect logs at all.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "output" / "run_metrics.jsonl"


def record_run_result(scope: str, row: Mapping[str, Any], path: Optional[Path] = None) -> bool:
    """Append one row to the shared trend log. Never raises -- recording a
    run's result must never fail the run it's recording. Returns True on
    success, False on failure."""
    target = Path(path) if path is not None else DEFAULT_PATH
    full_row = {
        "scope": scope,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **row,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(full_row) + "\n")
        return True
    except Exception:
        logger.error(
            "record_run_result: failed to persist a %r run's result to %s",
            scope, target, exc_info=True,
        )
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_shared_run_recorder.py -v`
Expected: PASS, all 6 tests

- [ ] **Step 5: Commit**

```bash
git add components/shared/__init__.py components/shared/run_recorder.py tests/test_shared_run_recorder.py
git commit -m "Add shared run-result recorder, replacing two independently-unreliable paths"
```

---

### Task 2: Wire Scope #1 (`run_task.py`) to the shared recorder

**Files:**
- Modify: `run_task.py:378-390` (the existing `evaluate_run()` call and inline
  persist block, inside the `finally:` clause)
- Test: `tests/test_run_task_metrics_persistence.py`

**Interfaces:**
- Consumes: `record_run_result(scope, row, path=None) -> bool` from Task 1.
- Consumes: `evaluate_run(...)` from `scripts/eval_metrics.py` (unchanged
  signature).

This task also fixes the second half of the root cause: `evaluate_run(...)`
itself is currently called with no try/except around it at all, directly
inside the `finally:` block — if it raised, nothing after it (including the
persist step) would ever run, and the exception's visibility would depend on
how the parent process happens to be watching stderr. Wrapping it means a
crash inside metric computation degrades to a logged error with safe
zero-metrics, instead of silently skipping persistence entirely.

Since `run_task.py` is a top-level script (no functions to unit-test
directly), this task tests the *logic* by extracting it into one small,
testable function the script calls, matching the project's own existing
pattern of keeping script bodies thin over testable functions.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_task_metrics_persistence.py
"""
Regression test for run_task.py's finally: block persisting metrics through
the shared recorder (Piece 2, docs/superpowers/specs/2026-08-21-scope-
unification-design.md), and for evaluate_run() itself being safe to call --
previously an unguarded call inside finally: that could skip persistence
entirely if it raised.
"""
import logging

import pytest

import run_task


def test_persist_scope1_metrics_calls_shared_recorder(monkeypatch):
    calls = []
    monkeypatch.setattr(
        run_task, "record_run_result",
        lambda scope, row, path=None: calls.append((scope, row)) or True,
    )
    run_task._persist_scope1_metrics(
        metrics={"task_completion_rate": 1.0, "summary": "should be dropped"},
        goal="Fill the form", provider="lmstudio",
    )
    assert len(calls) == 1
    scope, row = calls[0]
    assert scope == "scope1"
    assert row["task_completion_rate"] == 1.0
    assert row["goal"] == "Fill the form"
    assert row["provider"] == "lmstudio"
    assert "summary" not in row  # the long report string doesn't belong in the trend log


def test_safe_evaluate_run_returns_evaluate_runs_result_on_success(monkeypatch):
    monkeypatch.setattr(run_task, "evaluate_run", lambda *a, **kw: {"task_completion_rate": 0.5})
    result = run_task._safe_evaluate_run(results=[{"a": 1}], goal="g")
    assert result == {"task_completion_rate": 0.5}


def test_safe_evaluate_run_falls_back_and_logs_on_crash(monkeypatch, caplog):
    def _boom(*a, **kw):
        raise ValueError("bad step shape")

    monkeypatch.setattr(run_task, "evaluate_run", _boom)
    with caplog.at_level(logging.ERROR, logger="run_task"):
        result = run_task._safe_evaluate_run(results=[{"a": 1}], goal="g")
    assert result["task_completion_rate"] == 0.0
    assert result["action_prediction_accuracy"] == 0.0
    assert result["execution_success_rate"] == 0.0
    assert any(r.levelno == logging.ERROR for r in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_run_task_metrics_persistence.py -v`
Expected: FAIL with `AttributeError: module 'run_task' has no attribute '_persist_scope1_metrics'`

- [ ] **Step 3: Implement — module-level imports, two small functions, wire them into the `finally:` block**

`components` is already on `sys.path` at module level (`run_task.py:32-36`,
`_COMP_DIR`), so `shared.run_recorder` is already importable from the top of
the file. `scripts/` (where `eval_metrics.py` lives) is not — add that one
`sys.path.insert` at module level too, right after the existing loop.

This matters for testability, not just tidiness: the original code imported
`eval_metrics` *inside* the `finally:` block (deferred, only reachable when
run as `__main__`), which means `run_task.evaluate_run` would not exist as a
module attribute for a test to `monkeypatch.setattr` against just from
`import run_task` — moving both imports to module level is what makes Step 1's
tests able to patch `run_task.evaluate_run` / `run_task.record_run_result`
directly. Add right after the existing `sys.path` loop (`run_task.py:32-36`):

```python
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
from eval_metrics import evaluate_run  # noqa: E402
from shared.run_recorder import record_run_result  # noqa: E402
```

Then add these two functions alongside the existing helpers (after the
`import signal as _signal` block, before `def print_countdown`):

```python
def _safe_evaluate_run(**kwargs):
    """Wraps eval_metrics.evaluate_run() so a crash inside metric
    computation can't silently skip the persistence step after it --
    the previous version called evaluate_run() unguarded directly inside
    run_task.py's finally: block."""
    try:
        return evaluate_run(**kwargs)
    except Exception:
        logger.error("evaluate_run() crashed -- this run's metrics are lost:", exc_info=True)
        return {
            "task_completion_rate": 0.0,
            "action_prediction_accuracy": 0.0,
            "execution_success_rate": 0.0,
            "summary": "evaluate_run() crashed -- see log for traceback",
        }


def _persist_scope1_metrics(metrics: dict, goal: str, provider: str) -> None:
    """Persists one Scope #1 run's metrics through the shared recorder
    (components/shared/run_recorder.py), replacing the old inline
    open()/write() block that silently swallowed failures at
    logger.debug()."""
    record_run_result(
        scope="scope1",
        row={
            "goal": goal,
            "provider": provider,
            **{k: v for k, v in metrics.items() if k != "summary"},
        },
    )
```

Replace the existing block at `run_task.py:378-390` (the `# ── Evaluation
metrics ──` comment through the end of the old persist `try/except`):

```python
        # ── Evaluation metrics (always runs, even on early stop or crash) ──────
        _metrics = _safe_evaluate_run(
            results=results, goal=GOAL, heuristic_steps=getattr(agent, "_heuristic_steps", []),
            run_duration_sec=getattr(agent, "_run_duration_sec", None),
            time_to_first_action_sec=getattr(agent, "_time_to_first_action_sec", None),
            manual_interventions=getattr(agent, "_manual_interventions", 0),
        )

        # ── Persist metrics to the shared trend log (both scopes' runs) ───────
        _persist_scope1_metrics(_metrics, goal=GOAL, provider=PROVIDER)
```

`evaluate_run` and `record_run_result` are now module-level imports (Step 3's
first change above), so both are already available by the time the `finally:`
block runs — no per-call import needed here anymore, and both names are
directly monkeypatchable on the `run_task` module from a test.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_run_task_metrics_persistence.py -v`
Expected: PASS, all 3 tests

- [ ] **Step 5: Run the full existing suite to confirm no regression**

Run: `pytest -q`
Expected: same pass count as before this task (1293 passed, 9 skipped) plus
the 3 new tests — no existing test's outcome changes.

- [ ] **Step 6: Commit**

```bash
git add run_task.py tests/test_run_task_metrics_persistence.py
git commit -m "Route Scope #1's metrics through the shared recorder; make evaluate_run() crash-safe"
```

---

### Task 3: Wire Scope #2 (`automate.py`) to the shared recorder

**Files:**
- Modify: `components/scope2/automate.py` (around the existing `log.write(log_path)`
  call — see file read during design, line ~230)
- Test: `tests/scope2/test_automate_metrics_persistence.py`

**Interfaces:**
- Consumes: `record_run_result(scope, row, path=None) -> bool` from Task 1.

Scope #2 currently has no trend log at all — only a per-run JSON file
(`data/runs/automate_<variant>_<timestamp>.json`, written by `log.write()`).
This task adds a call to the shared recorder alongside that existing file
write (not replacing it — the per-run file remains the detailed record; the
shared trend log is new, giving Scope #2 a "did my runs improve over time"
view for the first time).

- [ ] **Step 1: Write the failing test**

```python
# tests/scope2/test_automate_metrics_persistence.py
"""
Scope #2's automate.py previously had no trend-log recording at all --
only a per-run JSON file. This test confirms it now also calls the
shared recorder (components/shared/run_recorder.py), per Piece 2 of
docs/superpowers/specs/2026-08-21-scope-unification-design.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "components" / "scope2"))

import automate


def test_persist_scope2_metrics_calls_shared_recorder(monkeypatch):
    calls = []
    monkeypatch.setattr(
        automate, "record_run_result",
        lambda scope, row, path=None: calls.append((scope, row)) or True,
    )

    class _FakeRow:
        def __init__(self, status):
            self.status = status

    class _FakeLog:
        rows = [_FakeRow("filled"), _FakeRow("filled"), _FakeRow("failed")]
        commit_status = "committed"

    class _FakeAssignment:
        pass

    class _FakeMapping:
        auto = [_FakeAssignment(), _FakeAssignment()]
        abstained = [_FakeAssignment()]

    log = _FakeLog()
    automate._persist_scope2_metrics(
        filled=[r for r in log.rows if r.status == "filled"],
        failed=[r for r in log.rows if r.status != "filled"],
        commit_status=log.commit_status,
        mapping=_FakeMapping(), rules=[object()], variant="v0_base",
    )

    assert len(calls) == 1
    scope, row = calls[0]
    assert scope == "scope2"
    assert row["variant"] == "v0_base"
    assert row["rows_filled"] == 2
    assert row["rows_failed"] == 1
    assert row["commit_status"] == "committed"
    assert row["columns_mapped"] == 2
    assert row["columns_abstained"] == 1
    assert row["fields_filled_by_rule"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/scope2/test_automate_metrics_persistence.py -v`
Expected: FAIL with `AttributeError: module 'automate' has no attribute '_persist_scope2_metrics'`

- [ ] **Step 3: Implement**

Add to `components/scope2/automate.py`, after the existing `sys.path.insert(0,
str(REPO))` line near the top:

```python
sys.path.insert(0, str(REPO.parent))
from shared.run_recorder import record_run_result  # noqa: E402
```

Add this function near the other helper functions in the file (adjacent to
where `run_executor`/similar helpers are used):

```python
def _persist_scope2_metrics(filled, failed, commit_status, mapping, rules, variant: str) -> None:
    """Persists one Scope #2 run's summary through the shared recorder
    (components/shared/run_recorder.py) -- Scope #2 previously had no
    trend log at all, only the per-run JSON file written below. Takes
    filled/failed pre-computed by the caller rather than recomputing them,
    since main() already needs the same two lists for its own print()."""
    record_run_result(
        scope="scope2",
        row={
            "variant": variant,
            "rows_filled": len(filled),
            "rows_failed": len(failed),
            "commit_status": commit_status,
            "columns_mapped": len(mapping.auto),
            "columns_abstained": len(mapping.abstained),
            "fields_filled_by_rule": len(rules),
        },
    )
```

Replace the existing block right after `log.write(log_path)` (originally
`filled = [...]` / `failed = [...]` / `print(...)`) with:

```python
    log.write(log_path)

    filled = [r for r in log.rows if r.status == "filled"]
    failed = [r for r in log.rows if r.status != "filled"]
    _persist_scope2_metrics(filled, failed, log.commit_status, mapping, rules, variant=args.variant)

    print(f"  {len(filled)} rows filled and verified, {len(failed)} failed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/scope2/test_automate_metrics_persistence.py -v`
Expected: PASS

- [ ] **Step 5: Run the full existing suite to confirm no regression**

Run: `pytest -q`
Expected: same pass count as after Task 2, plus this task's new test — no
existing test's outcome changes.

- [ ] **Step 6: Commit**

```bash
git add components/scope2/automate.py tests/scope2/test_automate_metrics_persistence.py
git commit -m "Give Scope #2 a trend log for the first time, via the shared recorder"
```

---

### Task 4: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the complete test suite one more time**

Run: `pytest -q`
Expected: all tests pass (previous 1293 + 9 new from this plan, 9 skipped
unchanged), 0 failures.

- [ ] **Step 2: `git status` check**

Run: `git status`
Expected: clean working tree, all changes committed across Tasks 1–3.

- [ ] **Step 3: Push the branch**

```bash
git push origin experiment/scope-unification
```

- [ ] **Step 4: Flag the live-verification gap honestly**

This plan closes the *reliability* half of Piece 2 (both scopes now go
through one hardened, tested, visibly-logging recorder). It does **not**
itself prove that a real live Scope #1 run now produces a row in
`data/output/run_metrics.jsonl` — that requires an actual live GUI-automation
run, which per this project's standing rule is the user's call to execute,
not something to run automatically here. Note this plan as code-complete and
ask the user to run one live record through `run_task.py` and confirm a new
row appears with `"scope": "scope1"`.
