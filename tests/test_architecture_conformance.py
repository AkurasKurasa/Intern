"""Conformance to intern-capstone-architecture.md.

Not behaviour - shape. These tests fail when the code drifts from the document
or when the document's own contracts stop being honoured, which is the failure
mode that is easiest to miss and hardest to notice later.

Deliberate deviations are asserted *as* deviations, so they stay visible and
cannot be mistaken for accidents.

Run:  python -m pytest tests/test_architecture_conformance.py -q
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from features.extractor import DIMS, FEATURE_NAMES, VERSION  # noqa: E402
from labeling.resolve import CASCADE  # noqa: E402

MAPPING = REPO / "data" / "mappings" / "v0_handwritten.json"


# ------------------------------------------------------- 5. repo layout


def test_modules_that_exist_sit_where_the_layout_says():
    for relative in [
        "recorder/excel_recorder.py",
        "recorder/extension/manifest.json",
        "recorder/extension/content.js",
        "recorder/extension/background.js",
        "recorder/reconciler.py",
        "labeling/resolve.py",
        "features/extractor.py",
        "features/encoders.py",
        "executor/scanner.py",
        "executor/runner.py",
        "model/matcher.py",
        "model/train.py",
        "model/baselines.py",
        "resolver/assign.py",
        "rules/detect.py",
        "rules/induce.py",
        "rules/options.py",
        "eval/run_variants.py",
        "mocksite",
        "data/sheets",
        "data/runs",
        "tests",
    ]:
        assert (REPO / relative).exists(), f"5 lists {relative}, it is missing"


def test_additions_to_the_layout_are_intentional():
    """Files the architecture's tree does not list. Each is here for a stated
    reason; this test exists so a future reader knows they were not strays."""
    additions = {
        # 3.4 specifies a Sheet Reader but the tree gives it no file.
        "executor/sheet_reader.py",
        # 3.5's anti-drift split: the browser half of the label cascade.
        "executor/extract_context.js",
        # Milestone 3's hand-written mapping, in the 2.5 shape.
        "data/mappings",
        # Embedding cache, so a rerun does not re-encode.
        "data/cache",
        # 3.3's confirmation gate; the tree names no file for it.
        "recorder/confirm.py",
        # Event contracts 2.1/2.2 and their JSONL form.
        "recorder/events.py",
        # Milestone 4's reproducible demonstration harness.
        "recorder/demo_session.py",
        # Milestone 6 end to end; the tree names no file for it.
        "rules/induce_from_session.py",
    }
    for relative in additions:
        assert (REPO / relative).exists(), f"{relative} is documented but missing"


def test_no_rpa_figure_is_ever_invented():
    """Milestone 10's RPA half needs the tool and a human operator. The harness
    may measure this system automatically, but it must never supply a default,
    an estimate or a prediction for the RPA column - that table is the one most
    likely to be quoted out of context."""
    source = (REPO / "eval" / "rpa_comparison.py").read_text(encoding="utf-8")

    # The measurements file is read, never written or seeded.
    assert "def load_measurements" in source
    assert ".write_text" not in source.split("def load_measurements")[1][:400]

    # Missing values surface as an explicit marker rather than a number.
    assert 'NOT_MEASURED = "not measured"' in source
    assert "INCOMPLETE" in source

    # The operator's own file may exist - they have to fill it in somewhere -
    # but the template it comes from must, so a blank one can always be made.
    assert (REPO / "eval" / "rpa_measurements.template.json").exists()
    assert (REPO / "eval" / "rpa_protocol.md").exists()


# --------------------------------------------------- 3.2 label cascade


def test_cascade_has_the_six_rules_in_the_documented_order():
    assert [key for key, _ in CASCADE] == [
        "label_for", "label_wrapping", "aria", "placeholder",
        "preceding_text", "name_attr",
    ]


def test_the_cascade_has_exactly_one_implementation():
    """3.5: the recorder and the scanner must not each resolve labels. The
    browser side may only gather raw context."""
    import re

    js = (REPO / "executor" / "extract_context.js").read_text(encoding="utf-8")
    # Comments legitimately talk about the cascade; only code counts.
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    code = re.sub(r"//.*", "", code)

    for forbidden in ["resolveLabel", "bestLabel", "pickLabel", "chooseLabel"]:
        assert forbidden not in code, f"extract_context.js decides labels ({forbidden})"

    # The giveaway would be the browser emitting a single settled name rather
    # than one field per cascade rule.
    assert "label:" not in code, "extract_context.js emits a decided label"
    for key in ("label_for", "label_wrapping", "aria", "placeholder",
                "preceding_text", "name"):
        assert f"{key}:" in code, f"raw context omits {key}"

    # Only one module may walk the cascade. Keying on the function name alone
    # is too crude - resolver/assign.py also has a resolve(), for assignment,
    # which is a different thing entirely.
    walkers = [
        path for path in REPO.rglob("*.py")
        if "tests" not in path.parts
        and "for index, (key, _) in enumerate(CASCADE" in path.read_text(encoding="utf-8")
    ]
    assert [p.name for p in walkers] == ["resolve.py"], walkers


# ------------------------------------------------ 3.6 feature extractor


def test_feature_count_matches_the_documented_list_not_its_heading():
    """3.6 heads its table 'v1, 16 dims' and then lists seventeen features, and
    3.7 declares Linear(16->32). The list is authoritative; this pins the
    resolution so the matcher is not built against the wrong width."""
    assert DIMS == 17
    assert len(FEATURE_NAMES) == 17
    assert "17d" in VERSION


def test_every_documented_feature_group_is_present():
    groups = {
        "sem_": 4,   # 1-4 semantic
        "lex_": 4,   # 5-8 lexical
        "val_": 4,   # 9-12 value shape
        "str_": 3,   # 13-15 structural
        "pos_": 1,   # 16 positional
        "opt_": 1,   # 17 option set
    }
    for prefix, count in groups.items():
        found = [n for n in FEATURE_NAMES if n.startswith(prefix)]
        assert len(found) == count, f"{prefix}: {found}"


def test_extractor_version_is_recorded_for_model_artifacts():
    """3.6: 'a feature change invalidates a trained matcher'."""
    assert VERSION and isinstance(VERSION, str)


# ------------------------------------------------------ 2.4/2.5 contracts


@pytest.fixture(scope="module")
def mapping():
    return json.loads(MAPPING.read_text(encoding="utf-8"))


def test_mapping_matches_the_2_5_shape(mapping):
    assert "assignments" in mapping
    assert "unmapped_fields" in mapping
    assert "unmapped_columns" in mapping
    for assignment in mapping["assignments"]:
        assert set(assignment) >= {
            "source_header", "target_label", "score", "margin", "status"
        }
        assert assignment["status"] in {"auto", "abstain"}


def test_derived_rule_matches_the_2_4_shape(mapping):
    for rule in mapping["derived_rules"]:
        assert set(rule) >= {
            "field", "kind", "depends_on_field", "operator", "cutoff",
            "if_true", "if_false", "observed_interval", "status",
        }
        assert rule["kind"] == "threshold"
        assert rule["status"] in {"proposed", "confirmed"}
        low, high = rule["observed_interval"]
        assert low <= rule["cutoff"] <= high, (
            "3.8: the cutoff must sit inside the interval the demos constrain"
        )


def test_derived_rule_depends_on_a_field_not_a_column(mapping):
    """2.4: depends_on_field names another *form field*, so the rule survives
    its driver's source column being relabelled."""
    targets = {a["target_label"] for a in mapping["assignments"]}
    headers = {a["source_header"] for a in mapping["assignments"]}
    for rule in mapping["derived_rules"]:
        assert rule["depends_on_field"] in targets
        assert rule["depends_on_field"] not in headers


