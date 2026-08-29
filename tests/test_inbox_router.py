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
import decision_recorder
import reply_recorder
from decision_recorder import load_examples


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

    def test_no_signal_defers_to_llm(self, tmp_path):
        registry_path = self._registry(tmp_path, [])
        rules = RuleLayer(PatternProfile(path=str(tmp_path / "profile.json")), registry_path=registry_path)

        result = rules.classify(EmailMessage(**_msg("i1", "stranger@x.com", "totally unrelated")))
        assert result.decision == ""


class TestLLMClassifierOffline:
    def test_no_provider_is_not_available_and_flags(self):
        classifier = LLMClassifier(provider="none")
        assert classifier.available is False
        result = classifier.classify(EmailMessage(**_msg("i1", "a@b.com", "x")), None, None)
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
        return InboxRouter(client, profile, rules, classifier, history_path=history_path,
                            inbox_checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"),
                            examples_path=str(tmp_path / "data" / "training_examples.jsonl"),
                            reply_examples_path=str(tmp_path / "data" / "reply_examples.jsonl"))

    def test_poll_once_marks_processed_so_it_does_not_reappear(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "totally unrelated")])
        routed = router.poll_once()
        assert len(routed) == 1

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

    def test_confirm_reply_with_real_text_creates_one_draft_with_that_text(self, tmp_path):
        # No LLM involved: the draft's body is exactly the real text
        # passed in, nothing generated.
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()  # -> flag (no LLM configured)
        router.confirm_suggestion("i1", "reply", reply_body="Thanks, I'll take a look.")

        drafts = json.loads((tmp_path / "data" / "mock_drafts.json").read_text())["drafts"]
        assert len(drafts) == 1
        assert drafts[0]["to"] == "stranger@x.com"
        assert drafts[0]["body"] == "Thanks, I'll take a look."

    def test_confirm_reply_with_no_text_creates_an_empty_draft_not_an_ai_one(self, tmp_path):
        # The honesty guarantee: no reply_body means an empty draft, never
        # an LLM filling in words on the user's behalf.
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        router.confirm_suggestion("i1", "reply")

        drafts = json.loads((tmp_path / "data" / "mock_drafts.json").read_text())["drafts"]
        assert len(drafts) == 1
        assert drafts[0]["body"] == ""

    def test_override_to_reply_with_real_text_now_creates_a_draft(self, tmp_path):
        # Regression: override_decision() used to do nothing Gmail-side
        # at all when overriding TO "reply" -- there was no way to
        # override into a reply and actually get a draft out of it.
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        router.override_decision("i1", "reply", reply_body="Sure, sounds good.")

        drafts = json.loads((tmp_path / "data" / "mock_drafts.json").read_text())["drafts"]
        assert len(drafts) == 1
        assert drafts[0]["body"] == "Sure, sounds good."

    def test_confirm_reply_with_real_text_records_a_real_reply_example(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated", body="Can you help?")])
        router.poll_once()
        router.confirm_suggestion("i1", "reply", reply_body="Yes, happy to.")

        examples = reply_recorder.load_reply_examples(path=router._reply_examples_path)
        assert len(examples) == 1
        assert examples[0]["reply_body"] == "Yes, happy to."
        assert examples[0]["body_text"] == "Can you help?"

    def test_confirm_reply_with_no_text_records_no_reply_example(self, tmp_path):
        # Nothing real was written, so nothing gets saved as if it were.
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        router.confirm_suggestion("i1", "reply")

        assert reply_recorder.load_reply_examples(path=router._reply_examples_path) == []

    def test_confirm_flag_creates_no_draft(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        router.confirm_suggestion("i1", "flag")

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

    def test_confirm_suggestion_records_a_training_example(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        router.confirm_suggestion("i1", "leave_alone")

        examples = decision_recorder.load_examples(path=router._examples_path)
        assert len(examples) == 1
        assert examples[0]["decision"] == "leave_alone"
        assert examples[0]["source"] == "live"

    def test_override_decision_records_a_training_example(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        router.override_decision("i1", "reply", reason="actually needs a reply")

        examples = decision_recorder.load_examples(path=router._examples_path)
        assert len(examples) == 1
        assert examples[0]["decision"] == "reply"
        assert examples[0]["source"] == "live"

    def test_pending_entries_returns_only_unconfirmed(self, tmp_path):
        router = self._build(tmp_path, inbox=[
            _msg("i1", "stranger@x.com", "totally unrelated"),
            _msg("i2", "stranger@x.com", "also unrelated"),
        ])
        router.poll_once()
        assert len(router.pending_entries()) == 2
        router.confirm_suggestion("i1", "leave_alone")
        pending = router.pending_entries()
        assert len(pending) == 1
        assert pending[0]["message_id"] == "i2"

    def test_pending_entries_includes_body_text(self, tmp_path):
        router = self._build(tmp_path, inbox=[
            _msg("i1", "sender@x.com", "test subject", body="this is the email body text"),
        ])
        router.poll_once()
        pending = router.pending_entries()
        assert len(pending) == 1
        assert pending[0]["body_text"] == "this is the email body text"

    def test_list_unprocessed_stubs_has_no_decision_and_does_not_mark_processed(self, tmp_path):
        router = self._build(tmp_path, inbox=[
            _msg("i1", "stranger@x.com", "unrelated one"),
            _msg("i2", "stranger@x.com", "unrelated two"),
        ])
        stubs = router.list_unprocessed_stubs()
        assert len(stubs) == 2
        assert stubs[0] == {
            "message_id": "i1", "subject": "unrelated one",
            "sender": "Someone <stranger@x.com>", "sender_email": "stranger@x.com",
        }
        assert "decision" not in stubs[0]
        # A peek, not a poll -- calling it again must return the same two,
        # not an empty list.
        assert len(router.list_unprocessed_stubs()) == 2
        assert router.pending_entries() == []

    def test_process_next_unprocessed_classifies_exactly_one_via_the_real_pipeline(self, tmp_path):
        router = self._build(tmp_path, inbox=[
            _msg("i1", "broker@x.com", "insurance intake form"),
            _msg("i2", "stranger@x.com", "totally unrelated"),
        ])

        first = router.process_next_unprocessed()
        assert first["message_id"] == "i1"
        assert first["decision"] == "flag"
        assert first["layer"] == "llm"
        # Only the one message was processed -- the other is still waiting.
        assert len(router.list_unprocessed_stubs()) == 1
        assert len(router.pending_entries()) == 1

        second = router.process_next_unprocessed()
        assert second["message_id"] == "i2"
        assert len(router.list_unprocessed_stubs()) == 0
        assert len(router.pending_entries()) == 2

    def test_process_next_unprocessed_returns_none_when_inbox_is_empty(self, tmp_path):
        router = self._build(tmp_path, inbox=[])
        assert router.process_next_unprocessed() is None

    def test_process_next_unprocessed_matches_poll_once_for_the_same_message(self, tmp_path):
        # Same underlying _classify_and_record() call -- this pins that
        # stepping through one-at-a-time produces byte-identical decisions
        # to the existing bulk path, not a second, divergent code path.
        bulk_router = self._build(tmp_path / "bulk", inbox=[_msg("i1", "broker@x.com", "insurance intake form")])
        bulk_result = bulk_router.poll_once()[0]

        step_router = self._build(tmp_path / "step", inbox=[_msg("i1", "broker@x.com", "insurance intake form")])
        step_result = step_router.process_next_unprocessed()

        for key in ("decision", "capsule_name", "confidence", "rationale", "layer"):
            assert bulk_result[key] == step_result[key]


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
        return InboxRouter(client, profile, rules, classifier, history_path=history_path,
                            inbox_checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"),
                            examples_path=str(tmp_path / "data" / "training_examples.jsonl"),
                            reply_examples_path=str(tmp_path / "data" / "reply_examples.jsonl"))

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
            inbox=[_msg("i1", "boss@work.com", "status update"),
                   _msg("i2", "stranger@x.com", "unrelated")],
        )
        # Keyword-based capsule routing is gone -- a lopsided sender
        # pattern is now the only thing that resolves at the rule layer.
        pattern = router._profile._get_or_create("work.com")
        pattern.reply_count, pattern.forward_count, pattern.ignore_count = 9, 0, 1
        router.poll_once()
        metrics_path = tmp_path / "run_metrics.jsonl"
        router._record_session_metrics(path=str(metrics_path))

        row = json.loads(metrics_path.read_text(encoding="utf-8").splitlines()[0])
        assert row["decisions"]["reply"] == 1
        assert row["decisions"]["flag"] == 1

    def test_record_session_metrics_counts_rule_vs_llm_layer(self, tmp_path):
        router = self._build(
            tmp_path,
            inbox=[_msg("i1", "boss@work.com", "status update"),
                   _msg("i2", "stranger@x.com", "unrelated")],
        )
        pattern = router._profile._get_or_create("work.com")
        pattern.reply_count, pattern.forward_count, pattern.ignore_count = 9, 0, 1
        router.poll_once()  # i1 -> rule (lopsided pattern), i2 -> llm (falls through, no LLM configured -> flag)
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


class TestPracticeInbox:
    def _build(self, tmp_path, inbox=None, sent=None):
        _write_fixture(tmp_path / "data", inbox=inbox or [], sent=sent or [])
        client = MockGmailClient(data_dir=str(tmp_path / "data"))
        profile = PatternProfile(path=str(tmp_path / "data" / "profile.json"))
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps({"capsules": []}), encoding="utf-8")
        rules = RuleLayer(profile, registry_path=str(registry_path))
        classifier = LLMClassifier(provider="none")
        history_path = str(tmp_path / "data" / "routed_history.json")
        return InboxRouter(client, profile, rules, classifier, history_path=history_path,
                            inbox_checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"),
                            examples_path=str(tmp_path / "data" / "training_examples.jsonl"),
                            reply_examples_path=str(tmp_path / "data" / "reply_examples.jsonl"))

    def test_list_practice_inbox_returns_all_messages_unfiltered(self, tmp_path):
        router = self._build(tmp_path, inbox=[
            _msg("i1", "stranger@x.com", "first"),
            _msg("i2", "stranger@x.com", "second"),
        ])
        # Mark one as already processed via the real triage flow -- practice
        # mode must still show it, unlike poll_once()'s unprocessed-only view.
        router.poll_once()
        messages = router.list_practice_inbox()
        assert {m.id for m in messages} == {"i1", "i2"}

    def test_record_practice_decision_writes_a_real_example(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "hello", body="real body text")])
        router.record_practice_decision("i1", "reply")

        examples = load_examples(path=str(tmp_path / "data" / "training_examples.jsonl"))
        assert len(examples) == 1
        assert examples[0]["message_id"] == "i1"
        assert examples[0]["decision"] == "reply"
        assert examples[0]["source"] == "live"
        assert examples[0]["body_text"] == "real body text"

    def test_record_practice_decision_updates_pattern_profile(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "boss@work.com", "hello")])
        router.record_practice_decision("i1", "reply")

        pattern = router._profile.pattern_for("boss@work.com")
        assert pattern is not None
        assert pattern.reply_count == 1

    def test_record_practice_decision_unknown_message_id_does_not_raise(self, tmp_path):
        router = self._build(tmp_path)
        router.record_practice_decision("does-not-exist", "reply")  # must not raise
        examples = load_examples(path=str(tmp_path / "data" / "training_examples.jsonl"))
        assert examples == []


