"""Build a RELABELED intake packet: same values, different wording.

    python scripts/make_relabeled_intake.py

WHY THIS EXISTS
---------------
The REORDERED packet was the wrong lever, and the run proved it: 64 Source to
1 LLM, essentially unchanged from the base packet. The reason is in the agent:

    _bf_targets = self._navproto.find_all_visible_empty_targets(state, ...)
    for _bf_el in _bf_targets:
        _bf_label = _bf_el.get("label")...

The fill loop iterates the FORM's fields and looks each label up in the
record. A dictionary lookup does not care what order the dictionary was built
in, so shuffling the packet could never change the outcome.

What the lookup DOES care about is wording. This packet renames the labels to
synonyms plain matching cannot bridge, which is exactly the case the agent's
own escalation comment describes:

    "lookup found nothing" and "genuinely blank" aren't the same thing -- a
    relabeled field ('Policy Reference #' instead of 'Policy Number') has a
    real answer under different wording plain text matching can never bridge
    but real judgment can.

This is the direct analogue of the mocksite's v2_relabeled, and the same claim
Scope #2 already makes for its matcher.

WHY THE RENAMES LOOK THE WAY THEY DO
------------------------------------
_lookup_field falls back to a fuzzy match that strips punctuation and matches
any record key "that contains all words". So a rename only defeats it if the
HEAD NOUN changes: 'Birth Date' would still match 'Date of Birth' (same words),
while 'Given Name' does not match 'First Name' (no shared head). Every pair
below was chosen on that basis, not by eye.

WHAT IS AND IS NOT CHANGED
--------------------------
Changed:   the LABEL on the left of the colon.
Unchanged: every VALUE, every tab, every section, and the order.

The correct output is therefore byte-for-byte the same as the base packet: the
agent has to work out that 'Given Name' belongs in the form's 'First Name'
field. Any field left blank is a failure to bridge wording, not missing data.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "data_entry_tasks" / "data_entry_intake.txt"
TARGET = REPO / "data_entry_tasks" / "data_entry_intake_RELABELED_TEST.txt"

FIELD_RE = re.compile(r"^(?P<indent>\s*)(?P<label>[^:\n]+?)(?P<pad>\s*): (?P<value>.*)$")

# label in the packet -> synonym the form does NOT use.
# Each pair deliberately changes the head noun, so the fuzzy "contains all
# words" fallback cannot bridge it.
RENAMES = {
    "Policy Number":        "Policy Reference #",
    "Policy Status":        "Coverage State",
    "Policy Type":          "Coverage Tier",
    "Policy Term":          "Contract Length",
    "Effective Date":       "Inception Day",
    "Expiration Date":      "Lapse Day",
    "Agent ID":             "Producer Code",
    "Agent Name":           "Producer",
    "Agency Name":          "Branch Office",
    "First Name":           "Given Name",
    "Middle Name":          "Second Forename",
    "Last Name":            "Surname",
    "Date of Birth":        "Born On",
    "Cell Phone":           "Mobile Contact",
    "Home Phone":           "Landline Contact",
    "Email":                "Electronic Contact",
    "Street Address":       "Mailing Line 1",
    "Street Address 2":     "Mailing Line 2",
    "City":                 "Municipality",
    "State":                "Province",
    "ZIP":                  "Postal Code",
    "County":               "District",
    "Country":              "Nation",
    "Credit Score":         "Risk Rating",
    "Occupation":           "Job Title",
    "Employer":             "Workplace",
    "Marital Status":       "Civil Standing",
    "Bank Name":            "Institution",
    "Account Number":       "Deposit Reference",
    "Account Type":         "Deposit Category",
    "Card Number":          "Payment Instrument #",
    "Cardholder Name":      "Instrument Holder",
    "Color":                "Paint Finish",
    "Body Type":            "Chassis Style",
    "Cylinders":            "Engine Count",
    "Current Mileage":      "Odometer Reading",
    "Claim Number":         "Incident Reference",
    "Claim Type":           "Incident Category",
    "Claim Status":         "Incident State",
    "Claim Description":    "Incident Narrative",
    "Adjuster Name":        "Assessor",
}


# A realistic subset, used by the DISORDERED packet.
#
# Renaming all 41 was measured and is unusable: every miss costs the full
# three-step escalation -- refresh the record cache, scroll Notepad looking for
# the field (_peek_notepad, which is the window visibly rocking back and
# forth), then ask the LLM -- at roughly three seconds a field. A run where
# EVERY field pays that looks frozen rather than slow, and nothing lands.
#
# Renaming a slice is also the more honest fixture. A real department packet
# uses its own wording for some fields, not a thesaurus pass over all of them.
# These twelve are spread across tabs so the escalation is exercised
# throughout the run rather than bunched at the start.
SUBSET = [
    "Policy Number", "Agent ID", "First Name", "Last Name",
    "Street Address", "City", "Occupation", "Bank Name",
    "Color", "Current Mileage", "Claim Number", "Adjuster Name",
]

PARTIAL_RENAMES = {k: v for k, v in RENAMES.items() if k in SUBSET}


def relabel(text: str, renames: dict[str, str] | None = None) -> str:
    """Rewrite the label on each `Label : Value` line, keeping the column.

    The packet is column-aligned, so the padding before the colon is rebuilt
    to the original width where the new label still fits -- a fixture that
    looked hand-mangled would be a different variable.
    """
    renames = RENAMES if renames is None else renames
    out = []
    for line in text.split("\n"):
        m = FIELD_RE.match(line)
        if not m or m.group("label").strip().startswith("["):
            out.append(line)
            continue
        label = m.group("label").strip()
        if label not in renames:
            out.append(line)
            continue
        new = renames[label]
        width = len(label) + len(m.group("pad"))
        out.append(f"{m.group('indent')}{new:<{width}}: {m.group('value')}")
    return "\n".join(out)


def values_in(text: str) -> list[str]:
    """Every value, in order. This is what must not change."""
    return [m.group("value").strip()
            for m in (FIELD_RE.match(l) for l in text.split("\n"))
            if m and not m.group("label").strip().startswith("[")]


def main() -> int:
    if not SOURCE.exists():
        raise SystemExit(f"no source packet at {SOURCE}")

    base = SOURCE.read_text(encoding="utf-8")
    result = relabel(base)

    if values_in(base) != values_in(result):
        raise SystemExit("relabeling changed a value - refusing to write")

    renamed = sum(1 for a, b in zip(base.split("\n"), result.split("\n")) if a != b)
    if not renamed:
        raise SystemExit("nothing was renamed - the packet's labels have changed")

    header_note = (
        "listed. IMPORTANT - this packet uses the department's OWN field names,\n"
        "which do not always match the wording on the form. 'Given Name' is the\n"
        "form's 'First Name', and so on. Match on meaning, not on the exact words."
    )
    old_header = (
        "listed. Fields are organized tab-by-tab and section-by-section to match the\n"
        "exact sequence of the form. Work top-to-bottom within each tab, then move to\n"
        "the next tab."
    )
    if old_header in result:
        result = result.replace(old_header, header_note, 1)

    TARGET.write_text(result, encoding="utf-8", newline="")
    print(f"wrote {TARGET.relative_to(REPO)}")
    print(f"  {len(RENAMES)} labels renamed, {renamed} lines changed")
    print(f"  {len(values_in(base))} values, all identical to the base packet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
