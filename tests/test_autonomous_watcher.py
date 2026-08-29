import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMP = os.path.join(_ROOT, "components")
_INBOX_DIR = os.path.join(_COMP, "inbox_router")
for _p in (_ROOT, _COMP, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.capsule import CapsuleRegistry
from gmail_client import EmailMessage
import autonomous_watcher as watcher


class FakeReplySuggestion:
    def __init__(self, reply_body="", confidence=0.0, source_message_id=""):
        self.reply_body = reply_body
        self.confidence = confidence
        self.source_message_id = source_message_id


class FakeReplyAgent:
    """Stands in for reply_agent.ReplyAgent -- returns a scripted
    suggestion instead of actually scoring anything, so tests can
    exercise handle_entry()'s auto-draft branch without loading a real
    trained model."""

    def __init__(self, suggestion: FakeReplySuggestion):
        self._suggestion = suggestion
        self.calls = []

    def suggest_reply(self, message):
        self.calls.append(message)
        return self._suggestion


class FakeGmailClient:
    """Stands in for GmailClientBase -- records every create_draft() call
    instead of writing anywhere real. No send() method exists on this
    fake either, matching the real interface's own hard boundary."""

    def __init__(self, messages):
        self._messages = {m.id: m for m in messages}
        self.drafts_created = []

    def get_message(self, message_id):
        return self._messages.get(message_id)

    def create_draft(self, to, subject, body, thread_id=""):
        draft_id = f"fake-draft-{len(self.drafts_created) + 1}"
        self.drafts_created.append({"to": to, "subject": subject, "body": body, "thread_id": thread_id})
        return draft_id


def _msg(mid="m1", sender_email="boss@work.com", subject="Status update", thread_id="t1"):
    return EmailMessage(
        id=mid, thread_id=thread_id, sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text="Where do things stand?",
        received_at="2026-08-27T00:00:00Z",
    )


class FakePopen:
    """Stands in for subprocess.Popen -- records every call instead of
    actually spawning a real process, so tests can verify exactly what
    WOULD have been launched (e.g. real Scope #2 automation) without any
    real automation ever running during a test."""

    def __init__(self):
        self.calls = []
        self._next_pid = 1000

    def __call__(self, argv, cwd=None):
        self._next_pid += 1
        self.calls.append({"argv": argv, "cwd": cwd})
        return _FakeProcess(self._next_pid)


class _FakeProcess:
    def __init__(self, pid):
        self.pid = pid


class FakeRouter:
    """Returns a scripted sequence of pending-entry dicts from
    process_next_unprocessed(), then None once the script is exhausted
    -- mirrors InboxRouter's real contract without needing a real
    classification pipeline or real mail."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def process_next_unprocessed(self):
        self.calls += 1
        if self._script:
            return self._script.pop(0)
        return None


def _entry(decision, capsule_name="", message_id="m1", subject="Test email"):
    return {
        "message_id": message_id, "subject": subject, "decision": decision,
        "capsule_name": capsule_name, "rationale": "x", "confidence": 0.9,
    }


def _build_registry(tmp_path, script_entrypoint=None):
    registry_path = tmp_path / "registry.json"
    capsules = [{
        "name": "Sheet-to-Portal Matcher", "description": "", "model_path": "",
        "trigger_keywords": [], "trigger_apps": [], "kind": "script",
        "entrypoint": script_entrypoint or "components/scope2/automate.py",
        "args": ["--variant", "v0_base", "--commit"], "cwd": "",
    }]
    registry_path.write_text(json.dumps({"capsules": capsules}), encoding="utf-8")
    return CapsuleRegistry(registry_path=str(registry_path))


class TestHandleEntry:
    def test_all_non_reply_forward_decisions_are_left_pending(self, tmp_path):
        # Task 1 removed routing entirely -- there is nothing left to
        # dispatch or surface specially. Every decision handle_entry()
        # doesn't have reply_agent/gmail_client wiring for (which is all
        # of them here, since neither is supplied) falls to the one
        # remaining fallback outcome.
        registry = _build_registry(tmp_path)
        popen = FakePopen()
        dispatch_log = tmp_path / "dispatch_log.jsonl"
        needs_attention = tmp_path / "needs_attention.jsonl"

        for decision in ("reply", "forward", "schedule", "cold_email", "flag", "leave_alone"):
            outcome = watcher.handle_entry(_entry(decision), registry, _ROOT, popen=popen,
                                            dispatch_log_path=dispatch_log,
                                            needs_attention_path=needs_attention)
            assert outcome["action"] == "left_pending"

        assert popen.calls == []
        assert not dispatch_log.exists()
        assert not needs_attention.exists()


class TestHandleEntryAutoDraft:
    def test_confident_reply_creates_draft_and_logs(self, tmp_path):
        registry = _build_registry(tmp_path)
        message = _msg(mid="m1", sender_email="boss@work.com", subject="Status update")
        gmail_client = FakeGmailClient([message])
        reply_agent = FakeReplyAgent(FakeReplySuggestion(
            reply_body="Thanks, looks on track.", confidence=0.91, source_message_id="old1"))
        entry = _entry("reply", message_id="m1", subject="Status update")
        auto_draft_log = tmp_path / "auto_drafts.jsonl"

        outcome = watcher.handle_entry(entry, registry, _ROOT,
                                        reply_agent=reply_agent, gmail_client=gmail_client,
                                        auto_draft_log_path=auto_draft_log)

        assert outcome["action"] == "auto_drafted"
        assert len(gmail_client.drafts_created) == 1
        draft = gmail_client.drafts_created[0]
        assert draft["to"] == "boss@work.com"
        assert draft["subject"] == "Re: Status update"
        assert draft["body"] == "Thanks, looks on track."
        logged = json.loads(auto_draft_log.read_text().splitlines()[0])
        assert logged["confidence"] == 0.91
        assert logged["source_message_id"] == "old1"

    def test_forward_decision_uses_forward_to_and_fwd_subject(self, tmp_path):
        registry = _build_registry(tmp_path)
        message = _msg(mid="m2", subject="Weekly doc")
        gmail_client = FakeGmailClient([message])
        reply_agent = FakeReplyAgent(FakeReplySuggestion(
            reply_body="Passing this along.", confidence=0.8))
        entry = _entry("forward", message_id="m2", subject="Weekly doc")
        entry["forward_to"] = "team@work.com"

        outcome = watcher.handle_entry(entry, registry, _ROOT,
                                        reply_agent=reply_agent, gmail_client=gmail_client,
                                        auto_draft_log_path=tmp_path / "auto_drafts.jsonl")

        assert outcome["action"] == "auto_drafted"
        draft = gmail_client.drafts_created[0]
        assert draft["to"] == "team@work.com"
        assert draft["subject"] == "Fwd: Weekly doc"

    def test_low_confidence_suggestion_falls_through_to_left_pending(self, tmp_path):
        registry = _build_registry(tmp_path)
        message = _msg(mid="m1")
        gmail_client = FakeGmailClient([message])
        # An empty reply_body is exactly ReplyAgent's own contract for
        # "not confident enough" -- never invents anything to draft.
        reply_agent = FakeReplyAgent(FakeReplySuggestion(reply_body="", confidence=0.3))
        entry = _entry("reply", message_id="m1")

        outcome = watcher.handle_entry(entry, registry, _ROOT,
                                        reply_agent=reply_agent, gmail_client=gmail_client,
                                        auto_draft_log_path=tmp_path / "auto_drafts.jsonl")

        assert outcome["action"] == "left_pending"
        assert gmail_client.drafts_created == []

    def test_missing_message_falls_through_to_left_pending(self, tmp_path):
        registry = _build_registry(tmp_path)
        gmail_client = FakeGmailClient([])   # message not found
        reply_agent = FakeReplyAgent(FakeReplySuggestion(reply_body="won't be reached", confidence=0.99))
        entry = _entry("reply", message_id="does-not-exist")

        outcome = watcher.handle_entry(entry, registry, _ROOT,
                                        reply_agent=reply_agent, gmail_client=gmail_client)

        assert outcome["action"] == "left_pending"
        assert gmail_client.drafts_created == []
        assert reply_agent.calls == []

    def test_requires_both_reply_agent_and_gmail_client_to_activate(self, tmp_path):
        registry = _build_registry(tmp_path)
        message = _msg(mid="m1")
        gmail_client = FakeGmailClient([message])
        reply_agent = FakeReplyAgent(FakeReplySuggestion(reply_body="would draft this", confidence=0.99))
        entry = _entry("reply", message_id="m1")

        # Only reply_agent supplied, no gmail_client -- must not half-activate.
        outcome = watcher.handle_entry(entry, registry, _ROOT, reply_agent=reply_agent)
        assert outcome["action"] == "left_pending"
        assert gmail_client.drafts_created == []

        # Only gmail_client supplied, no reply_agent -- same.
        outcome = watcher.handle_entry(entry, registry, _ROOT, gmail_client=gmail_client)
        assert outcome["action"] == "left_pending"
        assert gmail_client.drafts_created == []

    def test_non_reply_forward_decision_unaffected_by_reply_agent_presence(self, tmp_path):
        # Sanity: wiring in reply_agent/gmail_client must not cause a
        # non-reply/forward decision to do anything Gmail-side.
        registry = _build_registry(tmp_path)
        popen = FakePopen()
        gmail_client = FakeGmailClient([_msg(mid="m1")])
        reply_agent = FakeReplyAgent(FakeReplySuggestion(reply_body="irrelevant", confidence=0.99))
        entry = _entry("flag", message_id="m1")

        outcome = watcher.handle_entry(entry, registry, _ROOT, popen=popen,
                                        reply_agent=reply_agent, gmail_client=gmail_client,
                                        dispatch_log_path=tmp_path / "d.jsonl",
                                        needs_attention_path=tmp_path / "n.jsonl")

        assert outcome["action"] == "left_pending"
        assert popen.calls == []
        assert gmail_client.drafts_created == []


class TestWatch:
    def test_stop_when_idle_exits_without_sleeping(self, tmp_path):
        router = FakeRouter([
            _entry("flag", message_id="m1"),
            _entry("schedule", message_id="m2"),
        ])
        registry = _build_registry(tmp_path)
        popen = FakePopen()
        slept = []

        outcomes = watcher.watch(router, registry, _ROOT, stop_when_idle=True,
                                  popen=popen, sleep=lambda s: slept.append(s),
                                  dispatch_log_path=tmp_path / "d.jsonl",
                                  needs_attention_path=tmp_path / "n.jsonl")

        assert len(outcomes) == 2
        assert outcomes[0]["action"] == "left_pending"
        assert outcomes[1]["action"] == "left_pending"
        assert slept == []

    def test_continuous_mode_sleeps_when_idle_then_keeps_watching(self, tmp_path):
        router = FakeRouter([_entry("flag")])  # one entry, then None forever
        registry = _build_registry(tmp_path)
        popen = FakePopen()
        slept = []

        def fake_sleep(seconds):
            slept.append(seconds)
            if len(slept) >= 3:
                raise StopIteration  # ends the test's own patience, not the loop's

        try:
            watcher.watch(router, registry, _ROOT, poll_interval=5, stop_when_idle=False,
                          popen=popen, sleep=fake_sleep,
                          dispatch_log_path=tmp_path / "d.jsonl",
                          needs_attention_path=tmp_path / "n.jsonl")
        except StopIteration:
            pass

        assert slept == [5, 5, 5]

    def test_max_iterations_caps_even_with_more_pending(self, tmp_path):
        router = FakeRouter([_entry("flag", message_id=f"m{i}") for i in range(5)])
        registry = _build_registry(tmp_path)
        popen = FakePopen()

        outcomes = watcher.watch(router, registry, _ROOT, stop_when_idle=True,
                                  max_iterations=2, popen=popen, sleep=lambda s: None,
                                  dispatch_log_path=tmp_path / "d.jsonl",
                                  needs_attention_path=tmp_path / "n.jsonl")

        assert len(outcomes) == 2

    def test_threads_reply_agent_and_gmail_client_into_handle_entry(self, tmp_path):
        message = _msg(mid="m1", subject="Status update")
        router = FakeRouter([_entry("reply", message_id="m1", subject="Status update")])
        registry = _build_registry(tmp_path)
        gmail_client = FakeGmailClient([message])
        reply_agent = FakeReplyAgent(FakeReplySuggestion(reply_body="Thanks!", confidence=0.9))

        outcomes = watcher.watch(router, registry, _ROOT, stop_when_idle=True,
                                  popen=FakePopen(), sleep=lambda s: None,
                                  reply_agent=reply_agent, gmail_client=gmail_client,
                                  dispatch_log_path=tmp_path / "d.jsonl",
                                  needs_attention_path=tmp_path / "n.jsonl",
                                  auto_draft_log_path=tmp_path / "auto.jsonl")

        assert len(outcomes) == 1
        assert outcomes[0]["action"] == "auto_drafted"
        assert len(gmail_client.drafts_created) == 1
