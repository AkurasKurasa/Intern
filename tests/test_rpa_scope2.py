"""Tests for scripts/rpa_scope2.py (Objective 7, Scope 2): the position-replay
baseline and the value-level scoring. Pure functions only -- no browser."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import rpa_scope2 as s2  # noqa: E402

V0 = [{"label": "Course", "key": "course", "pos": 1}, {"label": "Year 1-5", "key": "year", "pos": 2},
      {"label": "Grade 0-100", "key": "grade", "pos": 5}, {"label": "Remarks", "key": "remarks", "pos": 6},
      {"label": "Recommendations", "key": "recommendations", "pos": 7}]
V1 = [{"label": "Remarks", "key": "remarks", "pos": 1}, {"label": "Recommendations", "key": "recommendations", "pos": 2},
      {"label": "Grade 0-100", "key": "grade", "pos": 5}, {"label": "Course", "key": "course", "pos": 6},
      {"label": "Year 1-5", "key": "year", "pos": 7}]
INTERN_V0 = {"variant": "v0_base", "sheet": {}, "row_alignment": {},
             "assignments": [{"source_header": "YEAR LEVEL", "target_label": "Year 1-5"},
                             {"source_header": "FINAL GRADE", "target_label": "Grade 0-100"}],
             "derived_rules": [{"field": "Remarks", "depends_on_field": "Grade 0-100", "cutoff": 75}]}


def test_recording_is_complete_and_correct_on_the_base_portal():
    rec = s2.record_baseline(INTERN_V0, V0)
    m = s2.replay_mapping(rec, V0, "v0_base")
    assert s2.placement(m, V0)["correct"] == 4          # incl. PROGRAM -> Course added to the recording


def test_reordered_portal_sends_each_column_to_whatever_sits_at_its_old_position():
    rec = s2.record_baseline(INTERN_V0, V0)
    m = s2.replay_mapping(rec, V1, "v1_reordered")
    to = {a["source_header"]: a["target_label"] for a in m["assignments"]}
    assert to["PROGRAM"] == "Remarks"                  # Course's old position 1 now holds Remarks
    assert to["YEAR LEVEL"] == "Recommendations"
    assert to["FINAL GRADE"] == "Grade 0-100"          # unchanged position
    assert s2.placement(m, V1)["correct"] == 1


def test_a_field_is_written_once_even_if_two_positions_collide():
    rec = s2.record_baseline(INTERN_V0, V0)
    m = s2.replay_mapping(rec, V1, "v1_reordered")
    labels = [a["target_label"] for a in m["assignments"]] + [r["field"] for r in m["derived_rules"]]
    assert len(labels) == len(set(labels))


def test_value_scoring_marks_swapped_values_wrong_and_the_rule_right():
    expected = {"S1": {"course": "BS IS", "year": 2.0, "grade": 85.0, "remarks": "Passed"}}
    good = {"rows": [{"student_id": "S1", "status": "filled",
                      "verified": {"Course": "BS IS", "Year 1-5": "2", "Grade 0-100": "85", "Remarks": "Passed"}}]}
    swapped = {"rows": [{"student_id": "S1", "status": "filled",
                         "verified": {"Course": "2", "Year 1-5": "BS IS", "Grade 0-100": "85", "Remarks": "Passed"}}]}
    assert s2.score_log(good, V0, expected) == {"rows": 1, "rows_correct": 1, "values_correct": 4, "values_total": 4, "rows_refused": 0}
    s = s2.score_log(swapped, V0, expected)
    assert s["rows_correct"] == 0 and s["values_correct"] == 2


def test_refused_row_is_never_correct():
    expected = {"S1": {"course": "BS IS", "year": 2.0, "grade": 85.0, "remarks": "Passed"}}
    log = {"rows": [{"student_id": "S1", "status": "failed",
                     "verified": {"Course": "BS IS", "Year 1-5": "2", "Grade 0-100": "85", "Remarks": "Passed"}}]}
    s = s2.score_log(log, V0, expected)
    assert s["rows_correct"] == 0 and s["rows_refused"] == 1
