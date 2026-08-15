"""Milestones 7-9: the evaluation harness, the ablations and the HiTL loop.

These tests guard the *measurement*, which is the part that fails silently. A
scorer that quietly counts an abstention as a success, or ground truth that
drifts from the portal, would produce a clean table full of wrong numbers.

Run:  python -m pytest tests/test_eval.py -q
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent / "components" / "scope2"
sys.path.insert(0, str(REPO))

from eval.ground_truth import (  # noqa: E402
    COLUMN_TO_KEY, COLUMNS_WITHOUT_TARGET, DERIVED_KEY, FIELDS_WITHOUT_SOURCE,
    TRUE_CUTOFF, TRUE_OPERATOR, expected_key, scorable_fields, should_abstain,
)
from eval.hitl import accuracy, escalate, examples_from_answers, oracle  # noqa: E402
from eval.run_variants import VariantScore, score_variant  # noqa: E402
from executor.scanner import VARIANTS, scan_variants  # noqa: E402
from executor.sheet_reader import SourceColumn, read_sheet  # noqa: E402
from resolver.assign import resolve  # noqa: E402

SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"

needs_sheet = pytest.mark.skipif(not SHEET.exists(), reason="run make_sheets.py first")


def column(header, index=0):
    return SourceColumn(header=header, index=index, inferred_type="text",
                        samples=["x"], non_null=50, total=50)


# ------------------------------------------------------- ground truth


@needs_sheet
def test_ground_truth_names_columns_that_actually_exist():
    """Ground truth drifting from the sheet would silently score everything
    against columns that are not there."""
    _, columns = read_sheet(SHEET, "SUMMARY", 11, "STUDENT NUMBER")
    headers = {c.header for c in columns if c.header}

    for header in COLUMN_TO_KEY:
        assert header in headers, f"{header!r} is not a column in the sheet"
    for header in COLUMNS_WITHOUT_TARGET:
        assert header in headers, f"{header!r} is not a column in the sheet"

    # Together they must account for every column: a column in neither set is
    # one nobody decided about.
    assert headers == set(COLUMN_TO_KEY) | COLUMNS_WITHOUT_TARGET


def test_ground_truth_keys_exist_on_every_variant():
    """The answer is stated against data-key so it survives relabelling; if a
    key vanished from a variant the table would score a field that is gone."""
    scanned = scan_variants(list(VARIANTS))
    for variant in VARIANTS:
        keys = {d.truth_key for d in scanned[variant] if d.kind == "input"}
        for wanted in COLUMN_TO_KEY.values():
            assert wanted in keys, f"{variant} has no field with key {wanted!r}"
        assert DERIVED_KEY in keys
        for forbidden in FIELDS_WITHOUT_SOURCE[variant]:
            assert forbidden in keys, f"{variant} has no field {forbidden!r}"


def test_expected_key_and_should_abstain_agree():
    assert expected_key("FINAL GRADE") == "grade"
    assert expected_key("MIDTERM") is None
    assert should_abstain("MIDTERM")
    assert not should_abstain("FINAL GRADE")


def test_the_derived_field_is_excluded_from_scoring():
    """Remarks is derived, so it never enters the score matrix and must not be
    counted as a mapping miss."""
    scanned = scan_variants(["v0_base"])["v0_base"]
    fields = scorable_fields(scanned, "v0_base")
    assert DERIVED_KEY not in {f.truth_key for f in fields}
    assert all(f.kind == "input" for f in fields)


# ------------------------------------------------------------ scoring


def test_a_perfect_mapping_scores_full_marks():
    scanned = scan_variants(["v0_base"])["v0_base"]
    fields = scorable_fields(scanned, "v0_base")
    columns = [column(h, i) for i, h in enumerate(COLUMN_TO_KEY)]

    key_index = {f.truth_key: j for j, f in enumerate(fields)}
    matrix = [[0.01] * len(fields) for _ in columns]
    for i, header in enumerate(COLUMN_TO_KEY):
        matrix[i][key_index[COLUMN_TO_KEY[header]]] = 0.99

    score, _ = score_variant("v0_base", columns, fields, matrix)
    assert score.mapped_correct == score.mapped_total == 3
    assert score.mapped_wrong == []


def test_abstaining_on_everything_scores_zero_accuracy_not_full_marks():
    """The failure this guards: counting 'mapped nothing' as success. Accuracy
    and abstention are separate numbers and both have to be visible."""
    scanned = scan_variants(["v0_base"])["v0_base"]
    fields = scorable_fields(scanned, "v0_base")
    columns = [column(h, i) for i, h in enumerate(COLUMN_TO_KEY)]
    matrix = [[0.01] * len(fields) for _ in columns]

    score, _ = score_variant("v0_base", columns, fields, matrix)
    assert score.mapped_correct == 0
    assert score.accuracy == 0.0
    assert score.abstention_precision == 1.0   # abstained on nothing it shouldn't


def test_a_wrong_mapping_is_recorded_not_swallowed():
    scanned = scan_variants(["v0_base"])["v0_base"]
    fields = scorable_fields(scanned, "v0_base")
    columns = [column("FINAL GRADE", 0)]

    course_index = next(j for j, f in enumerate(fields) if f.truth_key == "course")
    matrix = [[0.01] * len(fields)]
    matrix[0][course_index] = 0.99

    score, _ = score_variant("v0_base", columns, fields, matrix)
    assert score.mapped_correct == 0
    assert score.mapped_wrong and "FINAL GRADE" in score.mapped_wrong[0]


def test_mapping_a_column_that_should_abstain_counts_against_precision():
    scanned = scan_variants(["v0_base"])["v0_base"]
    fields = scorable_fields(scanned, "v0_base")
    columns = [column("MIDTERM", 0)]

    grade_index = next(j for j, f in enumerate(fields) if f.truth_key == "grade")
    matrix = [[0.01] * len(fields)]
    matrix[0][grade_index] = 0.99

    score, _ = score_variant("v0_base", columns, fields, matrix)
    assert score.abstain_correct == 0
    assert score.abstention_precision < 1.0


def test_variant_score_arithmetic():
    score = VariantScore("v", mapped_correct=2, mapped_total=3,
                         abstain_correct=4, abstain_total=5)
    assert score.accuracy == pytest.approx(2 / 3)
    assert score.abstention_recall == pytest.approx(4 / 5)
    assert score.abstention_precision == 1.0

    score.false_abstentions.append("x")
    assert score.abstention_precision == pytest.approx(4 / 5)


# ------------------------------------------------- 3.10 escalation loop


def test_the_oracle_can_answer_none_of_these():
    """A column with no correct target must be answerable as such, or the loop
    would force a wrong mapping to close a question."""
    scanned = scan_variants(["v0_base"])["v0_base"]
    fields = scorable_fields(scanned, "v0_base")

    assert oracle("FINAL GRADE", fields).truth_key == "grade"
    assert oracle("MIDTERM", fields) is None
    assert oracle("NAME OF STUDENT", fields) is None


def test_every_abstention_becomes_exactly_one_question():
    """3.10: 'any abstain from the Resolver pauses and asks the user to confirm
    that single mapping'."""
    scanned = scan_variants(["v0_base"])["v0_base"]
    fields = scorable_fields(scanned, "v0_base")
    columns = [column("FINAL GRADE", 0), column("MIDTERM", 1)]

    # Scores high enough to assign but too close to be sure -> both abstain.
    matrix = [[0.95] * len(fields), [0.94] * len(fields)]
    mapping = resolve(columns, fields, matrix)

    questions = escalate(mapping, columns, fields, "v0_base")
    assert len(questions) == len(mapping.abstained)
    assert {q.source_header for q in questions} <= {"FINAL GRADE", "MIDTERM"}


