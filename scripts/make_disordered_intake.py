"""Build the DISORDERED intake packet: both levers at once.

    python scripts/make_disordered_intake.py

WHY BOTH
--------
Reordering alone did almost nothing. Measured on a real run against the
REORDERED packet: 64 Source to 1 LLM, essentially the base packet's result.
The reason is in the agent's fill loop:

    _bf_targets = self._navproto.find_all_visible_empty_targets(state, ...)
    for _bf_el in _bf_targets:
        _bf_label = _bf_el.get("label")...

It iterates the FORM's fields and looks each label up in the record. A
dictionary lookup does not care what order the dictionary was built in, so
shuffling the packet was never going to change the outcome.

Wording is what the lookup actually depends on. So this packet applies both: a
SUBSET of labels renamed to synonyms plain matching cannot bridge, AND the
order scrambled. Relabeling is what moves the numbers; the reordering is kept
so the packet is not merely a renamed copy, and so a navigation effect (if
there is one) is not excluded by construction.

A subset, not all of them, and that is a measured decision. Renaming all 41
made the run unusable: every miss pays the full three-step escalation --
refresh the record cache, scroll Notepad hunting for the field (_peek_notepad,
which is the window visibly rocking back and forth), then ask the LLM -- at
roughly three seconds a field. With every field missing, nothing lands and the
run looks frozen rather than slow. Twelve renames across 161 fields per record
keeps it moving while still exercising the escalation throughout.

WHAT IS AND IS NOT CHANGED
--------------------------
Changed:   the LABEL on 12 of the fields, and the ORDER of lines in a section.
Unchanged: every VALUE, every tab, every section, and which section a field
           belongs to.

So the correct output stays byte-for-byte what the base packet produces, and
the two runs are directly comparable. A field left blank here is a failure to
bridge wording, not missing data.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import make_relabeled_intake as rel  # noqa: E402
import make_reordered_intake as reo  # noqa: E402

SOURCE = REPO / "data_entry_tasks" / "data_entry_intake.txt"
TARGET = REPO / "data_entry_tasks" / "data_entry_intake_DISORDERED_TEST.txt"

OLD_HEADER = (
    "listed. Fields are organized tab-by-tab and section-by-section to match the\n"
    "exact sequence of the form. Work top-to-bottom within each tab, then move to\n"
    "the next tab."
)
NEW_HEADER = (
    "listed. IMPORTANT - this packet is NOT in form order, and it uses the\n"
    "department's own field names rather than the form's. 'Given Name' is the\n"
    "form's 'First Name', and fields appear in no particular sequence. Match on\n"
    "meaning and find each field, rather than tabbing straight down the form."
)


def build(text: str) -> str:
    """Relabel a subset, then reorder.

    Order matters: relabel keys off the ORIGINAL label names, so it has to run
    before anything moves the lines around.

    PARTIAL_RENAMES, not the full set. Renaming all 41 was measured and is
    unusable -- every miss pays the full three-step escalation (refresh cache,
    scroll Notepad, ask the LLM) at roughly three seconds a field, so a run
    where every field misses looks frozen and nothing lands. Twelve renames
    across 161 fields per record keeps the run moving while still exercising
    the escalation throughout it.
    """
    return reo.reorder(rel.relabel(text, rel.PARTIAL_RENAMES))


def main() -> int:
    if not SOURCE.exists():
        raise SystemExit(f"no source packet at {SOURCE}")

    base = SOURCE.read_text(encoding="utf-8")
    result = build(base)

    # Compared as a MULTISET, not in order -- `values_in` returns values in
    # document order, and reordering changes that by design. Sorting is what
    # makes this a check on the data rather than on the sequence; the ordered
    # comparison fired here during development and was right to, since this is
    # the one generator where order is legitimately expected to differ.
    if sorted(rel.values_in(base)) != sorted(rel.values_in(result)):
        raise SystemExit("disordering changed a value - refusing to write")

    relabeled_only = rel.relabel(base, rel.PARTIAL_RENAMES)
    if reo.fields_in(relabeled_only) != reo.fields_in(result):
        raise SystemExit("disordering lost or altered a field - refusing to write")

    if OLD_HEADER in result:
        result = result.replace(OLD_HEADER, NEW_HEADER, 1)
    else:
        print("  ! the packet's instruction block has changed; header left as-is")

    TARGET.write_text(result, encoding="utf-8", newline="")

    moved = sum(1 for a, b in zip(base.split("\n"), result.split("\n")) if a != b)
    print(f"wrote {TARGET.relative_to(REPO)}")
    print(f"  {len(rel.PARTIAL_RENAMES)} of {len(rel.RENAMES)} labels renamed, AND field order scrambled")
    print(f"  {len(rel.values_in(base))} values, all identical to the base packet")
    print(f"  {moved} lines differ from the base packet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
