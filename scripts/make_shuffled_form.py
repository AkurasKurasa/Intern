"""Build a SHUFFLED copy of the car insurance form: same fields, moved.

    python scripts/make_shuffled_form.py

WHY THIS EXISTS
---------------
The transformer's element encoder carries BOTH position and identity:

    [1.0, x1/W, y1/H, x2/W, y2/H, conf, role, is_focused,
     ctrl_type, is_filled, attempted] + text_embedding

Four of the eleven scalars are the normalised bounding box; the label
embedding is another 384 dimensions. So identity dominates numerically, and
the pointer heads select an ELEMENT out of the live list rather than a
coordinate -- which suggests a moved field should still be found by name.

That is a hypothesis, not a result. Nobody has tested it. This fixture is the
test: move the fields, keep everything else identical, and see whether the
learned pointer follows the label or the position.

  still finds them -> it learned semantics, which is a real generalisation
                      claim and the strongest thing Scope #1 could show
  misses them      -> it learned positions, which would explain why it adds
                      nothing over Tab on the original layout

WHAT IS AND IS NOT CHANGED
--------------------------
Changed:   the ORDER of `self._row(...)` declarations inside a section, which
           moves a field both visually and in tab order.
Unchanged: every label, every field id, every widget type, every section, and
           which section a field belongs to.

THE ORIGINAL IS NEVER TOUCHED. car_insurance_form_wx.py stays exactly as it
is, because every run recorded so far was measured against it; a shuffled
layout would invalidate that comparison. This writes a separate file.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "practice_apps" / "car_insurance_entry" / "car_insurance_form_wx.py"
TARGET = REPO / "practice_apps" / "car_insurance_entry" / "car_insurance_form_wx_SHUFFLED.py"

SEED = 20260925

ROW_START = re.compile(r"^\s*self\._row\(")


def statements(lines: list[str]) -> list[tuple[int, int]]:
    """Spans of every self._row(...) call, as (start, end_exclusive).

    A row can span more than one line -- the choice/date fields put their
    lambda on the next line -- so the span is closed by paren balance rather
    than by assuming one statement per line.
    """
    spans = []
    i = 0
    while i < len(lines):
        if ROW_START.match(lines[i]):
            depth = 0
            j = i
            while j < len(lines):
                depth += lines[j].count("(") - lines[j].count(")")
                j += 1
                if depth <= 0:
                    break
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def shuffle_rows(text: str, seed: int = SEED) -> str:
    """Reorder adjacent row declarations within each contiguous run.

    A run ends at anything that is not a row -- a _section() call, a checkbox
    block, a blank line -- so fields never cross a section boundary. Moving a
    field to another tab would be a different experiment, and would make a
    wrong answer look like a navigation failure rather than a lookup one.
    """
    rng = random.Random(seed)
    lines = text.split("\n")
    spans = statements(lines)

    # group spans that sit directly against one another
    runs, current = [], []
    for span in spans:
        if current and span[0] == current[-1][1]:
            current.append(span)
        else:
            if len(current) > 1:
                runs.append(current)
            current = [span]
    if len(current) > 1:
        runs.append(current)

    # rewrite back to front so earlier indices stay valid
    for run in reversed(runs):
        blocks = [lines[s:e] for s, e in run]
        shuffled = blocks[:]
        for _ in range(20):
            rng.shuffle(shuffled)
            if shuffled != blocks:
                break
        flat = [line for block in shuffled for line in block]
        lines[run[0][0]:run[-1][1]] = flat

    return "\n".join(lines)


def row_labels(text: str) -> list[str]:
    """Every field label, sorted. This is what must not change."""
    return sorted(re.findall(r'self\._row\([^,]+,\s*[^,]+,\s*"([^"]+)"', text))


def main() -> int:
    if not SOURCE.exists():
        raise SystemExit(f"no form at {SOURCE}")

    base = SOURCE.read_text(encoding="utf-8")
    result = shuffle_rows(base)

    # Same fields, or the experiment is measuring two things at once.
    if row_labels(base) != row_labels(result):
        raise SystemExit("shuffling changed the field set - refusing to write")

    # Still valid Python, checked here rather than discovered at launch.
    import ast
    try:
        ast.parse(result)
    except SyntaxError as exc:
        raise SystemExit(f"shuffled form is not valid Python: {exc}")

    if result == base:
        raise SystemExit("nothing moved - the form's layout calls have changed shape")

    banner = (
        '"""SHUFFLED layout variant -- GENERATED, do not edit by hand.\n\n'
        "Same fields as car_insurance_form_wx.py, declared in a different order,\n"
        "which moves each one both visually and in tab order. Built by\n"
        "scripts/make_shuffled_form.py to test whether the transformer's pointer\n"
        "follows a field's LABEL or its POSITION.\n\n"
        "Regenerate rather than edit: the shuffle is seeded, so it rebuilds\n"
        "identically.\n"
        '"""\n\n'
    )
    TARGET.write_text(banner + result, encoding="utf-8", newline="")

    moved = sum(1 for a, b in zip(base.split("\n"), result.split("\n")) if a != b)
    print(f"wrote {TARGET.relative_to(REPO)}")
    print(f"  {len(row_labels(base))} fields, all labels and ids unchanged")
    print(f"  {moved} lines sit in a different position")
    print(f"  original untouched: {SOURCE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
