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


def _build_router(tmp_path, inbox, dominant_reply_sender_domain=None, dominant_forward_sender_domain=None):
    """dominant_reply_sender_domain/dominant_forward_sender_domain: when
    given, seeds that sender's pattern with a 100%-reply or 100%-forward
    history before the router is built, so the rule layer confidently
    decides "reply"/"forward" for that sender without needing a real LLM
    call -- the same seeding technique test_inbox_features.py uses."""
    data_dir = tmp_path / "data"
    os.makedirs(data_dir, exist_ok=True)
    (data_dir / "mock_inbox.json").write_text(json.dumps({"inbox": inbox, "sent": []}), encoding="utf-8")
    client = MockGmailClient(data_dir=str(data_dir))
    profile = PatternProfile(path=str(data_dir / "profile.json"))
    if dominant_reply_sender_domain:
        pattern = profile._get_or_create(dominant_reply_sender_domain)
        pattern.reply_count, pattern.forward_count, pattern.ignore_count = 3, 0, 0
        profile.save()
    if dominant_forward_sender_domain:
        pattern = profile._get_or_create(dominant_forward_sender_domain)
        pattern.reply_count, pattern.forward_count, pattern.ignore_count = 0, 3, 0
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
    needed), one generic (decides "leave_alone", same as real_page's
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


@pytest.fixture
def real_page_with_forward(tmp_path):
    """One real email from a sender whose seeded pattern history makes
    the rule layer confidently decide "forward" (no LLM call needed).
    Used to prove process_one()'s forward auto-draft branch against a
    real DOM/pipeline, not a guess from reading the code."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    router = _build_router(tmp_path, inbox=[
        _msg("f1", "ops@vendor.com", "grades to upload"),
    ], dominant_forward_sender_domain="vendor.com")
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


class TestAutoDraftReply:
    # auto_draft_reply=True: a deliberate, explicitly authorized exception
    # for "reply" only ("Break it goddamnit, I literally need to see it
    # full functioning before I can deem this as complete") -- scoped
    # exactly like automate_cold_email.py's own --commit exception, and
    # only inside this script; router.py's real confirm_suggestion() path
    # a person uses by hand is completely untouched.
    def test_a_real_ai_drafted_reply_is_typed_and_sent(self, real_page_with_reply, monkeypatch):
        import inbox_reply_llm
        monkeypatch.setattr(inbox_reply_llm, "generate_reply",
                             lambda sender, subject, body: "Thanks, I'll take care of it.")

        assert real_page_with_reply.locator(".row-item").count() == 2

        result = automate_inbox.process_one(real_page_with_reply, commit=True, index=0, skipped=0,
                                             auto_draft_reply=True)

        assert result["decision"] == "reply"
        assert result["outcome"] == "confirmed (AI-drafted reply -- draft created, not sent)"
        # The row is really gone -- a real send happened, not a preview.
        assert real_page_with_reply.locator(".row-item").count() == 1

    def test_falls_back_to_left_pending_when_lm_studio_returns_nothing(self, real_page_with_reply, monkeypatch):
        import inbox_reply_llm
        monkeypatch.setattr(inbox_reply_llm, "generate_reply", lambda sender, subject, body: "")

        result = automate_inbox.process_one(real_page_with_reply, commit=True, index=0, skipped=0,
                                             auto_draft_reply=True)

        assert result["outcome"] == "left pending -- LM Studio unavailable for auto-draft"
        # Nothing was sent -- the row must still be there.
        assert real_page_with_reply.locator(".row-item").count() == 2

    def test_only_a_real_reply_decision_triggers_reply_auto_draft(self, real_page_with_reply, monkeypatch):
        # index 1 in this fixture is the generic email, which the LLM
        # fallback (provider="none") decides "leave_alone" for -- not "reply".
        # Guards the exact-decision check: generate_reply() must never
        # fire for schedule/leave_alone (forward gets its own
        # separate auto-draft path, tested in TestAutoDraftForward).
        import inbox_reply_llm
        called = []
        monkeypatch.setattr(inbox_reply_llm, "generate_reply",
                             lambda sender, subject, body: called.append(1) or "should never be used")

        result = automate_inbox.process_one(real_page_with_reply, commit=True, index=1, skipped=1,
                                             auto_draft_reply=True)

        assert result["decision"] != "reply"
        assert not called, "generate_reply must only ever be called for a real 'reply' decision"


class TestAutoDraftForward:
    # Same shape as TestAutoDraftReply, for "forward": the recipient
    # address is never LLM-invented (forward_recipient() derives it
    # deterministically from the sender's own domain), only the note.
    def test_a_real_ai_drafted_forward_is_typed_and_sent(self, real_page_with_forward, monkeypatch):
        import inbox_reply_llm
        monkeypatch.setattr(inbox_reply_llm, "generate_forward_note",
                             lambda sender, subject, body: "Please take a look at this.")

        assert real_page_with_forward.locator(".row-item").count() == 1

        result = automate_inbox.process_one(real_page_with_forward, commit=True, index=0, skipped=0,
                                             auto_draft_reply=True)

        assert result["decision"] == "forward"
        assert result["outcome"] == "confirmed (AI-drafted forward -- draft created, not sent)"
        # The row is really gone -- a real send happened, not a preview.
        assert real_page_with_forward.locator(".row-item").count() == 0

    def test_falls_back_to_left_pending_when_lm_studio_returns_nothing(self, real_page_with_forward, monkeypatch):
        import inbox_reply_llm
        monkeypatch.setattr(inbox_reply_llm, "generate_forward_note", lambda sender, subject, body: "")

        result = automate_inbox.process_one(real_page_with_forward, commit=True, index=0, skipped=0,
                                             auto_draft_reply=True)

        assert result["outcome"] == "left pending -- LM Studio unavailable for auto-draft"
        assert real_page_with_forward.locator(".row-item").count() == 1

    def test_recipient_is_derived_from_sender_domain_not_invented(self, real_page_with_forward, monkeypatch):
        import inbox_reply_llm
        captured = {}

        def _fake_note(sender, subject, body):
            return "Please take a look at this."
        monkeypatch.setattr(inbox_reply_llm, "generate_forward_note", _fake_note)

        real_forward_recipient = inbox_reply_llm.forward_recipient

        def _capturing_recipient(sender_email):
            captured["sender_email"] = sender_email
            return real_forward_recipient(sender_email)
        monkeypatch.setattr(inbox_reply_llm, "forward_recipient", _capturing_recipient)

        automate_inbox.process_one(real_page_with_forward, commit=True, index=0, skipped=0,
                                    auto_draft_reply=True)

        assert captured["sender_email"] == "ops@vendor.com"


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


class TestMainWaitsForSlowFirstPoll:
    def test_main_reads_real_rows_even_when_the_first_api_inbox_call_is_slow(self, tmp_path, monkeypatch):
        # Regression, found live 2026-09-02: main() used to click Refresh
        # then wait a fixed 600ms before reading rows off the page. That
        # was only ever enough while /api/inbox read already-cached
        # decisions -- a real LLM classify() call (the whole point of the
        # llm_classifier.py model-id fix) takes real seconds, and the
        # fixed wait raced ahead of the response, reading 0 rows and
        # reporting "emails processed 0" even with real pending mail
        # waiting. Fixed by waiting for the actual /api/inbox response
        # instead of guessing how long it takes.
        pytest.importorskip("playwright.sync_api")
        import time
        import playwright.sync_api as psa

        router = _build_router(tmp_path, inbox=[
            _msg("s1", "one@x.com", "slow-poll test email"),
        ])
        base_handler_cls = ls.make_handler(router)

        class _SlowHandler(base_handler_cls):
            def do_GET(self):
                if self.path == "/api/inbox":
                    time.sleep(1.5)  # longer than the old fixed 600ms wait
                super().do_GET()

        httpd = HTTPServer(("127.0.0.1", 0), _SlowHandler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            monkeypatch.setattr(automate_inbox, "SERVER_URL", f"http://127.0.0.1:{port}/")
            monkeypatch.setattr(automate_inbox, "ensure_server_running", lambda: None)
            monkeypatch.setattr(automate_inbox, "REPO", tmp_path)
            log_path = tmp_path / "run.json"
            monkeypatch.setattr(sys, "argv", ["automate_inbox.py", "--commit", "--pace", "0",
                                               "--headless", "--log", str(log_path)])

            automate_inbox.main()

            saved = json.loads(log_path.read_text(encoding="utf-8"))
            assert saved["results"], "main() read 0 rows -- it raced ahead of the slow /api/inbox response"
            assert saved["results"][0]["subject"] == "slow-poll test email"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


# ── main()'s recovery when the browser closes mid-run ───────────────────────
# Same live-found issue and same fix as automate_cold_email.py's own
# main(): the browser/page can close out from under the loop for reasons
# entirely outside this script's own control, and it used to let that
# propagate as a raw, uncaught traceback, discarding the summary of real
# work already confirmed. Fakes stand in for Playwright -- this is a
# control-flow property of main(), not something that needs a real
# browser to prove. See test_automate_cold_email.py for the sibling tests.

class _FakeInboxPlaywrightError(Exception):
    pass


class _FakeInboxExpectResponseCtx:
    def __enter__(self): return self
    def __exit__(self, *exc_info): return False


class _FakeInboxPage:
    def goto(self, url): pass
    def click(self, sel): pass
    def wait_for_timeout(self, ms): pass
    def expect_response(self, predicate, timeout=None): return _FakeInboxExpectResponseCtx()


class _FakeInboxBrowser:
    def new_page(self):
        return _FakeInboxPage()

    def close(self):
        pass


class _FakeInboxChromium:
    def __init__(self, browser):
        self._browser = browser

    def launch(self, headless=False):
        return self._browser


class _FakeInboxPlaywrightContext:
    def __init__(self, browser):
        self.chromium = _FakeInboxChromium(browser)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class TestMainRecoversFromMidRunBrowserClosure:
    def test_two_real_results_survive_a_crash_on_the_third_call(self, tmp_path, monkeypatch, capsys):
        import playwright.sync_api as psa
        fake_browser = _FakeInboxBrowser()
        monkeypatch.setattr(psa, "sync_playwright", lambda: _FakeInboxPlaywrightContext(fake_browser))
        monkeypatch.setattr(psa, "Error", _FakeInboxPlaywrightError)
        monkeypatch.setattr(automate_inbox, "REPO", tmp_path)
        monkeypatch.setattr(automate_inbox, "ensure_server_running", lambda: None)

        calls = []

        def _fake_process_one(page, commit, index, skipped, dwell_ms=0, auto_draft_reply=False):
            calls.append((index, skipped))
            if len(calls) <= 2:
                return {"sender": "s", "subject": f"m{len(calls)}", "decision": "leave_alone",
                        "rationale": "r", "outcome": "confirmed"}
            raise _FakeInboxPlaywrightError("Target page, context or browser has been closed")

        monkeypatch.setattr(automate_inbox, "process_one", _fake_process_one)

        log_path = tmp_path / "run.json"
        monkeypatch.setattr(sys, "argv", ["automate_inbox.py", "--commit", "--pace", "0",
                                           "--headless", "--log", str(log_path)])

        result = automate_inbox.main()  # must not raise

        assert result == 0
        assert "Browser closed unexpectedly" in capsys.readouterr().out
        saved = json.loads(log_path.read_text(encoding="utf-8"))
        assert len(saved["results"]) == 2
        assert saved["results"][0]["outcome"] == "confirmed"
