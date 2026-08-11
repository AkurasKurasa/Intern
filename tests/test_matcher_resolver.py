"""Milestone 5: the model reproduces the V0 mapping from demonstrations alone,
and the Resolver assigns, abstains and partitions the way 3.9 requires.

Run:  python -m pytest tests/test_matcher_resolver.py -q
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from executor.scanner import FieldDescriptor, KIND_CONTROL, KIND_INPUT, scan_variants  # noqa: E402
from executor.sheet_reader import SourceColumn, read_sheet  # noqa: E402
from features import encoders  # noqa: E402
from features.extractor import DIMS, FEATURE_NAMES  # noqa: E402
from model import matcher as matcher_module  # noqa: E402
from model.baselines import cosine_matrix, llm_baseline, string_match_matrix  # noqa: E402
from model.matcher import ExtractorMismatch, Matcher, load, save  # noqa: E402
from model.train import build_dataset, paraphrases_for, score_matrix, train  # noqa: E402
from resolver.assign import (  # noqa: E402
    BUCKET_CONTROL, BUCKET_DERIVED, BUCKET_MAPPABLE, STATUS_ABSTAIN,
    STATUS_AUTO, margins, partition, resolve,
)

SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"
SESSION = REPO / "data" / "demos" / "v0_base_3rows.jsonl"

# Ground truth stated against the portal's data-key rather than its visible
# label, so it still holds on a variant that renames every field.
TRUTH_BY_KEY = {"PROGRAM": "course", "YEAR LEVEL": "year", "FINAL GRADE": "grade"}

needs_encoder = pytest.mark.skipif(
    not encoders.available(), reason="sentence-transformers unavailable"
)
needs_demo = pytest.mark.skipif(
    not (SHEET.exists() and SESSION.exists()),
    reason="run make_sheets.py and demo_session.py first",
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


def test_paraphrase_augmentation_covers_the_demonstrated_fields():
    assert "Final Rating" in paraphrases_for("Grade 0-100")
    assert "Degree Program" in paraphrases_for("Course")
    assert "Yr Level" in paraphrases_for("Year 1-5")
    assert paraphrases_for("Nonexistent Field") == []


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

    # Scores that would happily give Remarks the spare column.
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
    """The V5 case: two columns the model cannot tell apart. A confident score
    with a close runner-up is not a confident answer."""
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


# --------------------------------------------- Milestone 5 done-when


@pytest.fixture(scope="module")
def trained():
    _, columns = read_sheet(SHEET, "SUMMARY", 11, "STUDENT NUMBER")
    columns = [c for c in columns if c.header]
    examples, truth, derived = build_dataset(SESSION, "v0_base")
    model, _ = train(examples)
    return model, columns, truth, derived, examples


@needs_encoder
@needs_demo
def test_the_model_reproduces_the_v0_mapping_from_demos_alone(trained):
    """Milestone 5's done-when."""
    model, columns, truth, derived, _ = trained
    fields = [d for d in scan_variants(["v0_base"])["v0_base"]
              if d.kind == KIND_INPUT and d.label not in derived]

    mapping = resolve(columns, fields, score_matrix(model, columns, fields))
    assert mapping.as_truth() == truth

    # Recommendations has no source column and must stay unmapped (3.11).
    assert "Recommendations optional" in mapping.unmapped_fields


@needs_encoder
@needs_demo
def test_the_matcher_beats_both_implemented_baselines(trained):
    """3.7's comparison table. The architecture predicts string matching fails
    immediately - not one base pairing is an exact match and Program -> Course
    is a pure synonym."""
    model, columns, truth, derived, _ = trained
    fields = [d for d in scan_variants(["v0_base"])["v0_base"]
              if d.kind == KIND_INPUT and d.label not in derived]

    def correct(matrix):
        return sum(1 for label, header in resolve(columns, fields, matrix).as_truth().items()
                   if truth.get(label) == header)

    string_score = correct(string_match_matrix(columns, fields))
    cosine_score = correct(cosine_matrix(columns, fields))
    model_score = correct(score_matrix(model, columns, fields))

    assert model_score == len(truth) == 3
    assert string_score == 0, "string matching should fail on day one"
    assert model_score > cosine_score


def test_the_llm_baseline_is_absent_not_faked():
    """A fabricated reference ceiling is worse than a missing one."""
    with pytest.raises(NotImplementedError, match="API key"):
        llm_baseline()


# ------------------------------------------ the feature 16 ablation (7)


@needs_encoder
@needs_demo
def test_dropping_the_positional_feature_helps_on_a_reordered_ui(trained):
    """3.6 calls feature 16 'deliberately included and deliberately suspect':
    it helps on the base UI and hurts on reordered ones. Training with and
    without it, and reporting accuracy on V1, is the experiment 7 asks for.

    On V1 the columns are the same and only their order moves, so a model that
    leans on position has nothing else to fall back on.
    """
    _, columns, _, _, examples = trained
    position = FEATURE_NAMES.index("pos_rank_distance")

    scanned = scan_variants(["v1_reordered"])["v1_reordered"]
    fields = [d for d in scanned if d.kind == KIND_INPUT and d.truth_key != "remarks"]
    key_of = {f.label: f.truth_key for f in fields}

    def accuracy(model, mask):
        mapping = resolve(columns, fields, score_matrix(model, columns, fields, mask))
        return sum(1 for label, header in mapping.as_truth().items()
                   if TRUTH_BY_KEY.get(header) == key_of.get(label))

    with_position, _ = train(examples)
    without_position, _ = train(examples, feature_mask={position})

    assert accuracy(without_position, {position}) > accuracy(with_position, None)
