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
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from executor.runner import run as run_executor  # noqa: E402
from executor.scanner import KIND_INPUT, scan_variants  # noqa: E402
from executor.sheet_reader import read_sheet  # noqa: E402
from features import encoders  # noqa: E402
from features.extractor import FEATURE_NAMES  # noqa: E402
from model.matcher import load as load_matcher  # noqa: E402
from model.train import build_dataset, score_matrix, train  # noqa: E402
from resolver.assign import BUCKET_DERIVED, Mapping, resolve  # noqa: E402
from resolver.fallback import (  # noqa: E402
    VIA_LLM, VIA_LOOKUP, VIA_MATCHER, LMStudioAsker, llm_tier, lookup_tier, remaining,
)
from rules.induce_from_session import induce_from_session  # noqa: E402

SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"
SESSION = REPO / "data" / "demos" / "v0_6rows.jsonl"

# The sheet columns that align a sheet row to a portal row rather than feeding
# a field: the portal PRINTS the Student ID and Student Name, it does not take
# them as input. Already declared in the row_alignment block below; named once
# here so the lookup and LLM tiers can keep them out of the candidate pool.
KEY_COLUMN = "STUDENT NUMBER"
VERIFY_COLUMN = "NAME OF STUDENT"
ALIGNMENT_COLUMNS = (KEY_COLUMN, VERIFY_COLUMN)

# Feature 16 is position-based and measurably harmful across variants
# (11/24 with it, 18/24 without), so the shipped configuration drops it.
POSITION_FEATURE = FEATURE_NAMES.index("pos_rank_distance")
FEATURE_MASK = {POSITION_FEATURE}

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


