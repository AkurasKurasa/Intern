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


def test_record_run_result_never_raises_on_invalid_row(tmp_path):
    target = tmp_path / "run_metrics.jsonl"
    ok = record_run_result("scope1", row=None, path=target)
    assert ok is False  # invalid row (not a mapping) must not raise, must return False


def test_default_path_is_the_shared_run_metrics_jsonl():
    assert DEFAULT_PATH.name == "run_metrics.jsonl"
    assert DEFAULT_PATH.parent.name == "output"
    assert DEFAULT_PATH.parent.parent.name == "data"
