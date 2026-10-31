"""Tests for scripts/rpa_score.py (Objective 7 RPA comparison scorer)."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from bc_fidelity import _parse_intake_record  # noqa: E402
from rpa_score import fisher_two_sided, is_blank_instruction, score_submission  # noqa: E402

INTAKE = (ROOT / "data_entry_tasks" / "data_entry_intake.txt").read_text(encoding="utf-8")
POLICY_KEYS = ["policy_type", "policy_term", "effective_date", "expiration_date"]


def _gold(record: int = 1) -> dict:
    """Record `record` as a perfect fill would save it: fields whose intake
    value is a leave-blank instruction ("(leave blank)", "(none -- ...)") are
    left EMPTY, exactly as the form saves an untouched box."""
    return {k: ("" if is_blank_instruction(v) else v)
            for k, v in _parse_intake_record(INTAKE, record).items()}


def test_perfect_fill_is_fully_correct():
    """A fill identical to the intake record must score 100%, including the
    unchecked checkboxes that expected_vs_actual.py counts as 'missing'."""
    s = score_submission(_gold(), 1, INTAKE)
    assert s["fields_wrong"] == 0
    assert s["fully_correct"] is True
    assert s["field_correctness"] == 1.0


def test_unchecked_box_left_unchecked_is_correct():
    gold = _gold()
    unchecked = [k for k, v in gold.items() if v is False]
    assert unchecked, "record 1 should contain unchecked boxes"
    sub = dict(gold)
    for k in unchecked:
        sub.pop(k)  # absent from the submission == left unchecked
    assert score_submission(sub, 1, INTAKE)["fully_correct"] is True


def test_wrongly_checked_box_is_wrong():
    gold = _gold()
    k = next(k for k, v in gold.items() if v is False)
    sub = dict(gold, **{k: True})
    s = score_submission(sub, 1, INTAKE)
    assert k in s["wrong_fields"]
    assert s["fully_correct"] is False


def test_values_in_wrong_fields_are_caught():
    """The failure the comparison is built to detect: every value present,
    but shifted into the neighbouring field after the layout change."""
    gold = _gold()
    sub = dict(gold)
    vals = [gold[k] for k in POLICY_KEYS]
    for k, v in zip(POLICY_KEYS, vals[1:] + vals[:1]):
        sub[k] = v
    s = score_submission(sub, 1, INTAKE)
    assert set(POLICY_KEYS) <= set(s["wrong_fields"])
    assert s["policy_correct"] == s["policy_total"] - len(POLICY_KEYS)


def test_leave_blank_instruction_left_empty_is_correct():
    """Gate 3 finding (2026-11-01): the intake writes "(leave blank)" etc.
    where the box must stay empty. Leaving it empty is correct; typing the
    instruction into the box is wrong."""
    raw = dict(_parse_intake_record(INTAKE, 1))
    k = next(k for k, v in raw.items() if is_blank_instruction(v))
    assert score_submission(_gold(), 1, INTAKE)["fully_correct"] is True
    typed = dict(_gold(), **{k: raw[k]})
    assert k in score_submission(typed, 1, INTAKE)["wrong_fields"]


def test_phone_numbers_in_brackets_are_real_values():
    assert not is_blank_instruction("(909) 334-7820")
    assert is_blank_instruction("(N/A — no collision coverage)")
    assert is_blank_instruction("(none — owned outright)")
    assert not is_blank_instruction("(nonexistent)")


def test_fisher_matches_known_values():
    assert abs(fisher_two_sided(0, 5, 5, 0) - 0.007937) < 1e-5
    assert abs(fisher_two_sided(0, 5, 4, 1) - 0.047619) < 1e-5
    assert fisher_two_sided(5, 0, 5, 0) == 1.0


def test_cli_end_to_end(tmp_path):
    subs = tmp_path / "subs"
    subs.mkdir()
    json.dump(_gold(), (subs / "ok.json").open("w"))
    sheet = tmp_path / "sheet.csv"
    with sheet.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["system", "form_version", "run", "record", "submission",
                    "finished_without_help", "minutes", "notes"])
        w.writerow(["UI.Vision", "normal", "setup", "", "", "", "30", ""])
        w.writerow(["UI.Vision", "normal", "1", "1", "ok.json", "yes", "3", ""])
        w.writerow(["Intern", "normal", "1", "1", "", "no", "9", ""])
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "rpa_score.py"),
         "--sheet", str(sheet), "--submissions", str(subs)],
        capture_output=True, text=True, check=True,
    )
    scored = list(csv.DictReader((tmp_path / "sheet_scored.csv").open()))
    assert scored[1]["outcome"] == "correct"
    assert scored[2]["outcome"] == "did not reach Submit"
    assert "Fisher" in out.stdout
