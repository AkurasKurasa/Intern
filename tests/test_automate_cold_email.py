"""
Real-browser tests for automate_cold_email.py's process_one() -- same
pattern test_automate_inbox.py already uses: a real HTTPServer serving
the real merged Inbox Dispatch page (Cold Email is now a sidebar
section of it, not its own page), driven by a real Playwright browser,
against throwaway test data. Proves the walkthrough actually reads real
DOM state and never sends anything, not just that the code looks right.
"""
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
from cold_email_sender import ColdEmailSender
import local_server as ls
import automate_cold_email


class _FakeGmailClientForColdEmail:
    def __init__(self):
        self.drafts = []

    def create_draft(self, to, subject, body, thread_id=""):
        draft_id = f"fake-cold-draft-{len(self.drafts) + 1}"
        self.drafts.append({"to": to, "subject": subject, "body": body, "thread_id": thread_id})
        return draft_id


def _build_router(tmp_path):
    data_dir = tmp_path / "data"
    os.makedirs(data_dir, exist_ok=True)
    (data_dir / "mock_inbox.json").write_text(json.dumps({"inbox": [], "sent": []}), encoding="utf-8")
    client = MockGmailClient(data_dir=str(data_dir))
    profile = PatternProfile(path=str(data_dir / "profile.json"))
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"capsules": []}), encoding="utf-8")
    rules = RuleLayer(profile, registry_path=str(registry_path))
    classifier = LLMClassifier(provider="none")
    return InboxRouter(client, profile, rules, classifier,
                        history_path=str(data_dir / "routed_history.json"),
                        inbox_checkpoint_path=str(tmp_path / "no_checkpoint.pt"),
                        examples_path=str(data_dir / "training_examples.jsonl"))


