"""
Regression test for TrajectoryDataset's val-time augmentation bug.

Found 2026-08-07: train() and val_loader share ONE TrajectoryDataset instance
(train_ds/val_ds are just index subsets from the same random_split). __getitem__
applied aug_drop_prob's element-dropout + shuffle unconditionally, so the
"held-out validation" pass was scored on the SAME randomly-corrupted inputs as
training, not clean ones. Hiding ~10% of on-screen fields makes the click
choice *easier* (fewer confusable candidates), which silently inflated the
reported val_click_acc relative to what the model actually sees live.

Fix: TrajectoryDataset._eval_mode toggle — when True, __getitem__ skips the
augmentation block regardless of aug_drop_prob. train() sets it True only
around the val_loader epoch pass.
"""
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from intelligence.model.transformer import TrajectoryDataset, ELEM_FEATURES


def _make_dataset(aug_drop_prob: float, eval_mode: bool) -> TrajectoryDataset:
    ds = object.__new__(TrajectoryDataset)
    ds.max_elements = 8
    ds.hist_len = 1
    ds.aug_drop_prob = aug_drop_prob
    ds._eval_mode = eval_mode
    ds._sample_weights = None
    ds._zero_tensor = torch.zeros(ds.max_elements, ELEM_FEATURES)

    # 8 "real" elements, all-ones rows so dropout-zeroing is trivially detectable.
    fake_state = torch.ones(ds.max_elements, ELEM_FEATURES)
    fpath = Path("fake_trace_0.json")
    ds._tensor_cache = {fpath: fake_state}
    ds._grouped_files = [[fpath]]

    # tgt_click_idx=2, src_idx=-1 — one click sample, aug_drop_prob=1.0 so every
    # unprotected row would be zeroed if augmentation runs at all.
    ds._samples = [(0, 0, [], [], 0, 2, 0.0, -1)]
    return ds


def test_eval_mode_true_disables_augmentation_even_with_drop_prob_set():
    ds = _make_dataset(aug_drop_prob=1.0, eval_mode=True)
    states, *_ = ds[0]
    assert torch.all(states == 1.0), "eval_mode=True must return the clean, unaugmented state"


def test_eval_mode_false_applies_augmentation_when_drop_prob_set():
    ds = _make_dataset(aug_drop_prob=1.0, eval_mode=False)
    states, *_ = ds[0]
    # Every row except the protected click-target row (idx 2, post-shuffle) gets
    # zeroed at drop_prob=1.0 — the state can no longer be all-ones.
    assert not torch.all(states == 1.0), "eval_mode=False must still apply augmentation"


def test_eval_mode_defaults_to_false_on_construction():
    # __init__ needs real trace files to run fully; confirm via source that it
    # sets _eval_mode = False before any file scanning, so a fresh dataset
    # never accidentally starts in eval mode.
    import inspect
    src = inspect.getsource(TrajectoryDataset.__init__)
    assert "self._eval_mode" in src and "False" in src.split("self._eval_mode")[1][:20]
