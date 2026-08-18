"""
Regression test: train() selects the checkpoint to save by click_acc alone,
not a combined (action-type-accuracy + click_acc) score.

Found 2026-08-08 comparing two A/B training runs: ~95% of actions in this
task are type "click", so val_acc (action-TYPE accuracy) saturates near-
ceiling from epoch 1 just by always guessing "click" -- after that it's
noise, not signal. Summing it with click_acc let a lucky early-epoch val_acc
spike outvote a later epoch with much better real click-targeting accuracy:
one run saved epoch 1 (val_acc=0.914, click_acc=0.171) over epoch 44
(val_acc=0.747, click_acc=0.396) -- the worst click-targeting epoch in the
whole run beat one of the best. click_acc alone can't be gamed by the
dominant action type.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
import intelligence.model.transformer as transformer_mod


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


def test_checkpoint_selection_uses_click_acc_alone(tmp_path):
    _write_session(tmp_path)

    # (train, val) pairs per epoch. Epoch 1's val has the higher COMBINED
    # score (0.914 + 0.171 = 1.085) but the worse click_acc; epoch 2's val
    # has the lower combined score (0.747 + 0.396 = 1.143 -- actually higher
    # here too, so use numbers where combined would pick epoch 1 wrongly)
    # -- construct explicitly so combined-score selection would pick epoch 1
    # and click_acc-only selection picks epoch 2.
    # src_acc: a placeholder value -- checkpoint selection is click_acc-only,
    # this test doesn't exercise src_acc's own behavior -- but train()'s
    # per-epoch print statement reads val_m['src_acc'] unconditionally now
    # (added 2026-08-12 alongside the semantic-action-space port), so it must
    # be present or the mocked epoch metrics raise a KeyError there.
    fake_metrics = [
        dict(loss=1.0, l_type=0.1, l_click=0.1, l_key=0.0, accuracy=0.9, click_acc=0.1, src_acc=0.0),      # epoch1 train
        dict(loss=1.0, l_type=0.1, l_click=0.1, l_key=0.0, accuracy=0.914, click_acc=0.171, src_acc=0.0),  # epoch1 val
        dict(loss=0.8, l_type=0.1, l_click=0.1, l_key=0.0, accuracy=0.5, click_acc=0.35, src_acc=0.0),      # epoch2 train
        dict(loss=0.8, l_type=0.1, l_click=0.1, l_key=0.0, accuracy=0.4, click_acc=0.396, src_acc=0.0),     # epoch2 val
    ]
    assert fake_metrics[1]["accuracy"] + fake_metrics[1]["click_acc"] > \
           fake_metrics[3]["accuracy"] + fake_metrics[3]["click_acc"], \
           "test setup check: epoch1 must win under the OLD combined-score rule"
    assert fake_metrics[3]["click_acc"] > fake_metrics[1]["click_acc"], \
           "test setup check: epoch2 must win under click_acc-alone"

    calls = {"i": 0}

    def fake_run_epoch(model, loader, optimizer, device, lambda_click, lambda_key,
                        label_smoothing, class_weights=None, **_ignored_new_kwargs):
        # **_ignored_new_kwargs absorbs key_verb_id/lambda_src (added
        # 2026-08-12 for action_space="semantic") and any future additions,
        # so this mock doesn't need updating every time train() grows a new
        # pass-through parameter for _run_epoch -- this test is specifically
        # about checkpoint selection, not about _run_epoch's own call shape.
        m = fake_metrics[calls["i"]]
        calls["i"] += 1
        return m

    save_path = tmp_path / "out" / "model.pt"
    save_path.parent.mkdir(parents=True, exist_ok=True)

    with patch.object(transformer_mod, "_run_epoch", side_effect=fake_run_epoch):
        transformer_mod.train(
            data_dir=str(tmp_path), epochs=2, batch_size=4, hist_len=4,
            val_split=0.3, save_path=str(save_path), d_model=8, nhead=2,
            num_layers=1, dim_feedforward=16, verbose=False,
            # Isolated so this test's fake 4-example fixture never lands in the
            # real training log (see test_train_log_path_isolation.py) -- this
            # is the exact test that polluted it with 27 identical rows before.
            log_path=str(tmp_path / "training_log.jsonl"),
        )

    ckpt = torch.load(str(save_path), map_location="cpu", weights_only=False)
    assert ckpt["epoch"] == 2, "checkpoint should be from epoch 2 (higher click_acc), not epoch 1"
    assert ckpt["val_click_acc"] == 0.396
