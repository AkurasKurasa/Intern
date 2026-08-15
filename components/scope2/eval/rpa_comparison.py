"""Milestone 10: the comparison against a rule-based RPA tool.

The comparison has two halves and only one of them can be automated.

  This system's half runs here: machine setup time, accuracy on every variant
  without re-recording, and reconfiguration cost expressed as escalations.

  The RPA half needs the RPA tool and a person operating it. Those numbers are
  read from eval/rpa_measurements.json, which a human fills in while following
  eval/rpa_protocol.md. Anything not measured prints as "not measured". No RPA
  figure is ever estimated, predicted or defaulted - a fabricated benchmark is
  worse than an incomplete one, and this comparison is the one most likely to
  be quoted.

Usage:
    python eval/rpa_comparison.py                 # our half, plus whatever is filled in
    python eval/rpa_comparison.py --our-side-only
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from eval.ground_truth import COLUMN_TO_KEY, scorable_fields  # noqa: E402
from eval.hitl import accuracy, escalate  # noqa: E402
from executor.scanner import scan_variants  # noqa: E402
from executor.sheet_reader import read_sheet  # noqa: E402
from features import encoders  # noqa: E402
from features.extractor import FEATURE_NAMES  # noqa: E402
from model.train import build_dataset, score_matrix, train  # noqa: E402
from coworker_recorder.confirm import ACCEPT, Decision, apply_decisions, proposals  # noqa: E402
from coworker_recorder.reconciler import reconcile_session  # noqa: E402
from resolver.assign import resolve  # noqa: E402

SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"
SESSION = REPO / "data" / "demos" / "v0_6rows.jsonl"
MEASUREMENTS = REPO / "eval" / "rpa_measurements.json"

POSITION_FEATURE = FEATURE_NAMES.index("pos_rank_distance")

# The variants 7 names for the adaptability comparison: reordered, relabelled,
# and restructured. A recorded selector-based automation is expected to break on
# these; whether it does is a measurement, not an assumption.
CHANGE_VARIANTS = ["v1_reordered", "v2_relabeled", "v4_unassociated"]

NOT_MEASURED = "not measured"


@dataclass
class OurSide:
    machine_setup_seconds: float = 0.0
    reconcile_seconds: float = 0.0
    train_seconds: float = 0.0
    resolve_seconds: float = 0.0
    first_row_seconds: float = 0.0
    baseline_correct: int = 0
    baseline_total: int = 0
    per_variant: dict = dataclass_field(default_factory=dict)
    escalations: dict = dataclass_field(default_factory=dict)


def measure_our_side(session=SESSION, feature_mask={POSITION_FEATURE}):
    """Everything on this side that a clock can measure.

    Deliberately excludes the human demonstration: that is a person's time and
    is measured the same way for both systems, by stopwatch, in the template.
    Reporting the scripted demonstration as if it were setup time would flatter
    this system by minutes.
    """
    result = OurSide()

    _, columns = read_sheet(SHEET, "SUMMARY", 11, "STUDENT NUMBER")
    columns = [c for c in columns if c.header]

    started = time.perf_counter()
    reconciliation = reconcile_session(session)
    apply_decisions(
        reconciliation,
        [Decision(p["target_label"], ACCEPT) for p in proposals(reconciliation)],
    )
    result.reconcile_seconds = time.perf_counter() - started

    started = time.perf_counter()
    examples, _, _ = build_dataset(session, "v0_base")
    model, _ = train(examples, feature_mask=feature_mask)
    result.train_seconds = time.perf_counter() - started

    scanned = scan_variants(["v0_base"] + CHANGE_VARIANTS)

    started = time.perf_counter()
    fields = scorable_fields(scanned["v0_base"], "v0_base")
    mapping = resolve(columns, fields,
                      score_matrix(model, columns, fields, feature_mask))
    result.resolve_seconds = time.perf_counter() - started

    result.machine_setup_seconds = (
        result.reconcile_seconds + result.train_seconds + result.resolve_seconds
    )

    correct, total = accuracy(mapping, columns, fields)
    result.baseline_correct, result.baseline_total = correct, total

    # Time to the first verified row, using the induced mapping on the live
    # portal. This is the same milestone the RPA side is timed to.
    started = time.perf_counter()
    _first_row_check(mapping, columns, fields)
    result.first_row_seconds = result.machine_setup_seconds + (
        time.perf_counter() - started)

    # Adaptability: the same trained model, no re-demonstration, no edits.
    for variant in CHANGE_VARIANTS:
        variant_fields = scorable_fields(scanned[variant], variant)
        variant_mapping = resolve(
            columns, variant_fields,
            score_matrix(model, columns, variant_fields, feature_mask))
        got, want = accuracy(variant_mapping, columns, variant_fields)
        result.per_variant[variant] = (got, want)
        result.escalations[variant] = len(
            escalate(variant_mapping, columns, variant_fields, variant))

    return result


def _first_row_check(mapping, columns, fields):
    """Fill and verify one row through the executor, using the induced mapping."""
    import tempfile

    from executor.runner import run as run_executor

    assignments = []
    key_of = {f.label: f.truth_key for f in fields}
    for assignment in mapping.auto:
        if key_of.get(assignment.target_label) in COLUMN_TO_KEY.values():
            assignments.append({
                "source_header": assignment.source_header,
                "target_label": assignment.target_label,
                "score": assignment.score,
                "margin": assignment.margin,
                "status": "auto",
            })

    payload = {
        "variant": "v0_base",
        "sheet": {"path": "data/sheets/grade_sheet.xlsx", "sheet_name": "SUMMARY",
                  "header_row": 11, "key_column": "STUDENT NUMBER"},
        "assignments": assignments,
        "derived_rules": [],
        "unmapped_fields": [],
        "unmapped_columns": [],
        "control_fields": ["Select"],
        "row_alignment": {"key_column": "STUDENT NUMBER", "key_field": "Student ID",
                          "verify_column": "NAME OF STUDENT",
                          "verify_field": "Student Name"},
    }

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "induced.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        log = run_executor("v0_base", path, dry_run=True, limit=1,
                           capture_state=False)
    return log


def load_measurements(path=MEASUREMENTS):
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def show(value, suffix=""):
    if value is None:
        return NOT_MEASURED
    if isinstance(value, (int, float)):
        return f"{value:g}{suffix}"
    return str(value)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", type=Path, default=SESSION)
    ap.add_argument("--measurements", type=Path, default=MEASUREMENTS)
    ap.add_argument("--our-side-only", action="store_true")
    args = ap.parse_args()

    ours = measure_our_side(args.session)
    rpa = None if args.our_side_only else load_measurements(args.measurements)

    print("\nMILESTONE 10 - COMPARISON AGAINST A RULE-BASED RPA TOOL")

    if rpa is None:
        print("\nRPA side: no measurements file at "
              f"{args.measurements.name}. Only this system's half is shown.")
        print("To fill it in: copy eval/rpa_measurements.template.json to")
        print("eval/rpa_measurements.json and follow eval/rpa_protocol.md.")
    else:
        tool = rpa.get("tool", {})
        operator = rpa.get("operator", {})
        print(f"\nRPA tool     {show(tool.get('name'))} {show(tool.get('version'))}")
        print(f"operator     {show(operator.get('who'))} "
              f"(experience: {show(operator.get('rpa_experience'))})")

    setup = (rpa or {}).get("setup", {})
    print("\nSETUP COST")
    print(f"{'measure':<38} {'this system':>16} {'RPA tool':>16}")
    print("-" * 72)
    print(f"{'human demonstration (s)':<38} "
          f"{show(setup.get('human_demonstration_seconds')):>16} "
          f"{show(setup.get('human_demonstration_seconds')):>16}")
    print(f"{'machine configuration (s)':<38} "
          f"{ours.machine_setup_seconds:>16.1f} "
          f"{show(setup.get('configuration_seconds')):>16}")
    print(f"{'to first verified row (s)':<38} "
          f"{ours.first_row_seconds:>16.1f} "
          f"{show(setup.get('time_to_first_verified_row_seconds')):>16}")
    print("\n  Human demonstration is one number, not two: both systems are shown")
    print("  the same rows by the same person, so it is measured once and")
    print("  applies to both. Only what follows it differs.")

    print("\nADAPTABILITY - same configuration, changed interface, no re-recording")
    changes = (rpa or {}).get("after_ui_change", {})
    reconfig = (rpa or {}).get("reconfiguration", {})
    print(f"{'variant':<20} {'this system':>14} {'escalations':>12} "
          f"{'RPA correct':>12} {'RPA outcome':>22} {'refix (s)':>10}")
    print("-" * 94)
    for variant in CHANGE_VARIANTS:
        got, want = ours.per_variant.get(variant, (0, 0))
        entry = changes.get(variant, {}) if changes else {}
        print(f"{variant:<20} {f'{got}/{want}':>14} "
              f"{ours.escalations.get(variant, 0):>12} "
              f"{show(entry.get('rows_filled_correctly')):>12} "
              f"{show(entry.get('outcome')):>22} "
              f"{show(reconfig.get(variant + '_seconds')):>10}")

    print(f"\n  This system's baseline on v0_base: "
          f"{ours.baseline_correct}/{ours.baseline_total} columns mapped.")
    print("  'escalations' is what a person is asked when the interface changes:")
    print("  it is this system's equivalent of reconfiguration work.")

    if rpa is None or any(
        changes.get(v, {}).get("rows_filled_correctly") is None
        for v in CHANGE_VARIANTS
    ):
        print("\nINCOMPLETE - the RPA column is not fully measured. Do not quote")
        print("this table as a comparison until it is.")

    encoders.save_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
