"""
Regression test: transformer.train()'s own Python-level defaults for lr and
weight_decay must match what every actual training entry point in this
codebase uses (train.py, BCTrainer, transformer.py's own CLI -- all
lr=1e-3, weight_decay=1e-4).

Found 2026-08-08: train()'s function signature defaulted to lr=1e-4,
weight_decay=1e-2 -- 10x lower and 100x stronger than every real caller,
and inconsistent with train()'s own docstring (which describes 1e-4 as
"the default" for weight_decay). Every script that called train() directly
without passing these explicitly silently trained at a fraction of the
intended learning rate, capping val_click_acc far below what the same
architecture/data reaches under the real settings (28% vs 49% observed).
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from intelligence.model.transformer import train


def test_lr_default_matches_every_other_entry_point():
    sig = inspect.signature(train)
    assert sig.parameters["lr"].default == 1e-3


def test_weight_decay_default_matches_every_other_entry_point():
    sig = inspect.signature(train)
    assert sig.parameters["weight_decay"].default == 1e-4