@pytest.fixture
def real_page_with_task_list(tmp_path):
    """A real HTTPServer serving the real merged page against a
    throwaway two-target task list, driven by a real Playwright browser
    already switched to the Cold Email sidebar section."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    router = _build_router(tmp_path)
    task_list_path = tmp_path / "task_list.txt"
    task_list_path.write_text(
        "Cold email: Q3 outreach\n"
        "Dana Whitfield <dana@x.example.com>\n"
        "Marcus Oyelaran <marcus@x.example.com>\n",
        encoding="utf-8",
    )
    gmail = _FakeGmailClientForColdEmail()
    sender = ColdEmailSender(gmail, task_list_path=str(task_list_path),
                              state_path=str(tmp_path / "cold_email_state.json"))
    handler_cls = ls.make_handler(router, cold_email_sender=sender)
    httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_selector("#navColdEmail")
            page.click("#navColdEmail")
            page.wait_for_timeout(500)
            yield page, gmail
            browser.close()
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


class TestProcessOneDryRun:
    def test_reads_the_real_target_off_the_dom_and_never_sends(self, real_page_with_task_list):
        page, gmail = real_page_with_task_list
        result = automate_cold_email.process_one(page, False, 0)
        assert result["name"] == "Dana Whitfield"
        assert result["email"] == "dana@x.example.com"
        assert result["subject"] == "Q3 outreach"
        assert result["outcome"] == "left pending -- needs a real message typed by a human"
        assert gmail.drafts == []  # never sends anything on its own

    def test_walking_past_the_last_target_returns_none(self, real_page_with_task_list):
        page, _gmail = real_page_with_task_list
        assert automate_cold_email.process_one(page, False, 2) is None  # only 2 targets, index 2 is out of range

    def test_going_back_leaves_the_list_visible_for_the_next_target(self, real_page_with_task_list):
        page, _gmail = real_page_with_task_list
        automate_cold_email.process_one(page, False, 0)
        assert page.is_visible("#coldEmailListView")
        assert not page.is_visible("#coldEmailDetailView")
        second = automate_cold_email.process_one(page, False, 1)
        assert second["name"] == "Marcus Oyelaran"


class TestProcessOneCommit:
    """--commit is the one deliberate exception to "never invent text" in
    this whole project (direct instruction: "Break that rule for Scope
    #3") -- generate_cold_email() is monkeypatched here rather than
    hitting a real LM Studio, same test-isolation discipline every other
    test in this suite already holds; cold_email_llm.py has its own
    dedicated tests for the real LM Studio call."""

    def test_commit_types_the_generated_text_and_actually_sends(self, real_page_with_task_list, monkeypatch):
        page, gmail = real_page_with_task_list
        monkeypatch.setattr(automate_cold_email, "generate_cold_email",
                             lambda name, context: ("Real subject", "Real body text."))

        result = automate_cold_email.process_one(page, True, 0)

        assert result["outcome"] == "sent"
        assert result["subject"] == "Real subject"
        assert result["body"] == "Real body text."
        assert gmail.drafts == [{"to": "dana@x.example.com", "subject": "Real subject",
                                  "body": "Real body text.", "thread_id": ""}]

    def test_commit_sending_removes_the_target_so_the_next_call_reads_the_next_one(self, real_page_with_task_list, monkeypatch):
        page, gmail = real_page_with_task_list
        monkeypatch.setattr(automate_cold_email, "generate_cold_email",
                             lambda name, context: ("Real subject", "Real body text."))

        first = automate_cold_email.process_one(page, True, 0)
        assert first["name"] == "Dana Whitfield"
        # Dana is gone now -- re-reading index 0 lands on Marcus, not Dana again.
        second = automate_cold_email.process_one(page, True, 0)
        assert second["name"] == "Marcus Oyelaran"
        assert len(gmail.drafts) == 2

    def test_commit_with_no_llm_available_leaves_the_target_pending_and_sends_nothing(self, real_page_with_task_list, monkeypatch):
        page, gmail = real_page_with_task_list
        monkeypatch.setattr(automate_cold_email, "generate_cold_email", lambda name, context: ("", ""))

        result = automate_cold_email.process_one(page, True, 0)

        assert result["outcome"] == "left pending -- LM Studio unavailable"
        assert gmail.drafts == []


# ── main()'s recovery when the browser closes mid-run ───────────────────────
# Live-found: after 3 real sends completed, the 4th call (checking for more
# targets) hit TargetClosedError -- the browser/page had closed out from
# under the loop for reasons entirely outside this script's own control
# (nothing in the JS or Python ever closes it itself). main() used to let
# that propagate as a raw, uncaught traceback, discarding the summary of
# real work already done. Fakes stand in for Playwright here -- the crash
# is a control-flow property of main(), not something that needs a real
# browser to prove.

class _FakePlaywrightError(Exception):
    """Stands in for playwright.sync_api.Error in these tests -- main()
    catches the real one by class identity, so the fixture below installs
    this exact class as the module's Error for the duration of the test."""


class _FakePage:
    def goto(self, url): pass
    def wait_for_selector(self, sel): pass
    def click(self, sel): pass
    def wait_for_timeout(self, ms): pass


class _FakeBrowser:
    def __init__(self):
        self.closed = False

    def new_page(self):
        return _FakePage()

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    def launch(self, headless=False):
        return self._browser


class _FakePlaywrightContext:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def fake_playwright(monkeypatch):
    """Patches playwright.sync_api's own sync_playwright/Error, which is
    what main()'s local `from playwright.sync_api import ...` actually
    re-resolves on every call -- same reasoning as cold_email_llm's own
    test fixtures for its local `from openai import OpenAI`."""
    import playwright.sync_api as psa
    fake_browser = _FakeBrowser()
    monkeypatch.setattr(psa, "sync_playwright", lambda: _FakePlaywrightContext(fake_browser))
    monkeypatch.setattr(psa, "Error", _FakePlaywrightError)
    return fake_browser


class TestMainRecoversFromMidRunBrowserClosure:
    def test_two_real_results_survive_a_crash_on_the_third_call(self, tmp_path, monkeypatch, fake_playwright, capsys):
        # main()'s own final print (log_path.relative_to(REPO)) assumes the
        # log path is under REPO -- true for its own default, not for an
        # arbitrary tmp_path passed via --log, so REPO is repointed at
        # tmp_path here too rather than writing a real log into the actual
        # repo tree during a test.
        monkeypatch.setattr(automate_cold_email, "REPO", tmp_path)
        monkeypatch.setattr(automate_cold_email, "ensure_server_running", lambda: None)

        calls = []

        def _fake_process_one(page, commit, index):
            calls.append(index)
            if len(calls) <= 2:
                return {"name": f"Target {len(calls)}", "email": f"t{len(calls)}@x.example.com",
                        "subject": "s", "body": "b", "outcome": "sent"}
            raise _FakePlaywrightError("Target page, context or browser has been closed")

        monkeypatch.setattr(automate_cold_email, "process_one", _fake_process_one)

        log_path = tmp_path / "run.json"
        monkeypatch.setattr(sys, "argv", ["automate_cold_email.py", "--commit", "--pace", "0",
                                           "--headless", "--log", str(log_path)])

        result = automate_cold_email.main()  # must not raise

        assert result == 0
        assert "Browser closed unexpectedly" in capsys.readouterr().out
        saved = json.loads(log_path.read_text(encoding="utf-8"))
        assert len(saved["results"]) == 2  # the 2 real sends survive; the crash doesn't erase them
        assert saved["results"][0]["outcome"] == "sent"
        assert saved["results"][1]["outcome"] == "sent"

    def test_a_second_closure_error_from_browser_close_itself_is_also_swallowed(self, tmp_path, monkeypatch, fake_playwright):
        """The browser already died once (mid-loop); calling close() on an
        already-dead browser can raise the exact same error a second time.
        That second error must not escape either -- there's nothing left
        to clean up, and main() has already reported the crash once."""
        monkeypatch.setattr(automate_cold_email, "REPO", tmp_path)
        monkeypatch.setattr(automate_cold_email, "ensure_server_running", lambda: None)
        monkeypatch.setattr(automate_cold_email, "process_one",
                             lambda page, commit, index: (_ for _ in ()).throw(_FakePlaywrightError("closed")))

        def _close_raises_again():
            raise _FakePlaywrightError("Target page, context or browser has been closed")
        fake_playwright.close = _close_raises_again

        monkeypatch.setattr(sys, "argv", ["automate_cold_email.py", "--commit", "--pace", "0",
                                           "--headless", "--log", str(tmp_path / "run.json")])

        result = automate_cold_email.main()  # must not raise even though close() itself also fails

        assert result == 0
