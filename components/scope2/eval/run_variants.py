"""Milestones 7 and 8: the accuracy table, the baselines and the ablations.

Everything here is scored against eval/ground_truth.py, which states the answer
against the portal's data-key so a relabelled variant is scored the same way as
the base one.

Two metrics are reported side by side and must not be conflated:

  mapping accuracy  - of the columns that *should* map, how many did, correctly.
  abstention        - of the columns that should NOT map, how many were
                      correctly left alone, and how often an abstention was
                      wrong. 3.9 calls abstention a first-class output, so a
                      system that maps nothing scores 0 on the first and 100%
                      on the second, and both numbers have to be visible.

Usage:
    python eval/run_variants.py
    python eval/run_variants.py --json data/runs/eval.json
"""

import argparse
import json
import sys
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from eval.ground_truth import (  # noqa: E402
    FIELDS_WITHOUT_SOURCE, TRUE_CUTOFF, TRUE_OPERATOR, expected_key,
    scorable_fields,
)
from executor.runner import relative_to_repo  # noqa: E402
from executor.scanner import VARIANTS, scan_variants  # noqa: E402
from executor.sheet_reader import read_sheet  # noqa: E402
from features import encoders  # noqa: E402
from features.extractor import FEATURE_NAMES  # noqa: E402
from model.baselines import cosine_matrix, string_match_matrix  # noqa: E402
from model.train import build_dataset, score_matrix, train  # noqa: E402
from resolver.assign import resolve  # noqa: E402
from rules.induce_from_session import induce_from_session  # noqa: E402

SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"
DEMOS = REPO / "data" / "demos"
DEFAULT_SESSION = DEMOS / "v0_6rows.jsonl"

POSITION_FEATURE = FEATURE_NAMES.index("pos_rank_distance")
VALUE_SHAPE_FEATURES = {FEATURE_NAMES.index(n) for n in
                        ("val_type_match", "val_regex_family",
                         "val_constraints", "val_length_fit")}
OPTION_FEATURE = {FEATURE_NAMES.index("opt_option_overlap")}


@dataclass
class VariantScore:
    variant: str
    mapped_correct: int = 0
    mapped_total: int = 0
    mapped_wrong: list = dataclass_field(default_factory=list)
    abstain_correct: int = 0
    abstain_total: int = 0
    false_abstentions: list = dataclass_field(default_factory=list)

    @property
    def accuracy(self):
        return self.mapped_correct / self.mapped_total if self.mapped_total else 0.0

    @property
    def abstention_recall(self):
        """Of the columns that should abstain, how many did."""
        return self.abstain_correct / self.abstain_total if self.abstain_total else 0.0

    @property
    def abstention_precision(self):
        """Of everything abstained on, how much should have been."""
        total = self.abstain_correct + len(self.false_abstentions)
        return self.abstain_correct / total if total else 1.0


def score_variant(variant, columns, fields, matrix):
    """Compare one resolved mapping against ground truth."""
    key_of = {f.label: f.truth_key for f in fields}
    mapping = resolve(columns, fields, matrix)

    produced = {}       # source header -> data-key it was mapped into
    for assignment in mapping.auto:
        produced[assignment.source_header] = key_of.get(assignment.target_label)

    score = VariantScore(variant=variant)
    for column in columns:
        wanted = expected_key(column.header)
        got = produced.get(column.header)

        if wanted is not None:
            score.mapped_total += 1
            if got == wanted:
                score.mapped_correct += 1
            else:
                score.mapped_wrong.append(
                    f"{column.header}->{got or 'abstained'} (want {wanted})")
        else:
            score.abstain_total += 1
            if got is None:
                score.abstain_correct += 1
            else:
                score.false_abstentions.append(f"{column.header}->{got}")

    # A field that should have no source but received one is the other half of
    # 3.11's non-assignment test.
    forbidden = FIELDS_WITHOUT_SOURCE.get(variant, set())
    for assignment in mapping.auto:
        if key_of.get(assignment.target_label) in forbidden:
            score.false_abstentions.append(
                f"{assignment.source_header}->{assignment.target_label}")

    return score, mapping


