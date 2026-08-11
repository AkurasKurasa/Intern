"""Milestone 6 end to end: a demonstration becomes a confirmed threshold rule.

    python rules/induce_from_session.py --session data/demos/v0_base_3rows.jsonl
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from recorder.events import read_session  # noqa: E402
from recorder.reconciler import reconcile  # noqa: E402
from rules.detect import detect, outcomes_from_candidates, row_values  # noqa: E402
from rules.induce import check_against_demonstrations, confirm, induce  # noqa: E402
from rules.options import resolve_rule_options  # noqa: E402


def induce_from_session(path, auto_confirm=False):
    excel_events, browser_events = read_session(path)
    reconciliation = reconcile(excel_events, browser_events)
    rows = row_values(browser_events)

    # Every already-filled field is a driver candidate (3.8 step 1), and that
    # includes the mapped ones - Grade is filled from a source column and is
    # exactly what Remarks derives from. Only other derived fields are excluded,
    # since a rule may not depend on something that is itself still unresolved.
    derived_labels = {c.target_label for c in reconciliation.derived_candidates}

    results = []
    for label in sorted(derived_labels):
        outcomes = outcomes_from_candidates(reconciliation.derived_candidates, label)
        option_list = next(
            (c.options for c in reconciliation.derived_candidates
             if c.target_label == label and c.options), []
        )
        detection = detect(label, outcomes, rows, exclude=derived_labels - {label})
        rule = induce(detection, options=option_list)

        entry = {"field": label, "detection": detection, "rule": rule,
                 "failures": [], "options": {}}
        if rule is not None:
            entry["failures"] = check_against_demonstrations(rule, detection)
            entry["options"] = resolve_rule_options(rule, option_list)
            if auto_confirm and not entry["failures"]:
                confirm(rule)
        results.append(entry)

    return results, reconciliation


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", type=Path,
                    default=REPO / "data" / "demos" / "v0_base_3rows.jsonl")
    ap.add_argument("--confirm", action="store_true",
                    help="accept the proposed cutoff (a real run asks the user)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    results, _ = induce_from_session(args.session, auto_confirm=args.confirm)
    if not results:
        print("no derived candidates in this session")
        return 0

    for entry in results:
        detection, rule = entry["detection"], entry["rule"]
        print(f"\nfield      {entry['field']}")
        print(f"detection  {detection.status} - {detection.reason}")
        for separation in detection.drivers:
            low, high = separation.interval
            print(f"  driver   {separation.driver_label}: "
                  f"{separation.low_class} below, {separation.high_class} above, "
                  f"interval ({low:g}, {high:g}]")

        if rule is None:
            print("  no rule proposed")
            continue

        print(f"\n  {rule.describe()}")
        print(f"  status   {rule.status}")
        if entry["failures"]:
            print(f"  MISMATCH on demonstrated rows: {entry['failures']}")
        for outcome, resolution in entry["options"].items():
            state = (f"-> {resolution.option!r} ({resolution.method})"
                     if resolution.resolved else f"UNRESOLVED - {resolution.reason}")
            print(f"  option   {outcome!r} {state}")

    if args.out:
        payload = [
            {"field": e["field"], "status": e["detection"].status,
             "rule": e["rule"].to_dict() if e["rule"] else None}
            for e in results
        ]
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
