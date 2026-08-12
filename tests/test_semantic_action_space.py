"""
Regression tests for the Universal Semantic Action Space port
(action_space="legacy"|"semantic" in TrajectoryDataset/train()/predict()).

Context: found investigating why tonight's bug fixes kept needing to be
applied twice, to duplicate deterministic code paths in agent.py
(components/agent/agent.py) that never shared logic. origin/verb-loop-
rewrite (diverged from master 2026-07-10, never merged) built a genuinely
different fix for the underlying cause: a richer action vocabulary +
split pointer heads, trained instead of hand-coded, reporting click_acc
0.957 vs its own legacy baseline's 0.878 on its own dataset.

Ported 2026-08-12: semantic_action.py (Verb enum + SemanticAction),
action_labeler.py (offline demo-step -> verb translator), and this file's
own subject -- wiring action_space="semantic" through TrajectoryDataset,
train(), and predict() as a NEW, opt-in option alongside the existing
"legacy" default, which is unchanged. The model architecture itself
needed ZERO changes (TransformerAgentNetwork.num_actions was already a
parameter, and the click_elem/source_elem pointer heads are re-purposed,
not duplicated) -- confirmed by reading the class before touching it.

These tests exercise the dataset-building plumbing with small synthetic
fixtures (matching test_rare_weight_basis_toggle.py's own pattern) --
NOT action_labeler.translate_step()'s own classification logic, which is
tested separately and was already validated against all 5 real eight_Tabs
sessions before this file existed.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from intelligence.model.transformer import (
    TrajectoryDataset, NUM_SEMANTIC_ACTIONS, NUM_ACTIONS, VERB_TO_ID, ID_TO_VERB,
)
from agent.semantic_action import Verb


def _typed_step(field: str, text: str, bbox=(100, 100, 300, 130)) -> dict:
    """A simple keyboard-typed step -- action_labeler classifies this as
    SET_VALUE without needing a next_state (text comes straight from
    pasted_text, not a before/after value diff). Deliberately NO mouse
    click -- a real recorded step only ever has one or the other (the click
    that focuses a field is always its own separate prior step); both
    action_labeler.translate_step and legacy _decode_actions check for a
    click FIRST and would otherwise never reach the keyboard branch at all."""
    return {
        "state": {
            "screen_resolution": [1920, 1080],
            "focused_element_id": "e1",
            "elements": [
                {"element_id": "e1", "type": "editcontrol", "window_role": "active",
                 "label": field, "text": field, "value": "", "bbox": list(bbox),
                 "confidence": 1.0},
            ],
        },
        "mouse": {"actions": []},
        "keyboard": {"actions": [{"strokes": [{"pasted_text": text}]}]},
    }


def _write_semantic_session(directory: Path, n: int = 6) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (directory / f"live_step_{i:04d}.json").write_text(
            json.dumps(_typed_step("Field", f"value{i}")), encoding="utf-8")


class TestActionSpaceValidation:
    def test_invalid_action_space_raises(self, tmp_path):
        _write_semantic_session(tmp_path)
        with pytest.raises(ValueError):
            TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4, action_space="bogus")

    def test_default_action_space_is_legacy(self, tmp_path):
        _write_semantic_session(tmp_path)
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4)
        assert ds.action_space == "legacy"


class TestSemanticModeBuildsRealSamples:
    def test_semantic_mode_produces_samples(self, tmp_path):
        _write_semantic_session(tmp_path)
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4, action_space="semantic")
        assert len(ds) > 0

    def test_semantic_targets_are_valid_verb_ids(self, tmp_path):
        """Every target_type in the built samples must be a real VERB_TO_ID
        value (0..NUM_SEMANTIC_ACTIONS-1), not a legacy ACTION_* id -- the
        two schemes numerically collide (e.g. FOCUS=0 == ACTION_NOOP=0), so
        this pins down that semantic mode is actually using its own scheme,
        not silently falling back to the legacy one."""
        _write_semantic_session(tmp_path)
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4, action_space="semantic")
        target_types = {s[4] for s in ds._samples}
        assert target_types, "expected at least one sample"
        assert target_types <= set(ID_TO_VERB.keys())

    def test_typed_steps_are_labeled_set_value(self, tmp_path):
        """Direct, concrete check: a session of pure keyboard-typed steps
        should classify as SET_VALUE (or FOCUS for the very first click-only
        entry into the window), not some other verb."""
        _write_semantic_session(tmp_path)
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4, action_space="semantic")
        target_types = {s[4] for s in ds._samples}
        assert VERB_TO_ID[Verb.SET_VALUE] in target_types

    def test_legacy_mode_unaffected_by_same_session(self, tmp_path):
        """No regression: the identical session, in legacy mode, still
        classifies as ACTION_KEYBOARD (2), not any semantic verb id."""
        _write_semantic_session(tmp_path)
        from intelligence.model.transformer import ACTION_KEYBOARD
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4, action_space="legacy")
        target_types = {s[4] for s in ds._samples}
        assert target_types <= {ACTION_KEYBOARD, 0}  # keyboard, or noop-padding


class TestClassCountsAreActionSpaceAware:
    def test_semantic_mode_reports_verb_names_not_legacy_names(self, tmp_path):
        _write_semantic_session(tmp_path)
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4, action_space="semantic")
        counts = ds.class_counts()
        # set_value's real id (1) numerically collides with legacy ACTION_CLICK (1) --
        # if class_counts() used the legacy names dict, this would misreport as "click".
        assert "set_value" in counts
        assert "click" not in counts

    def test_legacy_mode_reports_legacy_names(self, tmp_path):
        _write_semantic_session(tmp_path)
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4, action_space="legacy")
        counts = ds.class_counts()
        assert "keyboard" in counts


class TestVerbConstants:
    def test_num_semantic_actions_matches_verb_enum_length(self):
        assert NUM_SEMANTIC_ACTIONS == len(list(Verb))

    def test_semantic_and_legacy_action_counts_differ(self):
        """A real check that these are genuinely two different vocabularies,
        not the same scheme relabeled."""
        assert NUM_SEMANTIC_ACTIONS != NUM_ACTIONS

    def test_verb_to_id_and_id_to_verb_are_inverses(self):
        for verb, i in VERB_TO_ID.items():
            assert ID_TO_VERB[i] is verb
