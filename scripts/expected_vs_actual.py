"""
scripts/expected_vs_actual.py
==============================
Per-record expected-vs-actual correctness report -- `scope1_e2e_metric`'s
sibling checklist item `scope1_expected_vs_actual` (Task Tree / DEVELOPERS.md
Scope #1 section): "Expected-vs-actual diff at submit — Per-record
correctness report."

`scripts/bc_fidelity.py` already computes field-level matching (correct
values vs the intake source) as part of its overall fidelity score, but its
`mismatches` list is capped at 10 entries and only covers fields the agent
actually filled — it was built to produce one aggregate score, not a
complete audit trail. This reuses bc_fidelity's own parsing/matching/tab
logic (no duplicated comparison rules) to build the full picture instead:
every expected field, organized by tab, with its status --

    match     — agent's value matches the intake source
    mismatch  — agent filled it, but with the wrong value
    missing   — agent never filled it at all

Usage
-----
  python scripts/expected_vs_actual.py --submission data/output/submissions/<run>.json --record 1
  python scripts/expected_vs_actual.py --record 1   # scores the latest submission
  python scripts/expected_vs_actual.py --record 1 --json   # machine-readable output
"""
from __future__ import annotations

import json
import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from bc_fidelity import (  # noqa: E402 -- path set above
    _parse_intake_record, _tab_of, _normalize, _SKIP_FIELDS, SUBMISSIONS_DIR,
)

_INTAKE_PATH = ROOT / "data_entry_tasks" / "data_entry_intake.txt"


def _latest_submission() -> Path | None:
    if not SUBMISSIONS_DIR.exists():
        return None
    files = sorted(SUBMISSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _load_json(path: Path) -> Any:
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def build_report(submission: dict, record_num: int, intake_path: Path = _INTAKE_PATH) -> dict:
    """Build the full per-field expected-vs-actual report for one record.

    Returns a dict with a per-tab breakdown (each field's expected/actual/
    status) plus per-tab and overall summary counts. Pure function of its
    inputs -- no file I/O beyond reading intake_path, so this is directly
    unit-testable without a real submission file on disk.
    """
    text = intake_path.read_text(encoding="utf-8")
    gold_fields = _parse_intake_record(text, record_num)

    by_tab: dict[str, list[dict]] = {}
    for key, expected in gold_fields.items():
        tab = _tab_of(key)
        actual = submission.get(key)
        is_metadata = key in _SKIP_FIELDS

        if actual in ("", None, False):
            status = "missing"
        elif _normalize(actual) == _normalize(expected):
            status = "match"
        else:
            status = "mismatch"

        by_tab.setdefault(tab, []).append({
            "field":    key,
            "expected": expected,
            "actual":   actual,
            "status":   status,
            "scored":   not is_metadata,
        })

    tab_summary = {}
    total_match = total_mismatch = total_missing = total_scored = 0
    for tab, fields in by_tab.items():
        scored_fields = [f for f in fields if f["scored"]]
        m  = sum(1 for f in scored_fields if f["status"] == "match")
        mm = sum(1 for f in scored_fields if f["status"] == "mismatch")
        ms = sum(1 for f in scored_fields if f["status"] == "missing")
        tab_summary[tab] = {
            "total": len(scored_fields), "match": m, "mismatch": mm, "missing": ms,
            "correctness": round(m / len(scored_fields), 4) if scored_fields else 1.0,
        }
        total_match += m
        total_mismatch += mm
        total_missing += ms
        total_scored += len(scored_fields)

    return {
        "record_num":   record_num,
        "by_tab":       by_tab,
        "tab_summary":  tab_summary,
        "overall": {
            "total_fields": total_scored,
            "match":        total_match,
            "mismatch":     total_mismatch,
            "missing":      total_missing,
            "correctness":  round(total_match / total_scored, 4) if total_scored else 1.0,
        },
    }


def _print_report(report: dict, submission_name: str) -> None:
    print(f"\n{'='*90}")
    print(f"  EXPECTED vs ACTUAL — Record {report['record_num']}")
    print(f"  Submission: {submission_name}")
    print(f"{'='*90}")

    _STATUS_MARK = {"match": "OK", "mismatch": "XX", "missing": ".."}
    for tab, fields in report["by_tab"].items():
        s = report["tab_summary"][tab]
        print(f"\n  [{tab}]  {s['match']}/{s['total']} correct ({s['correctness']*100:.0f}%)")
        for f in fields:
            if not f["scored"]:
                continue
            if f["status"] == "match":
                continue  # only show fields worth looking at
            mark = _STATUS_MARK[f["status"]]
            exp = str(f["expected"])[:40]
            act = str(f["actual"])[:40] if f["actual"] not in ("", None, False) else "(not filled)"
            print(f"    [{mark}] {f['field']:<28} expected={exp!r:<42} got={act!r}")

    o = report["overall"]
    print(f"\n  {'-'*86}")
    print(f"  OVERALL   {o['match']}/{o['total_fields']} correct  "
          f"({o['correctness']*100:.1f}%)   "
          f"mismatch={o['mismatch']}  missing={o['missing']}")
    print(f"{'='*90}\n")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Per-record expected-vs-actual correctness report.")
    ap.add_argument("--submission", help="Path to a submission JSON. Defaults to the latest one.")
    ap.add_argument("--record", type=int, default=1, help="Record number in the intake file (1-based).")
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of a table.")
    args = ap.parse_args()

    sub_path = Path(args.submission) if args.submission else _latest_submission()
    if not sub_path or not sub_path.exists():
        print("ERROR: no submission found. Pass --submission or run a live task first.")
        sys.exit(1)

    submission = _load_json(sub_path)
    if submission is None:
        print(f"ERROR: could not read {sub_path}")
        sys.exit(1)

    report = build_report(submission, args.record)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_report(report, sub_path.name)


if __name__ == "__main__":
    main()
