"""
scripts/rpa_scope2.py
=====================
Objective 7, Scope 2 (grade sheet -> web portal): Intern vs a position-based
record-and-replay baseline, EXECUTED on every portal variant.

  Intern     components/scope2/automate.py --no_llm on each variant (dry run, all
             50 rows). The mapping it decides is captured on its way to the
             portal runner and saved next to its run log.
  Baseline   recorded once on v0_base as "column X goes into the field at
             position N" -- the 'click the 4th input box' selector described in
             components/scope2/eval/rpa_protocol.md -- and replayed unedited on
             each variant through the SAME portal runner (executor/runner.run):
             column X is written to whatever field occupies position N there.
             The recording is complete: it also maps PROGRAM -> Course, which a
             person recording on the base portal would do.

Scoring per row, from the values the runner read back after writing (verified):
  target values  course <- PROGRAM, year <- YEAR LEVEL, grade <- FINAL GRADE,
                 remarks <- pass/fail by the true cutoff (eval/ground_truth.py),
                 each looked up in the field whose data-key is that target.
  row correct    all four target values correct and the row was not refused.
Field placement (which field each target column reached) is reported too.

Usage:  python scripts/rpa_scope2.py
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S2 = ROOT / "components" / "scope2"
sys.path.insert(0, str(S2))

RUNS = ROOT / "docs" / "rpa_comparison" / "scope2_runs"
OUT = ROOT / "docs" / "rpa_comparison" / "scope2_results.json"
SHEET = S2 / "data" / "sheets" / "grade_sheet.xlsx"
TARGET_COLUMNS = {"PROGRAM": "course", "YEAR LEVEL": "year", "FINAL GRADE": "grade"}
TARGETS = ("course", "year", "grade", "remarks")


# ── portal fields ─────────────────────────────────────────────────────────────
def scan(variants):
    from executor.scanner import scan_variants
    return {name: [{"label": d.label, "key": d.truth_key, "pos": d.column_index}
                   for d in ds if d.kind == "input"]
            for name, ds in scan_variants(variants).items()}


# ── Intern: run automate.py, capture the mapping it hands to the runner ───────
def run_intern(variant: str) -> tuple[dict, dict]:
    import automate
    captured = {}
    real = automate.run_executor

    def spy(v, mapping_path, **kw):
        captured["mapping"] = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
        return real(v, mapping_path, **kw)

    automate.run_executor = spy
    tmp_rel = Path("data") / "runs" / f"o7_intern_{variant}.json"     # automate.py requires a path inside its own folder
    log_path = RUNS / f"intern_{variant}.json"
    argv = sys.argv
    sys.argv = ["automate.py", "--variant", variant, "--no_llm", "--log", str(tmp_rel)]
    try:
        with redirect_stdout(io.StringIO()):
            automate.main()
    finally:
        sys.argv = argv
        automate.run_executor = real
    shutil.move(str(S2 / tmp_rel), str(log_path))
    (RUNS / f"intern_{variant}_mapping.json").write_text(json.dumps(captured["mapping"], indent=2), encoding="utf-8")
    return captured["mapping"], json.loads(log_path.read_text(encoding="utf-8"))


# ── baseline: position replay ─────────────────────────────────────────────────
def record_baseline(intern_v0_mapping: dict, v0_fields: list) -> dict:
    """The base-portal recording: Intern's v0 mapping, completed with
    PROGRAM -> Course, expressed as field POSITIONS rather than labels."""
    pos_of = {f["label"]: f["pos"] for f in v0_fields}
    course_label = next(f["label"] for f in v0_fields if f["key"] == "course")
    assignments = [dict(a) for a in intern_v0_mapping["assignments"]]
    if not any(a["source_header"] == "PROGRAM" for a in assignments):
        assignments.append({"source_header": "PROGRAM", "target_label": course_label, "score": None,
                            "margin": None, "status": "auto", "via": "recorded"})
    rec = dict(intern_v0_mapping)
    rec["assignments"] = [dict(a, target_pos=pos_of[a["target_label"]]) for a in assignments]
    rec["derived_rules"] = [dict(r, field_pos=pos_of[r["field"]], depends_on_pos=pos_of[r["depends_on_field"]])
                            for r in intern_v0_mapping.get("derived_rules", [])]
    return rec


def replay_mapping(recording: dict, fields: list, variant: str) -> dict:
    """Apply the recording unedited: each position -> whatever field is there now."""
    label_at = {f["pos"]: f["label"] for f in fields}
    m = {k: v for k, v in recording.items() if k not in ("assignments", "derived_rules")}
    m["variant"] = variant
    m["assignments"] = [{k: v for k, v in dict(a, target_label=label_at[a["target_pos"]]).items() if k != "target_pos"}
                        for a in recording["assignments"] if a["target_pos"] in label_at]
    m["derived_rules"] = []
    for r in recording["derived_rules"]:
        if r["field_pos"] in label_at and r["depends_on_pos"] in label_at:
            nr = {k: v for k, v in r.items() if k not in ("field_pos", "depends_on_pos")}
            nr["field"], nr["depends_on_field"] = label_at[r["field_pos"]], label_at[r["depends_on_pos"]]
            m["derived_rules"].append(nr)
    # one value per field: a later assignment to an already-used field is dropped
    seen, kept = set(), []
    for a in m["assignments"]:
        if a["target_label"] not in seen:
            kept.append(a); seen.add(a["target_label"])
    m["assignments"] = kept
    m["derived_rules"] = [r for r in m["derived_rules"] if r["field"] not in seen]
    return m


def run_baseline(mapping: dict, variant: str) -> dict:
    from executor.runner import run
    d = Path(tempfile.mkdtemp())
    path = d / "baseline_mapping.json"
    path.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    shutil.copy(path, RUNS / f"baseline_{variant}_mapping.json")
    out = RUNS / f"baseline_{variant}.json"
    try:
        with redirect_stdout(io.StringIO()):
            log = run(variant, path, dry_run=True, capture_state=True)
    except Exception as exc:
        # The replay put a value into a field that rejects it (e.g. text into a
        # number box): the run stops with an error, as an RPA tool would. Every
        # row counts as not done.
        err = str(exc).splitlines()[0][:300]
        data = {"variant": variant, "stopped_with_error": err,
                "rows": [{"student_id": sid, "status": "failed", "reason": err, "verified": {}} for sid in expected_rows()]}
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data
    log.write(out)
    return json.loads(out.read_text(encoding="utf-8"))


# ── scoring ───────────────────────────────────────────────────────────────────
def expected_rows():
    from executor.sheet_reader import read_sheet
    from eval.ground_truth import TRUE_CUTOFF, TRUE_OPERATOR
    df, _ = read_sheet(SHEET, "SUMMARY", 11, "STUDENT NUMBER")
    cut, op = TRUE_CUTOFF["0-100"], TRUE_OPERATOR["0-100"]
    exp = {}
    for _, r in df.iterrows():
        g = float(r["FINAL GRADE"])
        passed = g >= cut if op == ">=" else g <= cut
        exp[str(r["STUDENT NUMBER"])] = {"course": str(r["PROGRAM"]), "year": float(r["YEAR LEVEL"]),
                                          "grade": g, "remarks": "Passed" if passed else "Failed"}
    return exp


def _same(a, b) -> bool:
    if a in (None, ""):
        return False
    if isinstance(b, float):
        try:
            return abs(float(a) - b) < 1e-6
        except (TypeError, ValueError):
            return False
    return str(a).strip().lower() == str(b).strip().lower()


def score_log(log: dict, fields: list, expected: dict) -> dict:
    keys = {}
    for f in fields:
        if f["key"] in TARGETS:
            keys.setdefault(f["key"], f["label"])
    rows_ok = values_ok = refused = 0
    for row in log["rows"]:
        exp = expected.get(str(row["student_id"]))
        got = row.get("verified") or {}
        ok = [_same(got.get(keys.get(k)), exp[k]) for k in TARGETS] if exp else [False] * 4
        values_ok += sum(ok)
        refused += row.get("status") != "filled"
        rows_ok += all(ok) and row.get("status") == "filled"
    n = len(log["rows"])
    return {"rows": n, "rows_correct": rows_ok, "values_correct": values_ok, "values_total": 4 * n, "rows_refused": refused}


def placement(mapping: dict, fields: list) -> dict:
    key_of = {f["label"]: f["key"] for f in fields}
    landed = {TARGET_COLUMNS[a["source_header"]]: key_of.get(a["target_label"])
              for a in mapping["assignments"] if a["source_header"] in TARGET_COLUMNS}
    rule = next(iter(mapping.get("derived_rules", [])), None)
    landed["remarks"] = key_of.get(rule["field"]) if rule else None
    for k in TARGETS:
        landed.setdefault(k, None)
    return {"landed": landed, "correct": sum(landed[k] == k for k in TARGETS), "of": 4}


def main():
    from executor.scanner import VARIANTS
    RUNS.mkdir(parents=True, exist_ok=True)
    fields = scan(VARIANTS)
    expected = expected_rows()
    intern_maps, res = {}, {"method": "executed", "variants": {}}
    for v in VARIANTS:
        intern_maps[v], ilog = run_intern(v)
        res["variants"][v] = {"intern": {**placement(intern_maps[v], fields[v]), **score_log(ilog, fields[v], expected)}}
    recording = record_baseline(intern_maps["v0_base"], fields["v0_base"])
    for v in VARIANTS:
        bmap = replay_mapping(recording, fields[v], v)
        blog = run_baseline(bmap, v)
        res["variants"][v]["baseline"] = {**placement(bmap, fields[v]), **score_log(blog, fields[v], expected),
                                          "stopped_with_error": blog.get("stopped_with_error", "")}
        b, i = res["variants"][v]["baseline"], res["variants"][v]["intern"]
        print(f"{v:<20} baseline fields {b['correct']}/4 rows {b['rows_correct']}/{b['rows']} values {b['values_correct']}/{b['values_total']} refused {b['rows_refused']}"
              f"  |  intern fields {i['correct']}/4 rows {i['rows_correct']}/{i['rows']} values {i['values_correct']}/{i['values_total']} refused {i['rows_refused']}", flush=True)
    demo = (S2 / "data" / "demos" / "v0_6rows.jsonl").read_text(encoding="utf-8").splitlines()
    res["intern_setup"] = {"demonstration": "data/demos/v0_6rows.jsonl", "events": len(demo), "rows_demonstrated": 6,
                           "note": "timestamps are synthetic (1-second steps), so no setup time is claimed"}
    res["fields"] = fields
    OUT.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print("written", OUT)


if __name__ == "__main__":
    main()
