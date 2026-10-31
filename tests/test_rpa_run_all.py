"""Tests for scripts/rpa_run_all.py's run-sheet handling (the live GUI parts
are exercised by running it; these pin the bookkeeping around them)."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import rpa_run_all  # noqa: E402


def _sheet(tmp_path, monkeypatch):
    p = tmp_path / "run_sheet.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(rpa_run_all.SHEET_FIELDS)
        w.writerow(["UI.Vision", "normal", "setup", "", "", "", "", ""])
        w.writerow(["UI.Vision", "normal", "1", "1", "", "", "", ""])
        w.writerow(["Intern", "modified", "1", "1", "", "", "", ""])
    monkeypatch.setattr(rpa_run_all, "SHEET", p)
    return p


def test_baseline_rows_are_renamed_to_the_tool_actually_used(tmp_path, monkeypatch):
    _sheet(tmp_path, monkeypatch)
    rows = rpa_run_all._load_sheet()
    assert [r["system"] for r in rows] == ["PyAutoGUI", "PyAutoGUI", "Intern"]


def test_put_fills_exactly_one_row_and_saves(tmp_path, monkeypatch):
    p = _sheet(tmp_path, monkeypatch)
    rows = rpa_run_all._load_sheet()
    rpa_run_all._put(rows, "Intern", "modified", 1,
                     {"submission": "x.json", "finished_without_help": "yes", "minutes": "1.02",
                      "notes": "reached Submit"})
    saved = list(csv.DictReader(p.open(encoding="utf-8")))
    assert saved[2]["submission"] == "x.json" and saved[2]["minutes"] == "1.02"
    assert saved[1]["submission"] == ""


def test_wait_new_submission_returns_only_new_files(tmp_path, monkeypatch):
    monkeypatch.setattr(rpa_run_all, "FORM_SUBMISSIONS", tmp_path)
    (tmp_path / "old.json").write_text("{}")
    before = rpa_run_all._submissions()
    assert rpa_run_all._wait_new_submission(before, timeout=0.6) == ""
    (tmp_path / "new.json").write_text("{}")
    assert rpa_run_all._wait_new_submission(before, timeout=2) == "new.json"
