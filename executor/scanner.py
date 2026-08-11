"""Page Scanner - a live page becomes a list of field descriptors (3.5).

The portal is a sheet: one column of inputs repeated down fifty rows. Emitting
one descriptor per input, as 3.5 originally reads, would hand the Resolver 250
targets for 5 source columns and break the one-to-one assignment in 3.9. So
controls are grouped by the column they sit in, and one descriptor is emitted
per *column of inputs*. A control outside a table is a column of one, which is
what a conventional single-record form reduces to.

Labels are never decided here. The browser emits raw context
(executor/extract_context.js) and labeling/resolve.py runs the cascade.

Usage:
    python executor/scanner.py                       # scan every variant
    python executor/scanner.py v0_base --json out.json
"""

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from labeling.resolve import (  # noqa: E402
    Resolved, common_label, resolve, rule_histogram, RULE_NAMES,
)

MOCKSITE = REPO / "mocksite"
EXTRACT_JS = (Path(__file__).parent / "extract_context.js").read_text(encoding="utf-8")

# The chromium that shipped with the environment's existing Playwright install.
CHROMIUM = (Path.home() / "AppData" / "Local" / "ms-playwright"
            / "chromium-1208" / "chrome-win64" / "chrome.exe")

VARIANTS = [
    "v0_base", "v1_reordered", "v2_relabeled", "v3_extra_fields",
    "v4_unassociated", "v5_near_duplicates", "v6a_options", "v6b_scale",
]

# 3.9 step 0 buckets a field before any assignment runs. The scanner supplies
# the only part it can see from the page alone: whether a column is a data
# input at all. `derived` and `unmapped` are decided later, with the rule set
# and the assignment in hand.
KIND_INPUT = "input"
KIND_CONTROL = "control"


@dataclass
class FieldDescriptor:
    """One column of a sheet, or one control of a plain form."""

    label: str
    label_rule: int
    kind: str
    input_type: str
    column_key: str
    column_index: int = None
    header_text: str = ""
    name: str = ""
    placeholder: str = ""   # feature 3 reads this; it is also cascade rule 4
    required: bool = False
    min: str = None
    max: str = None
    step: str = None
    options: list = None
    maxlength: int = None
    dom_order: int = 0
    control_count: int = 1
    row_labels_agree: bool = True
    truth_key: str = None

    @property
    def label_rule_description(self):
        return RULE_NAMES.get(self.label_rule, "unresolved")


def classify(sample):
    """Data input, or a control that must never be written to (3.9)."""
    if sample["input_type"] in ("checkbox", "radio", "button", "submit", "reset", "file"):
        return KIND_CONTROL
    if sample["disabled"] or sample["readonly"]:
        return KIND_CONTROL
    return KIND_INPUT


def most_common(values):
    values = [v for v in values if v not in (None, "")]
    return Counter(values).most_common(1)[0][0] if values else None


def group_columns(contexts):
    """Collapse raw control contexts into one descriptor per column."""
    columns = {}
    for ctx in contexts:
        columns.setdefault(ctx["column_key"], []).append(ctx)

    descriptors = []
    for key, members in columns.items():
        members.sort(key=lambda c: c["dom_order"])
        sample = members[0]

        resolutions = [resolve(c) for c in members]
        labels = [r.label for r in resolutions]
        label = common_label(labels)

        # Which rule to report for the column: the rule the majority of its
        # controls resolved by. A split here means the column is not uniform,
        # which `row_labels_agree` flags.
        rule = Counter(r.rule for r in resolutions).most_common(1)[0][0]

        truths = {c["truth_key"] for c in members if c["truth_key"]}

        # True when the column's label actually accounts for every row, rather
        # than being a majority vote over rows that disagree. A False here means
        # one row is labelled unlike its neighbours - worth seeing, since the
        # column is the unit the Resolver assigns to.
        uniform = len(set(labels)) <= 1 or (
            bool(label) and all(l.startswith(label) for l in labels)
        )

        descriptors.append(
            FieldDescriptor(
                label=label,
                label_rule=rule,
                kind=classify(sample),
                input_type=sample["input_type"],
                column_key=key,
                column_index=sample["column_index"],
                header_text=sample["header_text"],
                name=most_common([c["name"] for c in members]) or "",
                placeholder=most_common([c["placeholder"] for c in members]) or "",
                required=any(c["required"] for c in members),
                min=most_common([c["min"] for c in members]),
                max=most_common([c["max"] for c in members]),
                step=most_common([c["step"] for c in members]),
                options=sample["options"],
                maxlength=most_common([c["maxlength"] for c in members]),
                dom_order=sample["dom_order"],
                control_count=len(members),
                row_labels_agree=uniform,
                truth_key=truths.pop() if len(truths) == 1 else None,
            )
        )

    descriptors.sort(key=lambda d: d.dom_order)
    return descriptors