def test_derived_field_is_kept_out_of_the_assignment(mapping):
    """3.9: 'leave Remarks in and it competes for a source column'. Partition
    first, assign second."""
    derived = {r["field"] for r in mapping["derived_rules"]}
    assigned = {a["target_label"] for a in mapping["assignments"]}
    assert not (derived & assigned)


def test_control_fields_are_never_assignment_targets(mapping):
    controls = set(mapping["control_fields"])
    assigned = {a["target_label"] for a in mapping["assignments"]}
    assert not (controls & assigned)
    assert "Select" in controls


def test_documented_deviation_identity_columns_are_alignment_not_mapping(mapping):
    """3.11 lists Student ID and Student Name as mappable, which held for the
    single-record form. The sheet portal prints them, so they align rows and
    verify the alignment instead. Asserted here so the deviation stays explicit
    rather than looking like two columns that failed to map."""
    alignment = mapping["row_alignment"]
    assert alignment["key_column"] == "STUDENT NUMBER"
    assert alignment["verify_column"] == "NAME OF STUDENT"

    assigned_headers = {a["source_header"] for a in mapping["assignments"]}
    assert alignment["key_column"] not in assigned_headers
    assert alignment["verify_column"] not in assigned_headers
    assert alignment["verify_column"] in mapping["unmapped_columns"]


def test_the_base_case_still_tests_non_assignment(mapping):
    """3.11: 'Four of the nine fields must not receive a column'. The sheet
    portal changes the count but not the property - controls, a derived field
    and a legitimately unmapped field all remain."""
    assert mapping["control_fields"]
    assert mapping["derived_rules"]
    assert "Recommendations optional" in mapping["unmapped_fields"]
    assert mapping["unmapped_columns"]
