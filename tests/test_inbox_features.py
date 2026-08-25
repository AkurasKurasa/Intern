import math
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage
from pattern_profile import PatternProfile
from routing_rules import RuleLayer
import inbox_features as feats


def _msg(sender_email="alice@vendor.com", subject="Hello", body="Just checking in."):
    return EmailMessage(
        id="m1", thread_id="t1", sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text=body, received_at="2026-08-25T00:00:00Z",
    )


def _rule_layer(tmp_path, profile, capsules=None):
    import json
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"capsules": capsules or []}), encoding="utf-8")
    return RuleLayer(profile, registry_path=str(registry_path))


class TestExtract:
    def test_returns_correct_length(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile)
        result = feats.extract(_msg(), None, {}, rules)
        assert len(result) == feats.DIMS
        assert all(isinstance(v, float) for v in result)

    def test_pattern_ratios_reflect_sender_history(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        pattern = profile._get_or_create("vendor.com")
        pattern.reply_count, pattern.forward_count, pattern.ignore_count = 3, 1, 0
        rules = _rule_layer(tmp_path, profile)
        result = feats.extract(_msg(), pattern, {}, rules)
        idx = feats.FEATURE_NAMES.index("pattern_reply_ratio")
        assert result[idx] == pytest.approx(0.75)

    def test_no_pattern_gives_zero_ratios(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile)
        result = feats.extract(_msg(), None, {}, rules)
        idx = feats.FEATURE_NAMES.index("pattern_reply_ratio")
        assert result[idx] == 0.0

    def test_rule_hit_scope1_sets_correct_feature(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile, capsules=[
            {"name": "form_filling", "description": "", "model_path": "x.pt",
             "trigger_keywords": ["insurance"], "trigger_apps": []},
        ])
        result = feats.extract(_msg(subject="insurance intake"), None, {}, rules)
        idx1 = feats.FEATURE_NAMES.index("rule_hit_scope1")
        idx2 = feats.FEATURE_NAMES.index("rule_hit_scope2")
        assert result[idx1] == 1.0
        assert result[idx2] == 0.0

    def test_rule_hit_scope2_sets_correct_feature(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile, capsules=[
            {"name": "Sheet-to-Portal Matcher", "description": "", "kind": "script",
             "model_path": "", "trigger_keywords": ["grades"], "trigger_apps": []},
        ])
        result = feats.extract(_msg(subject="grades roster"), None, {}, rules)
        idx1 = feats.FEATURE_NAMES.index("rule_hit_scope1")
        idx2 = feats.FEATURE_NAMES.index("rule_hit_scope2")
        assert result[idx1] == 0.0
        assert result[idx2] == 1.0

    def test_no_rule_match_sets_both_zero(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile)
        result = feats.extract(_msg(subject="totally unrelated"), None, {}, rules)
        idx1 = feats.FEATURE_NAMES.index("rule_hit_scope1")
        idx2 = feats.FEATURE_NAMES.index("rule_hit_scope2")
        assert result[idx1] == 0.0
        assert result[idx2] == 0.0

    def test_body_length_scaled_is_bounded(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile)
        short = feats.extract(_msg(body="hi"), None, {}, rules)
        long = feats.extract(_msg(body="word " * 5000), None, {}, rules)
        idx = feats.FEATURE_NAMES.index("body_length_scaled")
        assert 0.0 <= short[idx] <= 1.0
        assert 0.0 <= long[idx] <= 1.0
        assert long[idx] > short[idx]

    def test_semantic_features_favor_matching_centroid(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile)
        centroids = feats.compute_centroids([
            {"subject": "please reply soon", "body_text": "Can you get back to me?", "decision": "reply"},
            {"subject": "fwd this along", "body_text": "Please pass this to the team.", "decision": "forward"},
        ])
        result = feats.extract(_msg(subject="please reply soon", body="Can you get back to me?"),
                                None, centroids, rules)
        reply_idx = feats.FEATURE_NAMES.index("sem_sim_reply")
        forward_idx = feats.FEATURE_NAMES.index("sem_sim_forward")
        assert result[reply_idx] > result[forward_idx]


class TestComputeCentroids:
    def test_empty_examples_returns_empty_dict(self):
        assert feats.compute_centroids([]) == {}

    def test_groups_by_decision(self):
        examples = [
            {"subject": "a", "body_text": "reply text one", "decision": "reply"},
            {"subject": "b", "body_text": "reply text two", "decision": "reply"},
            {"subject": "c", "body_text": "forward text", "decision": "forward"},
        ]
        centroids = feats.compute_centroids(examples)
        assert set(centroids.keys()) == {"reply", "forward"}
        assert len(centroids["reply"]) == len(centroids["forward"])
