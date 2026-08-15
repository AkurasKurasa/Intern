"""Milestone 2: the scanner prints the correct label for every field, on every
variant, and the cascade has exactly one implementation.

Run:  python -m pytest tests/test_scanner.py -q
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent / "components" / "scope2"
sys.path.insert(0, str(REPO))

from executor.scanner import (  # noqa: E402
    CHROMIUM, KIND_CONTROL, KIND_INPUT, VARIANTS,
    extract_contexts, group_columns, variant_url,
)
from labeling.resolve import CASCADE, common_label, de_snake_case, resolve  # noqa: E402

# What the scanner must report for each variant: the label of every input
# column, keyed by the data-key the portal carries as ground truth, plus the
# cascade rule those columns are expected to resolve by.
EXPECTED = {
    "v0_base": (3, {
        "course": "Course",
        "year": "Year 1-5",
        "grade": "Grade 0-100",
        "remarks": "Remarks",
        "recommendations": "Recommendations optional",
    }),
    "v1_reordered": (3, {
        "remarks": "Remarks",
        "recommendations": "Recommendations optional",
        "grade": "Grade 0-100",
        "course": "Course",
        "year": "Year 1-5",
    }),
    "v2_relabeled": (3, {
        "course": "Degree Program",
        "year": "Year 1-5",
        "grade": "Final Rating 0-100",
        "remarks": "Academic Standing",
        "recommendations": "Recommendations optional",
    }),
    "v3_extra_fields": (3, {
        "course": "Course",
        "year": "Year 1-5",
        "section": "Section",
        "grade": "Grade 0-100",
        "remarks": "Remarks",
        "adviser": "Adviser",
        "recommendations": "Recommendations optional",
    }),
    # The point of V4: no aria-labelledby, so the cascade must fall through to
    # rule 5 and read the column header. Same labels, different route.
    "v4_unassociated": (5, {
        "course": "Course",
        "year": "Year 1-5",
        "grade": "Grade 0-100",
        "remarks": "Remarks",
        "recommendations": "Recommendations optional",
    }),
    "v5_near_duplicates": (3, {
        "course": "Course",
        "year": "Year 1-5",
        "year_enrolled": "Year Enrolled yyyy",
        "grade": "Grade 0-100",
        "grade_recomputed": "Grade (Recomputed) 0-100",
        "remarks": "Remarks",
        "recommendations": "Recommendations optional",
    }),
    "v6a_options": (3, {
        "course": "Course",
        "year": "Year 1-5",
        "grade": "Grade 0-100",
        "remarks": "Remarks",
        "recommendations": "Recommendations optional",
    }),
    "v6b_scale": (3, {
        "course": "Course",
        "year": "Year 1-5",
        "grade": "Grade 1.00-5.00",
        "remarks": "Remarks",
        "recommendations": "Recommendations optional",
    }),
}

ROSTER_ROWS = 50


@pytest.fixture(scope="session")
def scans():
    """One browser, one pass over every variant. Raw contexts and grouped
    descriptors are both kept - the anti-drift test needs the raw side."""
    from playwright.sync_api import sync_playwright

    out = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=str(CHROMIUM) if CHROMIUM.exists() else None
        )
        page = browser.new_page()
        try:
            for name in VARIANTS:
                contexts = extract_contexts(page, variant_url(name))
                out[name] = (contexts, group_columns(contexts))
        finally:
            browser.close()
    return out


def inputs_by_truth(descriptors):
    return {d.truth_key: d for d in descriptors if d.kind == KIND_INPUT}


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_input_column_resolves_to_the_right_label(scans, variant):
    _, descriptors = scans[variant]
    expected_rule, expected = EXPECTED[variant]
    found = inputs_by_truth(descriptors)

    assert set(found) == set(expected), (
        f"{variant}: input columns {sorted(found)} != expected {sorted(expected)}"
    )
    for key, label in expected.items():
        assert found[key].label == label, (
            f"{variant}.{key}: got {found[key].label!r}, want {label!r}"
        )
        assert found[key].label_rule == expected_rule, (
            f"{variant}.{key}: resolved by rule {found[key].label_rule}, "
            f"expected rule {expected_rule}"
        )


@pytest.mark.parametrize("variant", VARIANTS)
def test_one_descriptor_per_column_not_per_input(scans, variant):
    """3.9's assignment is one-to-one, so 50 rows of a column must collapse to
    a single target. This is the whole reason the scanner groups."""
    contexts, descriptors = scans[variant]
    _, expected = EXPECTED[variant]

    assert len(contexts) > len(descriptors), "grouping did nothing"
    for d in descriptors:
        if d.kind == KIND_INPUT:
            assert d.control_count == ROSTER_ROWS, (
                f"{variant}: column {d.label!r} has {d.control_count} controls, "
                f"expected {ROSTER_ROWS}"
            )
    assert len(inputs_by_truth(descriptors)) == len(expected)


@pytest.mark.parametrize("variant", VARIANTS)
def test_row_checkbox_is_a_control_never_an_input(scans, variant):
    """3.10: 'never leave a controls field touched'. The Select column must not
    reach the score matrix."""
    _, descriptors = scans[variant]
    controls = [d for d in descriptors if d.kind == KIND_CONTROL]
    assert len(controls) == 1, [d.label for d in controls]
    assert controls[0].input_type == "checkbox"
    assert controls[0].label == "Select"
    assert controls[0].truth_key is None


def test_v4_takes_a_different_cascade_route_than_v0(scans):
    """The V4 ablation is only meaningful if the two actually differ."""
    v0 = inputs_by_truth(scans["v0_base"][1])
    v4 = inputs_by_truth(scans["v4_unassociated"][1])

    assert {k: d.label for k, d in v0.items()} == {k: d.label for k, d in v4.items()}
    assert all(d.label_rule == 3 for d in v0.values())
    assert all(d.label_rule == 5 for d in v4.values())


def test_v2_labels_never_exactly_match_a_sheet_column():
    """Regression. V2 once renamed Course to 'Program' while the sheet's column
    was also 'Program', which handed string matching a free win on the variant
    built to defeat it. Any relabelling that collides with a source header
    silently inverts the experiment."""
    pd = pytest.importorskip("pandas")
    sheet = REPO / "data" / "sheets" / "grade_sheet.xlsx"
    if not sheet.exists():
        pytest.skip("run data/sheets/make_sheets.py first")

    df = pd.read_excel(sheet, sheet_name="SUMMARY", header=11)
    headers = {str(c).strip().casefold() for c in df.columns}

    _, expected = EXPECTED["v2_relabeled"]
    for key, label in expected.items():
        assert label.casefold() not in headers, (
            f"V2 label {label!r} is an exact match for a sheet column"
        )


@pytest.mark.parametrize("variant", VARIANTS)
def test_required_is_visible_in_the_dom(scans, variant):
    """Feature 14 reads the field's required flag. The portal enforces Grade,
    so the control must say so - validation living only in JS would leave the
    feature permanently zero and silently uninformative."""
    _, descriptors = scans[variant]
    found = inputs_by_truth(descriptors)
    assert found["grade"].required is True
    assert found["course"].required is False


def test_v6_variants_isolate_one_change_each(scans):
    """V6a moves the option casing, V6b moves the scale. If either moved both,
    a failure could not be attributed to option matching or to direction."""
    v0 = inputs_by_truth(scans["v0_base"][1])
    v6a = inputs_by_truth(scans["v6a_options"][1])
    v6b = inputs_by_truth(scans["v6b_scale"][1])

    assert v0["remarks"].options == ["Passed", "Failed"]
    assert v6a["remarks"].options == ["PASSED", "FAILED"]
    assert v6b["remarks"].options == ["Passed", "Failed"]

    assert (v0["grade"].min, v0["grade"].max) == ("0", "100")
    assert (v6a["grade"].min, v6a["grade"].max) == ("0", "100")
    assert (v6b["grade"].min, v6b["grade"].max) == ("1", "5")


@pytest.mark.parametrize("variant", VARIANTS)
def test_column_labels_account_for_every_row(scans, variant):
    """row_labels_agree must be earned, not assumed: the column label has to
    cover all 50 rows, not win a majority vote over rows that disagree."""
    _, descriptors = scans[variant]
    for d in descriptors:
        assert d.row_labels_agree, f"{variant}: column {d.label!r} has odd rows"


@pytest.mark.parametrize("variant", VARIANTS)
def test_browser_side_emits_every_cascade_key(scans, variant):
    """Anti-drift (3.5). The browser must hand over raw context for all six
    rules and decide nothing; a missing key would silently disable a rule."""
    contexts, _ = scans[variant]
    keys = {key for key, _ in CASCADE if key != "name_attr"} | {"name"}
    for ctx in contexts:
        missing = keys - set(ctx)
        assert not missing, f"{variant}: context missing {missing}"


def test_cascade_order_is_first_hit_wins():
    ctx = {
        "label_for": "From Label",
        "label_wrapping": "From Wrapper",
        "aria": "From Aria",
        "placeholder": "From Placeholder",
        "preceding_text": "From Header",
        "name": "from_name",
    }
    for expected_rule, (key, _) in enumerate(CASCADE, start=1):
        got = resolve(ctx)
        assert got.rule == expected_rule, f"expected rule {expected_rule}, got {got.rule}"
        ctx = {**ctx, key if key != "name_attr" else "name": ""}
    assert resolve(ctx).rule == 0


def test_de_snake_case():
    assert de_snake_case("midterm_exam") == "Midterm Exam"
    assert de_snake_case("midterm-exam") == "Midterm Exam"
    assert de_snake_case("midtermExam") == "Midterm Exam"
    assert de_snake_case("") == ""


def test_common_label_strips_the_per_row_part():
    assert common_label([
        "Grade 0-100 Abad, Andrea A.",
        "Grade 0-100 Aguilar, Benjamin L.",
    ]) == "Grade 0-100"
    assert common_label(["Course", "Course"]) == "Course"
    assert common_label(["Course"]) == "Course"
    # No shared prefix: the majority label wins rather than returning nothing.
    assert common_label(["Grade", "Grade", "Rating"]) == "Grade"
