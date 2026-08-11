"""Milestone 3: the Feature Extractor, the module 3.6 calls the critical one.

The load-bearing test here is test_no_demonstration_only_signal. Everything else
is arithmetic; that one is the architectural invariant.

Run:  python -m pytest tests/test_features.py -q
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from executor.scanner import FieldDescriptor, KIND_INPUT  # noqa: E402
from executor.sheet_reader import SourceColumn  # noqa: E402
from features import encoders  # noqa: E402
from features.extractor import (  # noqa: E402
    DIMS, FEATURE_NAMES, VERSION, Candidate, candidates, containment, extract,
    extract_named, is_abbreviation, jaccard, levenshtein_ratio,
    option_overlap, positional, structural, tokens, value_shape,
)

needs_encoder = pytest.mark.skipif(
    not encoders.available(), reason="sentence-transformers unavailable"
)


def column(header="FINAL GRADE", index=7, inferred_type="numeric",
           samples=(85.0, 96.0, 95.0, 87.0, 72.0), non_null=50, total=50):
    return SourceColumn(header=header, index=index, inferred_type=inferred_type,
                        samples=list(samples), non_null=non_null, total=total)


def field(label="Grade 0-100", input_type="number", dom_order=4, **kw):
    base = dict(label=label, label_rule=3, kind=KIND_INPUT, input_type=input_type,
                column_key="t:col6", column_index=6, header_text=label,
                dom_order=dom_order, min="0", max="100", step="0.01",
                required=True, control_count=50)
    base.update(kw)
    return FieldDescriptor(**base)


def make(col=None, fld=None, n_columns=8, n_fields=5, filled=frozenset()):
    return Candidate(column=col or column(), field=fld or field(),
                     n_columns=n_columns, n_fields=n_fields,
                     filled_fields=filled)


# ---------------------------------------------------------------- contract


def test_vector_shape_and_names_agree():
    assert DIMS == 17
    assert len(FEATURE_NAMES) == DIMS
    assert len(set(FEATURE_NAMES)) == DIMS
    assert VERSION.endswith(f"{DIMS}d")


@needs_encoder
def test_every_feature_is_normalised_to_unit_range():
    for cand in candidates([column(), column("PROGRAM", 5, "text", ["BS CS"])],
                           [field(), field("Course", "text", 0)]):
        vector = extract(cand)
        assert len(vector) == DIMS
        assert all(0.0 <= v <= 1.0 for v in vector), vector


@needs_encoder
def test_no_demonstration_only_signal(monkeypatch):
    """3.1's load-bearing rule: the extractor is one implementation used in both
    training and execution, so nothing it reads may exist only at demo time.

    The portal carries the answer in data-key, and the scanner surfaces it as
    truth_key for evaluation. If any feature ever consulted it, the matcher
    would score perfectly in training and collapse on an unseen UI.
    """
    truthful = make(fld=field(truth_key="grade"))
    blinded = make(fld=field(truth_key=None))
    assert extract(truthful) == extract(blinded)

    # Same for a deliberately misleading truth label.
    misleading = make(fld=field(truth_key="recommendations"))
    assert extract(truthful) == extract(misleading)


def test_semantic_features_fail_loudly_when_the_encoder_is_missing(monkeypatch):
    monkeypatch.setattr(encoders, "available", lambda: False)
    with pytest.raises(encoders.EncoderUnavailable):
        extract(make())
    # ...and can be explicitly waived to inspect the other thirteen.
    vector = extract(make(), require_encoder=False)
    assert vector[:4] == [0.0, 0.0, 0.0, 0.0]
    assert len(vector) == DIMS


# ------------------------------------------------------------------ units


def test_levenshtein_ratio():
    assert levenshtein_ratio("grade", "grade") == 1.0
    assert levenshtein_ratio("", "") == 1.0
    assert levenshtein_ratio("grade", "") == 0.0
    assert 0.0 < levenshtein_ratio("Yr Level", "Year Level") < 1.0
    assert levenshtein_ratio("GRADE", "grade") == 1.0  # lowercased


def test_jaccard_and_containment():
    assert jaccard(tokens("final grade"), tokens("grade final")) == 1.0
    assert jaccard(tokens("final grade"), tokens("grade 0 100")) == pytest.approx(0.25)
    assert jaccard([], []) == 0.0
    assert containment("grade", "final grade") == 1.0
    assert containment("Grade", "GRADE 0-100") == 1.0
    assert containment("grade", "course") == 0.0
    assert containment("", "course") == 0.0


def test_is_abbreviation():
    assert is_abbreviation("DOB", "date of birth") == 1.0
    # Vowel-drop contraction, not a prefix: "year" does not start with "yr".
    # This is the exact header the architecture's own sheet spec uses.
    assert is_abbreviation("Yr Level", "Year Level") == 1.0
    assert is_abbreviation("Sec", "Section") == 1.0
    assert is_abbreviation("Year Level", "Year Level") == 0.0  # identical, not abbrev
    assert is_abbreviation("Program", "Course") == 0.0
    assert is_abbreviation("Grade", "Course") == 0.0
    assert is_abbreviation("", "Course") == 0.0
    # Symmetric: argument order must not decide the answer.
    assert is_abbreviation("Year Level", "Yr Level") == 1.0


def test_value_shape_rewards_a_column_that_fits_the_control():
    fits = value_shape(make())
    assert fits[0] == 1.0          # numeric column, number input
    assert fits[1] == 1.0          # every sample matches the numeric family
    assert fits[2] == 1.0          # every sample inside min/max

    # The same numbers offered to a text field satisfy nothing.
    text_field = field("Course", "text", 0, min=None, max=None, required=False)
    assert value_shape(make(fld=text_field))[0] == 0.0

    # Out-of-range values are caught: a 1-5 control fed 0-100 grades.
    scaled = field("Grade 1.00-5.00", "number", 4, min="1", max="5")
    assert value_shape(make(fld=scaled))[2] == 0.0


def test_structural_required_fit_and_fill_state():
    f13, f14, f15 = structural(make())
    assert f13 == 1.0
    assert f14 == 1.0                       # required field, complete column
    assert f15 == 0.0

    sparse = column(non_null=25, total=50)
    assert structural(make(col=sparse))[1] == pytest.approx(0.5)

    optional = field("Recommendations optional", "textarea", 8,
                     required=False, min=None, max=None)
    assert structural(make(fld=optional))[1] == 0.5   # indifferent

    already = make(filled=frozenset({"Grade 0-100"}))
    assert structural(already)[2] == 1.0


def test_positional_feature_peaks_on_aligned_ranks():
    # Same normalised rank in both orderings -> 1.0
    aligned = make(col=column(index=0), fld=field(dom_order=0))
    assert positional(aligned)[0] == pytest.approx(1.0)

    # Opposite ends -> 0.0. This is the signal V1 is built to punish.
    opposed = make(col=column(index=0), fld=field(dom_order=4))
    assert positional(opposed)[0] == pytest.approx(0.0)

    assert positional(make(n_columns=1, n_fields=1))[0] == 1.0


def test_option_overlap_is_the_feature_that_maps_a_select():
    remarks = field("Remarks", "select", 5, options=["Passed", "Failed"],
                    min=None, max=None, required=False)

    # A precomputed Status column overlaps the option set exactly (3.11).
    status = column("REMARKS", 10, "text", ["PASSED", "FAILED", "PASSED"])
    assert option_overlap(make(col=status, fld=remarks))[0] == 1.0

    # A grade column overlaps nothing - corroborating evidence the select is
    # derived rather than copied.
    assert option_overlap(make(fld=remarks))[0] == 0.0

    # Non-selects score zero by definition.
    assert option_overlap(make())[0] == 0.0


@needs_encoder
def test_the_right_pairing_outscores_its_near_duplicate():
    """MIDTERM and FINAL GRADE are both numeric 0-100 columns, so only the
    semantic features can separate them. If this inverts, V5's abstention test
    is measuring the wrong thing."""
    grade_field = field()
    right = extract_named(make(col=column("FINAL GRADE", 7), fld=grade_field))
    wrong = extract_named(make(col=column("MIDTERM", 3), fld=grade_field))

    assert right["sem_header_label"] > wrong["sem_header_label"]
    assert right["val_type_match"] == wrong["val_type_match"] == 1.0


def test_candidate_grid_is_the_full_cross_product():
    cols = [column(), column("PROGRAM", 5, "text")]
    fields = [field(), field("Course", "text", 0)]
    grid = candidates(cols, fields)
    assert len(grid) == 4
    assert all(c.n_columns == 2 and c.n_fields == 2 for c in grid)


# ------------------------------------------------- instrument diagnostics


@pytest.fixture(scope="module")
def live_fields():
    """Descriptors scanned off the real portal, not built in this file.

    The earlier version of this test used synthetic descriptors, so it kept
    passing while describing a portal that no longer existed. Features are
    checked against what the instrument actually serves.
    """
    from executor.scanner import KIND_INPUT, scan_variants

    scanned = scan_variants(["v0_base", "v2_relabeled", "v4_unassociated"])
    return {
        variant: {d.truth_key: d for d in descriptors if d.kind == KIND_INPUT}
        for variant, descriptors in scanned.items()
    }


@needs_encoder
def test_name_placeholder_and_length_features_are_live(live_fields):
    """Features 2, 3 and 12 were structurally zero while the portal gave its
    controls no name, placeholder or maxlength. All three now read real
    attributes."""
    fields = live_fields["v0_base"]
    columns = {
        "grade": column("FINAL GRADE", 7, "numeric", [85.0, 96.0]),
        "course": column("PROGRAM", 5, "text", ["BS Information Systems"]),
    }

    grade = extract_named(make(col=columns["grade"], fld=fields["grade"]))
    assert grade["sem_header_name"] > 0.5, "feature 2 still dead"
    assert grade["sem_header_placeholder"] > 0.0, "feature 3 still dead"

    course = extract_named(make(col=columns["course"], fld=fields["course"]))
    assert course["val_length_fit"] > 0.0, "feature 12 still dead"


def test_v4_has_no_placeholder_so_its_ablation_survives(live_fields):
    """Cascade rule 4 precedes rule 5. A placeholder on V4 would give its
    controls an accessible name and collapse the fallback the variant exists to
    test, so V4 is the one variant that must not have one."""
    for key, field_descriptor in live_fields["v4_unassociated"].items():
        assert field_descriptor.placeholder == "", key
        assert field_descriptor.label_rule == 5, key

    # Every other variant keeps its placeholders and still resolves at rule 3.
    for key, field_descriptor in live_fields["v0_base"].items():
        assert field_descriptor.label_rule == 3, key


def test_relabelled_variant_renames_its_name_attributes_too(live_fields):
    """Feature 2 must not become a back door to the original identity. A real
    relabelled system renames its fields; so does V2."""
    v0 = live_fields["v0_base"]
    v2 = live_fields["v2_relabeled"]

    assert v0["grade"].name == "grade"
    assert v2["grade"].name == "final_rating"
    assert v0["course"].name == "course"
    assert v2["course"].name == "degree_program"
    assert v2["remarks"].name == "academic_standing"

    # None of V2's names leaks the data-key the portal uses as ground truth.
    for key, field_descriptor in v2.items():
        if key in {"course", "grade", "remarks"}:
            assert field_descriptor.name != key, (
                f"V2 name {field_descriptor.name!r} still exposes {key!r}"
            )


@needs_encoder
def test_only_the_two_known_features_are_constant(live_fields):
    """A feature that never varies cannot influence any decision, and that is
    invisible in accuracy numbers - so it is asserted here instead.

    Two are constant today, for different reasons:

      8  lex_abbreviation   - no sheet header is an abbreviation of a field
                              label. The architecture's own sheet spec uses
                              'Yr Level', which would fire it; this instrument's
                              headers are spelled out, and the field labels
                              carry format hints ('Year 1-5') that break the
                              token-wise comparison either way.
      15 str_already_filled - reads live fill state, which nothing supplies
                              until the Resolver and Executor drive it at
                              Milestone 5. Inert, not broken.

    A third constant appearing means a feature has silently stopped reading its
    input, which is what happened to 2, 3 and 12 before the portal was given
    name, placeholder and maxlength attributes.
    """
    import statistics

    from executor.sheet_reader import read_sheet

    sheet = REPO / "data" / "sheets" / "grade_sheet_status.xlsx"
    if not sheet.exists():
        pytest.skip("run data/sheets/make_sheets.py first")

    _, columns = read_sheet(sheet, "SUMMARY", 11, "STUDENT NUMBER")
    columns = [c for c in columns if c.header]
    fields = list(live_fields["v0_base"].values())

    vectors = [extract(c) for c in candidates(columns, fields)]
    constant = {
        name
        for index, name in enumerate(FEATURE_NAMES)
        if statistics.pstdev(v[index] for v in vectors) == 0.0
    }
    assert constant == {"lex_abbreviation", "str_already_filled"}, constant