def scorers(model, ablated):
    return [
        ("string", lambda c, f: string_match_matrix(c, f), None),
        ("cosine", lambda c, f: cosine_matrix(c, f), None),
        ("matcher", lambda c, f: score_matrix(model, c, f), None),
        ("matcher-noPos", lambda c, f: score_matrix(ablated, c, f, {POSITION_FEATURE}),
         {POSITION_FEATURE}),
    ]


def run(session=DEFAULT_SESSION, variants=VARIANTS):
    _, columns = read_sheet(SHEET, "SUMMARY", 11, "STUDENT NUMBER")
    columns = [c for c in columns if c.header]

    examples, _, _ = build_dataset(session, "v0_base")
    model, _ = train(examples)
    ablated, _ = train(examples, feature_mask={POSITION_FEATURE})

    scanned = scan_variants(list(variants))
    results = {}
    for variant in variants:
        fields = scorable_fields(scanned[variant], variant)
        results[variant] = {}
        for name, build_matrix, _ in scorers(model, ablated):
            score, mapping = score_variant(variant, columns, fields,
                                           build_matrix(columns, fields))
            results[variant][name] = score
    return results, model, ablated, columns, scanned


def ablation_table(examples, columns, scanned, variants):
    """7's ablations: with and without each feature group."""
    settings = [
        ("all 17 features", None),
        ("no positional (16)", {POSITION_FEATURE}),
        ("no value shape (9-12)", VALUE_SHAPE_FEATURES),
        ("no option set (17)", OPTION_FEATURE),
    ]
    rows = []
    for label, mask in settings:
        model, _ = train(examples, feature_mask=mask)
        total = correct = 0
        for variant in variants:
            fields = scorable_fields(scanned[variant], variant)
            score, _ = score_variant(
                variant, columns, fields, score_matrix(model, columns, fields, mask))
            correct += score.mapped_correct
            total += score.mapped_total
        rows.append((label, correct, total))
    return rows


def demonstration_curve(columns, scanned, variants):
    """7's demonstration-efficiency curve.

    It cannot start at one row: on a sheet portal a control's accessible name
    covers its column and its row, and the column's own name only emerges by
    comparing across rows.
    """
    import re

    rows = []
    sessions = []
    for path in DEMOS.glob("v0*rows.jsonl"):
        found = re.search(r"(\d+)rows", path.stem)
        if found:
            sessions.append((int(found.group(1)), path))

    for count, path in sorted(sessions):
        try:
            examples, _, _ = build_dataset(path, "v0_base")
        except Exception as exc:  # noqa: BLE001 - a session may be unusable
            rows.append((count, None, str(exc)[:40]))
            continue
        model, _ = train(examples)
        correct = total = 0
        for variant in variants:
            fields = scorable_fields(scanned[variant], variant)
            score, _ = score_variant(
                variant, columns, fields, score_matrix(model, columns, fields))
            correct += score.mapped_correct
            total += score.mapped_total
        rows.append((count, correct, total))
    return rows


