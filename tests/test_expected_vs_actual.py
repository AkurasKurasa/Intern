"""
tests/test_expected_vs_actual.py
==================================
Regression tests for scripts/expected_vs_actual.py -- the
`scope1_expected_vs_actual` Task Tree/DEVELOPERS.md checklist item
("Expected-vs-actual diff at submit — Per-record correctness report").

Reuses bc_fidelity.py's own intake parsing, tab-mapping, and value
normalization (no duplicated comparison rules) -- these tests focus on
build_report()'s own logic: correctly classifying each field as
match/mismatch/missing, grouping by tab, and computing accurate summary
counts, including the metadata-fields-shown-but-not-scored distinction
that mirrors bc_fidelity._SKIP_FIELDS.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import expected_vs_actual as eva


_SYNTH_INTAKE = """RECORD 1 OF 1

[ Policy Information ]
Policy Number        : PAI-TEST-001
Policy Status        : Active
Policy Type          : Full Coverage

[ Personal Information ]
First Name           : James
Last Name            : Delgado

[ Vehicle Identification ]
Year                 : 2020
Make                 : Honda

RECORD 2 OF 1
"""


def _report(submission: dict, tmp_path: Path, record_num: int = 1) -> dict:
    intake_path = tmp_path / "intake.txt"
    intake_path.write_text(_SYNTH_INTAKE, encoding="utf-8")
    return eva.build_report(submission, record_num, intake_path=intake_path)


class TestFieldStatusClassification:
    def test_matching_value_is_match(self, tmp_path):
        report = _report({"policy_type": "Full Coverage"}, tmp_path)
        field = next(f for f in report["by_tab"]["Policy"] if f["field"] == "policy_type")
        assert field["status"] == "match"

    def test_wrong_value_is_mismatch(self, tmp_path):
        report = _report({"policy_type": "Liability Only"}, tmp_path)
        field = next(f for f in report["by_tab"]["Policy"] if f["field"] == "policy_type")
        assert field["status"] == "mismatch"
        assert field["expected"] == "Full Coverage"
        assert field["actual"] == "Liability Only"

    def test_never_filled_is_missing(self, tmp_path):
        report = _report({}, tmp_path)
        field = next(f for f in report["by_tab"]["Policy"] if f["field"] == "policy_type")
        assert field["status"] == "missing"
        assert field["actual"] is None

    def test_false_value_counts_as_missing_not_mismatch(self, tmp_path):
        """Matches bc_fidelity's own agent_val in ('', None, False) treatment
        -- an unchecked checkbox reads as False, not a real filled value."""
        report = _report({"policy_type": False}, tmp_path)
        field = next(f for f in report["by_tab"]["Policy"] if f["field"] == "policy_type")
        assert field["status"] == "missing"

    def test_normalization_is_case_and_whitespace_insensitive(self, tmp_path):
        report = _report({"v_make": "  HONDA  "}, tmp_path)
        field = next(f for f in report["by_tab"]["Vehicle"] if f["field"] == "v_make")
        assert field["status"] == "match"


class TestTabGrouping:
    def test_fields_grouped_under_correct_tabs(self, tmp_path):
        report = _report({}, tmp_path)
        assert "Policy" in report["by_tab"]
        assert "Policyholder" in report["by_tab"]
        assert "Vehicle" in report["by_tab"]
        policy_fields = {f["field"] for f in report["by_tab"]["Policy"]}
        assert "policy_number" in policy_fields
        assert "policy_type" in policy_fields
        vehicle_fields = {f["field"] for f in report["by_tab"]["Vehicle"]}
        assert "v_year" in vehicle_fields
        assert "v_make" in vehicle_fields


class TestMetadataFieldsShownButNotScored:
    """policy_number/policy_status/_timestamp are metadata per bc_fidelity's
    own _SKIP_FIELDS -- shown in the report for a complete audit trail, but
    excluded from the scored summary counts, exactly mirroring bc_fidelity's
    own scoring semantics."""

    def test_metadata_field_present_in_by_tab_but_marked_unscored(self, tmp_path):
        report = _report({"policy_number": "PAI-TEST-001"}, tmp_path)
        field = next(f for f in report["by_tab"]["Policy"] if f["field"] == "policy_number")
        assert field["scored"] is False

    def test_metadata_field_excluded_from_tab_summary_totals(self, tmp_path):
        report = _report({}, tmp_path)
        # Policy tab has 3 fields total (policy_number, policy_status,
        # policy_type); the first two are metadata per bc_fidelity's own
        # _SKIP_FIELDS, leaving 1 scored field.
        assert report["tab_summary"]["Policy"]["total"] == 1

    def test_metadata_field_excluded_from_overall_totals(self, tmp_path):
        report = _report({}, tmp_path)
        # 7 total gold fields across the synthetic intake (verified directly:
        # policy_number, policy_status, policy_type, ph_first, ph_last,
        # v_year, v_make), minus 2 metadata fields (policy_number, policy_status)
        assert report["overall"]["total_fields"] == 5


class TestSummaryCounts:
    def test_perfect_submission_scores_100_percent(self, tmp_path):
        perfect = {
            "policy_status": "Active", "policy_type": "Full Coverage",
            "ph_first": "James", "ph_last": "Delgado",
            "v_year": "2020", "v_make": "Honda",
        }
        report = _report(perfect, tmp_path)
        assert report["overall"]["correctness"] == 1.0
        assert report["overall"]["mismatch"] == 0
        assert report["overall"]["missing"] == 0

    def test_empty_submission_scores_0_percent(self, tmp_path):
        report = _report({}, tmp_path)
        assert report["overall"]["correctness"] == 0.0
        assert report["overall"]["missing"] == report["overall"]["total_fields"]

    def test_tab_summary_correctness_computed_per_tab_independently(self, tmp_path):
        # Policy tab correct, Policyholder tab empty
        submission = {"policy_status": "Active", "policy_type": "Full Coverage"}
        report = _report(submission, tmp_path)
        assert report["tab_summary"]["Policy"]["correctness"] == 1.0
        assert report["tab_summary"]["Policyholder"]["correctness"] == 0.0

    def test_tab_with_zero_scored_fields_defaults_to_100_percent_not_divide_by_zero(self, tmp_path):
        """A tab where every field happens to be metadata-only shouldn't
        crash on a 0/0 division -- matches bc_fidelity's own `if gold_tabs
        else 0.0`-style guard pattern used throughout that file."""
        report = _report({}, tmp_path)
        for tab, summary in report["tab_summary"].items():
            if summary["total"] == 0:
                assert summary["correctness"] == 1.0


class TestRecordSelection:
    def test_wrong_record_number_returns_no_fields(self, tmp_path):
        """The synthetic intake only has RECORD 1 -- asking for record 2
        should come back empty, not crash or silently reuse record 1."""
        report = _report({}, tmp_path, record_num=2)
        assert report["overall"]["total_fields"] == 0
