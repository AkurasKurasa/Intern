"""The system, end to end.

    python automate.py                      # dry run on v0_base
    python automate.py --commit             # actually save
    python automate.py --variant v2_relabeled --commit

One command takes a grade sheet and a portal it has never been configured for,
works out which column belongs in which field, works out which field is derived
by a rule rather than copied, fills every row, verifies each write, and saves.

Nothing here is portal-specific. The variant name only picks which URL to open;
no selectors, no field names and no column mapping are written down anywhere for
it. That is the claim the whole project exists to make, so this script is
deliberately the shortest path to checking it.

Stages, matching the architecture:

    1  read the sheet                 3.4
    2  scan the portal                3.5
    3  score every column/field pair  3.6 + 3.7
    4  assign, or abstain             3.9
    5  induce the derived rule        3.8
    6  fill, verify, save             3.10
"""

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from executor.runner import run as run_executor  # noqa: E402
from executor.scanner import KIND_INPUT, scan_variants  # noqa: E402
from executor.sheet_reader import read_sheet  # noqa: E402
from features import encoders  # noqa: E402
from features.extractor import FEATURE_NAMES  # noqa: E402
from model.train import build_dataset, score_matrix, train  # noqa: E402
from resolver.assign import BUCKET_DERIVED, resolve  # noqa: E402
from rules.induce_from_session import induce_from_session  # noqa: E402

SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"
SESSION = REPO / "data" / "demos" / "v0_6rows.jsonl"

# Feature 16 is position-based and measurably harmful across variants
# (11/24 with it, 18/24 without), so the shipped configuration drops it.
POSITION_FEATURE = FEATURE_NAMES.index("pos_rank_distance")
FEATURE_MASK = {POSITION_FEATURE}

RULE = "-" * 74


def banner(number, title):
    print(f"\n{RULE}\n {number}. {title}\n{RULE}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", default="v0_base",
                    help="which portal to drive (default: v0_base)")
    ap.add_argument("--sheet", type=Path, default=SHEET)
    ap.add_argument("--session", type=Path, default=SESSION,
                    help="the recorded demonstration to learn from")
    ap.add_argument("--commit", action="store_true",
                    help="actually save; the default is a dry run")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N students")
    ap.add_argument("--show", action="store_true",
                    help="run in a visible browser and leave it open at the end")
    ap.add_argument("--log", type=Path, default=None,
                    help="where to write the run log (default: data/runs/)")
    args = ap.parse_args()

    if not args.sheet.exists():
        raise SystemExit(f"no sheet at {args.sheet} - run data/sheets/make_sheets.py")
    if not args.session.exists():
        raise SystemExit(f"no demonstration at {args.session} - "
                         "run recorder/demo_session.py")

    print(f"\n  sheet        {args.sheet.name}")
    print(f"  portal       {args.variant}")
    print(f"  learned from {args.session.name}")
    print(f"  mode         {'COMMIT' if args.commit else 'dry run'}")

    # ---------------------------------------------------------------- 1
    banner(1, "Reading the grade sheet")
    frame, columns = read_sheet(args.sheet, "SUMMARY", 11, "STUDENT NUMBER")
    columns = [c for c in columns if c.header]
    print(f"  {len(frame)} students, {len(columns)} named columns")
    print("  " + ", ".join(c.header for c in columns))

    # ---------------------------------------------------------------- 2
    banner(2, "Scanning the portal")
    descriptors = scan_variants([args.variant])[args.variant]
    fields = [d for d in descriptors if d.kind == KIND_INPUT]
    controls = [d for d in descriptors if d.kind != KIND_INPUT]
    print(f"  {len(fields)} editable columns, {len(controls)} control "
          f"({', '.join(c.label for c in controls)})")
    for field in fields:
        print(f"    {field.label:<30} {field.input_type:<9} "
              f"(named by cascade rule {field.label_rule})")

    # ---------------------------------------------------------------- 5a
    # The rule is induced first, because a derived field must be kept out of
    # the assignment entirely - otherwise it competes for a source column.
    banner(3, "Looking for fields that are computed, not copied")
    induced, _ = induce_from_session(args.session, auto_confirm=True)
    rules = [entry for entry in induced if entry["rule"] is not None]
    derived_labels = {entry["rule"].field for entry in rules}

    if not rules:
        print("  none found")
    for entry in rules:
        print(f"  {entry['rule'].describe()}")

    # ---------------------------------------------------------------- 3
    banner(4, "Matching columns to fields")
    examples, _, _ = build_dataset(args.session, "v0_base", args.sheet)
    model, _ = train(examples, feature_mask=FEATURE_MASK)

    scorable = [f for f in fields if f.label not in derived_labels]
    matrix = score_matrix(model, columns, scorable, FEATURE_MASK)

    # ---------------------------------------------------------------- 4
    mapping = resolve(columns, scorable, matrix, derived_labels=derived_labels)

    for assignment in mapping.auto:
        print(f"  {assignment.source_header:<20} -> {assignment.target_label:<28} "
              f"confidence {assignment.score:.2f}")
    for assignment in mapping.abstained:
        print(f"  {assignment.source_header:<20} -> ABSTAINED "
              f"(score {assignment.score:.2f}, margin {assignment.margin:.2f})")
    if mapping.unmapped_fields:
        print(f"  left empty: {', '.join(mapping.unmapped_fields)}")
    if mapping.partition.get(BUCKET_DERIVED):
        print(f"  filled by rule: {', '.join(mapping.partition[BUCKET_DERIVED])}")

    if not mapping.auto:
        raise SystemExit("\n  nothing could be mapped confidently - stopping "
                         "rather than guessing")

    # ---------------------------------------------------------------- 6
    banner(5, "Filling the portal" + ("" if args.commit else " (dry run)"))

    payload = {
        "variant": args.variant,
        "sheet": {"path": str(args.sheet), "sheet_name": "SUMMARY",
                  "header_row": 11, "key_column": "STUDENT NUMBER"},
        "assignments": [a.to_dict() for a in mapping.auto],
        "derived_rules": [e["rule"].to_dict() for e in rules],
        "unmapped_fields": mapping.unmapped_fields,
        "unmapped_columns": mapping.unmapped_columns,
        "control_fields": [c.label for c in controls],
        "row_alignment": {
            "key_column": "STUDENT NUMBER", "key_field": "Student ID",
            "verify_column": "NAME OF STUDENT", "verify_field": "Student Name",
        },
    }

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "induced_mapping.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        log = run_executor(args.variant, path, dry_run=not args.commit,
                           limit=args.limit, capture_state=True,
                           show=args.show)

    filled = [r for r in log.rows if r.status == "filled"]
    failed = [r for r in log.rows if r.status != "filled"]

    print(f"  {len(filled)} rows filled and verified, {len(failed)} failed")
    for row in failed[:5]:
        print(f"    row {row.row} ({row.student_id}): {row.reason}")
    print(f"  {log.commit_status}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = args.log or (REPO / "data" / "runs" /
                            f"automate_{args.variant}_{stamp}.json")
    if not log_path.is_absolute():
        log_path = REPO / log_path
    log.write(log_path)

    banner(6, "Result")
    print(f"  columns mapped        {len(mapping.auto)}")
    print(f"  abstained             {len(mapping.abstained)}")
    print(f"  fields filled by rule {len(rules)}")
    print(f"  rows verified         {len(filled)}/{len(log.rows)}")
    print(f"  run log               {log_path.relative_to(REPO)}")
    if not args.commit:
        print("\n  Nothing was saved. Re-run with --commit to write for real.")

    encoders.save_cache()
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
