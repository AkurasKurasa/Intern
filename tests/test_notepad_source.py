"""
Tests for NotepadDataSource parse + lookup (the value pipeline).

After Tier-1 #3 the agent reads field values only through the injected data
source, so this locks the source's parse+lookup behavior against the REAL intake
file. The live Win32 window-read isn't unit-testable (covered by live smoke);
the parse+lookup logic IS pure and is what's pinned here.
"""
import os
import pytest

from data_sources.notepad_source import _parse_records, NotepadDataSource

_INTAKE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data_entry_tasks", "data_entry_intake.txt",
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
