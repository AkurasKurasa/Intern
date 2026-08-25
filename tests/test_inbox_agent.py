# tests/test_inbox_agent.py
import json
import os
import sys

import torch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage
from llm_classifier import LLMClassifier
from pattern_profile import PatternProfile
from routing_rules import RuleLayer
import decision_recorder as rec
import inbox_agent as ia
import inbox_model as im
import train_inbox_agent as trainer


def _msg(sender_email="alice@vendor.com", subject="Hello", body="Just checking in."):
    return EmailMessage(
        id="m1", thread_id="t1", sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text=body, received_at="2026-08-25T00:00:00Z",
    )


def _agent(tmp_path, checkpoint_path, high_confidence=0.75, capsules=None):
    profile = PatternProfile(path=str(tmp_path / "profile.json"))
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"capsules": capsules or []}), encoding="utf-8")
    rules = RuleLayer(profile, registry_path=str(registry_path))
    classifier = LLMClassifier(provider="none")
    return ia.InboxAgent(profile, rules, classifier, checkpoint_path=checkpoint_path,
                          high_confidence=high_confidence)


class TestColdStart:
    def test_no_checkpoint_always_reasons(self, tmp_path):
        agent = _agent(tmp_path, checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"))
        result = agent.decide(_msg())
        assert result.layer in ("rule", "llm")

    def test_no_checkpoint_flags_unresolved_email(self, tmp_path):
        agent = _agent(tmp_path, checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"))
        result = agent.decide(_msg(sender_email="stranger@nowhere.com", subject="totally unrelated"))
        assert result.decision == "flag"
        assert result.layer == "llm"

    def test_corrupt_checkpoint_falls_back_to_reasoning(self, tmp_path):
        checkpoint_path = str(tmp_path / "corrupt_checkpoint.pt")
        (tmp_path / "corrupt_checkpoint.pt").write_bytes(b"not a real checkpoint")
        # Construction should not raise even with corrupt checkpoint
        agent = _agent(tmp_path, checkpoint_path=checkpoint_path)
        # decide() should still work via reasoning
        result = agent.decide(_msg())
        assert result.layer in ("rule", "llm")


class TestFastFillWithTrainedCheckpoint:
    def _build_checkpoint(self, tmp_path):
        examples_path = str(tmp_path / "examples.jsonl")
        for i in range(3):
            rec.record_example(
                _msg(sender_email="boss@work.com", subject=f"status {i}", body="Please reply when you can."),
                "reply", source="live", path=examples_path,
            )
        for i in range(3):
            rec.record_example(
                _msg(sender_email="newsletter@vendor.com", subject=f"digest {i}", body="This week's roundup."),
                "leave_alone", source="live", path=examples_path,
            )
        save_path = str(tmp_path / "model.pt")
        # Same tmp_path registry/profile _agent() will construct the inference-time
        # RuleLayer/PatternProfile from below -- training against the real repo's
        # tasks/registry.json here while inference uses an isolated empty one would
        # be a train/inference skew on the rule_hit_scope1/scope2 features.
        trainer.train(examples_path, save_path, epochs=300, lr=5e-2, val_split=0.0,
                       profile_path=str(tmp_path / "profile.json"),
                       registry_path=str(tmp_path / "registry.json"))
        return save_path

    def test_confident_prediction_fast_fills(self, tmp_path):
        checkpoint_path = self._build_checkpoint(tmp_path)
        agent = _agent(tmp_path, checkpoint_path=checkpoint_path, high_confidence=0.0)
        result = agent.decide(_msg(sender_email="boss@work.com", subject="status 99",
                                    body="Please reply when you can."))
        assert result.layer == "fast_fill"

    def test_confident_prediction_fast_fills_with_shipped_default_threshold(self, tmp_path):
        # Uses the real shipped default (high_confidence=0.75), not a
        # degenerate 0.0/0.999999 threshold -- and asserts the fast-filled
        # *decision* is actually correct, not just that a fast-fill happened.
        checkpoint_path = self._build_checkpoint(tmp_path)
        agent = _agent(tmp_path, checkpoint_path=checkpoint_path)
        result = agent.decide(_msg(sender_email="boss@work.com", subject="status 99",
                                    body="Please reply when you can."))
        assert result.layer == "fast_fill"
        assert result.decision == "reply"

    def test_high_threshold_forces_reasoning(self, tmp_path):
        checkpoint_path = self._build_checkpoint(tmp_path)
        agent = _agent(tmp_path, checkpoint_path=checkpoint_path, high_confidence=0.999999)
        result = agent.decide(_msg(sender_email="boss@work.com", subject="status 99",
                                    body="Please reply when you can."))
        assert result.layer in ("rule", "llm")

    def test_route_decision_without_verified_capsule_falls_through(self, tmp_path):
        examples_path = str(tmp_path / "examples.jsonl")
        for i in range(3):
            rec.record_example(
                _msg(sender_email="broker@insure.com", subject=f"intake {i}", body="Please process this form."),
                "route_scope1", source="live", path=examples_path,
            )
        for i in range(3):
            rec.record_example(
                _msg(sender_email="newsletter@vendor.com", subject=f"digest {i}", body="This week's roundup."),
                "leave_alone", source="live", path=examples_path,
            )
        save_path = str(tmp_path / "model.pt")
        # Same registry/profile paths _agent() below will use -- both empty,
        # so the rule_hit_scope1/scope2 features agree at train and inference time.
        trainer.train(examples_path, save_path, epochs=300, lr=5e-2, val_split=0.0,
                       profile_path=str(tmp_path / "profile.json"),
                       registry_path=str(tmp_path / "registry.json"))
        # No capsules registered -- match_capsule() can never verify a name,
        # so even a confident route_scope1 prediction must fall through.
        agent = _agent(tmp_path, checkpoint_path=save_path, high_confidence=0.0, capsules=[])
        result = agent.decide(_msg(sender_email="broker@insure.com", subject="intake 99",
                                    body="Please process this form."))
        assert result.layer in ("rule", "llm")
