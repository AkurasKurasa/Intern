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
import inbox_features as feats


def _msg(sender_email="alice@vendor.com", subject="Hello", body="Just checking in."):
    return EmailMessage(
        id="m1", thread_id="t1", sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text=body, received_at="2026-08-25T00:00:00Z",
    )


class TestExtract:
    def test_returns_correct_length(self):
        result = feats.extract(_msg(), None, {})
        assert len(result) == feats.DIMS
        assert all(isinstance(v, float) for v in result)

    def test_pattern_ratios_reflect_sender_history(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        pattern = profile._get_or_create("vendor.com")
        pattern.reply_count, pattern.forward_count, pattern.ignore_count = 3, 1, 0
        result = feats.extract(_msg(), pattern, {})
        idx = feats.FEATURE_NAMES.index("pattern_reply_ratio")
        assert result[idx] == pytest.approx(0.75)

    def test_no_pattern_gives_zero_ratios(self):
        result = feats.extract(_msg(), None, {})
        idx = feats.FEATURE_NAMES.index("pattern_reply_ratio")
        assert result[idx] == 0.0

    def test_body_length_scaled_is_bounded(self):
        short = feats.extract(_msg(body="hi"), None, {})
        long = feats.extract(_msg(body="word " * 5000), None, {})
        idx = feats.FEATURE_NAMES.index("body_length_scaled")
        assert 0.0 <= short[idx] <= 1.0
        assert 0.0 <= long[idx] <= 1.0
        assert long[idx] > short[idx]

    def test_semantic_features_favor_matching_centroid(self):
        centroids = feats.compute_centroids([
            {"subject": "please reply soon", "body_text": "Can you get back to me?", "decision": "reply"},
            {"subject": "fwd this along", "body_text": "Please pass this to the team.", "decision": "forward"},
        ])
        result = feats.extract(_msg(subject="please reply soon", body="Can you get back to me?"),
                                None, centroids)
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


class TestDecisionsOrder:
    def test_matches_the_redefined_decision_space(self):
        # Task 1's whole point: route_scope1/route_scope2 are gone,
        # schedule/cold_email are the replacements. Every later task in
        # this plan assumes this exact list, in this exact order.
        assert feats.DECISIONS_ORDER == ["reply", "forward", "schedule", "cold_email", "flag", "leave_alone"]

    def test_feature_names_has_no_rule_hit_scope_features(self):
        assert "rule_hit_scope1" not in feats.FEATURE_NAMES
        assert "rule_hit_scope2" not in feats.FEATURE_NAMES
        assert feats.DIMS == len(feats.FEATURE_NAMES)
