"""Derived-field dependency detection (3.8 step 1).

A derived candidate is a field the demonstrator filled with no source cell
behind it. This module asks the next question: which *other field on the same
form* explains it?

The test is perfect separation. A numeric field is a valid driver only if its
demonstrated values put every row of one option class strictly on one side of
some cut and every row of the other class on the other. Anything less is not a
threshold rule, and if several fields separate the classes equally well the
answer is to abstain and ask - with three demonstrated rows, coincidence is
cheap.
"""

import sys
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


@dataclass
class Separation:
    """How cleanly one candidate driver splits the observed classes."""
    driver_label: str
    clean: bool
    low_class: str = ""      # the class sitting below the cut
    high_class: str = ""
    interval: tuple = ()     # (max of low side, min of high side) - open range
    values: dict = dataclass_field(default_factory=dict)   # row -> numeric value

    @property
    def width(self):
        if not self.interval:
            return None
        return self.interval[1] - self.interval[0]


@dataclass
class Detection:
    field: str
    outcomes: dict = dataclass_field(default_factory=dict)   # row -> option chosen
    drivers: list = dataclass_field(default_factory=list)    # clean Separations
    rejected: list = dataclass_field(default_factory=list)   # unclean ones
    status: str = "undetermined"
    reason: str = ""

    @property
    def driver(self):
        return self.drivers[0] if len(self.drivers) == 1 else None


STATUS_OK = "driver_found"
STATUS_AMBIGUOUS = "ambiguous_driver"
STATUS_NO_DRIVER = "no_driver"
STATUS_ONE_CLASS = "insufficient_demonstration"


def as_number(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def row_values(browser_events):
    """Per row, the last value written into each field label."""
    rows = {}
    for event in sorted(browser_events, key=lambda e: (e.row, e.seq)):
        rows.setdefault(event.row, {})[event.label] = event.value
    return rows


def separate(values_by_row, outcomes_by_row):
    """Do these numeric values split the outcome classes cleanly?

    Returns a Separation. `interval` is the open range the demonstrations
    actually constrain - every cut inside it is equally consistent, which is
    what 3.8 step 2 has to reason about rather than hiding behind a point.
    """
    grouped = {}
    for row, outcome in outcomes_by_row.items():
        number = as_number(values_by_row.get(row))
        if number is None:
            return None
        grouped.setdefault(outcome, []).append(number)

    if len(grouped) != 2:
        return None

    (class_a, values_a), (class_b, values_b) = sorted(grouped.items())

    # Whichever class sits lower defines the direction; overlap means no cut.
    if max(values_a) < min(values_b):
        low, high = class_a, class_b
        interval = (max(values_a), min(values_b))
    elif max(values_b) < min(values_a):
        low, high = class_b, class_a
        interval = (max(values_b), min(values_a))
    else:
        return None

    return Separation(
        driver_label="",
        clean=True,
        low_class=low,
        high_class=high,
        interval=interval,
        values={row: as_number(v) for row, v in values_by_row.items()},
    )


def detect(candidate_label, outcomes_by_row, rows, exclude=()):
    """Find which field drives `candidate_label`.

    `rows` maps row -> {field label: value}. Only fields demonstrated in every
    row that has an outcome are considered - a field the user filled once
    cannot be shown to explain anything.
    """
    detection = Detection(field=candidate_label, outcomes=dict(outcomes_by_row))

    classes = set(outcomes_by_row.values())
    if len(classes) < 2:
        detection.status = STATUS_ONE_CLASS
        detection.reason = (
            f"every demonstrated row chose {next(iter(classes), 'nothing')!r}; "
            "demonstrate at least one row of the other outcome before a "
            "threshold can be induced"
        )
        return detection

    excluded = set(exclude) | {candidate_label}
    relevant_rows = [r for r in outcomes_by_row if r in rows]

    shared = None
    for row in relevant_rows:
        labels = {label for label in rows[row] if label not in excluded}
        shared = labels if shared is None else (shared & labels)
    shared = shared or set()

    for label in sorted(shared):
        values = {row: rows[row].get(label) for row in relevant_rows}
        if any(as_number(v) is None for v in values.values()):
            continue

        separation = separate(values, outcomes_by_row)
        if separation is None:
            detection.rejected.append(Separation(driver_label=label, clean=False))
            continue

        separation.driver_label = label
        detection.drivers.append(separation)

    if not detection.drivers:
        detection.status = STATUS_NO_DRIVER
        detection.reason = (
            "no demonstrated numeric field separates the outcomes; this may not "
            "be a threshold rule"
        )
    elif len(detection.drivers) > 1:
        detection.status = STATUS_AMBIGUOUS
        names = ", ".join(d.driver_label for d in detection.drivers)
        detection.reason = (
            f"{names} separate the outcomes equally well; more demonstrated "
            "rows are needed to tell them apart"
        )
    else:
        detection.status = STATUS_OK
        detection.reason = f"{detection.drivers[0].driver_label} separates the outcomes"

    return detection


def outcomes_from_candidates(derived_candidates, label):
    return {c.row: c.value for c in derived_candidates if c.target_label == label}
