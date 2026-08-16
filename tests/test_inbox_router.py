"""
Tests for components/inbox_router/ (Scope #3 — Inbox Router).

Everything here runs against MockGmailClient and tmp_path-isolated data
files -- no real Gmail account, no real OAuth, no network call except the
one opt-in live-LLM test (marked, skipped by default, and even then it's
just an API call, the same category of thing agent.py already does live --
never a Gmail action, never GUI automation).
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components" / "inbox_router"))

from gmail_client import EmailMessage, MockGmailClient
from pattern_profile import PatternProfile
from routing_rules import RuleLayer
from llm_classifier import LLMClassifier
from router import InboxRouter


def _write_fixture(data_dir: Path, inbox=None, sent=None) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "mock_inbox.json").write_text(
        json.dumps({"inbox": inbox or [], "sent": sent or []}), encoding="utf-8"
    )


def _msg(id_, sender_email, subject, thread_id=None, body="", to=""):
    return {
        "id": id_, "thread_id": thread_id or f"thread-{id_}",
        "sender": f"Someone <{sender_email}>", "sender_email": sender_email,
        "subject": subject, "snippet": subject, "body_text": body,
        "received_at": "2026-08-16T09:00:00-07:00", "labels": ["INBOX"], "to": to,
    }


class TestMockGmailClient:
    def test_processed_ids_persist_across_new_instances(self, tmp_path):
        _write_fixture(tmp_path, inbox=[_msg("m1", "a@b.com", "Hi")])
        client = MockGmailClient(data_dir=str(tmp_path))
        assert len(client.list_inbox_unprocessed()) == 1

        client.mark_processed("m1")
        # A brand-new instance reading the same data_dir must see the same
        # processed state -- this is what makes "restart the poller" safe.
        client2 = MockGmailClient(data_dir=str(tmp_path))
        assert client2.list_inbox_unprocessed() == []

    def test_create_draft_persists_and_returns_incrementing_ids(self, tmp_path):
        _write_fixture(tmp_path)
        client = MockGmailClient(data_dir=str(tmp_path))
        d1 = client.create_draft(to="x@y.com", subject="s1", body="b1")
        d2 = client.create_draft(to="x@y.com", subject="s2", body="b2")
        assert d1 != d2
        drafts = json.loads((tmp_path / "mock_drafts.json").read_text())["drafts"]
        assert [d["subject"] for d in drafts] == ["s1", "s2"]

    def test_list_sent_filters_by_since(self, tmp_path):
        old = _msg("s1", "a@b.com", "old")
        old["received_at"] = "2020-01-01T00:00:00-07:00"
        new = _msg("s2", "a@b.com", "new")
        new["received_at"] = "2026-08-01T00:00:00-07:00"
        _write_fixture(tmp_path, sent=[old, new])
        client = MockGmailClient(data_dir=str(tmp_path))
        assert [m.id for m in client.list_sent(since="2026-01-01T00:00:00-07:00")] == ["s2"]

    def test_no_send_method_exists_anywhere_on_the_interface(self):
        # Structural, not a policy toggle: there is no method capable of
        # sending real email. create_draft() is as far as it goes.
        assert not hasattr(MockGmailClient, "send")
        assert not hasattr(MockGmailClient, "send_message")


class TestPatternProfile:
    def test_observe_sent_history_counts_replies_and_forwards(self, tmp_path):
        inbox = [_msg("i1", "vendor@x.com", "Question", thread_id="t1")]
        sent = [_msg("s1", "me@example.com", "Re: Question", thread_id="t1", to="vendor@x.com")]
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        profile.observe_sent_history(sent=[EmailMessage(**s) for s in sent],
                                      inbox=[EmailMessage(**m) for m in inbox])
        pattern = profile.pattern_for("vendor@x.com")
        assert pattern is not None
        assert pattern.reply_count == 1
        assert pattern.forward_count == 0

    def test_observe_sent_history_detects_forward_by_marker(self, tmp_path):
        inbox = [_msg("i1", "it@x.com", "Alert", thread_id="t1")]
        sent = [_msg("s1", "me@example.com", "Fwd: Alert", thread_id="t1", to="ops@example.com",
                      body="---------- Forwarded message ----------")]
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        profile.observe_sent_history(sent=[EmailMessage(**s) for s in sent],
                                      inbox=[EmailMessage(**m) for m in inbox])
        pattern = profile.pattern_for("it@x.com")
        assert pattern.forward_count == 1
        assert "ops@example.com" in pattern.common_forward_targets

    def test_unanswered_thread_counts_as_ignored(self, tmp_path):
        inbox = [_msg("i1", "news@x.com", "Newsletter", thread_id="t1")]
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        profile.observe_sent_history(sent=[], inbox=[EmailMessage(**m) for m in inbox])
        assert profile.pattern_for("news@x.com").ignore_count == 1

    def test_dominant_action_requires_lopsided_share(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        pattern = profile._get_or_create("x.com")
        pattern.reply_count = 3
        pattern.forward_count = 2
        assert pattern.dominant_action(min_share=0.75) is None  # 3/5 = 60%, not lopsided
        pattern.reply_count = 9
        pattern.forward_count = 1
        assert pattern.dominant_action(min_share=0.75) == "reply"

    def test_confirmed_decision_persists_and_reloads(self, tmp_path):
        path = str(tmp_path / "profile.json")
        profile = PatternProfile(path=path)
        msg = EmailMessage(**_msg("i1", "a@b.com", "Hi"))
        profile.record_confirmed_decision(msg, "reply")

        reloaded = PatternProfile(path=path)
        assert reloaded.pattern_for("a@b.com").reply_count == 1

    def test_override_moves_the_counter_toward_the_correction(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        msg = EmailMessage(**_msg("i1", "a@b.com", "Hi"))
        profile.record_override(msg, old_decision="leave_alone", new_decision="reply")
        assert profile.pattern_for("a@b.com").reply_count == 1


class TestRuleLayer:
    def _registry(self, tmp_path, capsules):
        path = tmp_path / "registry.json"
        path.write_text(json.dumps({"capsules": capsules}), encoding="utf-8")
        return str(path)

    def test_lopsided_pattern_wins_without_needing_a_keyword_match(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        pattern = profile._get_or_create("vendor.com")
        pattern.reply_count, pattern.forward_count, pattern.ignore_count = 9, 0, 1
        registry_path = self._registry(tmp_path, [])
        rules = RuleLayer(profile, registry_path=registry_path)

        result = rules.classify(EmailMessage(**_msg("i1", "p@vendor.com", "Random subject")))
        assert result.decision == "reply"
        assert result.confidence > 0.5

    def test_keyword_match_routes_to_scope1(self, tmp_path):
        registry_path = self._registry(tmp_path, [
            {"name": "form_filling", "description": "", "model_path": "x.pt",
             "trigger_keywords": ["insurance", "intake"], "trigger_apps": []},
        ])
        rules = RuleLayer(PatternProfile(path=str(tmp_path / "profile.json")), registry_path=registry_path)

        result = rules.classify(EmailMessage(**_msg("i1", "broker@x.com", "New insurance intake form")))
        assert result.decision == "route_scope1"
        assert result.capsule_name == "form_filling"

    def test_keyword_match_routes_to_scope2_for_script_kind_capsule(self, tmp_path):
        registry_path = self._registry(tmp_path, [
            {"name": "Sheet-to-Portal Matcher", "description": "", "model_path": "",
             "trigger_keywords": ["grade sheet"], "trigger_apps": [], "kind": "script"},
        ])
        rules = RuleLayer(PatternProfile(path=str(tmp_path / "profile.json")), registry_path=registry_path)

        result = rules.classify(EmailMessage(**_msg("i1", "reg@x.edu", "grade sheet ready")))
        assert result.decision == "route_scope2"

    def test_no_signal_defers_to_llm(self, tmp_path):
        registry_path = self._registry(tmp_path, [])
        rules = RuleLayer(PatternProfile(path=str(tmp_path / "profile.json")), registry_path=registry_path)

        result = rules.classify(EmailMessage(**_msg("i1", "stranger@x.com", "totally unrelated")))
        assert result.decision == ""


class TestLLMClassifierOffline:
    def test_no_provider_is_not_available_and_flags(self):
        classifier = LLMClassifier(provider="none")
        assert classifier.available is False
        result = classifier.classify(EmailMessage(**_msg("i1", "a@b.com", "x")), None, None, [])
        assert result.decision == "flag"
        assert result.confidence == 0.0

    def test_draft_message_returns_empty_when_unavailable(self):
        classifier = LLMClassifier(provider="none")
        assert classifier.draft_message(EmailMessage(**_msg("i1", "a@b.com", "x")), "reply") == ""


class TestInboxRouterPollOnce:
    def _build(self, tmp_path, inbox=None, sent=None, capsules=None):
        _write_fixture(tmp_path / "data", inbox=inbox or [], sent=sent or [])
        client = MockGmailClient(data_dir=str(tmp_path / "data"))
        profile = PatternProfile(path=str(tmp_path / "data" / "profile.json"))
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps({"capsules": capsules or []}), encoding="utf-8")
        rules = RuleLayer(profile, registry_path=str(registry_path))
        classifier = LLMClassifier(provider="none")  # offline -> unresolved emails get "flag"
        history_path = str(tmp_path / "data" / "routed_history.json")
        return InboxRouter(client, profile, rules, classifier, history_path=history_path)

    def test_poll_once_routes_and_marks_processed(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "broker@x.com", "insurance intake form")],
                              capsules=[{"name": "form_filling", "description": "", "model_path": "x.pt",
                                         "trigger_keywords": ["insurance", "intake"], "trigger_apps": []}])
        routed = router.poll_once()
        assert len(routed) == 1
        assert routed[0]["decision"] == "route_scope1"
        assert routed[0]["capsule_name"] == "form_filling"
        assert routed[0]["layer"] == "rule"

        # Second poll: the message was marked processed, must not reappear.
        assert router.poll_once() == []

    def test_unresolved_email_falls_through_to_flag_with_no_llm(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "totally unrelated")])
        routed = router.poll_once()
        assert routed[0]["decision"] == "flag"
        assert routed[0]["layer"] == "llm"

    def test_history_file_reflects_routed_entries(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        history = json.loads(Path(router._history_path).read_text())["messages"]
        assert len(history) == 1
        assert history[0]["message_id"] == "i1"

    def test_hallucinated_capsule_name_is_rejected_and_flagged(self, tmp_path, monkeypatch):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        # Force the LLM path to "succeed" with a decision + a capsule name
        # that was never registered -- the guard in _classify_and_record()
        # must catch this, since the UI would otherwise call
        # capsulesAPI.run() on a name that doesn't exist.
        from llm_classifier import ClassificationResult
        monkeypatch.setattr(
            router._llm, "classify",
            lambda *a, **k: ClassificationResult(decision="route_scope1", confidence=0.9,
                                                  rationale="x", capsule_name="not_a_real_capsule"),
        )
        routed = router.poll_once()
        assert routed[0]["decision"] == "flag"
        assert routed[0]["capsule_name"] == ""

    def test_confirm_reply_creates_exactly_one_draft(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()  # -> flag (no LLM configured)
        # Simulate the user overriding to "reply" then confirming it.
        router.override_decision("i1", "reply")
        router.confirm_suggestion("i1", "reply")

        drafts = json.loads((tmp_path / "data" / "mock_drafts.json").read_text())["drafts"]
        assert len(drafts) == 1
        assert drafts[0]["to"] == "stranger@x.com"

    def test_confirm_route_scope1_creates_no_draft(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "broker@x.com", "insurance intake")],
                              capsules=[{"name": "form_filling", "description": "", "model_path": "x.pt",
                                         "trigger_keywords": ["insurance"], "trigger_apps": []}])
        router.poll_once()
        router.confirm_suggestion("i1", "route_scope1")

        assert not (tmp_path / "data" / "mock_drafts.json").exists()
        history = json.loads(Path(router._history_path).read_text())["messages"]
        assert history[0]["status"] == "confirmed"

    def test_override_updates_history_and_pattern_profile(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        router.override_decision("i1", "leave_alone", reason="not relevant")

        history = json.loads(Path(router._history_path).read_text())["messages"]
        assert history[0]["decision"] == "leave_alone"
        assert history[0]["status"] == "overridden"
        assert router._profile.pattern_for("stranger@x.com").ignore_count >= 1
