import json
import os
import sys

import pytest

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
        from routing_rules import RuleLayer
        path = str(tmp_path / "examples.jsonl")
        n = _seed_examples(path)
        examples = rec.load_examples(path)
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = RuleLayer(profile, registry_path=str(tmp_path / "registry.json"))
        x, y, centroids = trainer.build_dataset(examples, profile, rules)
        assert x.shape == (n, trainer.DIMS)
        assert y.shape == (n,)
        assert set(centroids.keys()) == {"reply", "leave_alone", "forward"}

    def test_registry_path_affects_rule_hit_scope1_feature(self, tmp_path):
        from inbox_features import FEATURE_NAMES
        from pattern_profile import PatternProfile
        from routing_rules import RuleLayer

        # Seed examples
        path = str(tmp_path / "examples.jsonl")
        _seed_examples(path)
        examples = rec.load_examples(path)

        # Create a registry with a capsule that matches m1's subject ("status update")
        registry_path = str(tmp_path / "registry.json")
        registry = {
            "capsules": [
                {
                    "name": "status_monitor",
                    "trigger_keywords": ["status"],
                    "trigger_apps": [],
                    "kind": "agent",
                    "model_path": ""
                }
            ]
        }
        with open(registry_path, "w") as f:
            json.dump(registry, f)

        # Build dataset with the registry
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = RuleLayer(profile, registry_path=registry_path)
        x, y, centroids = trainer.build_dataset(examples, profile, rules)

        # Find the feature index for rule_hit_scope1
        rule_hit_scope1_idx = FEATURE_NAMES.index("rule_hit_scope1")

        # m1 (index 0) has subject "status update" -- should match the "status" keyword
        assert x[0, rule_hit_scope1_idx].item() == 1.0, \
            "Example m1 (status update) should have rule_hit_scope1=1.0"

        # m2, m3, m4, m5, m6 don't have "status" in subject/body -- should be 0.0
        for i in [1, 2, 3, 4, 5]:
            assert x[i, rule_hit_scope1_idx].item() == 0.0, \
                f"Example at index {i} should have rule_hit_scope1=0.0"


class TestTrain:
    def test_too_few_examples_raises(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        rec.record_example(_msg("m1", "a@b.com", "hi", "hi"), "reply", source="live", path=path)
        save_path = str(tmp_path / "model.pt")
        with pytest.raises(trainer.TooFewExamplesError):
            trainer.train(path, save_path, epochs=5, lr=1e-2, val_split=0.15)

    def test_trains_and_saves_loadable_checkpoint(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        _seed_examples(path)
        save_path = str(tmp_path / "model.pt")
        result = trainer.train(path, save_path, epochs=20, lr=1e-2, val_split=0.15,
                                profile_path=str(tmp_path / "profile.json"),
                                registry_path=str(tmp_path / "registry.json"))
        assert os.path.exists(save_path)
        assert 0.0 <= result["train_acc"] <= 1.0
        assert result["num_examples"] == 6
        model, artifact = im.load(save_path)
        assert isinstance(model, im.InboxDecisionNet)
        assert artifact["centroids"]
