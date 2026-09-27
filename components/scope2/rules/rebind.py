"""Point a rule learned on one portal at the fields of the portal being filled.

A rule is learned from a demonstration on V0, so it names V0's labels: "set
Remarks from Grade 0-100". Another portal may call those fields something
else -- V2 says "Academic Standing" and "Final Rating 0-100" -- and then the
rule named fields that did not exist, and the run crashed in fill_order()
with "derived fields have unmet dependencies". Found by filling every variant
end to end; the crash predates the source -> LLM change.

The fix anchors each end of the rule on something that survives relabelling:

  * its INPUT on the sheet column the demonstration filled that field from
    (FINAL GRADE). On any portal, the input is whichever field the current
    mapping writes that column into.
  * its OUTPUT on what it writes. The rule's outcomes (Passed / Failed) must
    be options of the field; if the demonstrated label is not on the page,
    the one choice field offering every outcome is used.

Neither end is guessed: no unique answer means the rule is skipped and its
field left empty, said out loud, rather than written from the wrong input.
Nothing here names a field, a column or a variant.
"""

from collections import Counter
from typing import Iterable, Optional

from executor.runner import resolve_option


def driver_column(pairs: Iterable, demo_label: str) -> Optional[str]:
    """The sheet column the demonstration filled `demo_label` from.

    `pairs` is the reconciler's (source_header, target_label) list, one per
    demonstrated write. The most common header wins; a tie is no answer.
    """
    counts = Counter(p.source_header for p in pairs
                     if p.target_label == demo_label and p.source_header)
    if not counts:
        return None
    ranked = counts.most_common(2)
    if len(ranked) == 2 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]


def rebind_target(rule: dict, fields) -> Optional[str]:
    """The field on this portal the rule writes into, or None."""
    outcomes = [rule["if_true"], rule["if_false"]]

    def takes_all(field):
        return all(resolve_option(o, field.options or []) for o in outcomes)

    by_label = {f.label: f for f in fields}
    same = by_label.get(rule["field"])
    if same is not None and takes_all(same):
        return same.label
    fits = [f for f in fields if f.options and takes_all(f)]
    return fits[0].label if len(fits) == 1 else None


def rebind_driver(column: Optional[str], assignments) -> Optional[str]:
    """The field on this portal the mapping fills from `column`, or None."""
    if not column:
        return None
    hits = [a["target_label"] for a in assignments if a["source_header"] == column]
    return hits[0] if len(hits) == 1 else None
