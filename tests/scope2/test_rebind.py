"""A rule learned on V0 pointed at another portal's fields (rules/rebind.py).

Found by filling every variant end to end: the Remarks rule named V0's labels
("Remarks", "Grade 0-100"), so on V2 -- "Academic Standing", "Final Rating
0-100" -- and on V6b -- "Grade 1.00-5.00" -- the run crashed in fill_order()
with "derived fields have unmet dependencies". The rule is now anchored on the
sheet column its input came from and on the outcomes it writes.

Run:  python -m pytest tests/scope2/test_rebind.py -q
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent.parent / "components" / "scope2"
sys.path.insert(0, str(REPO))

from descriptors import FieldDescriptor  # noqa: E402
from rules.rebind import driver_column, rebind_driver, rebind_target  # noqa: E402

RULE = {"field": "Remarks", "kind": "threshold", "depends_on_field": "Grade 0-100",
        "operator": ">=", "cutoff": 75.0, "if_true": "Passed", "if_false": "Failed"}


def fld(label, input_type="text", options=None):
    return FieldDescriptor(label=label, label_rule=3, kind="input",
                           input_type=input_type, column_key="k", options=options)


def pair(header, label):
    return SimpleNamespace(source_header=header, target_label=label)


def test_the_input_column_is_the_one_the_demonstration_filled_it_from():
    pairs = [pair("FINAL GRADE", "Grade 0-100")] * 6 + [pair("PROGRAM", "Course")] * 6
    assert driver_column(pairs, "Grade 0-100") == "FINAL GRADE"


def test_a_tied_or_unseen_input_column_is_no_answer():
    pairs = [pair("FINAL", "Grade 0-100"), pair("FINAL GRADE", "Grade 0-100")]
    assert driver_column(pairs, "Grade 0-100") is None
    assert driver_column(pairs, "Nothing") is None


def test_the_same_label_is_kept_when_the_page_has_it():
    fields = [fld("Remarks", "select", ["Passed", "Failed"]), fld("Course")]
    assert rebind_target(RULE, fields) == "Remarks"


def test_a_renamed_output_field_is_found_by_what_it_offers():
    """V2 calls Remarks 'Academic Standing'; it is the one field that offers
    both Passed and Failed."""
    fields = [fld("Academic Standing", "select", ["Passed", "Failed"]),
              fld("Degree Program")]
    assert rebind_target(RULE, fields) == "Academic Standing"


def test_differently_worded_options_still_count():
    """V6a's options differ in case; resolve_option already accepts that."""
    fields = [fld("Remarks", "select", ["PASSED", "FAILED"])]
    assert rebind_target(RULE, fields) == "Remarks"


def test_two_fields_that_could_take_the_outcome_is_no_answer():
    fields = [fld("Standing", "select", ["Passed", "Failed"]),
              fld("Status", "select", ["Passed", "Failed"])]
    assert rebind_target(RULE, fields) is None


def test_the_input_field_is_wherever_this_mapping_writes_the_column():
    assignments = [{"source_header": "FINAL GRADE", "target_label": "Final Rating 0-100"},
                   {"source_header": "PROGRAM", "target_label": "Degree Program"}]
    assert rebind_driver("FINAL GRADE", assignments) == "Final Rating 0-100"


def test_an_input_column_that_fills_nothing_skips_the_rule():
    """V6b: FINAL GRADE's values do not fit the 1.00-5.00 field, so nothing is
    filled from it, and a Remarks computed from nothing would be invented."""
    assignments = [{"source_header": "PROGRAM", "target_label": "Course"}]
    assert rebind_driver("FINAL GRADE", assignments) is None
    assert rebind_driver(None, assignments) is None