class TestScheduleRecording:
    def _build(self, tmp_path, inbox=None, sent=None, capsules=None):
        _write_fixture(tmp_path / "data", inbox=inbox or [], sent=sent or [])
        client = MockGmailClient(data_dir=str(tmp_path / "data"))
        profile = PatternProfile(path=str(tmp_path / "data" / "profile.json"))
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps({"capsules": capsules or []}), encoding="utf-8")
        rules = RuleLayer(profile, registry_path=str(registry_path))
        classifier = LLMClassifier(provider="none")
        history_path = str(tmp_path / "data" / "routed_history.json")
        return InboxRouter(client, profile, rules, classifier, history_path=history_path,
                            inbox_checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"),
                            examples_path=str(tmp_path / "data" / "training_examples.jsonl"),
                            reply_examples_path=str(tmp_path / "data" / "reply_examples.jsonl"),
                            schedule_log_path=str(tmp_path / "data" / "schedule.txt"))

    def test_confirm_schedule_with_real_text_records_a_schedule_entry(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "boss@work.com", "vendor call")])
        router.poll_once()

        router.confirm_suggestion("i1", "schedule", reply_body="Aug 30 -- vendor call re: pricing")

        schedule_path = str(tmp_path / "data" / "schedule.txt")
        with open(schedule_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Aug 30 -- vendor call re: pricing" in content

    def test_confirm_schedule_with_no_text_records_nothing(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "boss@work.com", "vendor call")])
        router.poll_once()

        router.confirm_suggestion("i1", "schedule", reply_body="")

        schedule_path = str(tmp_path / "data" / "schedule.txt")
        assert not os.path.exists(schedule_path)

    def test_confirm_schedule_does_not_touch_reply_examples(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "boss@work.com", "vendor call")])
        router.poll_once()

        router.confirm_suggestion("i1", "schedule", reply_body="a real note")

        reply_examples_path = str(tmp_path / "data" / "reply_examples.jsonl")
        assert not os.path.exists(reply_examples_path)

    def test_override_to_schedule_with_real_text_records_a_schedule_entry(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "boss@work.com", "random subject")])
        router.poll_once()

        router.override_decision("i1", "schedule", "manual override", reply_body="Sept 2 -- follow up")

        schedule_path = str(tmp_path / "data" / "schedule.txt")
        with open(schedule_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Sept 2 -- follow up" in content


class TestReadStdinCommandsCarryReplyBody:
    """_read_stdin_commands() is the far end of the Electron 'Inbox' tab's
    own confirm/override chain (preload.js -> main.js -> recorder_bridge.py
    -> this). That chain has no live UI wired to it today, but its
    reply_body plumbing was found to be entirely missing while fixing the
    same defect in automate_inbox.py -- fixed here so it can never
    reintroduce the same silent-empty-draft bug if a UI is ever wired to
    it later."""

    def _build(self, tmp_path, inbox=None):
        _write_fixture(tmp_path / "data", inbox=inbox or [], sent=[])
        client = MockGmailClient(data_dir=str(tmp_path / "data"))
        profile = PatternProfile(path=str(tmp_path / "data" / "profile.json"))
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps({"capsules": []}), encoding="utf-8")
        rules = RuleLayer(profile, registry_path=str(registry_path))
        classifier = LLMClassifier(provider="none")
        history_path = str(tmp_path / "data" / "routed_history.json")
        return InboxRouter(client, profile, rules, classifier, history_path=history_path,
                            inbox_checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"),
                            examples_path=str(tmp_path / "data" / "training_examples.jsonl"),
                            reply_examples_path=str(tmp_path / "data" / "reply_examples.jsonl"))

    def test_confirm_command_threads_reply_body_through(self, tmp_path, monkeypatch):
        router = self._build(tmp_path)
        captured = {}
        monkeypatch.setattr(router, "confirm_suggestion",
                             lambda message_id, decision, reply_body="": captured.update(
                                 message_id=message_id, decision=decision, reply_body=reply_body))
        line = json.dumps({"cmd": "confirm", "message_id": "m1", "decision": "reply",
                            "reply_body": "Sure, that works."})
        monkeypatch.setattr(sys, "stdin", iter([line]))

        router._read_stdin_commands()

        assert captured == {"message_id": "m1", "decision": "reply", "reply_body": "Sure, that works."}

    def test_override_command_threads_reply_body_through(self, tmp_path, monkeypatch):
        router = self._build(tmp_path)
        captured = {}
        monkeypatch.setattr(router, "override_decision",
                             lambda message_id, new_decision, reason="", reply_body="": captured.update(
                                 message_id=message_id, new_decision=new_decision,
                                 reason=reason, reply_body=reply_body))
        line = json.dumps({"cmd": "override", "message_id": "m1", "new_decision": "forward",
                            "reason": "wrong guess", "reply_body": "Passing this along."})
        monkeypatch.setattr(sys, "stdin", iter([line]))

        router._read_stdin_commands()

        assert captured == {"message_id": "m1", "new_decision": "forward",
                             "reason": "wrong guess", "reply_body": "Passing this along."}

    def test_confirm_command_without_reply_body_defaults_to_empty(self, tmp_path, monkeypatch):
        # Every OTHER existing caller of "confirm" (there are none live
        # today) must keep working with no reply_body key at all.
        router = self._build(tmp_path)
        captured = {}
        monkeypatch.setattr(router, "confirm_suggestion",
                             lambda message_id, decision, reply_body="": captured.update(reply_body=reply_body))
        line = json.dumps({"cmd": "confirm", "message_id": "m1", "decision": "flag"})
        monkeypatch.setattr(sys, "stdin", iter([line]))

        router._read_stdin_commands()

        assert captured["reply_body"] == ""
