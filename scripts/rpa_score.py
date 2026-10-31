"""
scripts/rpa_score.py
====================
Objective 7 (panel minimum): score the Intern vs UI.Vision runs on the Scope 1
form and print the comparison table.

Both systems submit through the same form app, which auto-saves every Submit
to data/output/submissions/*.json. So the two systems are scored the same way,
from the same kind of file, against the same intake record.

Usage
-----
  python scripts/rpa_score.py                                   # default sheet
  python scripts/rpa_score.py --sheet docs/rpa_comparison/run_sheet.csv
  python scripts/rpa_score.py --out docs/rpa_comparison/run_sheet_scored.csv

The run sheet has one row per run. Fill in, per row:
  submission             file name in data/output/submissions/ (blank if the
                         run never reached Submit)
  finished_without_help  yes / no
  minutes                wall-clock minutes for that run (or setup, on setup rows)
Rows whose `run` is "setup" are not scored; their minutes are the setup time.

Scoring rule
------------
A field is correct when the submitted value matches the intake record.
One deliberate difference from expected_vs_actual.py: a field whose expected
value is empty / unchecked (False) is CORRECT when the submission is also empty
/ unchecked. expected_vs_actual.py counts those as "missing", so a perfect fill
of record 1 scores only 146/171 (85.4%) there and no run could ever count as
fully correct.

Second difference, found by Gate 3 (2026-11-01): the intake packet writes an
instruction instead of a value for fields that must stay empty -- "(leave
blank)", "(none -- owned outright)", "(N/A -- no collision coverage)". The
right fill is an EMPTY box, so those expectations are treated as empty. Without
this, a perfect fill of record 1 that leaves those boxes empty is scored wrong.
Phone numbers such as "(909) 334-7820" also start with "(" and are NOT affected.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from math import comb
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from bc_fidelity import (  # noqa: E402
    _parse_intake_record, _tab_of, _normalize, _SKIP_FIELDS, SUBMISSIONS_DIR,
)

DEFAULT_SHEET = ROOT / "docs" / "rpa_comparison" / "run_sheet.csv"
DEFAULT_INTAKE = ROOT / "data_entry_tasks" / "data_entry_intake.txt"
EMPTY = ("", None, False)
_BLANK_INSTRUCTION = re.compile(r"^\(\s*(leave blank|none|n/a)(?![a-z])", re.IGNORECASE)


def is_blank_instruction(v) -> bool:
    """True for intake values that tell the operator to leave the field empty."""
    return isinstance(v, str) and bool(_BLANK_INSTRUCTION.match(v.strip()))


def _load_json(path: Path):
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def score_submission(submission: dict, record_num: int, intake_text: str) -> dict:
    """Per-field correctness for one submission. Pure function."""
    gold = _parse_intake_record(intake_text, record_num)
    total = correct = 0
    by_tab: dict[str, list[int]] = {}
    wrong_fields: list[str] = []
    for key, expected in gold.items():
        if key in _SKIP_FIELDS:
            continue
        actual = submission.get(key)
        if expected in EMPTY or is_blank_instruction(expected):
            ok = actual in EMPTY
        else:
            ok = actual not in EMPTY and _normalize(actual) == _normalize(expected)
        tab = _tab_of(key)
        t = by_tab.setdefault(tab, [0, 0])
        t[0] += ok
        t[1] += 1
        total += 1
        correct += ok
        if not ok:
            wrong_fields.append(key)
    return {
        "fields_total": total,
        "fields_wrong": total - correct,
        "field_correctness": correct / total if total else 1.0,
        "policy_correct": by_tab.get("Policy", [0, 0])[0],
        "policy_total": by_tab.get("Policy", [0, 0])[1],
        "fully_correct": total > 0 and correct == total,
        "wrong_fields": wrong_fields,
    }


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Fisher's exact test, two-sided, for the 2x2 table [[a, b], [c, d]]."""
    r1, c1, n = a + b, a + c, a + b + c + d

    def p(x: int) -> float:
        return comb(r1, x) * comb(n - r1, c1 - x) / comb(n, c1)

    observed = p(a)
    lo, hi = max(0, c1 - (n - r1)), min(r1, c1)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= observed * (1 + 1e-9)))


def _yes(v: str) -> bool:
    return str(v).strip().lower() in ("yes", "y", "true", "1")


