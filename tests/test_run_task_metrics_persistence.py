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
