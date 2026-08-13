"""
Ported from RJGanzon/Intern (coworker's Scope #2 progress),
tests/test_matcher_resolver.py -- the self-contained subset covering the
matcher's shape/versioning contract and the resolver's partition/assign/
abstain logic. Original file also has a "Milestone 5 done-when" section
(training the matcher on their real demo session, scanning their live
portal, the feature-16 ablation) -- not ported here, since it needs their
full training pipeline (model.train, model.baselines) and live demo/portal
data this project doesn't have. That section is the natural next step once
there's real demonstration data to train against.

Original docstring, preserved: "Milestone 5: the model reproduces the V0
mapping from demonstrations alone, and the Resolver assigns, abstains and
partitions the way 3.9 requires."
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "components" / "scope2"))

from descriptors import FieldDescriptor, KIND_CONTROL, KIND_INPUT, SourceColumn  # noqa: E402
from features.extractor import DIMS, FEATURE_NAMES  # noqa: E402
from model import matcher as matcher_module  # noqa: E402
from model.matcher import ExtractorMismatch, Matcher, load, save  # noqa: E402
from resolver.assign import (  # noqa: E402
    BUCKET_CONTROL, BUCKET_DERIVED, BUCKET_MAPPABLE, STATUS_ABSTAIN,
    STATUS_AUTO, margins, partition, resolve,
)


def field(label, key, dom_order, input_type="text", kind=KIND_INPUT):
    return FieldDescriptor(
        label=label, label_rule=3, kind=kind, input_type=input_type,
        column_key=f"t:col{dom_order}", column_index=dom_order,
        header_text=label, dom_order=dom_order, truth_key=key,
    )


def column(header, index):
    return SourceColumn(header=header, index=index, inferred_type="text",
                        samples=["x"], non_null=50, total=50)


# ----------------------------------------------------------- 3.7 matcher


def test_matcher_is_the_documented_size():
    model = Matcher()
    assert model.dims == DIMS == 17
    # 3.7 says "~1,100 params"; 17 inputs give 1,121.
    assert 1000 <= model.parameter_count() <= 1200


def test_matcher_input_width_follows_the_extractor():
    """The network must never carry its own hard-coded width - that is how a
    feature change becomes a silent shape error."""
    assert Matcher().net[0].in_features == DIMS


def test_saved_model_refuses_to_load_against_different_features(tmp_path):
    """3.6: a feature change invalidates a trained matcher."""
    path = save(Matcher(), tmp_path / "m.pt")
    model, artifact = load(path)
    assert model.dims == DIMS
    assert artifact["feature_names"] == FEATURE_NAMES

    original = matcher_module.EXTRACTOR_VERSION
    try:
        matcher_module.EXTRACTOR_VERSION = "extractor-v2-20d"
        with pytest.raises(ExtractorMismatch):
            load(path)
        model, _ = load(path, allow_mismatch=True)   # explicit override only
        assert model.dims == DIMS
    finally:
        matcher_module.EXTRACTOR_VERSION = original


# ---------------------------------------------------------- 3.9 resolver


def test_partition_runs_before_assignment():
    fields = [
        field("Select", None, 0, "checkbox", KIND_CONTROL),
        field("Course", "course", 1),
        field("Grade", "grade", 2, "number"),
        field("Remarks", "remarks", 3, "select"),
    ]
    buckets = partition(fields, derived_labels={"Remarks"})

    assert [f.label for f in buckets[BUCKET_MAPPABLE]] == ["Course", "Grade"]
    assert [f.label for f in buckets[BUCKET_DERIVED]] == ["Remarks"]
    assert [f.label for f in buckets[BUCKET_CONTROL]] == ["Select"]


def test_a_derived_field_never_receives_a_column():
    """3.9: 'leave Remarks in and it competes for a source column, and
    Hungarian's one-to-one constraint may then displace a correct mapping'."""
    columns = [column("FINAL GRADE", 0), column("STATUS", 1)]
    fields = [field("Grade", "grade", 0, "number"), field("Remarks", "remarks", 1, "select")]

    matrix = [[0.99, 0.90], [0.80, 0.95]]
    mapping = resolve(columns, fields, matrix, derived_labels={"Remarks"})

    assert "Remarks" not in {a.target_label for a in mapping.assignments}
    assert mapping.partition[BUCKET_DERIVED] == ["Remarks"]
    assert mapping.as_truth() == {"Grade": "FINAL GRADE"}


def test_a_control_field_never_receives_a_column():
    columns = [column("No.", 0)]
    fields = [field("Select", None, 0, "checkbox", KIND_CONTROL),
              field("Course", "course", 1)]
    mapping = resolve(columns, fields, [[0.99, 0.10]])

    assert "Select" not in {a.target_label for a in mapping.assignments}
    assert mapping.partition[BUCKET_CONTROL] == ["Select"]


def test_abstains_on_a_low_score():
    columns = [column("MYSTERY", 0)]
    fields = [field("Course", "course", 0), field("Grade", "grade", 1, "number")]
    mapping = resolve(columns, fields, [[0.40, 0.10]], tau=0.6, delta=0.15)

    assert mapping.assignments[0].status == STATUS_ABSTAIN
    assert mapping.as_truth() == {}


def test_abstains_on_a_narrow_margin_even_when_the_score_is_high():
    """The V5 case: two columns the model cannot tell apart. A confident
    score with a close runner-up is not a confident answer."""
    columns = [column("FINAL GRADE", 0)]
    fields = [field("Grade", "grade", 0, "number"),
              field("Grade (Recomputed)", "grade_recomputed", 1, "number")]
    mapping = resolve(columns, fields, [[0.95, 0.94]], tau=0.6, delta=0.15)

    assignment = mapping.assignments[0]
    assert assignment.score >= 0.6
    assert assignment.margin < 0.15
    assert assignment.status == STATUS_ABSTAIN


def test_margin_is_measured_over_scores_not_over_the_assignment():
    assert margins([[0.9, 0.2, 0.1]]) == [pytest.approx(0.7)]
    assert margins([[0.5]]) == [1.0]
    assert margins([[]]) == [0.0]


def test_surplus_fields_are_reported_unmapped():
    """3.9: pad so unequal counts are handled and surplus fields go unmapped."""
    columns = [column("PROGRAM", 0)]
    fields = [field("Course", "course", 0),
              field("Recommendations", "recommendations", 1, "textarea")]
    mapping = resolve(columns, fields, [[0.99, 0.05]])

    assert mapping.as_truth() == {"Course": "PROGRAM"}
    assert "Recommendations" in mapping.unmapped_fields


def test_surplus_columns_are_reported_unmapped():
    columns = [column("PROGRAM", 0), column("MIDTERM", 1)]
    fields = [field("Course", "course", 0)]
    mapping = resolve(columns, fields, [[0.99], [0.20]])

    assert "MIDTERM" in mapping.unmapped_columns


def test_resolver_output_matches_the_2_5_contract():
    columns = [column("PROGRAM", 0)]
    fields = [field("Course", "course", 0)]
    payload = resolve(columns, fields, [[0.99]]).to_dict()

    assert set(payload) >= {"assignments", "unmapped_fields", "unmapped_columns"}
    for assignment in payload["assignments"]:
        assert set(assignment) == {
            "source_header", "target_label", "score", "margin", "status"
        }
        assert assignment["status"] in {STATUS_AUTO, STATUS_ABSTAIN}