# Printed columns carry no control, so they never appear in the descriptors -
# but the executor still has to find the Student ID and Student Name it aligns
# rows on. Their position is read from the header text on this page load, not
# stored from a demonstration, so a reordered variant resolves correctly.
HEADER_JS = """() => {
  const table = document.querySelector('table');
  const headRow = table && table.tHead && table.tHead.rows[0];
  if (!headRow) return [];
  return Array.from(headRow.children).map((th, i) => ({
    index: i,
    text: (th.textContent || '').replace(/\\s+/g, ' ').trim(),
    id: th.id || null
  }));
}"""


def header_columns(page):
    return page.evaluate(HEADER_JS)


def header_index(headers, label):
    """Position of the column whose header names `label`. Exact match first,
    then prefix, so 'Grade' finds a 'Grade 0-100' header but never picks
    'Grade (Recomputed)' over it."""
    folded = label.casefold()
    for h in headers:
        if h["text"].casefold() == folded:
            return h["index"]
    prefixed = [h for h in headers if h["text"].casefold().startswith(folded)]
    if len(prefixed) == 1:
        return prefixed[0]["index"]
    return None


def extract_contexts(page, url):
    """The raw browser-side context, before any label is decided."""
    page.goto(url, wait_until="load")
    page.wait_for_selector("input, select, textarea", timeout=10_000)
    return page.evaluate(EXTRACT_JS)


def scan_page(page, url):
    return group_columns(extract_contexts(page, url))


def variant_url(name, base_url=None):
    if base_url:
        return f"{base_url.rstrip('/')}/{name}/index.html"
    return (MOCKSITE / name / "index.html").as_uri()


def scan_variants(names, base_url=None):
    from playwright.sync_api import sync_playwright

    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=str(CHROMIUM) if CHROMIUM.exists() else None
        )
        page = browser.new_page()
        try:
            for name in names:
                results[name] = scan_page(page, variant_url(name, base_url))
        finally:
            browser.close()
    return results


def print_report(name, descriptors):
    print(f"\n{name}")
    print(f"  {'label':<34} {'rule':<5} {'kind':<8} {'type':<9} "
          f"{'ctrls':<6} {'truth':<16}")
    print("  " + "-" * 84)
    for d in descriptors:
        label = d.label if d.label else "(unresolved)"
        print(f"  {label[:33]:<34} {d.label_rule:<5} {d.kind:<8} "
              f"{d.input_type:<9} {d.control_count:<6} {str(d.truth_key or ''):<16}")

    inputs = [d for d in descriptors if d.kind == KIND_INPUT]
    hist = rule_histogram([Resolved(d.label, d.label_rule) for d in descriptors])
    parts = [f"rule {r}: {n}" for r, n in hist.items()]
    print(f"  {len(descriptors)} columns ({len(inputs)} input, "
          f"{len(descriptors) - len(inputs)} control) | " + ", ".join(parts))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("variants", nargs="*", default=None,
                    help="variant directory names (default: all)")
    ap.add_argument("--base-url", default=None,
                    help="serve from http instead of file:// (e.g. http://127.0.0.1:8765)")
    ap.add_argument("--json", type=Path, default=None, help="write descriptors to a file")
    args = ap.parse_args()

    names = args.variants or VARIANTS
    results = scan_variants(names, args.base_url)

    for name in names:
        print_report(name, results[name])

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {n: [asdict(d) for d in ds] for n, ds in results.items()}
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
