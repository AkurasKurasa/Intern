"""Confirmation gate (3.3) - the user signs off before anything is trained.

"I observed: Midterm -> Midterm Exam, ..." for the user to confirm or correct.
This is what collapses residual recorder noise and makes the labels trustworthy,
and the count of corrections is itself a reportable metric (3.3, and the
corrections curve in 7).

Interactive by default; `apply_decisions` is the pure core so the gate is
testable and can be driven by a GUI later without changing what it means.
"""

import json
import sys
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from coworker_recorder.reconciler import (  # noqa: E402
    CONFIDENCE_CONFIRMED, Pair, reconcile_session, summarise,
)

ACCEPT = "accept"
REJECT = "reject"
CORRECT = "correct"


@dataclass
class Decision:
    """One user answer about one proposed field mapping."""
    target_label: str
    action: str
    source_header: str = ""     # required when action is CORRECT


@dataclass
class ConfirmationResult:
    pairs: list = dataclass_field(default_factory=list)
    corrections: int = 0
    rejections: int = 0
    accepted: int = 0

    def to_dict(self):
        return {
            "pairs": [p.to_dict() for p in self.pairs],
            "corrections": self.corrections,
            "rejections": self.rejections,
            "accepted": self.accepted,
        }


def proposals(result):
    """One proposal per target field: the header that won its rows, and how
    many rows agreed. A field whose rows disagree is shown as it is, not
    silently majority-voted."""
    grouped = {}
    for pair in result.pairs:
        grouped.setdefault(pair.target_label, []).append(pair)

    out = []
    for label, pairs in sorted(grouped.items()):
        counts = {}
        for pair in pairs:
            counts[pair.source_header] = counts.get(pair.source_header, 0) + 1
        winner = max(counts, key=counts.get)
        out.append({
            "target_label": label,
            "source_header": winner,
            "rows": sorted(p.row for p in pairs),
            "agreement": counts[winner] / len(pairs),
            "alternatives": sorted(h for h in counts if h != winner),
            "needs_review": any(p.confidence != "reconciled" for p in pairs),
        })
    return out


def apply_decisions(result, decisions):
    """Turn proposals plus user answers into confirmed pairs (2.3).

    Only confirmed pairs are training data. A rejected proposal produces no
    pair at all rather than a low-confidence one - 3.3 gates on trust, not on
    weighting.
    """
    by_label = {d.target_label: d for d in decisions}
    out = ConfirmationResult()

    for proposal in proposals(result):
        label = proposal["target_label"]
        decision = by_label.get(label)
        if decision is None or decision.action == REJECT:
            out.rejections += 1
            continue

        header = proposal["source_header"]
        if decision.action == CORRECT:
            if not decision.source_header:
                raise ValueError(f"correction for {label!r} names no source header")
            header = decision.source_header
            out.corrections += 1
        else:
            out.accepted += 1

        for row in proposal["rows"]:
            out.pairs.append(Pair(
                source_header=header,
                target_label=label,
                confidence=CONFIDENCE_CONFIRMED,
                row=row,
            ))
    return out


def render(result):
    lines = ["", "I observed:", ""]
    for proposal in proposals(result):
        flag = ""
        if proposal["agreement"] < 1.0:
            flag = f"  [rows disagree: also {', '.join(proposal['alternatives'])}]"
        elif proposal["needs_review"]:
            flag = "  [ambiguous join - please check]"
        lines.append(
            f"  {proposal['source_header']:<20} -> {proposal['target_label']}{flag}"
        )

    if result.derived_candidates:
        labels = sorted({c.target_label for c in result.derived_candidates})
        lines += ["", "No source cell was observed for:"]
        for label in labels:
            lines.append(f"  {label}  (candidate for a derived rule)")

    if result.unreconciled:
        lines += ["", "Unreconciled writes (not treated as derived):"]
        for item in result.unreconciled[:5]:
            lines.append(
                f"  {item['target_label']} = {item['value']!r} - {item['reason']}"
            )
    return "\n".join(lines) + "\n"


def prompt(result, input_fn=input, output=print):
    output(render(result))
    decisions = []
    for proposal in proposals(result):
        label, header = proposal["target_label"], proposal["source_header"]
        answer = input_fn(
            f"{header} -> {label}?  [Enter=yes / n=no / other column name]: "
        ).strip()
        if answer == "":
            decisions.append(Decision(label, ACCEPT))
        elif answer.lower() in ("n", "no"):
            decisions.append(Decision(label, REJECT))
        else:
            decisions.append(Decision(label, CORRECT, answer))
    return apply_decisions(result, decisions)


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--yes", action="store_true",
                    help="accept every proposal - for reproducible runs, not for a real demo")
    args = ap.parse_args()

    result = reconcile_session(args.session)
    print(summarise(result))

    if args.yes:
        confirmed = apply_decisions(
            result, [Decision(p["target_label"], ACCEPT) for p in proposals(result)]
        )
    else:
        confirmed = prompt(result)

    print(f"\n{confirmed.accepted} accepted, {confirmed.corrections} corrected, "
          f"{confirmed.rejections} rejected -> {len(confirmed.pairs)} confirmed pairs")

    out = args.out or args.session.with_suffix(".confirmed.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(confirmed.to_dict(), indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
