"""
Regression test: train() must not write its per-run metrics log to the real
production file (data/output/transformer_training_log.jsonl) when a caller
supplies its own log_path.

Found 2026-08-18: transformer.train()'s log write path was hardcoded
(anchored on this file's own location, not on save_path/data_dir), so every
pytest run of test_checkpoint_selection_by_click_acc.py silently appended a
row to the REAL log using its tiny 4-example test fixture. 27 identical rows
(n_train=4, click_acc=0.396) accumulated in the real log over three days,
making it useless as evidence of real training progress -- the log a human
would check to see "did more demos help" was entirely test noise.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
import intelligence.model.transformer as transformer_mod

_REAL_LOG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "output" / "transformer_training_log.jsonl"
)


def _write_session(directory: Path, n: int = 8) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        step = {
            "state": {
                "screen_resolution": [1920, 1080],
                "focused_element_id": None,
                "elements": [
                    {"element_id": "e0", "type": "editcontrol", "window_role": "active",
                     "label": "Field", "text": "Field", "value": "", "bbox": [100, 100, 300, 130],
                     "confidence": 1.0},
                ],
            },
            "mouse": {"actions": [{"type": "click", "position": [200, 115]}]},
            "keyboard": {"actions": []},
        }
        (directory / f"live_step_{i:04d}.json").write_text(json.dumps(step), encoding="utf-8")


def _fake_run_epoch(model, loader, optimizer, device, lambda_click, lambda_key,
                     label_smoothing, class_weights=None, **_ignored_new_kwargs):
    return dict(loss=1.0, l_type=0.1, l_click=0.1, l_key=0.0,
                accuracy=0.9, click_acc=0.5, src_acc=0.0)


def test_train_writes_to_supplied_log_path_not_the_real_one(tmp_path):
    _write_session(tmp_path)
    save_path = tmp_path / "out" / "model.pt"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    custom_log = tmp_path / "isolated_training_log.jsonl"

    real_log_lines_before = (
        _REAL_LOG_PATH.read_text(encoding="utf-8").splitlines() if _REAL_LOG_PATH.exists() else []
    )

    with patch.object(transformer_mod, "_run_epoch", side_effect=_fake_run_epoch):
        transformer_mod.train(
            data_dir=str(tmp_path), epochs=1, batch_size=4, hist_len=4,
            val_split=0.3, save_path=str(save_path), d_model=8, nhead=2,
            num_layers=1, dim_feedforward=16, verbose=False,
            log_path=str(custom_log),
        )

    assert custom_log.exists(), "train() should write its metrics row to the supplied log_path"
    rows = [json.loads(l) for l in custom_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1
    assert rows[0]["n_train"] >= 1

    real_log_lines_after = (
        _REAL_LOG_PATH.read_text(encoding="utf-8").splitlines() if _REAL_LOG_PATH.exists() else []
    )
    assert real_log_lines_after == real_log_lines_before, (
        "train() must not touch the real production log when a custom log_path is supplied"
    )


def test_train_still_defaults_to_the_real_log_when_no_path_given(tmp_path):
    """log_path is opt-in -- omitting it keeps the existing default behavior
    (real callers like scripts/train.py never pass it, so this must not regress
    them). Only checks the row gets written *somewhere* real, not the path itself,
    to avoid this test polluting the real log the same way the bug did."""
    import inspect
    assert "log_path" in inspect.signature(transformer_mod.train).parameters
    assert inspect.signature(transformer_mod.train).parameters["log_path"].default is None
