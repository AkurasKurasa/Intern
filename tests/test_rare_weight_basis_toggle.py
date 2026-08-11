"""
Unit tests for TrajectoryDataset's configurable rare_weight_basis /
disambiguate_attempted parameters.

Commit 7999efc7 bundled two changes together: field-identity rare-action
loss weighting, and rank-based repeated-label ("Driver 1/2/3") attempted-
feature disambiguation. That combination regressed val_click_acc to 43.2%
(from a 68.9% baseline); disambiguation ALONE (weighting reverted to
type-level) still regressed to 46.9%. Neither test isolated field-level
weighting on its own -- these two params make that possible. Defaults were
chosen to reproduce the known-good 68.9% baseline exactly (rare_weight_basis
="type", disambiguate_attempted=False); see TrajectoryDataset.__init__'s
docstring for the full rationale.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from intelligence.model.transformer import TrajectoryDataset

_COMMON_BBOX = [100, 100, 300, 130]
_RARE_BBOX   = [100, 200, 300, 230]


def _make_step(target: str) -> dict:
    bbox = _COMMON_BBOX if target == "common" else _RARE_BBOX
    cx, cy = (bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2
    return {
        "state": {
            "screen_resolution": [1920, 1080],
            "focused_element_id": None,
            "elements": [
                {"element_id": "e_common", "type": "editcontrol", "window_role": "active",
                 "label": "CommonField", "text": "CommonField", "value": "", "bbox": _COMMON_BBOX,
                 "confidence": 1.0},
                {"element_id": "e_rare", "type": "editcontrol", "window_role": "active",
                 "label": "RareField", "text": "RareField", "value": "", "bbox": _RARE_BBOX,
                 "confidence": 1.0},
            ],
        },
        "mouse": {"actions": [{"type": "click", "position": [cx, cy]}]},
        "keyboard": {"actions": []},
    }


def _write_same_type_session(directory: Path) -> None:
    """Same control type ('editcontrol') both times, wildly different
    individual click frequency -- the case type-level weighting can't see."""
    directory.mkdir(parents=True, exist_ok=True)
    targets = ["common", "common", "common", "rare"] + ["common"] * 8
    for i, target in enumerate(targets):
        (directory / f"live_step_{i:04d}.json").write_text(
            json.dumps(_make_step(target)), encoding="utf-8")


class TestDefaultsReproduceBaseline:
    def test_default_basis_is_type(self, tmp_path):
        _write_same_type_session(tmp_path)
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4)
        assert ds._rare_weight_basis == "type"

    def test_default_disambiguate_attempted_is_off(self, tmp_path):
        _write_same_type_session(tmp_path)
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4)
        assert ds._disambiguate_attempted == "none"


class TestRareWeightBasisType:
    def test_same_type_different_frequency_fields_get_equal_weight(self, tmp_path):
        """Pins down that the default really is the old, coarse, pre-7999efc7
        basis -- not silently still field-level."""
        _write_same_type_session(tmp_path)
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4)
        common_weights, rare_weights = [], []
        for sample, weight in zip(ds._samples, ds._sample_weights):
            tgt_click_idx = sample[5]
            if tgt_click_idx < 0:
                continue
            (rare_weights if tgt_click_idx == 1 else common_weights).append(weight)
        assert rare_weights and common_weights
        assert set(rare_weights) == set(common_weights) == {1.0}


class TestRareWeightBasisNone:
    def test_none_basis_gives_uniform_weight_to_every_sample(self, tmp_path):
        _write_same_type_session(tmp_path)
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4,
                                rare_weight_basis="none")
        assert ds._sample_weights == [1.0] * len(ds._samples)

    def test_invalid_basis_raises(self, tmp_path):
        _write_same_type_session(tmp_path)
        with pytest.raises(ValueError):
            TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4,
                               rare_weight_basis="bogus")


def _driver_step(clicked: str) -> dict:
    """Two elements sharing the identical label 'First Name' -- the Driver
    1/2 repeated-section pattern _attempt_key's disambiguation targets."""
    e0_bbox, e1_bbox = [100, 100, 300, 130], [100, 300, 300, 330]
    elements = [
        {"element_id": "e0", "type": "editcontrol", "window_role": "active",
         "label": "First Name", "text": "First Name", "value": "", "bbox": e0_bbox,
         "confidence": 1.0},
        {"element_id": "e1", "type": "editcontrol", "window_role": "active",
         "label": "First Name", "text": "First Name", "value": "", "bbox": e1_bbox,
         "confidence": 1.0},
    ]
    bbox = e0_bbox if clicked == "e0" else e1_bbox
    cx, cy = (bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2
    return {
        "state": {"screen_resolution": [1920, 1080], "focused_element_id": None,
                   "elements": elements},
        "mouse": {"actions": [{"type": "click", "position": [cx, cy]}]},
        "keyboard": {"actions": []},
    }


def _write_driver_session(directory: Path) -> list:
    """4 steps: click e0 (Driver 1's First Name), then e1 (Driver 2's First
    Name) three times -- enough steps to satisfy hist_len=4."""
    directory.mkdir(parents=True, exist_ok=True)
    targets = ["e0", "e1", "e1", "e1"]
    paths = []
    for i, target in enumerate(targets):
        p = directory / f"live_step_{i:04d}.json"
        p.write_text(json.dumps(_driver_step(target)), encoding="utf-8")
        paths.append(p)
    return paths


class TestDisambiguateAttemptedToggle:
    def test_off_by_default_collapses_repeated_labels_into_one_attempted_key(self, tmp_path):
        paths = _write_driver_session(tmp_path)
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4)
        # Step 0 clicked e0 (Driver 1). With disambiguation off, e1 (Driver 2,
        # never yet clicked) reads as already-attempted too -- this IS the
        # documented pre-regression baseline behavior, bug and all.
        attempted_before_step1 = ds._attempted_by_file[str(paths[1])]
        assert attempted_before_step1 == {"first name"}

    def test_on_gives_repeated_labels_distinct_attempted_keys(self, tmp_path):
        paths = _write_driver_session(tmp_path)
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4,
                                disambiguate_attempted="rank")
        attempted_before_step1 = ds._attempted_by_file[str(paths[1])]
        # Only e0's own disambiguated key (rank 0) should be marked -- e1's
        # distinct key (rank 1) must NOT be, since e1 hasn't been clicked yet.
        assert attempted_before_step1 == {("first name", 0)}
