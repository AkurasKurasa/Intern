import os
import sys

import pytest
import torch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import decision_recorder as rec
import inbox_model as im
import train_inbox_agent as trainer
from gmail_client import EmailMessage


def _msg(mid, sender_email, subject, body):
    return EmailMessage(
        id=mid, thread_id="", sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text=body, received_at="2026-08-25T00:00:00Z",
    )


def _seed_examples(path):
    examples = [
        ("m1", "boss@work.com", "status update", "Here's where things stand.", "reply"),
        ("m2", "boss@work.com", "quick question", "Do you have a minute?", "reply"),
        ("m3", "boss@work.com", "fyi", "Thought you'd want to see this.", "reply"),
        ("m4", "newsletter@vendor.com", "weekly digest", "This week's roundup.", "leave_alone"),
        ("m5", "newsletter@vendor.com", "weekly digest", "This week's roundup part 2.", "leave_alone"),
        ("m6", "team@work.com", "fwd: doc", "Passing this along to you.", "forward"),
    ]
    for mid, sender, subject, body, decision in examples:
        rec.record_example(_msg(mid, sender, subject, body), decision, source="live", path=path)
    return len(examples)


class TestBuildDataset:
    def test_produces_matching_tensor_and_label_shapes(self, tmp_path):
        from pattern_profile import PatternProfile
        path = str(tmp_path / "examples.jsonl")
        n = _seed_examples(path)
        examples = rec.load_examples(path)
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        x, y, centroids = trainer.build_dataset(examples, profile)
        assert x.shape == (n, trainer.DIMS)
        assert y.shape == (n,)
        assert set(centroids.keys()) == {"reply", "leave_alone", "forward"}

    def test_pattern_profile_affects_pattern_ratio_features(self, tmp_path):
        # Replaces the old rule_hit_scope1/scope2 registry-driven test --
        # the analogous "an injected dependency actually changes the
        # feature vector" property now lives in the pattern-ratio features,
        # since that's the only per-sender signal extract() still computes.
        from inbox_features import FEATURE_NAMES
        from pattern_profile import PatternProfile

        path = str(tmp_path / "examples.jsonl")
        _seed_examples(path)
        examples = rec.load_examples(path)

        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        pattern = profile._get_or_create("work.com")
        pattern.reply_count, pattern.forward_count, pattern.ignore_count = 5, 0, 0
        profile.save()

        x, y, centroids = trainer.build_dataset(examples, profile)
        reply_ratio_idx = FEATURE_NAMES.index("pattern_reply_ratio")

        # m1/m2/m3 (boss@work.com) and m6 (team@work.com) all share the
        # work.com domain -- should reflect the seeded lopsided reply
        # history, keyed by domain not exact sender address.
        for i in (0, 1, 2, 5):
            assert x[i, reply_ratio_idx].item() == pytest.approx(1.0)

        # m4/m5 (newsletter@vendor.com) have no seeded pattern history for
        # their domain -- zero ratio.
        for i in (3, 4):
            assert x[i, reply_ratio_idx].item() == 0.0


class TestTrain:
    def test_too_few_examples_raises(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        rec.record_example(_msg("m1", "a@b.com", "hi", "hi"), "reply", source="live", path=path)
        save_path = str(tmp_path / "model.pt")
        with pytest.raises(trainer.TooFewExamplesError):
            trainer.train(path, save_path, epochs=5, lr=1e-2, val_split=0.15)

    def test_stale_decisions_from_before_a_decision_space_redefinition_are_skipped(self, tmp_path):
        # decision_recorder.record_example() now validates decision strings
        # at write time (a later fix closed that gap), so a stale
        # route_scope1/route_scope2 row can no longer be written through it
        # -- but a real project's log can still have rows like this sitting
        # on disk from before that validation existed, or from an older
        # version of the code. train() must skip them, not crash on
        # DECISIONS_ORDER.index() for a decision that no longer exists.
        # Appended directly (bypassing record_example()'s validation) to
        # simulate exactly that pre-existing, already-on-disk data.
        path = str(tmp_path / "examples.jsonl")
        _seed_examples(path)   # 6 valid examples
        import json
        stale_rows = [
            {"message_id": "m7", "subject": "intake", "sender_email": "broker@insure.com",
             "body_text": "form", "decision": "route_scope1", "source": "live",
             "recorded_at": "2026-08-20T00:00:00+00:00"},
            {"message_id": "m8", "subject": "grades", "sender_email": "reg@x.edu",
             "body_text": "sheet", "decision": "route_scope2", "source": "live",
             "recorded_at": "2026-08-20T00:00:00+00:00"},
        ]
        with open(path, "a", encoding="utf-8") as f:
            for row in stale_rows:
                f.write(json.dumps(row) + "\n")
        save_path = str(tmp_path / "model.pt")

        result = trainer.train(path, save_path, epochs=5, lr=1e-2, val_split=0.0,
                                profile_path=str(tmp_path / "profile.json"))

        # Only the 6 examples with a decision still in DECISIONS_ORDER
        # were actually used to train.
        assert result["num_examples"] == 6

    def test_trains_and_saves_loadable_checkpoint(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        _seed_examples(path)
        save_path = str(tmp_path / "model.pt")
        result = trainer.train(path, save_path, epochs=20, lr=1e-2, val_split=0.15,
                                profile_path=str(tmp_path / "profile.json"))
        assert os.path.exists(save_path)
        assert 0.0 <= result["train_acc"] <= 1.0
        assert result["num_examples"] == 6
        model, artifact = im.load(save_path)
        assert isinstance(model, im.InboxDecisionNet)
        assert artifact["centroids"]

    def test_train_threads_profile_path(self, tmp_path, monkeypatch):
        """Direct, deterministic proof that train() passes its profile_path
        argument straight through to the real PatternProfile constructor --
        spies on __init__ itself rather than inferring it from trained-model
        behavior. This test can't be fooled by random init noise because it
        captures the literal argument the constructor was called with,
        inside train() itself."""
        from pattern_profile import PatternProfile, DEFAULT_PROFILE_PATH

        examples_path = str(tmp_path / "examples.jsonl")
        _seed_examples(examples_path)
        save_path = str(tmp_path / "model.pt")
        profile_path = str(tmp_path / "profile.json")

        captured = {}

        original_profile_init = PatternProfile.__init__

        def spy_profile_init(self, path=DEFAULT_PROFILE_PATH):
            captured["profile_path"] = path
            original_profile_init(self, path)

        monkeypatch.setattr(PatternProfile, "__init__", spy_profile_init)

        trainer.train(examples_path, save_path, epochs=5, lr=1e-2, val_split=0.0,
                      profile_path=profile_path)

        assert captured["profile_path"] == profile_path
