"""
Scope #2's automate.py previously had no trend-log recording at all --
only a per-run JSON file. This test confirms it now also calls the
shared recorder (components/shared/run_recorder.py), per Piece 2 of
docs/superpowers/specs/2026-08-21-scope-unification-design.md.
"""
import sys
from pathlib import Path

import pytest

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


def test_main_persists_metrics_even_when_it_crashes_partway_through(monkeypatch):
    # main() previously had no try/finally at all -- if any step (sheet
    # read, portal scan, matching, rule induction, executor fill/verify)
    # raised, Scope #2 recorded nothing whatsoever in the shared trend
    # log, defeating the whole point of this branch (the same reliability
    # guarantee Scope #1 already has via run_task.py's finally: block).
    # This simulates a crash right after the sheet is read (stage 2, the
    # portal scan) and confirms record_run_result still gets called with
    # a safe fallback row, using the same monkeypatched-recorder pattern
    # as test_persist_scope2_metrics_calls_shared_recorder above.
    calls = []
    monkeypatch.setattr(
        automate, "record_run_result",
        lambda scope, row, path=None: calls.append((scope, row)) or True,
    )
    monkeypatch.setattr(automate, "print_countdown", lambda *a, **kw: None)

    def _boom(*a, **kw):
        raise RuntimeError("portal scan boom")

    monkeypatch.setattr(automate, "scan_variants", _boom)
    monkeypatch.setattr(sys, "argv", ["automate.py"])

    with pytest.raises(RuntimeError):
        automate.main()

    assert len(calls) == 1
    scope, row = calls[0]
    assert scope == "scope2"
    assert row["rows_filled"] == 0
    assert row["rows_failed"] == 0
    assert row.get("metrics_ok") is False
