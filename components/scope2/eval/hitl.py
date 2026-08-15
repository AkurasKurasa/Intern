"""Milestone 9: escalation, correction, retraining - the corrections curve.

3.10 says any abstention pauses and asks the user to confirm that one mapping,
and that the answer is appended as a new confirmed pair and can trigger
retraining. That closes the loop, and the curve it produces - corrections needed
against demonstrations given - is the headline number for the human-in-the-loop
claim.

The user is simulated here by an oracle that answers from ground truth. That is
honest for a measurement harness: it tells you how many questions the system
would ask and whether it asks the right ones. It does not tell you whether a
real encoder answers correctly, and it is not evidence about usability.

Usage:
    python eval/hitl.py
"""

import argparse
import sys
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from eval.ground_truth import COLUMN_TO_KEY, scorable_fields  # noqa: E402
from executor.scanner import VARIANTS, scan_variants  # noqa: E402
from executor.sheet_reader import read_sheet  # noqa: E402
from features import encoders  # noqa: E402
from features.extractor import FEATURE_NAMES, Candidate, extract  # noqa: E402
from model.train import Example, build_dataset, score_matrix, train  # noqa: E402
from resolver.assign import resolve  # noqa: E402

SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"
DEMOS = REPO / "data" / "demos"
POSITION_FEATURE = FEATURE_NAMES.index("pos_rank_distance")


@dataclass
class Escalation:
    variant: str
    source_header: str
    proposed_label: str
    score: float
    margin: float
    answer: str = ""          # the field label the oracle says is correct
    accepted: bool = False    # True when the proposal was already right


@dataclass
class Round:
    index: int
    variant: str
    correct_before: int
    total: int
    escalations: list = dataclass_field(default_factory=list)
    correct_after: int = 0

    @property
    def questions(self):
        return len(self.escalations)

    @property
    def corrections(self):
        return sum(1 for e in self.escalations if not e.accepted and e.answer)


def oracle(column_header, fields):
    """The user's answer: which field this column really belongs in.

    Returns None when the column has no correct target, which is the answer
    "none of these" - a real encoder gives it, and a system that cannot accept
    it would be forced into a wrong mapping.
    """
    wanted = COLUMN_TO_KEY.get(column_header)
    if wanted is None:
        return None
    for field in fields:
        if field.truth_key == wanted:
            return field
    return None


def escalate(mapping, columns, fields, variant):
    """3.10: every abstention becomes one question."""
    by_header = {c.header: c for c in columns}
    questions = []

    for assignment in mapping.abstained:
        column = by_header.get(assignment.source_header)
        if column is None:
            continue
        answer = oracle(assignment.source_header, fields)
        questions.append(Escalation(
            variant=variant,
            source_header=assignment.source_header,
            proposed_label=assignment.target_label,
            score=assignment.score,
            margin=assignment.margin,
            answer=answer.label if answer else "",
            accepted=bool(answer) and answer.label == assignment.target_label,
        ))
    return questions


def examples_from_answers(escalations, columns, fields, feature_mask=None):
    """Turn answers into training examples.

    A "none of these" answer is as informative as a correction - it is a
    labelled negative for every field - so it is kept rather than discarded.
    """
    by_header = {c.header: c for c in columns}
    new_examples = []

    for question in escalations:
        column = by_header.get(question.source_header)
        if column is None:
            continue
        for field in fields:
            positive = bool(question.answer) and field.label == question.answer
            new_examples.append(Example(
                vector=extract(Candidate(column=column, field=field,
                                         n_columns=len(columns),
                                         n_fields=len(fields))),
                positive=positive,
                source_header=column.header,
                target_label=field.label,
                origin="escalation",
            ))
    return new_examples


def accuracy(mapping, columns, fields):
    key_of = {f.label: f.truth_key for f in fields}
    produced = {a.source_header: key_of.get(a.target_label) for a in mapping.auto}
    correct = total = 0
    for column in columns:
        wanted = COLUMN_TO_KEY.get(column.header)
        if wanted is None:
            continue
        total += 1
        if produced.get(column.header) == wanted:
            correct += 1
    return correct, total


def loop(session, variants, rounds=None, feature_mask={POSITION_FEATURE}):
    """Walk the variants, escalating and retraining as it goes.

    Each variant is met once, in order, with whatever the model has learned from
    the ones before. That is the realistic shape of the loop: corrections should
    make later interfaces cheaper, and the curve is what shows whether they do.
    """
    _, columns = read_sheet(SHEET, "SUMMARY", 11, "STUDENT NUMBER")
    columns = [c for c in columns if c.header]

    examples, _, _ = build_dataset(session, "v0_base")
    scanned = scan_variants(list(variants))

    history = []
    for index, variant in enumerate(variants, start=1):
        fields = scorable_fields(scanned[variant], variant)

        model, _ = train(examples, feature_mask=feature_mask)
        mapping = resolve(columns, fields,
                          score_matrix(model, columns, fields, feature_mask))
        before, total = accuracy(mapping, columns, fields)

        questions = escalate(mapping, columns, fields, variant)
        examples = examples + examples_from_answers(questions, columns, fields)

        retrained, _ = train(examples, feature_mask=feature_mask)
        after_mapping = resolve(columns, fields,
                                score_matrix(retrained, columns, fields, feature_mask))
        after, _ = accuracy(after_mapping, columns, fields)

        history.append(Round(index=index, variant=variant, correct_before=before,
                             total=total, escalations=questions, correct_after=after))
        if rounds and index >= rounds:
            break

    return history


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", type=Path, default=DEMOS / "v0_6rows.jsonl")
    ap.add_argument("--rounds", type=int, default=None)
    args = ap.parse_args()

    if not args.session.exists():
        raise SystemExit(f"no session at {args.session}")

    history = loop(args.session, list(VARIANTS), args.rounds)

    print("\nCORRECTIONS CURVE  (each variant met once, in order, "
          "carrying what was learned)")
    print(f"{'#':<3} {'variant':<20} {'before':>8} {'questions':>10} "
          f"{'corrections':>12} {'after':>7}")
    print("-" * 68)

    cumulative = 0
    for round_ in history:
        cumulative += round_.corrections
        print(f"{round_.index:<3} {round_.variant:<20} "
              f"{str(round_.correct_before) + '/' + str(round_.total):>8} "
              f"{round_.questions:>10} {round_.corrections:>12} "
              f"{str(round_.correct_after) + '/' + str(round_.total):>7}")

    total_questions = sum(r.questions for r in history)
    total_corrections = sum(r.corrections for r in history)
    print("-" * 68)
    print(f"{'':<24} {'':>8} {total_questions:>10} {total_corrections:>12}")

    print(f"\n{total_questions} escalations over {len(history)} interfaces, "
          f"{total_corrections} of which were corrections rather than "
          f"confirmations.")
    print("The user is an oracle answering from ground truth: this measures how "
          "many questions get asked and whether they are the right ones, not "
          "whether a real encoder answers them correctly.")

    encoders.save_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
