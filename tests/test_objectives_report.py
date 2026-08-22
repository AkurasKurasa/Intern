"""
tests/test_objectives_report.py
=================================
scripts/objectives_report.py averages the last N rows of the SHARED trend
log data/output/run_metrics.jsonl for objectives 3, 5, 8, 9. Since Scope #2
(components/scope2/automate.py) now also writes rows to that same file
(tagged "scope": "scope2"), objectives_report.py must filter to Scope #1
rows BEFORE slicing to the last --recent N, or a scope2 row can silently
eat a slot meant for a scope1 run and get averaged in with Scope #1-shaped
keys it doesn't have. 70 pre-existing legacy rows have no "scope" key at
all and must be treated as "scope1".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import objectives_report as orep


def test_filter_scope1_excludes_scope2_rows():
    rows = [
        {"scope": "scope1", "id": 1},
        {"scope": "scope2", "id": 2},
    ]
    result = orep._filter_scope1(rows)
    assert [r["id"] for r in result] == [1]


def test_filter_scope1_treats_legacy_rows_with_no_scope_key_as_scope1():
    rows = [
        {"scope": "scope1", "id": 1},
        {"scope": "scope2", "id": 2},
        {"id": 3},  # legacy row, predates the "scope" key
    ]
    result = orep._filter_scope1(rows)
    assert [r["id"] for r in result] == [1, 3]


def test_filter_scope1_applied_before_recent_slice_excludes_scope2_from_slots():
    # Simulates the exact bug: a scope2 row landing among the most recent
    # writes must not eat a slot meant for a scope1 run when slicing to
    # the last N.
    rows = [{"scope": "scope1", "id": i} for i in range(5)]
    rows.append({"scope": "scope2", "id": "s2"})
    filtered_then_sliced = orep._filter_scope1(rows)[-5:]
    assert all(r["scope"] == "scope1" for r in filtered_then_sliced)
    assert [r["id"] for r in filtered_then_sliced] == [0, 1, 2, 3, 4]
