"""Reconciler (3.3) - two event streams become ground-truth pairs.

The join is on value *and* time proximity. Value alone breaks on duplicates -
two students with the same grade - and time alone breaks on a stale clipboard,
where the user copies once and types the rest.

A browser write with no matching Excel event is not discarded. It is the
evidence that a field is computed rather than copied, and it is handed to the
Rule Inducer as a derived candidate. The false positive 3.3 warns about is a
user who reads a value off the screen and types it: same signature, different
meaning. So a candidate is only called derived when it is a closed-option field
*and* every demonstrated row agrees; anything else is routed to the confirmation
UI as an ordinary unreconciled write.
"""

import sys
from collections import defaultdict
from dataclasses import dataclass, field as dataclass_field, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from coworker_recorder.events import parse_time, read_session  # noqa: E402

WINDOW_SECONDS = 60  # 3.3

CONFIDENCE_RECONCILED = "reconciled"
CONFIDENCE_NEEDS_REVIEW = "needs_review"
CONFIDENCE_CONFIRMED = "confirmed"


@dataclass
class Pair:
    """2.3 - a ground-truth pair, before or after the confirmation gate."""
    source_header: str
    target_label: str
    confidence: str
    row: int

    def to_dict(self):
        return asdict(self)


@dataclass
class DerivedCandidate:
    """A browser write with no source cell. Not yet a rule - just a suspicion."""
    target_label: str
    row: int
    value: str
    seq: int
    options: list = dataclass_field(default_factory=list)
    closed_option_field: bool = False


@dataclass
class Reconciliation:
    pairs: list = dataclass_field(default_factory=list)
    derived_candidates: list = dataclass_field(default_factory=list)
    unreconciled: list = dataclass_field(default_factory=list)
    voided: list = dataclass_field(default_factory=list)

    @property
    def confirmed_pairs(self):
        return [p for p in self.pairs if p.confidence == CONFIDENCE_CONFIRMED]

    def headers_for(self, target_label):
        return {p.source_header for p in self.pairs if p.target_label == target_label}


def normalize(value):
    """Values are compared as text, but 85 and 85.0 are the same cell."""
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    try:
        return f"{float(text):g}"
    except (TypeError, ValueError):
        return text.casefold()


def last_write_per_row(browser_events):
    """3.2 correction handling: if a field is cleared or overwritten within the
    same row, the earlier candidate is voided. Last write wins per row."""
    latest, voided = {}, []
    for event in browser_events:
        key = (event.row, event.label or event.context.get("id"))
        if key in latest:
            voided.append(latest[key])
        latest[key] = event

    kept = list(latest.values())
    kept.sort(key=lambda e: (e.row, e.seq))

    # A field cleared to empty is a retraction, not a value.
    cleared = [e for e in kept if normalize(e.value) == ""]
    kept = [e for e in kept if normalize(e.value) != ""]
    return kept, voided + cleared


def match_excel_event(browser_event, excel_events, window=WINDOW_SECONDS):
    """The Excel event with this value and the greatest timestamp before it.

    Returns (event, ambiguous). Ambiguity is a tie on value inside the window:
    3.3 says prefer the later one, and flag it when even that does not settle.
    """
    target = normalize(browser_event.value)
    written_at = parse_time(browser_event.t)

    candidates = []
    for event in excel_events:
        selected_at = parse_time(event.t)
        if selected_at >= written_at:
            continue
        if (written_at - selected_at).total_seconds() > window:
            continue
        if normalize(event.value) == target:
            candidates.append((selected_at, event))

    if not candidates:
        return None, False

    candidates.sort(key=lambda pair: pair[0])
    latest_time, latest_event = candidates[-1]

    # Prefer the later one; ambiguous only if two share that same instant and
    # disagree about which column they came from.
    tied = [e for t, e in candidates
            if t == latest_time and e.header != latest_event.header]
    return latest_event, bool(tied)


def reconcile(excel_events, browser_events, window=WINDOW_SECONDS):
    result = Reconciliation()

    kept, voided = last_write_per_row(browser_events)
    result.voided = [
        {"row": e.row, "target_label": e.label, "value": e.value, "seq": e.seq}
        for e in voided
    ]

    unmatched_by_label = defaultdict(list)

    for event in kept:
        match, ambiguous = match_excel_event(event, excel_events, window)
        if match is None:
            unmatched_by_label[event.label].append(event)
            continue

        result.pairs.append(Pair(
            source_header=match.header,
            target_label=event.label,
            confidence=CONFIDENCE_NEEDS_REVIEW if ambiguous else CONFIDENCE_RECONCILED,
            row=event.row,
        ))

    demonstrated_rows = {e.row for e in kept}

    for label, events in unmatched_by_label.items():
        closed = all(e.is_closed_option_field for e in events)
        every_row = {e.row for e in events} == demonstrated_rows

        # 3.3: closed-option field AND consistent across every demonstrated row.
        # Otherwise it may simply be a value the user typed by hand.
        if closed and every_row and len(demonstrated_rows) > 1:
            for event in events:
                result.derived_candidates.append(DerivedCandidate(
                    target_label=label,
                    row=event.row,
                    value=event.value,
                    seq=event.seq,
                    options=list(event.options or []),
                    closed_option_field=True,
                ))
        else:
            for event in events:
                result.unreconciled.append({
                    "target_label": label,
                    "row": event.row,
                    "value": event.value,
                    "seq": event.seq,
                    "reason": (
                        "not a closed-option field" if not closed
                        else "not written in every demonstrated row"
                        if not every_row else "only one row demonstrated"
                    ),
                })

    return result


def reconcile_session(path, window=WINDOW_SECONDS):
    excel, browser = read_session(path)
    return reconcile(excel, browser, window)


def summarise(result):
    by_target = defaultdict(set)
    for pair in result.pairs:
        by_target[pair.target_label].add(pair.source_header)

    lines = [f"{len(result.pairs)} pairs over {len(by_target)} fields"]
    for label, headers in sorted(by_target.items()):
        joined = ", ".join(sorted(headers))
        lines.append(f"  {label:<28} <- {joined}")
    if result.derived_candidates:
        labels = sorted({c.target_label for c in result.derived_candidates})
        lines.append(f"  derived candidates: {', '.join(labels)}")
    if result.unreconciled:
        lines.append(f"  unreconciled writes: {len(result.unreconciled)}")
    if result.voided:
        lines.append(f"  voided by correction: {len(result.voided)}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", type=Path)
    ap.add_argument("--window", type=int, default=WINDOW_SECONDS)
    args = ap.parse_args()
    print(summarise(reconcile_session(args.session, args.window)))