def main() -> None:
    ap = argparse.ArgumentParser(description="Score the Objective 7 RPA comparison runs.")
    ap.add_argument("--sheet", default=str(DEFAULT_SHEET))
    ap.add_argument("--out", default=None, help="Write the scored sheet here (default: <sheet>_scored.csv)")
    ap.add_argument("--intake", default=str(DEFAULT_INTAKE))
    ap.add_argument("--submissions", default=str(SUBMISSIONS_DIR))
    args = ap.parse_args()

    sheet = Path(args.sheet)
    intake_text = Path(args.intake).read_text(encoding="utf-8")
    sub_dir = Path(args.submissions)
    rows = list(csv.DictReader(sheet.open(encoding="utf-8")))

    scored = []
    for r in rows:
        r = dict(r)
        if r.get("run", "").strip().lower() == "setup":
            scored.append(r)
            continue
        sub_name = (r.get("submission") or "").strip()
        record = int(r.get("record") or 1)
        if not sub_name:
            r.update(fields_total="", fields_wrong="", field_correctness="",
                     policy_correct="", fully_correct="no",
                     outcome="did not reach Submit")
            scored.append(r)
            continue
        sub = _load_json(sub_dir / sub_name)
        if sub is None:
            print(f"ERROR: cannot read {sub_dir / sub_name}")
            sys.exit(1)
        s = score_submission(sub, record, intake_text)
        finished = _yes(r.get("finished_without_help", ""))
        if s["fully_correct"] and finished:
            outcome = "correct"
        elif finished:
            outcome = "finished, wrong fields"
        else:
            outcome = "needed help"
        r.update(
            fields_total=s["fields_total"],
            fields_wrong=s["fields_wrong"],
            field_correctness=f"{s['field_correctness']:.4f}",
            policy_correct=f"{s['policy_correct']}/{s['policy_total']}",
            fully_correct="yes" if s["fully_correct"] else "no",
            outcome=outcome,
        )
        scored.append(r)

    out = Path(args.out) if args.out else sheet.with_name(sheet.stem + "_scored.csv")
    fields = list(dict.fromkeys(k for r in scored for k in r.keys()))
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(scored)

    # ── Summary ───────────────────────────────────────────────────────────
    systems = list(dict.fromkeys(r["system"] for r in scored))
    versions = list(dict.fromkeys(r["form_version"] for r in scored))
    print(f"\nScored sheet written to {out}\n")
    print(f"{'System':<10}{'Form':<10}{'Runs':>5}{'Finished':>10}{'Fully correct':>15}"
          f"{'Mean field corr.':>18}{'Policy tab':>12}{'Mean min':>10}")
    cells = {}
    for sysname in systems:
        setup = [r for r in scored if r["system"] == sysname and r["run"].strip().lower() == "setup"]
        for ver in versions:
            runs = [r for r in scored if r["system"] == sysname and r["form_version"] == ver
                    and r["run"].strip().lower() != "setup"]
            if not runs:
                continue
            n = len(runs)
            fin = sum(_yes(r.get("finished_without_help", "")) for r in runs)
            ok = sum(r.get("fully_correct") == "yes" for r in runs)
            corr = [float(r["field_correctness"]) for r in runs if r.get("field_correctness")]
            pol = [r["policy_correct"] for r in runs if r.get("policy_correct")]
            mins = [float(r["minutes"]) for r in runs if (r.get("minutes") or "").strip()]
            cells[(sysname, ver)] = (ok, n)
            print(f"{sysname:<10}{ver:<10}{n:>5}{fin:>7}/{n:<2}{ok:>12}/{n:<2}"
                  f"{(sum(corr) / len(corr) * 100 if corr else float('nan')):>17.1f}%"
                  f"{(pol[0] if len(set(pol)) == 1 else 'varies') if pol else '-':>12}"
                  f"{(sum(mins) / len(mins) if mins else float('nan')):>10.1f}")
        for s in setup:
            print(f"{sysname:<10}{'setup':<10}{'':>5}  setup minutes = {s.get('minutes') or 'not recorded'}")

    if len(systems) == 2:
        a_sys, b_sys = systems
        print("\nFully-correct runs, Fisher's exact test (two-sided):")
        for ver in versions:
            if (a_sys, ver) in cells and (b_sys, ver) in cells:
                a_ok, a_n = cells[(a_sys, ver)]
                b_ok, b_n = cells[(b_sys, ver)]
                p = fisher_two_sided(a_ok, a_n - a_ok, b_ok, b_n - b_ok)
                print(f"  {ver:<10} {a_sys} {a_ok}/{a_n} vs {b_sys} {b_ok}/{b_n}   p = {p:.4f}"
                      f"{'   (significant at .05)' if p < 0.05 else ''}")
        print("  Setup time is one measurement per system, so it is reported descriptively, not tested.")
    print()


if __name__ == "__main__":
    main()
