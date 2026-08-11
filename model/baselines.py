"""Baselines to beat (3.7), and the Milestone 5 check.

Three of the four baselines 3.7 lists are implemented:

  1. exact / normalised string match
  2. cosine similarity only, with the same Hungarian assignment
  3. the trained matcher

The fourth, LLM zero-shot, is a reference ceiling and needs an API key. It is
deliberately absent rather than stubbed: a fabricated ceiling is worse than a
missing one. `llm_baseline` raises so it cannot be reported by accident.

Every baseline produces a score matrix and goes through the same Resolver, so
the comparison isolates the scorer rather than the assignment.

Usage:
    python model/baselines.py
"""

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from executor.scanner import KIND_INPUT, scan_variants  # noqa: E402
from executor.sheet_reader import read_sheet  # noqa: E402
from features import encoders  # noqa: E402
from features.extractor import levenshtein_ratio  # noqa: E402
from model.matcher import load as load_model  # noqa: E402
from model.train import build_dataset, score_matrix, train  # noqa: E402
from resolver.assign import render, resolve  # noqa: E402

SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"
SESSION = REPO / "data" / "demos" / "v0_base_3rows.jsonl"
MODEL_PATH = REPO / "data" / "models" / "matcher.pt"


def normalise(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def string_match_matrix(columns, fields):
    """Baseline 1. Exact after normalisation, else a Levenshtein ratio.

    The architecture predicts this fails on day one: not one base pairing is an
    exact match, and Program -> Course is a pure synonym.
    """
    matrix = []
    for column in columns:
        row = []
        for field in fields:
            left, right = normalise(column.header), normalise(field.label)
            row.append(1.0 if left == right else levenshtein_ratio(left, right))
        matrix.append(row)
    return matrix


def cosine_matrix(columns, fields):
    """Baseline 2. Embedding similarity alone - no learning, same assignment."""
    return [[encoders.similarity(c.header, f.label) for f in fields]
            for c in columns]


def llm_baseline(*args, **kwargs):
    raise NotImplementedError(
        "baseline 4 (LLM zero-shot) needs an API key and is not configured; "
        "report it as absent rather than substituting a value"
    )


def evaluate(matrix, columns, fields, truth, derived_labels, label):
    mapping = resolve(columns, fields, matrix, derived_labels)
    produced = mapping.as_truth()

    correct = sum(1 for field_label, header in truth.items()
                  if produced.get(field_label) == header)
    wrong = [(k, v) for k, v in produced.items() if truth.get(k) != v]

    return {
        "baseline": label,
        "correct": correct,
        "of": len(truth),
        "wrong": wrong,
        "abstained": len(mapping.abstained),
        "mapping": mapping,
    }


def run(variant="v0_base", session=SESSION, sheet=SHEET):
    _, columns = read_sheet(sheet, "SUMMARY", 11, "STUDENT NUMBER")
    columns = [c for c in columns if c.header]

    examples, truth, derived = build_dataset(session, variant, sheet)
    fields = [d for d in scan_variants([variant])[variant] if d.kind == KIND_INPUT]
    scorable = [f for f in fields if f.label not in derived]

    if MODEL_PATH.exists():
        model, _ = load_model(MODEL_PATH)
    else:
        model, _ = train(examples)

    results = [
        evaluate(string_match_matrix(columns, scorable), columns, scorable,
                 truth, derived, "1. string match"),
        evaluate(cosine_matrix(columns, scorable), columns, scorable,
                 truth, derived, "2. cosine + Hungarian"),
        evaluate(score_matrix(model, columns, scorable), columns, scorable,
                 truth, derived, "3. trained matcher"),
    ]
    return results, truth, derived


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", default="v0_base")
    ap.add_argument("--session", type=Path, default=SESSION)
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args()

    results, truth, derived = run(args.variant, args.session)

    print(f"\nvariant {args.variant}")
    print("truth   " + ", ".join(f"{v} -> {k}" for k, v in sorted(truth.items())))
    print(f"derived {sorted(derived)} (held out of the matrix)\n")

    print(f"{'baseline':<24} {'correct':<10} {'abstained':<11} wrong")
    print("-" * 72)
    for result in results:
        wrong = ", ".join(f"{v} -> {k}" for k, v in result["wrong"]) or "-"
        print(f"{result['baseline']:<24} {result['correct']}/{result['of']:<8} "
              f"{result['abstained']:<11} {wrong}")

    print("\n4. LLM zero-shot          not configured (needs an API key)")

    if args.detail:
        for result in results:
            print(f"\n--- {result['baseline']} ---")
            print(render(result["mapping"]))

    encoders.save_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
