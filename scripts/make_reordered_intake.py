"""Build a REORDERED intake packet: same data, different order.

    python scripts/make_reordered_intake.py

WHY THIS EXISTS
---------------
Scope #1's fixture is cooperative by design. The intake packet tells the
operator, in its own header:

    Fields are organized tab-by-tab and section-by-section to match the
    exact sequence of the form. Work top-to-bottom within each tab.

When the data is already in form order, "press Tab" is a perfect navigation
policy -- and the agent's batch fast-fill does exactly that, writing each value
directly and Tabbing on. Measured on a real run: 41 fields filled, and
`[TRANSFORMER]` appeared in the log **zero** times. The trained model was never
consulted, because the fixture removed the problem it was trained to solve.

Scope #2 does not have this gap: its mocksite ships eight variants that reorder
columns, rename fields, add decoys and near-duplicates, precisely so the
matcher has something to be right or wrong about. Scope #1 had three variants
and all three vary only the VALUES (foreign names, missing values, unformatted
input) -- none vary the STRUCTURE.

This is the missing structural variant, and the direct analogue of the
mocksite's v1_reordered.

WHAT IS AND IS NOT CHANGED
--------------------------
Changed:   the ORDER of the `Label : Value` lines within each section.
Unchanged: every label, every value, every tab, every section, and which
           section each field belongs to.

That is what makes it a controlled comparison: the correct output is
byte-for-byte the same as the base packet, so any difference in the result is
caused by order alone. The shuffle is seeded, so the file regenerates
identically.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "data_entry_tasks" / "data_entry_intake.txt"
TARGET = REPO / "data_entry_tasks" / "data_entry_intake_REORDERED_TEST.txt"

SEED = 20260924

FIELD_RE = re.compile(r"^(?P<label>[^:\n]+?)\s*:\s(?P<value>.*)$")
SECTION_RE = re.compile(r"^\[ .+ \]$")

OLD_INSTRUCTION = (
    "listed. Fields are organized tab-by-tab and section-by-section to match the\n"
    "exact sequence of the form. Work top-to-bottom within each tab, then move to\n"
    "the next tab."
)
NEW_INSTRUCTION = (
    "listed. IMPORTANT - unlike the standard packet, the fields below are NOT in\n"
    "form order. Each field still belongs to the tab and section it is printed\n"
    "under, but within a section the order is scrambled. You will have to find\n"
    "each field on the form rather than tabbing straight down it."
)


def is_field(line: str) -> bool:
    """A `Label : Value` data line, not a heading or a rule."""
    if not FIELD_RE.match(line):
        return False
    return not (SECTION_RE.match(line.strip()) or line.startswith(("=", "━", "-")))


def reorder(text: str, seed: int = SEED) -> str:
    """Shuffle field lines within each run of consecutive fields.

    A "run" is a block of adjacent data lines -- in practice exactly one
    section, since sections are separated by a blank line and a `[ heading ]`.
    Shuffling within the run keeps every field under its own section, so the
    only thing that changes is the order a human would work through.
    """
    rng = random.Random(seed)
    lines = text.split("\n")
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if len(run) > 1:
            shuffled = run[:]
            # Re-roll until the order actually differs, or the "reordered"
            # packet could contain sections identical to the base one.
            for _ in range(20):
                rng.shuffle(shuffled)
                if shuffled != run:
                    break
            out.extend(shuffled)
        else:
            out.extend(run)
        run.clear()

    for line in lines:
        if is_field(line):
            run.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)


def fields_in(text: str) -> list[str]:
    return sorted(line.strip() for line in text.split("\n") if is_field(line))


def main() -> int:
    if not SOURCE.exists():
        raise SystemExit(f"no source packet at {SOURCE}")

    base = SOURCE.read_text(encoding="utf-8")
    result = reorder(base)

    # The guarantee this whole fixture rests on: same fields, same values.
    # Checked BEFORE the header rewrite, deliberately -- the replacement prose
    # contains a colon, which `is_field` reads as a data line, and folding it
    # into this comparison made the guard fire on a change that is not data.
    before, after = fields_in(base), fields_in(result)
    if before != after:
        raise SystemExit("reordering changed the data itself - refusing to write")

    if OLD_INSTRUCTION in result:
        result = result.replace(OLD_INSTRUCTION, NEW_INSTRUCTION, 1)
    else:
        print("  ! the packet's instruction block has changed; header left as-is")

    TARGET.write_text(result, encoding="utf-8", newline="")

    moved = sum(1 for a, b in zip(base.split("\n"), result.split("\n")) if a != b)
    print(f"wrote {TARGET.relative_to(REPO)}")
    print(f"  {len(before)} field lines, all values identical to the base packet")
    print(f"  {moved} lines sit in a different position")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
