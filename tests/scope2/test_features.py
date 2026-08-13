"""
Ported from RJGanzon/Intern (coworker's Scope #2 progress), tests/test_features.py
-- the self-contained subset that only needs the matching core (features/,
labeling/, and this project's own components/scope2/types.py), not their
live browser/Excel scanning infrastructure (not ported here; that section
of their original test file, "instrument diagnostics", depends on
executor.scanner.scan_variants and executor.sheet_reader.read_sheet against
their own mock portal and synthetic sheets).

Original docstring, preserved: "Milestone 3: the Feature Extractor, the
module 3.6 calls the critical one. The load-bearing test here is
test_no_demonstration_only_signal. Everything else is arithmetic; that one
is the architectural invariant."

Adapted 2026-08-14 for this project's layout: SourceColumn/FieldDescriptor
come from components/scope2/types.py (extracted from their executor.scanner/
executor.sheet_reader specifically to avoid pulling in pandas/playwright for
a test that doesn't need either), and the sys.path bootstrap points at
components/scope2 instead of a flat repo root.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "components" / "scope2"))

from descriptors import FieldDescriptor, KIND_INPUT, SourceColumn  # noqa: E402
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
    """The load-bearing architectural invariant: the extractor is one
    implementation used in both training and execution, so nothing it reads
    may exist only at demo time. If any feature ever consulted the ground-
    truth key, the matcher would score perfectly in training and collapse
    on an unseen UI."""
    truthful = make(fld=field(truth_key="grade"))
    blinded = make(fld=field(truth_key=None))
    assert extract(truthful) == extract(blinded)

    misleading = make(fld=field(truth_key="recommendations"))
    assert extract(truthful) == extract(misleading)


def test_semantic_features_fail_loudly_when_the_encoder_is_missing(monkeypatch):
    monkeypatch.setattr(encoders, "available", lambda: False)
    with pytest.raises(encoders.EncoderUnavailable):
        extract(make())
    vector = extract(make(), require_encoder=False)
    assert vector[:4] == [0.0, 0.0, 0.0, 0.0]
    assert len(vector) == DIMS


# ------------------------------------------------------------------ units


def test_levenshtein_ratio():
    assert levenshtein_ratio("grade", "grade") == 1.0
    assert levenshtein_ratio("", "") == 1.0
    assert levenshtein_ratio("grade", "") == 0.0
    assert 0.0 < levenshtein_ratio("Yr Level", "Year Level") < 1.0
    assert levenshtein_ratio("GRADE", "grade") == 1.0


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
    assert is_abbreviation("Yr Level", "Year Level") == 1.0
    assert is_abbreviation("Sec", "Section") == 1.0
    assert is_abbreviation("Year Level", "Year Level") == 0.0
    assert is_abbreviation("Program", "Course") == 0.0
    assert is_abbreviation("Grade", "Course") == 0.0
    assert is_abbreviation("", "Course") == 0.0
    assert is_abbreviation("Year Level", "Yr Level") == 1.0


def test_value_shape_rewards_a_column_that_fits_the_control():
    fits = value_shape(make())
    assert fits[0] == 1.0
    assert fits[1] == 1.0
    assert fits[2] == 1.0

    text_field = field("Course", "text", 0, min=None, max=None, required=False)
    assert value_shape(make(fld=text_field))[0] == 0.0

    scaled = field("Grade 1.00-5.00", "number", 4, min="1", max="5")
    assert value_shape(make(fld=scaled))[2] == 0.0


def test_structural_required_fit_and_fill_state():
    f13, f14, f15 = structural(make())
    assert f13 == 1.0
    assert f14 == 1.0
    assert f15 == 0.0

    sparse = column(non_null=25, total=50)
    assert structural(make(col=sparse))[1] == pytest.approx(0.5)

    optional = field("Recommendations optional", "textarea", 8,
                     required=False, min=None, max=None)
    assert structural(make(fld=optional))[1] == 0.5

    already = make(filled=frozenset({"Grade 0-100"}))
    assert structural(already)[2] == 1.0


def test_positional_feature_peaks_on_aligned_ranks():
    aligned = make(col=column(index=0), fld=field(dom_order=0))
    assert positional(aligned)[0] == pytest.approx(1.0)

    opposed = make(col=column(index=0), fld=field(dom_order=4))
    assert positional(opposed)[0] == pytest.approx(0.0)

    assert positional(make(n_columns=1, n_fields=1))[0] == 1.0


def test_option_overlap_is_the_feature_that_maps_a_select():
    remarks = field("Remarks", "select", 5, options=["Passed", "Failed"],
                    min=None, max=None, required=False)

    status = column("REMARKS", 10, "text", ["PASSED", "FAILED", "PASSED"])
    assert option_overlap(make(col=status, fld=remarks))[0] == 1.0

    assert option_overlap(make(fld=remarks))[0] == 0.0
    assert option_overlap(make())[0] == 0.0


@needs_encoder
def test_the_right_pairing_outscores_its_near_duplicate():
    """MIDTERM and FINAL GRADE are both numeric 0-100 columns, so only the
    semantic features can separate them."""
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
