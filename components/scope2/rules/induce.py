"""Threshold induction: direction, cutoff, interval (3.8 step 2 and 3).

The demonstrations constrain an *interval*, not a point. Given (88, Passed),
(67, Failed), (91, Passed), any cut in (67, 88] is consistent, and pretending
otherwise is how a grading tool silently mislabels every borderline student.

Two things follow, and both are load-bearing:

  * The operator direction is induced, never assumed. A 0-100 scale passes
    above the cut; the 1.00-5.00 scale many Philippine universities use passes
    *below* it, because 1.00 is the highest mark. Hard-coding ">=" would be
    correct on one instrument and exactly backwards on the other.
  * The proposed cutoff is snapped to the most plausible round value inside the
    interval, and `observed_interval` travels with it so the uncertainty is
    visible rather than hidden behind a number.

Nothing here confirms a rule. 3.8 step 3 is a human decision, and
`Rule.status` stays "proposed" until someone says otherwise.
"""

import sys
from dataclasses import dataclass, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from rules.detect import STATUS_OK  # noqa: E402

STATUS_PROPOSED = "proposed"
STATUS_CONFIRMED = "confirmed"

# Preference order for snapping a cutoff, per 3.8: multiples of 5, then whole
# numbers, then the midpoint of the interval.
SNAP_STEPS = (5, 1)


@dataclass
class Rule:
    """2.4. `depends_on_field` names a form field, never a sheet column, so the
    rule survives its driver's source column being relabelled."""
    field: str
    kind: str = "threshold"
    depends_on_field: str = ""
    operator: str = ">="
    cutoff: float = 0.0
    if_true: str = ""
    if_false: str = ""
    observed_interval: tuple = ()
    status: str = STATUS_PROPOSED

    def to_dict(self):
        payload = asdict(self)
        payload["observed_interval"] = list(self.observed_interval)
        return payload

    def evaluate(self, value):
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return None
        hit = {
            ">=": number >= self.cutoff,
            ">": number > self.cutoff,
            "<=": number <= self.cutoff,
            "<": number < self.cutoff,
        }[self.operator]
        return self.if_true if hit else self.if_false

    def describe(self):
        """3.8 step 3: plain language, with the interval shown."""
        low, high = self.observed_interval
        comparison = {
            ">=": "is", ">": "is above",
            "<=": "is", "<": "is below",
        }[self.operator]
        bound = {
            ">=": f"{self.cutoff:g} or higher",
            ">": f"{self.cutoff:g}",
            "<=": f"{self.cutoff:g} or lower",
            "<": f"{self.cutoff:g}",
        }[self.operator]
        return (
            f"{self.field} is set to {self.if_true} when "
            f"{self.depends_on_field} {comparison} {bound}, "
            f"otherwise {self.if_false}. "
            f"The demonstrations only narrow the cutoff to between "
            f"{low:g} and {high:g}."
        )


def snap_cutoff(low, high, upper_closed=True):
    """The most plausible round value inside the interval the demos constrain.

    Which end is closed depends on the operator, and getting it wrong proposes a
    cutoff the demonstrations contradict:

      ">=" constrains (low, high] - `low` was demonstrated on the other side, so
           it cannot be the cut, while `high` was demonstrated to pass.
      "<=" constrains [low, high) - the mirror image.

    Among qualifying round values, the one chosen is whichever makes the
    reference outcome most inclusive: the lowest for ">=", the highest for "<=".
    That is the least surprising reading of "75 and above passes" and of "3.00
    and below passes" alike.
    """
    for step in SNAP_STEPS:
        candidates = []
        multiple = (int(low // step)) * step
        while multiple <= high:
            value = float(multiple)
            inside = (low < value <= high) if upper_closed else (low <= value < high)
            if inside:
                candidates.append(value)
            multiple += step
        if candidates:
            return min(candidates) if upper_closed else max(candidates)
    return (low + high) / 2.0


def induce(detection, driver_label=None, options=None):
    """Turn a clean detection into a proposed rule.

    Returns None when the detection did not settle on a single driver - 3.8 is
    explicit that a guess here is worse than a question.

    The rule is always stated in terms of one reference outcome (the field's
    first option, or the class demonstrated above the cut when no option list is
    supplied), and the *operator* is chosen to suit. That is what makes
    direction an observable: on a 0-100 scale Passed comes out ">= 75", and on
    the 1.00-5.00 scale the same demonstration comes out "<= 3.00". Reporting
    direction accuracy means comparing that operator, so it must not be a
    formatting accident.
    """
    if detection.status != STATUS_OK:
        return None

    separation = detection.driver
    if separation is None:
        return None
    if driver_label and separation.driver_label != driver_label:
        return None

    low, high = separation.interval
    reference = separation.high_class
    if options:
        for option in options:
            if option in (separation.high_class, separation.low_class):
                reference = option
                break

    if reference == separation.high_class:
        operator, if_true, if_false = ">=", separation.high_class, separation.low_class
        cutoff = snap_cutoff(low, high, upper_closed=True)
    else:
        # The reference outcome sits below the cut, so the comparison flips and
        # the closed end of the interval moves with it.
        operator, if_true, if_false = "<=", separation.low_class, separation.high_class
        cutoff = snap_cutoff(low, high, upper_closed=False)

    return Rule(
        field=detection.field,
        depends_on_field=separation.driver_label,
        operator=operator,
        cutoff=cutoff,
        if_true=if_true,
        if_false=if_false,
        observed_interval=(low, high),
        status=STATUS_PROPOSED,
    )


def confirm(rule, cutoff=None):
    """3.8 step 3. A rule is executable only after a human says so, and a
    corrected cutoff must still sit inside what was demonstrated."""
    if cutoff is not None:
        low, high = rule.observed_interval
        inside = (low < cutoff <= high) if rule.operator.startswith(">")             else (low <= cutoff < high)
        if not inside:
            raise ValueError(
                f"cutoff {cutoff:g} lies outside the demonstrated interval "
                f"({low:g}, {high:g})"
            )
        rule.cutoff = float(cutoff)
    rule.status = STATUS_CONFIRMED
    return rule


def check_against_demonstrations(rule, detection):
    """Does the proposed rule reproduce every row it was induced from?

    A rule that cannot replay its own demonstrations is not a rule, and this is
    cheap enough to run every time.
    """
    separation = detection.driver
    failures = []
    for row, outcome in detection.outcomes.items():
        value = separation.values.get(row)
        if rule.evaluate(value) != outcome:
            failures.append((row, value, outcome, rule.evaluate(value)))
    return failures
