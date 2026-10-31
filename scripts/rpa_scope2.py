"""
scripts/rpa_scope2.py
=====================
Objective 7, Scope 2 (grade sheet -> web portal): Intern vs a position-based
record-and-replay baseline, on every portal variant.

  Intern     components/scope2/automate.py --no_llm (dry run, all 50 rows) on
             each variant; its printed "column -> field" decisions and its
             rule-filled field are scored against eval/ground_truth.py.
  Baseline   recorded once on v0_base as "column X goes into the field at
             position N" (the 'click the 4th input box' selector described in
             components/scope2/eval/rpa_protocol.md), replayed unedited on each
             variant: column X lands in whatever field sits at position N there.

Scored per target (course, year, grade, remarks): correct when the value lands
in the field whose data-key is the right one. A row is fully correct when all
four land correctly; the decision is the same for every row of a run.

Usage:  python scripts/rpa_scope2.py [--out docs/rpa_comparison/scope2_results.json]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S2 = ROOT / "components" / "scope2"
sys.path.insert(0, str(S2))

TARGETS = {"PROGRAM": "course", "YEAR LEVEL": "year", "FINAL GRADE": "grade", "(rule)": "remarks"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def scan(variants):
    from executor.scanner import scan_variants
    out = {}
    for name, ds in scan_variants(variants).items():
        out[name] = [{"label": d.label, "key": d.truth_key, "pos": d.column_index}
                     for d in ds if d.kind == "input"]
    return out


def baseline(fields: dict) -> dict:
    """Position replay: recorded on v0_base, applied unedited to each variant."""
    rec = {key: next(f["pos"] for f in fields["v0_base"] if f["key"] == key) for key in TARGETS.values()}
    res = {}
    for v, fs in fields.items():
        by_pos = {f["pos"]: f for f in fs}
        landed = {key: (by_pos.get(pos) or {}).get("key") for key, pos in rec.items()}
        res[v] = {"landed": landed, "correct": sum(landed[k] == k for k in landed), "of": len(landed)}
    return res


def intern(variant: str, fields: list) -> dict:
    p = subprocess.run([sys.executable, "automate.py", "--variant", variant, "--no_llm"], cwd=str(S2),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    text = ANSI.sub("", p.stdout)
    by_label = {f["label"]: f["key"] for f in fields}
    landed = {}
    for line in text.splitlines():
        m = re.match(r"\s{2}(PROGRAM|YEAR LEVEL|FINAL GRADE)\s+->\s+(.+?)\s{2,}", line + "  ")
        if m:
            landed[TARGETS[m.group(1)]] = by_label.get(m.group(2).strip())
        m = re.match(r"\s*filled by rule:\s+(.+?)\s{2,}|\s*filled by rule:\s+(.+)$", line)
        if m:
            lab = (m.group(1) or m.group(2)).strip()
            landed["remarks"] = next((k for l, k in by_label.items() if lab.startswith(l)), None)
    rows = re.search(r"rows verified\s+(\d+)/(\d+)", text)
    for k in TARGETS.values():
        landed.setdefault(k, None)
    return {"landed": landed, "correct": sum(landed[k] == k for k in landed), "of": len(landed),
            "rows_verified": f"{rows.group(1)}/{rows.group(2)}" if rows else "0/50", "exit": p.returncode,
            "stdout_tail": text.splitlines()[-25:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "rpa_comparison" / "scope2_results.json"))
    args = ap.parse_args()
    from executor.scanner import VARIANTS
    fields = scan(VARIANTS)
    base = baseline(fields)
    res = {"variants": {}}
    for v in VARIANTS:
        it = intern(v, fields[v])
        res["variants"][v] = {"baseline": base[v], "intern": it}
        print(f"{v:<20} baseline {base[v]['correct']}/{base[v]['of']}   intern {it['correct']}/{it['of']}  rows verified {it['rows_verified']}", flush=True)
    res["fields"] = fields
    Path(args.out).write_text(json.dumps(res, indent=2), encoding="utf-8")
    print("written", args.out)


if __name__ == "__main__":
    main()
