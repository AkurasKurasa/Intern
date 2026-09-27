"""The REORDERED intake packet -- Scope #1's first structural variant.

Scope #1's fixture is cooperative by design. The intake packet's own header
tells the operator the fields are "organized tab-by-tab and section-by-section
to match the exact sequence of the form", and when the data is already in form
order, "press Tab" is a perfect navigation policy. Measured on a real run: 41
fields filled, `[TRANSFORMER]` in the log zero times.

Scope #2's mocksite ships eight variants that reorder, rename and add decoys,
so its matcher has something to be right or wrong about. Scope #1 had three
variants and all three vary only the VALUES. This is the missing structural
one, and what it is worth depends entirely on one property: the DATA must be
identical, so a difference in the result is caused by order alone.

Run:  python -m pytest tests/test_reordered_intake.py -q
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import make_reordered_intake as gen  # noqa: E402

BASE = REPO / "data_entry_tasks" / "data_entry_intake.txt"
REORDERED = REPO / "data_entry_tasks" / "data_entry_intake_REORDERED_TEST.txt"


@pytest.fixture(scope="module")
def packets():
    if not BASE.exists() or not REORDERED.exists():
        pytest.skip("run scripts/make_reordered_intake.py first")
    return BASE.read_text(encoding="utf-8"), REORDERED.read_text(encoding="utf-8")


# ------------------------------------------- the property it rests on


def test_every_field_and_value_survives_unchanged(packets):
    """The whole point. Same fields, same values, so the correct output is
    byte-for-byte what the base packet produces."""
    base, reordered = packets
    assert gen.fields_in(base) == gen.fields_in(reordered)


def test_it_is_actually_reordered(packets):
    """A 'reordered' fixture identical to the base would silently test
    nothing at all."""
    base, reordered = packets
    base_fields = [l for l in base.split("\n") if gen.is_field(l)]
    new_fields = [l for l in reordered.split("\n") if gen.is_field(l)]
    assert base_fields != new_fields
    moved = sum(1 for a, b in zip(base_fields, new_fields) if a != b)
    assert moved > len(base_fields) * 0.3, f"only {moved} of {len(base_fields)} moved"


def test_fields_stay_inside_their_own_section(packets):
    """Shuffling across sections would change which tab a field belongs to,
    which is a different experiment -- and would make a wrong answer look
    like a navigation failure."""
    base, reordered = packets

    def by_section(text):
        sections, current = {}, None
        for line in text.split("\n"):
            stripped = line.strip()
            if gen.SECTION_RE.match(stripped):
                current = stripped
            elif gen.is_field(line) and current:
                sections.setdefault(current, set()).add(line.strip())
        return sections

    assert by_section(base) == by_section(reordered)


def test_the_header_tells_the_reader_it_is_not_in_form_order(packets):
    """The base packet's instruction is the reason the transformer is never
    needed. A variant that kept it would be lying about itself."""
    _, reordered = packets
    assert "NOT in" in reordered and "form order" in reordered
    assert "match the\nexact sequence of the form" not in reordered


# ------------------------------------------------------ the generator


def test_regenerating_is_deterministic():
    """Seeded, so the fixture is reproducible -- an evaluation run against a
    packet nobody can rebuild is not evidence of anything."""
    base = BASE.read_text(encoding="utf-8")
    assert gen.reorder(base) == gen.reorder(base)


def test_a_different_seed_gives_a_different_order():
    base = BASE.read_text(encoding="utf-8")
    assert gen.reorder(base, seed=1) != gen.reorder(base, seed=2)


def test_section_headings_and_rules_are_not_treated_as_data():
    assert gen.is_field("Policy Number        : PAI-2026-00441")
    assert not gen.is_field("[ Policy Information ]")
    assert not gen.is_field("=" * 80)
    assert not gen.is_field("  TAB 1 - POLICY")
    assert not gen.is_field("")


def test_a_single_field_section_is_left_alone():
    """Nothing to shuffle, and the re-roll loop must not spin on it."""
    one = "[ Solo ]\nOnly Field           : value\n"
    assert gen.reorder(one) == one
