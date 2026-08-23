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


class TestInboxRouterSessionMetrics:
    """
    Scope #3 previously had no trend-log recording at all -- only the
    per-message routed_history.json. This follows Scope #1's OWN
    architecture as it exists on master (run_task.py's finally: block: a
    local metrics computation + a plain inline try/except JSONL append to
    data/output/run_metrics.jsonl), not the shared components/shared/
    recorder from the (unmerged) unification branch -- deliberately no
    dependency on that branch.

    Scope #1 records once per run (a bounded unit of work); Scope #3 is a
    forever-polling daemon with no such natural end, so it records once per
    session, on shutdown, accumulating counters across the whole session's
    lifetime.
    """

    def _build(self, tmp_path, inbox=None, sent=None, capsules=None):
        _write_fixture(tmp_path / "data", inbox=inbox or [], sent=sent or [])
        client = MockGmailClient(data_dir=str(tmp_path / "data"))
        profile = PatternProfile(path=str(tmp_path / "data" / "profile.json"))
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps({"capsules": capsules or []}), encoding="utf-8")
        rules = RuleLayer(profile, registry_path=str(registry_path))
        classifier = LLMClassifier(provider="none")
        history_path = str(tmp_path / "data" / "routed_history.json")
        return InboxRouter(client, profile, rules, classifier, history_path=history_path)

    def test_record_session_metrics_writes_a_row_tagged_scope3(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        metrics_path = tmp_path / "run_metrics.jsonl"
        router._record_session_metrics(path=str(metrics_path))

        lines = metrics_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["scope"] == "scope3"
        assert "timestamp" in row
        assert row["messages_routed"] == 1

    def test_record_session_metrics_counts_by_decision_type(self, tmp_path):
        router = self._build(
            tmp_path,
            inbox=[_msg("i1", "broker@x.com", "insurance intake form"),
                   _msg("i2", "stranger@x.com", "unrelated")],
            capsules=[{"name": "form_filling", "description": "", "model_path": "x.pt",
                       "trigger_keywords": ["insurance", "intake"], "trigger_apps": []}],
        )
        router.poll_once()
        metrics_path = tmp_path / "run_metrics.jsonl"
        router._record_session_metrics(path=str(metrics_path))

        row = json.loads(metrics_path.read_text(encoding="utf-8").splitlines()[0])
        assert row["decisions"]["route_scope1"] == 1
        assert row["decisions"]["flag"] == 1

    def test_record_session_metrics_counts_rule_vs_llm_layer(self, tmp_path):
        router = self._build(
            tmp_path,
            inbox=[_msg("i1", "broker@x.com", "insurance intake form"),
                   _msg("i2", "stranger@x.com", "unrelated")],
            capsules=[{"name": "form_filling", "description": "", "model_path": "x.pt",
                       "trigger_keywords": ["insurance", "intake"], "trigger_apps": []}],
        )
        router.poll_once()  # i1 -> rule (keyword match), i2 -> llm (falls through, no LLM configured -> flag)
        metrics_path = tmp_path / "run_metrics.jsonl"
        router._record_session_metrics(path=str(metrics_path))

        row = json.loads(metrics_path.read_text(encoding="utf-8").splitlines()[0])
        assert row["layer"]["rule"] == 1
        assert row["layer"]["llm"] == 1

    def test_record_session_metrics_counts_confirmed_and_overridden(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        router.override_decision("i1", "leave_alone", reason="not relevant")
        router.confirm_suggestion("i1", "leave_alone")

        metrics_path = tmp_path / "run_metrics.jsonl"
        router._record_session_metrics(path=str(metrics_path))
        row = json.loads(metrics_path.read_text(encoding="utf-8").splitlines()[0])
        assert row["confirmed"] == 1
        assert row["overridden"] == 1

    def test_record_session_metrics_computes_avg_confidence(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        routed = router.poll_once()
        expected_avg = routed[0]["confidence"]

        metrics_path = tmp_path / "run_metrics.jsonl"
        router._record_session_metrics(path=str(metrics_path))
        row = json.loads(metrics_path.read_text(encoding="utf-8").splitlines()[0])
        assert row["avg_confidence"] == expected_avg

    def test_record_session_metrics_zero_messages_still_writes_a_row(self, tmp_path):
        router = self._build(tmp_path, inbox=[])
        router.poll_once()
        metrics_path = tmp_path / "run_metrics.jsonl"
        router._record_session_metrics(path=str(metrics_path))

        row = json.loads(metrics_path.read_text(encoding="utf-8").splitlines()[0])
        assert row["messages_routed"] == 0
        assert row["avg_confidence"] is None

    def test_record_session_metrics_never_raises_on_write_failure(self, tmp_path, monkeypatch):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()

        def _boom(*a, **kw):
            raise OSError("disk is on fire")

        monkeypatch.setattr("builtins.open", _boom)
        # Must not raise -- matches Scope #1's own contract that recording
        # a run's result must never fail the run itself.
        router._record_session_metrics(path=str(tmp_path / "run_metrics.jsonl"))

    def test_record_session_metrics_default_path_is_the_shared_run_metrics_jsonl(self):
        import router as router_module
        assert router_module._SESSION_METRICS_PATH.replace("\\", "/").endswith(
            "data/output/run_metrics.jsonl"
        )

    def test_run_forever_records_session_metrics_on_shutdown(self, tmp_path, monkeypatch):
        router = self._build(tmp_path, inbox=[])
        router._stop = True  # loop body never executes; falls straight through to shutdown
        calls = []
        monkeypatch.setattr(router, "_record_session_metrics", lambda **kw: calls.append(kw))
        # The real stdin-reading background thread is irrelevant to what
        # this test verifies (the shutdown-path wiring), and it crashes
        # under pytest's captured stdin (OSError: reading from stdin while
        # output is captured) -- a real, unrelated pytest/thread quirk, not
        # a router bug. Stub the thread out so it never actually starts.
        monkeypatch.setattr(
            "router.threading.Thread",
            lambda *a, **kw: type("_NoopThread", (), {"start": lambda self: None})(),
        )
        router.run_forever()
        assert len(calls) == 1
