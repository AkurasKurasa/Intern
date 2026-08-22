"""
tests/test_show_progress.py
============================
scripts/show_progress.py reads data/output/run_metrics.jsonl, which is now a
SHARED trend log written by both Scope #1 (run_task.py) and Scope #2
(components/scope2/automate.py), tagged with a "scope" key. show_progress.py
only understands Scope #1's row shape (provider, transformer_dependency,
task_completion_rate, ...), so it must filter out Scope #2 rows before
rendering. 70 pre-existing legacy rows have no "scope" key at all and must
be treated as "scope1" (they predate the scope tag).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import show_progress as sp


def test_filter_scope1_excludes_scope2_rows():
    rows = [
        {"scope": "scope1", "id": 1},
        {"scope": "scope2", "id": 2},
    ]
    result = sp._filter_scope1(rows)
    assert [r["id"] for r in result] == [1]


def test_filter_scope1_treats_legacy_rows_with_no_scope_key_as_scope1():
    rows = [
        {"scope": "scope1", "id": 1},
        {"scope": "scope2", "id": 2},
        {"id": 3},  # legacy row, predates the "scope" key
    ]
    result = sp._filter_scope1(rows)
    assert [r["id"] for r in result] == [1, 3]


def test_filter_scope1_empty_input_returns_empty_list():
    assert sp._filter_scope1([]) == []
