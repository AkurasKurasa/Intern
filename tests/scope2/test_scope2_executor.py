"""Milestone 3: the executor fills every V0 row correctly from a hand-written
mapping, and the portal's own state agrees with the sheet.

The integration test does not trust the executor's readback. It reads the
portal's committed records out of the instrument's evaluation hook and compares
them against the spreadsheet independently.

Run:  python -m pytest tests/scope2/test_scope2_executor.py -q
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent / "components" / "scope2"
sys.path.insert(0, str(REPO))

from executor.runner import (  # noqa: E402
    apply_rule, fill_order, load_mapping, normalize_value, resolve_option,
    run, sheet_value,
)
from executor.sheet_reader import read_sheet  # noqa: E402

MAPPING_PATH = REPO / "data" / "mappings" / "v0_handwritten.json"
SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"

PASS_MARK = 75


# ------------------------------------------------------------------ units


def test_normalize_value_compares_numbers_numerically():
    # Writing 85.0 and reading back "85" is a match, not a mismatch.
    assert normalize_value(85.0) == normalize_value("85")
    assert normalize_value("91.50") == normalize_value(91.5)
    assert normalize_value(" Passed ") == normalize_value("passed")
    assert normalize_value(None) == ""
    assert normalize_value("85") != normalize_value("86")


def test_sheet_value_drops_the_float_tail():
    assert sheet_value(3.0) == "3"
    assert sheet_value(91.5) == "91.5"
    assert sheet_value("BS Nursing") == "BS Nursing"
    assert sheet_value(None) == ""


def test_resolve_option_matches_case_but_refuses_to_guess():
    assert resolve_option("Passed", ["Passed", "Failed"]) == "Passed"
    assert resolve_option("Passed", ["PASSED", "FAILED"]) == "PASSED"
    assert resolve_option("Pass", ["PASSED", "FAILED"]) == "PASSED"
    # 3.8 step 4: escalate rather than pick the nearest.
    assert resolve_option("Passed", ["INC", "DRP"]) is None
    assert resolve_option("P", ["PASSED", "PENDING"]) is None
    assert resolve_option("Passed", []) is None


def test_apply_rule_both_directions():
    up = {"operator": ">=", "cutoff": 75, "if_true": "Passed", "if_false": "Failed"}
    assert apply_rule(up, 75) == "Passed"
    assert apply_rule(up, 74.99) == "Failed"

    # The 1.00-5.00 scale passes below the cut - 3.8 says induce the direction,
    # never assume it.
    down = {"operator": "<=", "cutoff": 3.0, "if_true": "Passed", "if_false": "Failed"}
    assert apply_rule(down, 2.25) == "Passed"
    assert apply_rule(down, 3.01) == "Failed"

    assert apply_rule(up, "") is None
    assert apply_rule(up, "not a number") is None


def test_fill_order_puts_derived_fields_after_their_driver():
    mapping = load_mapping(MAPPING_PATH)
    mapped, derived = fill_order(mapping)
    assert "Grade 0-100" in mapped
    assert [r["field"] for r in derived] == ["Remarks"]
    assert derived[0]["depends_on_field"] in mapped


def test_fill_order_rejects_an_unmet_dependency():
    mapping = {
        "assignments": [{"target_label": "Course"}],
        "derived_rules": [
            {"field": "Remarks", "depends_on_field": "Grade 0-100"},
        ],
    }
    with pytest.raises(ValueError, match="unmet dependencies"):
        fill_order(mapping)


# ------------------------------------------------------- integration (M3)


@pytest.fixture(scope="module")
def committed_run():
    if not SHEET.exists():
        pytest.skip("run data/sheets/make_sheets.py first")
    return run("v0_base", MAPPING_PATH, dry_run=False, capture_state=True)


@pytest.fixture(scope="module")
def sheet_rows():
    df, _ = read_sheet(SHEET, "SUMMARY", 11, "STUDENT NUMBER")
    return {str(r["STUDENT NUMBER"]).strip(): r for _, r in df.iterrows()}


def test_every_row_filled_and_verified(committed_run):
    failed = [r for r in committed_run.rows if r.status != "filled"]
    assert not failed, [(r.student_id, r.reason) for r in failed]
    assert len(committed_run.rows) == 50
    assert committed_run.committed is True
    assert "50" in committed_run.commit_status


def test_portal_state_matches_the_sheet(committed_run, sheet_rows):
    """The independent check: the portal's own committed records, against the
    spreadsheet. Nothing here consults what the executor believed it wrote."""
    assert committed_run.portal_state, "no portal state captured"
    assert len(committed_run.portal_state) == 50

    for record in committed_run.portal_state:
        source = sheet_rows[record["student_id"]]
        assert record["course"] == source["PROGRAM"]
        assert float(record["year"]) == float(source["YEAR LEVEL"])
        assert float(record["grade"]) == float(source["FINAL GRADE"])


def test_derived_remarks_follows_the_rule_on_every_row(committed_run, sheet_rows):
    for record in committed_run.portal_state:
        grade = float(record["grade"])
        expected = "Passed" if grade >= PASS_MARK else "Failed"
        assert record["remarks"] == expected, (
            f"{record['student_id']}: grade {grade} -> {record['remarks']!r}"
        )

    # 3.8's degenerate case must not be what we are testing: both classes have
    # to be present or the rule is unconstrained.
    outcomes = {r["remarks"] for r in committed_run.portal_state}
    assert outcomes == {"Passed", "Failed"}


def test_unmapped_field_is_left_blank(committed_run):
    """3.9: a field that wins no column stays empty. Recommendations has no
    source column and must not absorb one."""
    assert all(r["recommendations"] == "" for r in committed_run.portal_state)


def test_identity_columns_are_never_written(committed_run, sheet_rows):
    """Student ID and Student Name are roster-owned on a sheet portal. The
    executor aligns on them and must not overwrite them."""
    for record in committed_run.portal_state:
        assert record["student_id"] in sheet_rows
        assert record["student_name"]
        assert "," in record["student_name"]  # still the roster's format


def test_dry_run_verifies_but_commits_nothing():
    log = run("v0_base", MAPPING_PATH, dry_run=True, limit=5, capture_state=True)
    assert all(r.status == "filled" for r in log.rows)
    assert log.committed is False
    assert "dry run" in log.commit_status
    # 3.10: dry-run "fill and verify but never submit" - so no record commits.
    assert all(r["grade"] == "" for r in log.portal_state)


# ------------------------------------------------- readback, proved


@pytest.fixture(scope="module")
def truncating_run(tmp_path_factory):
    """A sheet whose first Course value is longer than the field's maxlength.

    The portal's input truncates it to 60 characters, so what the field holds
    is not what was written. This is one of the exact cases 3.10 gives for
    mandatory readback, and without it the row would be committed wrong and
    silently.
    """
    import json
    import shutil

    import openpyxl

    if not SHEET.exists():
        pytest.skip("run data/sheets/make_sheets.py first")

    tmp = tmp_path_factory.mktemp("truncate")
    sheet_copy = tmp / "grade_sheet.xlsx"
    shutil.copy(SHEET, sheet_copy)

    workbook = openpyxl.load_workbook(sheet_copy)
    summary = workbook["SUMMARY"]
    summary.cell(15, 8).value = "X" * 70      # PROGRAM, first data row
    workbook.save(sheet_copy)

    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    mapping["sheet"]["path"] = str(sheet_copy)
    mapping_copy = tmp / "mapping.json"
    mapping_copy.write_text(json.dumps(mapping), encoding="utf-8")

    return run("v0_base", mapping_copy, dry_run=True, limit=1, capture_state=True)


def test_readback_catches_a_value_the_field_altered(truncating_run):
    row = truncating_run.rows[0]
    assert row.status == "failed", "a truncated write was accepted"
    assert "readback" in row.reason.lower()
    assert "Course" in row.reason


def test_a_failed_row_is_left_clean_not_half_filled(truncating_run):
    """3.10: on failure do not submit, and do not leave the row where the
    page's own save could pick it up."""
    row = truncating_run.rows[0]
    assert row.filled == {}
    assert row.verified == {}

    record = truncating_run.portal_state[0]
    assert record["course"] == ""
    assert record["grade"] == ""
    assert record["remarks"] == ""
