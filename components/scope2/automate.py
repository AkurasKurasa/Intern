"""The system, end to end.

    python automate.py                      # dry run on v0_base
    python automate.py --commit             # actually save
    python automate.py --variant v2_relabeled --commit

One command takes a grade sheet and a portal it has never been configured for,
works out which column belongs in which field, works out which field is derived
by a rule rather than copied, fills every row, verifies each write, and saves.

It decides the mapping the way Scope #1 fills its form: read the field's label,
find it in the source (the sheet's column names), and ask the LLM only for the
fields the source cannot name. No embedding model, no trained matcher.

Nothing here is portal-specific. The variant name only picks which URL to open;
no selectors, no field names and no column mapping are written down anywhere for
it. That is the claim the whole project exists to make, so this script is
deliberately the shortest path to checking it.

Stages, matching the architecture:

    1  read the sheet
    2  scan the portal
    3  induce the derived rule
    4  map columns to fields: source lookup, then the LLM per leftover field
    5  fill, verify, save
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from executor.runner import run as run_executor  # noqa: E402
from executor.scanner import KIND_INPUT, scan_variants  # noqa: E402
from executor.sheet_reader import read_sheet  # noqa: E402
from resolver.fallback import (  # noqa: E402
    VIA_LLM, VIA_SOURCE, LMStudioAsker, llm_tier, lookup_tier, remaining,
)
from rules.induce_from_session import induce_from_session  # noqa: E402
from rules.rebind import driver_column, rebind_driver, rebind_target  # noqa: E402

SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"
SESSION = REPO / "data" / "demos" / "v0_6rows.jsonl"

# The sheet columns that align a sheet row to a portal row rather than feeding
# a field: the portal PRINTS the Student ID and Student Name, it does not take
# them as input. Already declared in the row_alignment block below; named once
# here so the lookup and LLM tiers can keep them out of the candidate pool.
KEY_COLUMN = "STUDENT NUMBER"
VERIFY_COLUMN = "NAME OF STUDENT"
ALIGNMENT_COLUMNS = (KEY_COLUMN, VERIFY_COLUMN)

RULE = "-" * 74

# os.system("") forces the classic Windows console into ANSI/VT100 mode --
# the same dependency-free trick automate_inbox.py already uses. Scope #1 and
# Scope #3 both got colored output; this scope was still printing flat grey,
# which made its most interesting lines (an abstention, an induced rule) read
# exactly like its least interesting ones.
if sys.platform == "win32":
    os.system("")

_GREEN, _YELLOW, _RED, _BLUE, _BOLD, _DIM, _RESET = (
    "\033[32m", "\033[33m", "\033[31m", "\033[34m", "\033[1m", "\033[2m", "\033[0m",
)


def _color(text, code):
    return f"{code}{text}{_RESET}"


def banner(number, title):
    print(f"\n{_color(RULE, _DIM)}\n {_color(f'{number}. {title}', _BOLD)}\n{_color(RULE, _DIM)}")


def _flush_safe_print(text: str) -> None:
    """Write, then attempt-and-ignore the flush. When this script is
    launched by the Electron app's Play button (app/recorder_bridge.py
    spawns it with windowsHide=True, no console window), an explicit
    stdout.flush() can raise OSError: [Errno 22] Invalid argument on
    Windows even though the write itself already succeeded -- the same
    failure run_task.py's own print_countdown() hit and fixed the same
    way; this script gets spawned through the identical no-console chain,
    so it needs the identical guard."""
    print(text)
    try:
        sys.stdout.flush()
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", default="v0_base",
                    help="which portal to drive (default: v0_base)")
    ap.add_argument("--sheet", type=Path, default=SHEET)
    ap.add_argument("--session", type=Path, default=SESSION,
                    help="the recorded demonstration the Remarks rule is learned from")
    ap.add_argument("--commit", action="store_true",
                    help="actually save; the default is a dry run")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N students")
    ap.add_argument("--no_llm", action="store_true",
                    help="skip the LLM: source lookup only, to see what the "
                         "source decides on its own on the same portal.")
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

    # No countdown. Scope #1 counts down so the user can click the form
    # window before it starts typing; this script drives its own browser, so
    # the 5 s bought nothing -- measured, it was a fifth of the wait before
    # the portal appeared.

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
    induced, reconciliation = induce_from_session(args.session, auto_confirm=True)
    found = [entry for entry in induced if entry["rule"] is not None]

    if not found:
        print(_color("  none found", _DIM))
    rules = []   # rule dicts, pointed at THIS portal's fields (rules/rebind.py)
    for entry in found:
        # The headline claim of the whole scope: the cutoff was read off the
        # data, never configured. Worth being the one line that stands out.
        print(f"  {_color(entry['rule'].describe(), _BLUE)}")
        rule = entry["rule"].to_dict()
        target = rebind_target(rule, fields)
        if target is None:
            print(_color(f"  skipped on this portal: no single field takes "
                         f"{rule['if_true']}/{rule['if_false']}", _YELLOW))
            continue
        if target != rule["field"]:
            print(_color(f"  on this portal it writes {target!r}", _DIM))
        rule["field"] = target
        rule["driver_column"] = driver_column(reconciliation.pairs,
                                              rule["depends_on_field"])
        rules.append(rule)
    derived_labels = {rule["field"] for rule in rules}

    # ---------------------------------------------------------------- 4
    # Scope #1's order, cheapest first:
    #
    #   source   the field's label found among the sheet's column names
    #   LLM      asked per field, only for what the source could not name,
    #            and allowed to answer NONE
    #
    # Direct request: build Scope #2 like Scope #1 -- Scope #1 loads no
    # embedding model, so neither does this. Only the mapping decision lives
    # here; values are still written with fill(), the rule still fills Remarks.
    banner(4, "Matching columns to fields")
    scorable = [f for f in fields if f.label not in derived_labels]

    # -- source ----------------------------------------------------------
    looked_up = lookup_tier(scorable, columns, exclude=ALIGNMENT_COLUMNS)
    for d in looked_up:
        print(f"  {d.column:<20} -> {_color(f'{d.field:<28}', _GREEN)} "
              f"{_color('source', _DIM)}")
    source_fields = {d.field for d in looked_up}
    source_columns = {d.column for d in looked_up}

    # -- LLM, per field the source could not name ------------------------
    undecided = remaining(scorable, source_fields, key=lambda f: f.label)
    llm_decisions = []
    if undecided and args.no_llm:
        print(_color(f"  LLM skipped (--no_llm): {len(undecided)} field(s) "
                     "left for it", _DIM))
    elif undecided:
        asker = LMStudioAsker()
        pool = remaining(columns, source_columns, key=lambda c: c.header)
        _flush_safe_print(f"  asking the LLM about {len(undecided)} field(s) the "
                          "source could not name...")
        llm_decisions = llm_tier(undecided, pool, asker, exclude=ALIGNMENT_COLUMNS)
        if asker.model:
            print(_color(f"  (model: {asker.model})", _DIM))
        for d in llm_decisions:
            if d.column:
                print(f"  {d.column:<20} -> {_color(f'{d.field:<28}', _GREEN)} "
                      f"{_color('LLM', _BLUE)}")
            else:
                print(f"  {'':<20}    {d.field:<28} "
                      f"{_color(d.reason, _YELLOW)}")
        if asker.error and not asker.model:
            print(_color(f"  {asker.error} - those fields stay empty; the source "
                         "results stand.", _YELLOW))

    # -- the combined mapping the executor fills from -------------------
    assignments = ([d.to_assignment() for d in looked_up]
                   + [d.to_assignment() for d in llm_decisions if d.column])
    final_columns = {a["source_header"] for a in assignments}
    final_fields = {a["target_label"] for a in assignments}

    # Fields the LLM refused are kept as a result, not dropped: refusing is
    # the system working, and the HUD shows it.
    refused = [{"source_header": None, "target_label": d.field, "reason": d.reason}
               for d in llm_decisions if not d.column]
    unmapped_fields = [f.label for f in scorable if f.label not in final_fields]
    unmapped_columns = [c.header for c in columns if c.header not in final_columns]

    # The rule's input is the field this mapping fills from the column the
    # demonstration used -- never the demonstrated label, which another
    # portal may not have. No such field: the rule is skipped, not crashed.
    derived_rules = []
    for rule in rules:
        column = rule.pop("driver_column")
        driver = rebind_driver(column, assignments)
        if driver is None:
            print(_color(f"  rule for {rule['field']!r} skipped: its input column "
                         f"{column} fills no field here", _YELLOW))
            unmapped_fields.append(rule["field"])
            continue
        rule["depends_on_field"] = driver
        derived_rules.append(rule)

    if unmapped_fields:
        print(_color(f"  left empty: {', '.join(unmapped_fields)}", _DIM))
    for rule in derived_rules:
        source = f"(from {rule['depends_on_field']})"
        print(f"  filled by rule: {_color(rule['field'], _BLUE)} {_color(source, _DIM)}")

    via_counts = {v: sum(1 for a in assignments if a["via"] == v)
                  for v in (VIA_SOURCE, VIA_LLM)}
    print(_color(f"  decided by: source {via_counts[VIA_SOURCE]}, "
                 f"LLM {via_counts[VIA_LLM]}", _DIM))

    if not assignments:
        raise SystemExit("\n  nothing could be mapped confidently - stopping "
                         "rather than guessing")

    # ---------------------------------------------------------------- 5
    banner(5, "Filling the portal" + ("" if args.commit else " (dry run)"))

    payload = {
        "variant": args.variant,
        "sheet": {"path": str(args.sheet), "sheet_name": "SUMMARY",
                  "header_row": 11, "key_column": "STUDENT NUMBER"},
        # Every accepted pairing, each tagged with the tier that decided it
        # ("via": source or llm). fill_order() reads only the header/label
        # pair, so the tag rides along to the log and the HUD.
        "assignments": assignments,
        # Refusals travel with the mapping so the run log and the HUD record
        # them. fill_order() reads only "assignments" and "derived_rules", so
        # nothing downstream fills them.
        "abstained": refused,
        "derived_rules": derived_rules,
        "unmapped_fields": unmapped_fields,
        "unmapped_columns": unmapped_columns,
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

    print(f"  {_color(str(len(filled)), _GREEN)} rows filled and verified, "
          f"{_color(str(len(failed)), _RED if failed else _DIM)} failed")
    for row in failed[:5]:
        print(f"    {_color(f'row {row.row} ({row.student_id})', _RED)}: {row.reason}")
    print(f"  {log.commit_status}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = args.log or (REPO / "data" / "runs" /
                            f"automate_{args.variant}_{stamp}.json")
    if not log_path.is_absolute():
        log_path = REPO / log_path
    log.write(log_path)

    banner(6, "Result")
    print(f"  columns mapped        {_color(str(len(assignments)), _BOLD)}"
          f"   (source {via_counts[VIA_SOURCE]}, LLM {via_counts[VIA_LLM]})")
    print(f"  refused by the LLM    {_color(str(len(refused)), _YELLOW)}")
    print(f"  fields filled by rule {_color(str(len(derived_rules)), _BLUE)}")
    print(f"  rows verified         "
          f"{_color(f'{len(filled)}/{len(log.rows)}', _GREEN if not failed else _YELLOW)}")
    print(f"  run log               {log_path.relative_to(REPO)}")
    if not args.commit:
        print(_color("\n  Nothing was saved. Re-run with --commit to write for real.", _DIM))

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