def test_an_answer_becomes_labelled_training_data():
    """The answer is appended as a confirmed pair so retraining can use it."""
    scanned = scan_variants(["v0_base"])["v0_base"]
    fields = scorable_fields(scanned, "v0_base")
    columns = [column("FINAL GRADE", 0)]

    grade_label = next(f.label for f in fields if f.truth_key == "grade")
    questions = escalate(
        resolve(columns, fields, [[0.5] * len(fields)]), columns, fields, "v0_base")
    new_examples = examples_from_answers(questions, columns, fields)

    assert new_examples, "an escalation produced no training data"
    positives = [e for e in new_examples if e.positive]
    assert [e.target_label for e in positives] == [grade_label]
    assert all(e.origin == "escalation" for e in new_examples)


def test_a_none_of_these_answer_is_kept_as_negatives():
    """A 'no correct target' answer labels every field negative for that
    column, which is real information and must not be discarded."""
    scanned = scan_variants(["v0_base"])["v0_base"]
    fields = scorable_fields(scanned, "v0_base")
    columns = [column("MIDTERM", 0)]

    questions = escalate(
        resolve(columns, fields, [[0.5] * len(fields)]), columns, fields, "v0_base")
    new_examples = examples_from_answers(questions, columns, fields)

    assert new_examples
    assert not any(e.positive for e in new_examples)


def test_accuracy_helper_ignores_columns_with_no_target():
    scanned = scan_variants(["v0_base"])["v0_base"]
    fields = scorable_fields(scanned, "v0_base")
    columns = [column("MIDTERM", 0)]

    correct, total = accuracy(resolve(columns, fields, [[0.99] * len(fields)]),
                              columns, fields)
    assert total == 0 and correct == 0


# --------------------------------------------- documented true answers


def test_the_true_cutoffs_match_the_instrument():
    """The sheet transmutes onto 65-100 with 75 passing, and V6b runs the
    1.00-5.00 scale where 3.00 passes and the operator flips."""
    assert TRUE_CUTOFF["0-100"] == 75.0
    assert TRUE_OPERATOR["0-100"] == ">="
    assert TRUE_CUTOFF["1-5"] == 3.0
    assert TRUE_OPERATOR["1-5"] == "<="
