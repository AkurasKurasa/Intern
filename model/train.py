"""Train the matcher from confirmed demonstration pairs (3.7).

Training data is built exactly as 3.7 describes: every confirmed pair is a
positive, and every other (column, field) combination from the same
demonstration is a negative. The candidate grid is what makes the classes
imbalanced, which is why the loss weights positives.

Augmentation is the label-paraphrase step 3.7 asks to be recorded in Chapter 3:
a demonstrated field label is restated ("Final Rating", "Final Grade", "Grade")
and the positive is re-extracted against the paraphrase. This expands the
positive set without asking the user for more demonstrations. Paraphrases are
applied to the *field label only* - never to the source header, which is what
the system must generalise over.

Usage:
    python model/train.py --session data/demos/v0_base_3rows.jsonl
"""

import argparse
import random
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import torch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from executor.scanner import KIND_INPUT, scan_variants  # noqa: E402
from executor.sheet_reader import read_sheet  # noqa: E402
from features import encoders  # noqa: E402
from features.extractor import Candidate, extract  # noqa: E402
from model.matcher import Matcher, loss_function, save  # noqa: E402
from recorder.confirm import ACCEPT, Decision, apply_decisions, proposals  # noqa: E402
from recorder.reconciler import reconcile_session  # noqa: E402

MODELS_DIR = REPO / "data" / "models"
SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"

EPOCHS = 400
LEARNING_RATE = 0.01
SEED = 20260806

# Restatements of a demonstrated field label. Generic, not portal-specific: each
# is a way a form might name the same thing, so the positive survives rewording.
PARAPHRASE_RULES = [
    ("Grade", ["Final Grade", "Rating", "Final Rating", "Mark"]),
    ("Course", ["Program", "Degree Program", "Course of Study"]),
    ("Year", ["Year Level", "Yr Level", "Level"]),
    ("Remarks", ["Status", "Academic Standing", "Result"]),
    ("Recommendations", ["Adviser Notes", "Notes", "Comments"]),
    ("Section", ["Class Section", "Block"]),
    ("Adviser", ["Faculty Adviser", "Mentor"]),
]


@dataclass
class Example:
    vector: list
    positive: bool
    source_header: str
    target_label: str
    origin: str  # "observed" or "paraphrase"


def paraphrases_for(label):
    """Restatements of a field label, matched on its leading word."""
    head = label.split()[0] if label.split() else label
    for key, options in PARAPHRASE_RULES:
        if head.casefold() == key.casefold():
            return options
    return []


def confirmed_pairs(session_path):
    """Reconcile a session and accept every proposal, as a demo run would."""
    result = reconcile_session(session_path)
    confirmed = apply_decisions(
        result, [Decision(p["target_label"], ACCEPT) for p in proposals(result)]
    )
    mapping = {}
    for pair in confirmed.pairs:
        mapping[pair.target_label] = pair.source_header
    return mapping, result


def build_dataset(session_path, variant="v0_base", sheet=SHEET, augment=True):
    truth, reconciliation = confirmed_pairs(session_path)

    _, columns = read_sheet(sheet, "SUMMARY", 11, "STUDENT NUMBER")
    columns = [c for c in columns if c.header]

    fields = [d for d in scan_variants([variant])[variant] if d.kind == KIND_INPUT]

    # 3.9 step 0: a field with a derived rule never enters the score matrix, so
    # it must not enter training either - it would teach the model that Remarks
    # legitimately pairs with some column.
    derived = {c.target_label for c in reconciliation.derived_candidates}
    fields = [f for f in fields if f.label not in derived]

    examples = []
    for column in columns:
        for field in fields:
            positive = truth.get(field.label) == column.header
            candidate = Candidate(column=column, field=field,
                                  n_columns=len(columns), n_fields=len(fields))
            examples.append(Example(
                vector=extract(candidate),
                positive=positive,
                source_header=column.header,
                target_label=field.label,
                origin="observed",
            ))

            if positive and augment:
                for paraphrase in paraphrases_for(field.label):
                    restated = replace(field, label=paraphrase, header_text=paraphrase)
                    examples.append(Example(
                        vector=extract(Candidate(
                            column=column, field=restated,
                            n_columns=len(columns), n_fields=len(fields))),
                        positive=True,
                        source_header=column.header,
                        target_label=paraphrase,
                        origin="paraphrase",
                    ))

    return examples, truth, derived


def train(examples, epochs=EPOCHS, learning_rate=LEARNING_RATE, seed=SEED,
          feature_mask=None):
    """`feature_mask` zeroes chosen feature indices - the 7 ablations."""
    torch.manual_seed(seed)
    random.seed(seed)

    x = torch.tensor([e.vector for e in examples], dtype=torch.float32)
    y = torch.tensor([1.0 if e.positive else 0.0 for e in examples])

    if feature_mask:
        for index in feature_mask:
            x[:, index] = 0.0

    model = Matcher(x.shape[1])
    criterion = loss_function()
    optimiser = torch.optim.Adam(model.parameters(), lr=learning_rate)

    model.train()
    for _ in range(epochs):
        optimiser.zero_grad()
        loss = criterion(model(x), y)
        loss.backward()
        optimiser.step()

    model.eval()
    return model, float(loss.item())


def score_matrix(model, columns, fields, feature_mask=None):
    """Probability for every (column, field) pair, as the Resolver needs it."""
    vectors, index = [], []
    for ci, column in enumerate(columns):
        for fi, field in enumerate(fields):
            vectors.append(extract(Candidate(
                column=column, field=field,
                n_columns=len(columns), n_fields=len(fields))))
            index.append((ci, fi))

    x = torch.tensor(vectors, dtype=torch.float32)
    if feature_mask:
        for position in feature_mask:
            x[:, position] = 0.0

    probabilities = model.probability(x).tolist()

    matrix = [[0.0] * len(fields) for _ in columns]
    for (ci, fi), probability in zip(index, probabilities):
        matrix[ci][fi] = probability
    return matrix


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", type=Path,
                    default=REPO / "data" / "demos" / "v0_base_3rows.jsonl")
    ap.add_argument("--variant", default="v0_base")
    ap.add_argument("--epochs", type=int, default=EPOCHS)
    ap.add_argument("--no-augment", action="store_true")
    ap.add_argument("--out", type=Path, default=MODELS_DIR / "matcher.pt")
    args = ap.parse_args()

    if not args.session.exists():
        raise SystemExit(
            f"no session at {args.session}; run recorder/demo_session.py first"
        )

    examples, truth, derived = build_dataset(
        args.session, args.variant, augment=not args.no_augment
    )
    observed = [e for e in examples if e.origin == "observed"]
    positives = [e for e in examples if e.positive]

    print(f"session   {args.session.relative_to(REPO)}")
    print("truth     " + ", ".join(f"{v} -> {k}" for k, v in sorted(truth.items())))
    print(f"held out  {sorted(derived)} (derived, excluded from the matrix)")
    print(f"examples  {len(observed)} observed "
          f"+ {len(examples) - len(observed)} paraphrased "
          f"= {len(examples)} ({len(positives)} positive)")

    model, loss = train(examples, args.epochs)
    print(f"trained   {model.parameter_count()} params, final loss {loss:.4f}")

    path = save(model, args.out, metadata={
        "session": str(args.session.relative_to(REPO)),
        "variant": args.variant,
        "truth": truth,
        "examples": len(examples),
        "final_loss": loss,
    })
    print(f"saved     {path.relative_to(REPO)}")
    encoders.save_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
