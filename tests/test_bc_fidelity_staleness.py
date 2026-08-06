"""
Regression test for scripts/bc_fidelity.py's score_run() staleness guard.

Bug this locks down: data/output/submissions/ receives auto-saves from the
standalone car_insurance_form_wx.py app itself, not just from agent runs. A
run that crashes before ever submitting (e.g. Step 1 failure) previously
still got scored — score_run() picked "newest file by mtime" and silently
scored a leftover blank submission from an unrelated app launch, producing a
plausible-looking BC score for a run that did nothing. Found 2026-08-06 when
a crashed 0-step run still reported "BC SCORE: 27.4%".

Fix: score_run(run_start_ts=...) refuses to score a submission older than
the run being scored.
"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import bc_fidelity


@pytest.fixture
def fake_output_dirs(tmp_path, monkeypatch):
    ref_path = tmp_path / "reference" / "gold_standard.json"
    subs_dir = tmp_path / "submissions"
    progress_log = tmp_path / "bc_progress.jsonl"
    subs_dir.mkdir(parents=True)
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(json.dumps({
        "fields": {"policy_number": "PAI-2026-00441", "ph_first": "James"},
        "tab_order": ["Policy", "Policyholder"],
    }))
    monkeypatch.setattr(bc_fidelity, "REFERENCE_PATH", ref_path)
    monkeypatch.setattr(bc_fidelity, "SUBMISSIONS_DIR", subs_dir)
    monkeypatch.setattr(bc_fidelity, "PROGRESS_LOG", progress_log)
    return subs_dir


def _write_submission(subs_dir: Path, name: str, data: dict) -> Path:
    p = subs_dir / name
    p.write_text(json.dumps(data))
    return p


def test_refuses_to_score_submission_older_than_run_start(fake_output_dirs):
    subs_dir = fake_output_dirs
    _write_submission(subs_dir, "_stale_blank.json", {"policy_number": ""})

    # The run "starts" after the stale submission already existed on disk.
    run_start_ts = time.time() + 1.0

    result = bc_fidelity.score_run([], goal="test", run_start_ts=run_start_ts)

    assert result is None, "must not score a submission that predates this run's start"


def test_scores_submission_written_after_run_start(fake_output_dirs):
    subs_dir = fake_output_dirs
    run_start_ts = time.time()
    time.sleep(0.05)
    _write_submission(subs_dir, "PAI-2026-00441_fresh.json",
                       {"policy_number": "PAI-2026-00441", "ph_first": "James"})

    result = bc_fidelity.score_run([], goal="test", run_start_ts=run_start_ts)

    assert result is not None, "must score a submission written after this run started"
    assert result["field_match_rate"] == 1.0


def test_run_start_ts_none_preserves_old_behavior(fake_output_dirs):
    """When the caller doesn't pass run_start_ts (e.g. older call sites), the
    guard is skipped entirely — no false negatives from a missing timestamp."""
    subs_dir = fake_output_dirs
    _write_submission(subs_dir, "PAI-2026-00441_only.json",
                       {"policy_number": "PAI-2026-00441", "ph_first": "James"})

    result = bc_fidelity.score_run([], goal="test", run_start_ts=None)

    assert result is not None
