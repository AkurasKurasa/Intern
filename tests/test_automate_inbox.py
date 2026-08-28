import json
import os
import sys
import threading
from http.server import HTTPServer

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import MockGmailClient
from llm_classifier import LLMClassifier
from pattern_profile import PatternProfile
from routing_rules import RuleLayer
from router import InboxRouter
import local_server as ls
import automate_inbox


def _msg(mid, sender_email, subject, body="body text"):
    return {
        "id": mid, "thread_id": mid, "sender": f"Someone <{sender_email}>", "sender_email": sender_email,
        "subject": subject, "snippet": "", "body_text": body, "received_at": "2026-08-27T00:00:00Z",
        "labels": ["INBOX"],
    }


def _build_router(tmp_path, inbox, dominant_reply_sender_domain=None):
    """dominant_reply_sender_domain: when given, seeds that sender's
    pattern with a 100%-reply history before the router is built, so the
    rule layer confidently decides "reply" for that sender without
    needing a real LLM call -- the same seeding technique
    test_inbox_features.py uses."""
    data_dir = tmp_path / "data"
    os.makedirs(data_dir, exist_ok=True)
    (data_dir / "mock_inbox.json").write_text(json.dumps({"inbox": inbox, "sent": []}), encoding="utf-8")
    client = MockGmailClient(data_dir=str(data_dir))
    profile = PatternProfile(path=str(data_dir / "profile.json"))
    if dominant_reply_sender_domain:
        pattern = profile._get_or_create(dominant_reply_sender_domain)
        pattern.reply_count, pattern.forward_count, pattern.ignore_count = 3, 0, 0
        profile.save()
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"capsules": []}), encoding="utf-8")
    rules = RuleLayer(profile, registry_path=str(registry_path))
    classifier = LLMClassifier(provider="none")
    return InboxRouter(client, profile, rules, classifier,
                        history_path=str(data_dir / "routed_history.json"),
                        inbox_checkpoint_path=str(tmp_path / "no_checkpoint.pt"),
                        examples_path=str(data_dir / "training_examples.jsonl"))


@pytest.fixture
def real_page(tmp_path):
    """A real HTTPServer serving the real Inbox Dispatch page against
    throwaway test mail, driven by a real Playwright browser -- the same
    class of test already used for the bulk-bar and origin-check
    regressions, applied here to automate_inbox.py's own click-through
    logic instead of guessing it's correct from reading the code."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    router = _build_router(tmp_path, inbox=[
        _msg("i1", "one@x.com", "first test email"),
        _msg("i2", "two@x.com", "second test email"),
        _msg("i3", "three@x.com", "third test email"),
    ])
    handler_cls = ls.make_handler(router)
    httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.click("#toolbarRefreshBtn")
            page.wait_for_timeout(500)
            yield page
            browser.close()
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def real_page_with_reply(tmp_path):
    """Two real emails: one from a sender whose seeded pattern history
    makes the rule layer confidently decide "reply" (no LLM call
    needed), one generic (decides "flag", same as real_page's
    messages). Used to prove process_one() actually leaves a
    reply-decision row pending instead of blank-confirming it -- a real
    DOM/pipeline test, not a guess from reading the code."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    router = _build_router(tmp_path, inbox=[
        _msg("r1", "boss@work.com", "status update"),
        _msg("g1", "someone@else.com", "generic email"),
    ], dominant_reply_sender_domain="work.com")
    handler_cls = ls.make_handler(router)
    httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.click("#toolbarRefreshBtn")
            page.wait_for_timeout(500)
            yield page
            browser.close()
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


class TestProcessOneSkipsReplyForward:
    def test_reply_decision_is_left_pending_not_blank_confirmed(self, real_page_with_reply):
        # Regression: process_one() used to click #confirmBtn for every
        # decision, including "reply" -- with no text ever typed into the
        # reply textbox, that silently created an empty Gmail draft and
        # recorded nothing for the reply-training pipeline.
        assert real_page_with_reply.locator(".row-item").count() == 2

        result = automate_inbox.process_one(real_page_with_reply, commit=True, index=0, skipped=0)

        assert result["decision"] == "reply"
        assert result["outcome"] == "left pending -- needs a real reply typed by a human"
        # The row must still be there -- nothing was confirmed or drafted.
        assert real_page_with_reply.locator(".row-item").count() == 2

    def test_the_other_email_still_gets_confirmed_normally(self, real_page_with_reply):
        # First call hits the reply-decision row and leaves it pending;
        # skipped=1 on the next call must correctly point past it to the
        # generic (still-confirmable) row.
        first = automate_inbox.process_one(real_page_with_reply, commit=True, index=0, skipped=0)
        assert first["outcome"] == "left pending -- needs a real reply typed by a human"

        second = automate_inbox.process_one(real_page_with_reply, commit=True, index=1, skipped=1)

        assert second["decision"] != "reply"
        assert second["outcome"] == "confirmed"
        # The reply row is still there; only the confirmed one is gone.
        assert real_page_with_reply.locator(".row-item").count() == 1


class TestProcessOneDryRun:
    def test_dry_run_advances_through_distinct_emails_without_reprocessing(self, real_page):
        # Regression: process_one() used to always read row 0. A dry run
        # never confirms anything, so nothing ever leaves row 0 -- every
        # call showed the same first email again, making --limit N in a
        # dry run useless for previewing more than one message.
        first = automate_inbox.process_one(real_page, commit=False, index=0)
        second = automate_inbox.process_one(real_page, commit=False, index=1)
        third = automate_inbox.process_one(real_page, commit=False, index=2)

        assert {first["subject"], second["subject"], third["subject"]} == {
            "first test email", "second test email", "third test email",
        }
        assert first["outcome"] == "skipped (dry run)"

    def test_dry_run_returns_none_past_the_end(self, real_page):
        automate_inbox.process_one(real_page, commit=False, index=0)
        automate_inbox.process_one(real_page, commit=False, index=1)
        automate_inbox.process_one(real_page, commit=False, index=2)
        assert automate_inbox.process_one(real_page, commit=False, index=3) is None


class TestProcessOneCommit:
    def test_commit_actually_clicks_confirm_and_the_row_disappears(self, real_page):
        assert real_page.locator(".row-item").count() == 3

        result = automate_inbox.process_one(real_page, commit=True, index=0)

        assert result["outcome"] == "confirmed"
        assert real_page.locator(".row-item").count() == 2

    def test_commit_processes_every_email_down_to_empty(self, real_page):
        results = []
        while True:
            result = automate_inbox.process_one(real_page, commit=True, index=0)
            if result is None:
                break
            results.append(result)

        assert len(results) == 3
        assert real_page.locator(".row-item").count() == 0


class TestReplyTextareaCarriesMessageId:
    def test_reply_body_name_attribute_matches_open_message_id(self, real_page):
        real_page.locator(".row-item").nth(0).click()
        real_page.wait_for_selector("#detailView:not([hidden])")

        name_attr = real_page.locator("#replyBody").get_attribute("name")

        assert name_attr == "i1"  # real_page's fixture opens messages i1/i2/i3 in order