def derived_field_report():
    """7's derived-field metrics, reported separately from mapping accuracy."""
    rows = []
    for path, scale in [(DEMOS / "v0_6rows.jsonl", "0-100"),
                        (DEMOS / "v6b_6rows.jsonl", "1-5"),
                        (DEMOS / "v0_base_3rows.jsonl", "0-100")]:
        if not path.exists():
            continue
        results, _ = induce_from_session(path, auto_confirm=True)
        entry = next((e for e in results if e["field"].startswith("Remarks")), None)
        if entry is None:
            rows.append((path.name, scale, "no candidate", "-", "-", "-"))
            continue

        detection, rule = entry["detection"], entry["rule"]
        if rule is None:
            rows.append((path.name, scale, detection.status, "-", "-", "-"))
            continue

        low, high = rule.observed_interval
        rows.append((
            path.name, scale, detection.status,
            f"{rule.operator} {'OK' if rule.operator == TRUE_OPERATOR[scale] else 'WRONG'}",
            f"{rule.cutoff:g} (err {abs(rule.cutoff - TRUE_CUTOFF[scale]):g})",
            f"{high - low:g}",
        ))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", type=Path, default=DEFAULT_SESSION)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    if not args.session.exists():
        raise SystemExit(f"no session at {args.session}")

    variants = list(VARIANTS)
    results, model, ablated, columns, scanned = run(args.session, variants)
    names = [n for n, _, _ in scorers(model, ablated)]

    print("\nFIELD-MAPPING ACCURACY  (correct / mappable columns)")
    print(f"{'variant':<20} " + " ".join(f"{n:>14}" for n in names))
    print("-" * (20 + 15 * len(names)))
    totals = {n: [0, 0] for n in names}
    for variant in variants:
        cells = []
        for name in names:
            score = results[variant][name]
            totals[name][0] += score.mapped_correct
            totals[name][1] += score.mapped_total
            cells.append(f"{score.mapped_correct}/{score.mapped_total}")
        print(f"{variant:<20} " + " ".join(f"{c:>14}" for c in cells))
    print("-" * (20 + 15 * len(names)))
    print(f"{'TOTAL':<20} " +
          " ".join(f"{str(totals[n][0]) + '/' + str(totals[n][1]):>14}" for n in names))

    print("\nABSTENTION QUALITY  (matcher-noPos; columns with no correct target)")
    print(f"{'variant':<20} {'correct':>10} {'recall':>9} {'precision':>11}  wrong")
    print("-" * 72)
    for variant in variants:
        score = results[variant]["matcher-noPos"]
        wrong = ", ".join(score.false_abstentions[:2]) or "-"
        print(f"{variant:<20} {score.abstain_correct:>4}/{score.abstain_total:<5} "
              f"{score.abstention_recall:>9.2f} {score.abstention_precision:>11.2f}  {wrong}")

    examples, _, _ = build_dataset(args.session, "v0_base")

    print("\nABLATIONS  (total correct across all variants)")
    print(f"{'setting':<26} {'correct':>10}")
    print("-" * 38)
    for label, correct, total in ablation_table(examples, columns, scanned, variants):
        print(f"{label:<26} {str(correct) + '/' + str(total):>10}")

    print("\nDEMONSTRATION EFFICIENCY  (rows demonstrated -> total correct)")
    print(f"{'rows':<8} {'correct':>10}")
    print("-" * 20)
    for count, correct, total in demonstration_curve(columns, scanned, variants):
        value = f"{correct}/{total}" if correct is not None else "unusable"
        print(f"{count:<8} {value:>10}")

    print("\nDERIVED-FIELD METRICS  (reported separately - a different capability)")
    print(f"{'session':<24} {'scale':<7} {'detection':<26} {'direction':<12} "
          f"{'cutoff':<16} {'interval'}")
    print("-" * 106)
    for row in derived_field_report():
        print(f"{row[0]:<24} {row[1]:<7} {row[2]:<26} {row[3]:<12} {row[4]:<16} {row[5]}")

    print("\nBASELINE 4 (LLM zero-shot): not configured - needs an API key")

    if args.json:
        payload = {
            variant: {name: {
                "mapped_correct": s.mapped_correct, "mapped_total": s.mapped_total,
                "mapped_wrong": s.mapped_wrong,
                "abstain_correct": s.abstain_correct, "abstain_total": s.abstain_total,
                "abstention_precision": round(s.abstention_precision, 4),
                "abstention_recall": round(s.abstention_recall, 4),
            } for name, s in scores.items()}
            for variant, scores in results.items()
        }
        path = args.json if args.json.is_absolute() else REPO / args.json
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {relative_to_repo(path)}")

    encoders.save_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