def print_countdown(seconds: int = 5) -> None:
    """Pre-run countdown, mirroring run_task.py's own print_countdown() --
    added for consistency between the two Electron workflows, even though
    this script has no real window to click into (it drives its own
    isolated browser, not the user's screen). COUNTDOWN_BEGIN/COUNTDOWN N/
    COUNTDOWN_END are the exact sentinel lines the Play panel's
    handleCapsuleProgressLine() already parses -- reusing them means the
    existing countdown widget picks this up with zero changes on the
    Electron side."""
    _flush_safe_print("COUNTDOWN_BEGIN")
    _flush_safe_print("Starting the matcher -- no window to click, it opens its own browser.")
    for i in range(seconds, 0, -1):
        _flush_safe_print(f"COUNTDOWN {i}")
        time.sleep(1)
    _flush_safe_print("COUNTDOWN_END")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", default="v0_base",
                    help="which portal to drive (default: v0_base)")
    ap.add_argument("--sheet", type=Path, default=SHEET)
    ap.add_argument("--session", type=Path, default=SESSION,
                    help="the recorded demonstration to learn from")
    ap.add_argument("--matcher", type=Path, default=None,
                    help="load a previously trained matcher instead of training "
                         "one fresh from --session (see model/train.py --out)")
    ap.add_argument("--commit", action="store_true",
                    help="actually save; the default is a dry run")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N students")
    ap.add_argument("--no_llm", action="store_true",
                    help="skip the LLM fallback tier: lookup then matcher only, the "
                         "configuration eval/run_variants.py measures. For comparing "
                         "what the LLM tier adds on the same portal.")
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

    print_countdown()

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
        print(_color("  none found", _DIM))
    for entry in rules:
        # The headline claim of the whole scope: the cutoff was read off the
        # data, never configured. Worth being the one line that stands out.
        print(f"  {_color(entry['rule'].describe(), _BLUE)}")

    # ---------------------------------------------------------------- 3
    # Three tiers, cheapest first, each seeing only what the ones before it
    # left undecided:
    #
    #   lookup   Scope #1's label lookup -- a column whose name IS the field's
    #   matcher  the trained network + Hungarian + abstention, unchanged
    #   LLM      only for fields the matcher could not decide, and allowed to
    #            answer NONE
    #
    # Direct request: make Scope #2 work like Scope #1's form -- lookup, then
    # LLM fallback -- while keeping the matcher and keeping rule induction.
    # Only the mapping decision changed; values are still written with fill().
    banner(4, "Matching columns to fields")
    scorable = [f for f in fields if f.label not in derived_labels]

    # -- tier 1: lookup --------------------------------------------------
    looked_up = lookup_tier(scorable, columns, exclude=ALIGNMENT_COLUMNS)
    for d in looked_up:
        print(f"  {d.column:<20} -> {_color(f'{d.field:<28}', _GREEN)} "
              f"{_color('label match', _DIM)}")
    lookup_fields = {d.field for d in looked_up}
    lookup_columns = {d.column for d in looked_up}

    # -- tier 2: matcher, over what lookup left ------------------------
    m_fields = remaining(scorable, lookup_fields, key=lambda f: f.label)
    m_columns = remaining(columns, lookup_columns, key=lambda c: c.header)

    if args.matcher:
        model, artifact = load_matcher(args.matcher)
        meta = artifact.get("metadata", {})
        print(f"  loaded {args.matcher.name} ({meta.get('examples', '?')} examples, "
              f"final loss {meta.get('final_loss', float('nan')):.4f})")
    else:
        examples, _, _ = build_dataset(args.session, "v0_base", args.sheet)
        model, _ = train(examples, feature_mask=FEATURE_MASK)

    if m_fields and m_columns:
        # Say what is about to happen, because the next line is the longest
        # silent stretch in the whole run: score_matrix embeds every
        # column/field pair, which loads all-MiniLM-L6-v2 the first time.
        # Found live -- a user saw "loaded matcher.pt" and then nothing, and
        # stopped the run three seconds before the browser would have opened.
        _flush_safe_print(f"  scoring {len(m_columns)} columns against "
                          f"{len(m_fields)} fields (embedding, first run is slower)...")
        matrix = score_matrix(model, m_columns, m_fields, FEATURE_MASK)
        mapping = resolve(m_columns, m_fields, matrix, derived_labels=derived_labels)
    else:
        mapping = Mapping(unmapped_fields=[f.label for f in m_fields],
                          unmapped_columns=[c.header for c in m_columns],
                          partition={BUCKET_DERIVED: sorted(derived_labels)})

    # Pad first, color second: an f-string width applies to the whole string
    # including the invisible escape bytes, so coloring first pads the ANSI
    # codes instead of the text and the columns stop lining up.
    for assignment in mapping.auto:
        print(f"  {assignment.source_header:<20} -> "
              f"{_color(f'{assignment.target_label:<28}', _GREEN)} "
              f"confidence {assignment.score:.2f}")
    for assignment in mapping.abstained:
        # Yellow, not red: abstaining is the system working, not failing.
        print(f"  {assignment.source_header:<20} -> {_color('ABSTAINED', _YELLOW)} "
              f"(score {assignment.score:.2f}, margin {assignment.margin:.2f})")

    # -- tier 3: LLM, for what the matcher could not decide -------------
    claimed = lookup_columns | {a.source_header for a in mapping.auto}
    undecided = [f for f in m_fields if f.label in mapping.unmapped_fields]
    llm_decisions = []
    if undecided and args.no_llm:
        print(_color(f"  LLM tier skipped (--no_llm): {len(undecided)} field(s) "
                     "left for it", _DIM))
    elif undecided:
        asker = LMStudioAsker()
        pool = remaining(columns, claimed, key=lambda c: c.header)
        _flush_safe_print(f"  asking the LLM about {len(undecided)} field(s) the "
                          "matcher could not decide...")
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
            print(_color(f"  {asker.error} - those fields stay empty; the lookup "
                         "and matcher results stand.", _YELLOW))

    # -- the combined mapping the executor fills from -------------------
    llm_matched = [d for d in llm_decisions if d.column]
    assignments = (
        [d.to_assignment() for d in looked_up]
        + [{**a.to_dict(), "via": VIA_MATCHER} for a in mapping.auto]
        + [d.to_assignment() for d in llm_matched]
    )
    final_columns = {a["source_header"] for a in assignments}
    final_fields = {a["target_label"] for a in assignments}

    # A matcher abstention the LLM then resolved is no longer a refusal.
    abstained = [a for a in mapping.abstained if a.source_header not in final_columns]
    unmapped_fields = [f.label for f in scorable if f.label not in final_fields]
    unmapped_columns = [c.header for c in columns if c.header not in final_columns]

    if unmapped_fields:
        print(_color(f"  left empty: {', '.join(unmapped_fields)}", _DIM))
    if derived_labels:
        print(f"  filled by rule: {_color(', '.join(sorted(derived_labels)), _BLUE)}")

    via_counts = {v: sum(1 for a in assignments if a["via"] == v)
                  for v in (VIA_LOOKUP, VIA_MATCHER, VIA_LLM)}
    print(_color(f"  decided by: lookup {via_counts[VIA_LOOKUP]}, matcher "
                 f"{via_counts[VIA_MATCHER]}, LLM {via_counts[VIA_LLM]}", _DIM))

    if not assignments:
        raise SystemExit("\n  nothing could be mapped confidently - stopping "
                         "rather than guessing")

    # ---------------------------------------------------------------- 6
    banner(5, "Filling the portal" + ("" if args.commit else " (dry run)"))

    payload = {
        "variant": args.variant,
        "sheet": {"path": str(args.sheet), "sheet_name": "SUMMARY",
                  "header_row": 11, "key_column": "STUDENT NUMBER"},
        # Every accepted pairing from all three tiers, each tagged with the
        # tier that decided it ("via"). fill_order() reads only the
        # header/label pair, so the tag rides along to the log and the HUD.
        "assignments": assignments,
        # Abstentions were being dropped here, which meant the executor - and
        # so the run log and the HUD - had no record that the Resolver had
        # deliberately refused a column. Refusing a decoy is a result, not a
        # gap, so it travels with the mapping. fill_order() reads only
        # "assignments" and "derived_rules", so nothing downstream fills them.
        "abstained": [a.to_dict() for a in abstained],
        "derived_rules": [e["rule"].to_dict() for e in rules],
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
          f"   (lookup {via_counts[VIA_LOOKUP]}, matcher {via_counts[VIA_MATCHER]}, "
          f"LLM {via_counts[VIA_LLM]})")
    print(f"  abstained             {_color(str(len(abstained)), _YELLOW)}")
    print(f"  fields filled by rule {_color(str(len(rules)), _BLUE)}")
    print(f"  rows verified         "
          f"{_color(f'{len(filled)}/{len(log.rows)}', _GREEN if not failed else _YELLOW)}")
    print(f"  run log               {log_path.relative_to(REPO)}")
    if not args.commit:
        print(_color("\n  Nothing was saved. Re-run with --commit to write for real.", _DIM))

    encoders.save_cache()
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
