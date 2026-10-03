"""Tests for Scope #3's decision modes: habits / hybrid / reasoning.

unit         InboxAgent stops at the right layer in each mode
integration  the router re-decides pending email when the mode changes; the
             server's /api/mode endpoint and the confirm guard
e2e          automate_inbox leaves an undecided email pending on the real page
plumbing     the run script's --mode flag and the Play dialog's choices
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from http.server import HTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "components", "inbox_router")
for p in (ROOT, INBOX):
    if p not in sys.path:
        sys.path.insert(0, p)

import inbox_agent as ia                       # noqa: E402
from gmail_client import EmailMessage          # noqa: E402
from llm_classifier import ClassificationResult  # noqa: E402
from routing_rules import RuleDecision         # noqa: E402


def _message(mid="m1", sender="Dana <dana@north.com>"):
    return EmailMessage(id=mid, thread_id=mid, sender=sender, sender_email="dana@north.com",
                        subject="hello", snippet="", body_text="body", received_at="2026-08-27T00:00:00Z")


class FakeProfile:
    def __init__(self):
        self.asked = []

    def pattern_for(self, email):
        self.asked.append(email)
        return {"habit": "for " + email}


class FakeRules:
    def __init__(self, decision=""):
        self.decision = decision
        self.calls = 0

    def classify(self, message):
        self.calls += 1
        return RuleDecision(decision=self.decision, confidence=0.9 if self.decision else 0.0,
                            rationale="sender history" if self.decision else "")


class FakeLLM:
    def __init__(self):
        self.calls = []

    def classify(self, message, pattern, rule_hint):
        self.calls.append((pattern, rule_hint))
        return ClassificationResult(decision="schedule", confidence=0.7, rationale="reasoned")


def _agent(mode, rule_decision="", tmp_path=None):
    profile, rules, llm = FakeProfile(), FakeRules(rule_decision), FakeLLM()
    agent = ia.InboxAgent(profile, rules, llm,
                          checkpoint_path=str(tmp_path / "none.pt") if tmp_path else "no-such.pt",
                          mode=mode)
    return agent, profile, rules, llm


# ==========================================================================
# unit — each mode stops at the right layer
# ==========================================================================

def test_hybrid_uses_the_sender_habit_when_it_is_confident():
    agent, _, rules, llm = _agent("hybrid", rule_decision="reply")
    d = agent.decide(_message())
    assert (d.decision, d.layer) == ("reply", "rule")
    assert llm.calls == []


def test_hybrid_reasons_when_no_habit_is_confident():
    agent, _, rules, llm = _agent("hybrid")
    d = agent.decide(_message())
    assert (d.decision, d.layer) == ("schedule", "llm")
    assert len(llm.calls) == 1


def test_habits_only_never_calls_the_llm_and_leaves_it_undecided():
    agent, _, rules, llm = _agent("habits")
    d = agent.decide(_message())
    assert d.decision == ia.UNDECIDED
    assert d.layer == "none"
    assert llm.calls == [], "habits-only must never ask the LLM"


def test_habits_only_still_acts_on_a_confident_habit():
    agent, _, _, llm = _agent("habits", rule_decision="forward")
    assert agent.decide(_message()).decision == "forward"
    assert llm.calls == []


def test_reasoning_only_skips_habits_entirely():
    agent, profile, rules, llm = _agent("reasoning", rule_decision="reply")
    d = agent.decide(_message())
    assert (d.decision, d.layer) == ("schedule", "llm")
    assert rules.calls == 0, "reasoning-only must not consult sender history"
    pattern, hint = llm.calls[0]
    assert pattern is None and hint.decision == ""


def test_reasoning_only_skips_the_trained_model(monkeypatch):
    agent, _, _, llm = _agent("reasoning")
    monkeypatch.setattr(agent, "_try_fast_fill", lambda m: pytest.fail("trained model consulted"))
    agent.decide(_message())


def test_the_trained_model_is_tried_first_in_habits_and_hybrid(monkeypatch):
    for mode in ("habits", "hybrid"):
        agent, _, rules, llm = _agent(mode, rule_decision="reply")
        monkeypatch.setattr(agent, "_try_fast_fill", lambda m: ia.InboxDecision(
            decision="leave_alone", confidence=0.95, rationale="model", layer="fast_fill"))
        d = agent.decide(_message())
        assert d.layer == "fast_fill"
        assert rules.calls == 0 and llm.calls == []


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        _agent("guess")
    agent, *_ = _agent("hybrid")
    with pytest.raises(ValueError):
        agent.mode = "guess"


def test_default_mode_is_the_previous_behaviour():
    agent = ia.InboxAgent(FakeProfile(), FakeRules(), FakeLLM(), checkpoint_path="no-such.pt")
    assert agent.mode == "hybrid"


# ==========================================================================
# integration — router and server
# ==========================================================================

def _router(tmp_path):
    from calendar_client import MockCalendarClient
    from gmail_client import MockGmailClient
    from llm_classifier import LLMClassifier
    from pattern_profile import PatternProfile
    from router import InboxRouter
    from routing_rules import RuleLayer

    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    inbox = [{"id": i, "thread_id": i, "sender": f"S <{i}@x.com>", "sender_email": f"{i}@x.com",
              "subject": f"email {i}", "snippet": "", "body_text": "hi", "received_at": "2026-08-27T00:00:00Z",
              "labels": ["INBOX"]} for i in ("a", "b")]
    (data / "mock_inbox.json").write_text(json.dumps({"inbox": inbox, "sent": []}), encoding="utf-8")
    (tmp_path / "registry.json").write_text(json.dumps({"capsules": []}), encoding="utf-8")
    profile = PatternProfile(path=str(data / "profile.json"))
    return InboxRouter(MockGmailClient(data_dir=str(data)), profile,
                       RuleLayer(profile, registry_path=str(tmp_path / "registry.json")),
                       LLMClassifier(provider="none"),
                       history_path=str(data / "routed_history.json"),
                       inbox_checkpoint_path=str(tmp_path / "none.pt"),
                       examples_path=str(data / "examples.jsonl"),
                       reply_examples_path=str(data / "reply_examples.jsonl"),
                       schedule_log_path=str(data / "schedule.txt"),
                       calendar_client=MockCalendarClient(data_dir=str(data)))


def test_switching_mode_re_decides_every_pending_email(tmp_path):
    router = _router(tmp_path)
    router.poll_once()                                   # hybrid, no LLM -> leave_alone
    assert {e["decision"] for e in router.pending_entries()} == {"leave_alone"}

    assert router.set_decision_mode("habits") == 2
    pending = router.pending_entries()
    assert len(pending) == 2, "re-deciding must not duplicate emails"
    assert {e["decision"] for e in pending} == {ia.UNDECIDED}
    assert router.decision_mode == "habits"


def test_switching_mode_leaves_confirmed_emails_alone(tmp_path):
    router = _router(tmp_path)
    router.poll_once()
    router.confirm_suggestion("a", "leave_alone")
    assert router.set_decision_mode("habits") == 1
    assert [e["message_id"] for e in router.pending_entries()] == ["b"]


def test_server_sets_the_mode(tmp_path):
    import local_server as ls
    router = _router(tmp_path)
    router.poll_once()
    status, _, body, _ = ls.handle_request("POST", "/api/mode", b'{"mode": "habits"}', router)
    assert status == 200
    assert json.loads(body) == {"ok": True, "mode": "habits", "redecided": 2}


def test_server_rejects_an_unknown_mode(tmp_path):
    import local_server as ls
    status, _, _, _ = ls.handle_request("POST", "/api/mode", b'{"mode": "guess"}', _router(tmp_path))
    assert status == 400


def test_undecided_can_never_be_confirmed_as_a_decision(tmp_path):
    import local_server as ls
    router = _router(tmp_path)
    router.poll_once()
    router.set_decision_mode("habits")
    status, _, _, _ = ls.handle_request(
        "POST", "/api/confirm", b'{"message_id": "a", "decision": "undecided"}', router)
    assert status == 400
    assert not (tmp_path / "data" / "examples.jsonl").exists(), "no training example recorded"


def test_override_to_undecided_is_rejected(tmp_path):
    import local_server as ls
    router = _router(tmp_path)
    router.poll_once()
    status, _, _, _ = ls.handle_request(
        "POST", "/api/override", b'{"message_id": "a", "new_decision": "undecided"}', router)
    assert status == 400


# ==========================================================================
# e2e — the real page leaves an undecided email pending
# ==========================================================================

def test_e2e_undecided_email_is_left_pending(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    import automate_inbox
    import local_server as ls

    router = _router(tmp_path)
    router.set_decision_mode("habits")
    httpd = HTTPServer(("127.0.0.1", 0), ls.make_handler(router))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{httpd.server_address[1]}/")
            page.click("#toolbarRefreshBtn")
            page.wait_for_timeout(500)
            result = automate_inbox.process_one(page, commit=True, index=0, skipped=0)
            assert result["decision"] == "undecided"
            assert result["outcome"].startswith("left pending")
            assert len(page.locator(".row-item").all()) == 2, "nothing was removed"
            browser.close()
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


# ==========================================================================
# plumbing
# ==========================================================================

def test_run_script_offers_exactly_the_agent_modes():
    import automate_inbox
    assert set(automate_inbox.MODE_LABELS) == set(ia.DECISION_MODES)
    src = open(os.path.join(INBOX, "automate_inbox.py"), encoding="utf8").read()
    assert 'ap.add_argument("--mode", choices=list(MODE_LABELS), default="hybrid"' in src
    assert "set_decision_mode(args.mode)" in src


def test_an_old_server_without_modes_is_reported_not_ignored(monkeypatch):
    import urllib.error
    import automate_inbox

    def _404(*a, **k):
        raise urllib.error.HTTPError(automate_inbox.SERVER_URL + "api/mode", 404, "Not Found", {}, None)

    monkeypatch.setattr(automate_inbox.urllib.request, "urlopen", _404)
    with pytest.raises(SystemExit) as exc:
        automate_inbox.set_decision_mode("habits")
    assert "predates decision modes" in str(exc.value)


def test_play_dialog_offers_the_three_modes():
    renderer = open(os.path.join(ROOT, "app_electron", "renderer", "renderer.js"), encoding="utf8").read()
    block = renderer[renderer.index('"Inbox Dispatch": {'):]
    block = block[:block.index("\n  },\n")]
    assert re.findall(r'name: "([^"]+)"', block) == ["Habits only", "Habits + reasoning", "Reasoning only"]
    assert re.findall(r'args: \["--mode", "([a-z]+)"\]', block) == ["habits", "hybrid", "reasoning"]
