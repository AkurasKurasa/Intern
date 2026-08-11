"""The label-resolution cascade - single source of truth.

Architecture 3.2 defines a six-rule cascade for naming a form control, and 3.5
requires the Page Scanner to use *the same* cascade as the Browser Recorder.
Two implementations would drift, and a drift here is invisible: it shows up as
a mysterious train/inference gap in the matcher, not as a crash.

The anti-drift arrangement chosen here is 3.5's second option. The browser side
(Playwright now, the Chrome extension later) only ever extracts raw DOM context
and never decides a label. This module is the only place a label is decided.

    raw context (JS, browser)  ->  resolve(ctx)  ->  (label, rule)

`rule` is the 1-based index of the cascade rule that fired. 3.2 asks for it to
be recorded: "label resolved from <label for> in 78% of fields" is a reportable
statistic, and a variant that silently falls through to rule 6 is a bug worth
seeing.
"""

import re
from dataclasses import dataclass

# Cascade order, per architecture 3.2. The index into this list + 1 is the rule
# number reported downstream; do not reorder without updating the spec.
CASCADE = [
    ("label_for", "<label for> text"),
    ("label_wrapping", "wrapping <label> text"),
    ("aria", "aria-label / aria-labelledby"),
    ("placeholder", "placeholder"),
    ("preceding_text", "nearest preceding text (table <th> or previous sibling)"),
    ("name_attr", "name attribute, de-snake-cased"),
]

RULE_NAMES = {i + 1: desc for i, (_, desc) in enumerate(CASCADE)}


@dataclass(frozen=True)
class Resolved:
    label: str
    rule: int  # 1-6; 0 means nothing in the cascade produced a name

    @property
    def rule_description(self):
        return RULE_NAMES.get(self.rule, "unresolved")


def de_snake_case(value):
    """`midterm_exam` / `midterm-exam` / `midtermExam` -> `Midterm Exam`."""
    if not value:
        return ""
    spaced = re.sub(r"[_\-\.]+", " ", value)
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", spaced)
    spaced = re.sub(r"\s+", " ", spaced).strip()
    return " ".join(w[:1].upper() + w[1:] for w in spaced.split())


def normalize(text):
    """Collapse the whitespace the DOM leaves in text nodes."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def resolve(ctx):
    """Run the cascade over one control's raw context. First hit wins.

    `ctx` is the dict the browser side emits; every key is optional so the same
    function serves a recorder event and a scanner descriptor without either
    having to pad its payload.
    """
    for index, (key, _) in enumerate(CASCADE, start=1):
        if key == "name_attr":
            value = de_snake_case(ctx.get("name"))
        else:
            value = normalize(ctx.get(key))
        if value:
            return Resolved(value, index)
    return Resolved("", 0)


def common_label(labels):
    """The invariant part of a set of per-control labels.

    A sheet-style portal repeats one column of inputs down every row, and rule 3
    names each one for its column *and its row* - "Grade 0-100 Abad, Andrea A.",
    "Grade 0-100 Aguilar, Benjamin L.". The column's own label is what those
    share, so take the common leading token run rather than special-casing the
    aria-labelledby layout. Where every row resolves identically (rule 5, and
    every variant with unassociated headers) this returns that label unchanged.
    """
    token_lists = [l.split() for l in labels if l]
    if not token_lists:
        return ""
    if len(token_lists) == 1:
        return " ".join(token_lists[0])

    prefix = []
    for position in range(min(len(t) for t in token_lists)):
        token = token_lists[0][position]
        if all(t[position] == token for t in token_lists):
            prefix.append(token)
        else:
            break

    if prefix:
        return " ".join(prefix)

    # No shared prefix: fall back to the most common whole label, which is the
    # right answer when one stray row is labelled differently.
    counts = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return max(counts, key=counts.get)


def rule_histogram(resolutions):
    """Counts per cascade rule, for the 3.2 diagnostic statistic."""
    hist = {}
    for r in resolutions:
        hist[r.rule] = hist.get(r.rule, 0) + 1
    return dict(sorted(hist.items()))
