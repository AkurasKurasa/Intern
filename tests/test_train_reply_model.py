import os
import sys

import pytest
import torch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import reply_model as rm
import reply_recorder as rr
import train_reply_model as trainer
from gmail_client import EmailMessage


def _msg(mid, sender_email, subject, body):
    return EmailMessage(
        id=mid, thread_id="", sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text=body, received_at="2026-08-27T00:00:00Z",
    )


def _seed_examples(path):
    examples = [
        ("m1", "boss@work.com", "status update", "Here's where things stand.",
         "Thanks, that all looks on track."),
        ("m2", "boss@work.com", "quick question", "Do you have a minute?",
         "Sure, call me in 10."),
        ("m3", "boss@work.com", "fyi", "Thought you'd want to see this.",
         "Got it, thanks for flagging."),
        ("m4", "vendor@acme.com", "invoice #221", "Can you confirm receipt?",
         "Confirmed, invoice #221 received."),
        ("m5", "vendor@acme.com", "invoice #222", "Can you confirm receipt?",
         "Confirmed, invoice #222 received."),
        ("m6", "team@work.com", "fwd: doc", "Passing this along to you.",
         "Thanks for forwarding, will review today."),
    ]
    for mid, sender, subject, body, reply in examples:
        rr.record_reply_example(_msg(mid, sender, subject, body), reply, source="live", path=path)
    return len(examples)


class TestBuildDataset:
    def test_produces_one_positive_and_negatives_per_example(self, tmp_path):
        path = str(tmp_path / "reply_examples.jsonl")
        n = _seed_examples(path)
        examples = rr.load_reply_examples(path)
        x, y = trainer.build_dataset(examples)
        expected_rows = n * (1 + min(trainer.NEGATIVES_PER_POSITIVE, n - 1))
        assert x.shape == (expected_rows, trainer.DIMS)
        assert y.shape == (expected_rows,)
        assert float(y.sum()) == n   # exactly one positive per example

    def test_positive_pair_has_near_perfect_cosine(self, tmp_path):
        path = str(tmp_path / "reply_examples.jsonl")
        _seed_examples(path)
        examples = rr.load_reply_examples(path)
        x, y = trainer.build_dataset(examples)
        cos_idx = 0   # context_cosine_sim is always feature 0
        positive_rows = x[y == 1.0]
        assert torch.all(positive_rows[:, cos_idx] > 0.99)


class TestTrain:
    def test_too_few_examples_raises(self, tmp_path):
        path = str(tmp_path / "reply_examples.jsonl")
        rr.record_reply_example(_msg("m1", "a@b.com", "hi", "hi"), "sure thing", source="live", path=path)
        save_path = str(tmp_path / "reply_model.pt")
        with pytest.raises(trainer.TooFewExamplesError):
            trainer.train(path, save_path, epochs=5, lr=1e-2)

    def test_trains_and_saves_loadable_checkpoint(self, tmp_path):
        path = str(tmp_path / "reply_examples.jsonl")
        _seed_examples(path)
        save_path = str(tmp_path / "reply_model.pt")
        result = trainer.train(path, save_path, epochs=50, lr=1e-2)
        assert os.path.exists(save_path)
        assert 0.0 <= result["train_acc"] <= 1.0
        assert result["num_examples"] == 6
        model, artifact = rm.load(save_path)
        assert isinstance(model, rm.ReplyMatchNet)
        assert artifact["metadata"]["num_examples"] == 6
