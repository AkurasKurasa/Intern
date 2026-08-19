"""
Tests for NotepadDataSource parse + lookup (the value pipeline).

After Tier-1 #3 the agent reads field values only through the injected data
source, so this locks the source's parse+lookup behavior against the REAL intake
file. The live Win32 window-read isn't unit-testable (covered by live smoke);
the parse+lookup logic IS pure and is what's pinned here.
"""
import os
import pytest

from data_sources.notepad_source import (
    _parse_records, _find_field_line, _record_body_and_line_offset, NotepadDataSource,
)

_INTAKE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data_entry_tasks", "data_entry_intake.txt",
)
_FOREIGN_TEST_INTAKE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data_entry_tasks", "data_entry_intake_FOREIGN_TEST.txt",
)


@pytest.fixture(scope="module")
def record1():
    with open(_INTAKE, encoding="utf-8") as f:
        text = f.read()
    records = _parse_records(text)
    assert records, "intake should parse into records (RECORD N OF M)"
    return records[min(records)]   # first record


@pytest.fixture
def source(record1):
    src = NotepadDataSource()
    src._cache = dict(record1)
    return src


# ── parse+lookup matches the values seen in live runs ────────────────────────

@pytest.mark.parametrize("field,expected", [
    ("Policy Number",   "PAI-2026-00441"),
    ("Underwriter",     "Marcus D. Chen"),
    ("Agent ID",        "AGT-0092"),
    ("Effective Date",  "04/01/2026"),
    ("Expiration Date", "10/01/2026"),
])
def test_known_field_values(source, field, expected):
    assert source.lookup(field) == expected


def test_case_insensitive_key(source):
    assert source.lookup("policy number") == "PAI-2026-00441"


def test_missing_field_returns_none(source):
    assert source.lookup("Nonexistent Field XYZ") is None


def test_placeholder_values_skipped():
    # (none)/none/etc. must not be returned as real values
    src = NotepadDataSource()
    src._cache = {"Some Field": "(none)", "Other": "N/A", "Real": "Value123"}
    assert src.lookup("Some Field") is None
    assert src.lookup("Other") is None
    assert src.lookup("Real") == "Value123"


def test_section_prefixed_lookup():
    src = NotepadDataSource()
    src._cache = {"Driver 2 First Name": "Jordan", "First Name": "Alex"}
    assert src.lookup("First Name", section="Driver 2") == "Jordan"
    assert src.lookup("First Name") == "Alex"           # bare key still works
    assert src.lookup("First Name", section="Driver 9") == "Alex"  # missing section → fallback


def test_get_all_returns_copy(source):
    snap = source.get_all()
    snap["injected"] = "x"
    assert "injected" not in source.get_all()           # must be a copy


# ── record-scoped search (real bug found live) ───────────────────────────────
# _peek_notepad's raw-text search used to run over the ENTIRE multi-record
# file with no record-boundary awareness at all. Real live incident: record
# 1's intake used "Policy Reference #" instead of "Policy Number" (a real,
# legitimate relabeling), so the whole-file search skipped straight past
# record 1 and matched record 2's "Policy Number" line instead -- silently
# filling a DIFFERENT customer's policy number into record 1's form, with no
# error logged anywhere. _record_body_and_line_offset() scopes any field
# search to just the requested record's own text.

def test_record_body_and_offset_scopes_search_to_the_right_record():
    with open(_FOREIGN_TEST_INTAKE, encoding="utf-8") as f:
        raw_text = f.read()
    body, offset = _record_body_and_line_offset(raw_text, 2)
    assert body is not None
    hit = _find_field_line(body.splitlines(), "Policy Number")
    assert hit is not None
    assert hit[1] == "PAI-2026-00442"
    # The offset must convert back to the REAL absolute line in the full file.
    all_lines = raw_text.splitlines()
    absolute_line = all_lines[offset + hit[0]]
    assert "Policy Number" in absolute_line
    assert "PAI-2026-00442" in absolute_line


def test_mislabeled_field_is_not_found_via_a_different_records_data():
    """The exact live bug: record 1 genuinely has no 'Policy Number' key
    (only the relabeled 'Policy Reference #') -- scoped correctly to record
    1's own text, searching for 'Policy Number' must find NOTHING, not
    silently succeed against record 2's line."""
    with open(_FOREIGN_TEST_INTAKE, encoding="utf-8") as f:
        raw_text = f.read()
    body, offset = _record_body_and_line_offset(raw_text, 1)
    assert body is not None
    hit = _find_field_line(body.splitlines(), "Policy Number")
    assert hit is None, "must not cross into a different record's data"

    # The relabeled key IS genuinely present in record 1's own text, though.
    hit_relabeled = _find_field_line(body.splitlines(), "Policy Reference #")
    assert hit_relabeled is not None
    assert hit_relabeled[1] == "PAI-2026-00441"


def test_record_body_and_offset_returns_none_for_a_record_that_does_not_exist():
    with open(_FOREIGN_TEST_INTAKE, encoding="utf-8") as f:
        raw_text = f.read()
    body, offset = _record_body_and_line_offset(raw_text, 999)
    assert body is None
