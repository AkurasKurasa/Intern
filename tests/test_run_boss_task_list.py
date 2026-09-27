import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_boss_task_list


class _FakeExpectResponseCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakePage:
    def goto(self, url):
        pass

    def evaluate(self, script):
        pass

    def click(self, sel, **kwargs):
        pass

    def wait_for_timeout(self, ms):
        pass

    def expect_response(self, predicate, timeout=None):
        return _FakeExpectResponseCtx()


class _FakeBrowser:
    def __init__(self):
        self.new_page_calls = 0
        self._page = _FakePage()

    def new_page(self):
        self.new_page_calls += 1
        return self._page

    def close(self):
        pass


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser
        self.launch_calls = 0

    def launch(self, headless=False):
        self.launch_calls += 1
        return self._browser


class _FakePlaywrightContext:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakePlaywrightError(Exception):
    pass


@pytest.fixture
def fake_playwright(monkeypatch):
    """Same class of fake already used by automate_inbox.py's/
    automate_cold_email.py's own crash-recovery tests -- stands in for a
    real Playwright browser so this can be tested as pure control flow
    (one page, two phases) without needing an actual browser."""
    import playwright.sync_api as psa
    browser = _FakeBrowser()
    monkeypatch.setattr(psa, "sync_playwright", lambda: _FakePlaywrightContext(browser))
    monkeypatch.setattr(psa, "Error", _FakePlaywrightError)
    monkeypatch.setattr(run_boss_task_list, "ensure_server_running", lambda: None)
    return browser


def test_only_one_browser_and_one_page_for_both_phases(tmp_path, monkeypatch, fake_playwright):
    # The real point of the rewrite: "I want it to be one seamless thing,
    # it can't open the web browser again and again." One launch(), one
    # new_page() -- not one pair per phase.
    monkeypatch.setattr(run_boss_task_list, "REPO", tmp_path)
    monkeypatch.setattr(run_boss_task_list, "cold_email_process_one", lambda page, commit, index: None)
    monkeypatch.setattr(run_boss_task_list, "inbox_process_one", lambda *a, **kw: None)
    monkeypatch.setattr(sys, "argv", ["run_boss_task_list.py", "--pace", "0", "--headless"])

    run_boss_task_list.main()

    assert fake_playwright.new_page_calls == 1


def test_both_phases_use_the_exact_same_page_object(tmp_path, monkeypatch, fake_playwright):
    seen_pages = []

    def _fake_cold_email(page, commit, index):
        seen_pages.append(page)
        return None

    def _fake_inbox(page, commit, index, skipped, dwell_ms=0, auto_draft_reply=False):
        seen_pages.append(page)
        return None

    monkeypatch.setattr(run_boss_task_list, "REPO", tmp_path)
    monkeypatch.setattr(run_boss_task_list, "cold_email_process_one", _fake_cold_email)
    monkeypatch.setattr(run_boss_task_list, "inbox_process_one", _fake_inbox)
    monkeypatch.setattr(sys, "argv", ["run_boss_task_list.py", "--pace", "0", "--headless"])

    run_boss_task_list.main()

    assert len(seen_pages) == 2
    assert seen_pages[0] is seen_pages[1]  # cold email and inbox shared the one real page


def test_no_limit_by_default_walks_until_none_in_both_phases(tmp_path, monkeypatch, fake_playwright):
    # "It didn't go through the whole inbox... It has to navigate all
    # the mail in the inbox." No --limit passed here at all -- both
    # phases must keep going until their own process_one() says None,
    # not stop after some fixed count.
    cold_email_calls = []
    inbox_calls = []

    def _fake_cold_email(page, commit, index):
        cold_email_calls.append(index)
        return {"name": f"T{index}", "email": f"t{index}@x.com", "subject": "s", "body": "",
                "outcome": "left pending -- needs a real message typed by a human"} if len(cold_email_calls) <= 7 else None

    def _fake_inbox(page, commit, index, skipped, dwell_ms=0, auto_draft_reply=False):
        inbox_calls.append(index)
        return {"sender": "s", "subject": f"m{index}", "decision": "leave_alone",
                "rationale": "r", "outcome": "confirmed"} if len(inbox_calls) <= 12 else None

    monkeypatch.setattr(run_boss_task_list, "REPO", tmp_path)
    monkeypatch.setattr(run_boss_task_list, "cold_email_process_one", _fake_cold_email)
    monkeypatch.setattr(run_boss_task_list, "inbox_process_one", _fake_inbox)
    monkeypatch.setattr(sys, "argv", ["run_boss_task_list.py", "--pace", "0", "--headless"])

    run_boss_task_list.main()

    # 7 real items plus the final None-returning call each -- proves the
    # loop kept going well past any small hardcoded demo count.
    assert len(cold_email_calls) == 8
    assert len(inbox_calls) == 13


def test_explicit_limit_still_caps_each_phase_when_given(tmp_path, monkeypatch, fake_playwright):
    cold_email_calls = []
    inbox_calls = []

    def _fake_cold_email(page, commit, index):
        cold_email_calls.append(index)
        return {"name": "T", "email": "t@x.com", "subject": "s", "body": "", "outcome": "drafted"}

    def _fake_inbox(page, commit, index, skipped, dwell_ms=0, auto_draft_reply=False):
        inbox_calls.append(index)
        return {"sender": "s", "subject": "m", "decision": "leave_alone", "rationale": "r", "outcome": "confirmed"}

    monkeypatch.setattr(run_boss_task_list, "REPO", tmp_path)
    monkeypatch.setattr(run_boss_task_list, "cold_email_process_one", _fake_cold_email)
    monkeypatch.setattr(run_boss_task_list, "inbox_process_one", _fake_inbox)
    monkeypatch.setattr(sys, "argv", ["run_boss_task_list.py", "--pace", "0", "--headless", "--limit", "3"])

    run_boss_task_list.main()

    assert len(cold_email_calls) == 3
    assert len(inbox_calls) == 3


def test_a_crash_in_cold_email_still_lets_the_inbox_phase_run(tmp_path, monkeypatch, fake_playwright):
    # The real point of Cold Email being first: a crash there must not
    # take down the inbox phase too -- "everything the boss needs done"
    # means the rest still happens.
    def _fake_cold_email(page, commit, index):
        raise _FakePlaywrightError("Target page, context or browser has been closed")

    inbox_calls = []

    def _fake_inbox(page, commit, index, skipped, dwell_ms=0, auto_draft_reply=False):
        inbox_calls.append(index)
        return None

    monkeypatch.setattr(run_boss_task_list, "REPO", tmp_path)
    monkeypatch.setattr(run_boss_task_list, "cold_email_process_one", _fake_cold_email)
    monkeypatch.setattr(run_boss_task_list, "inbox_process_one", _fake_inbox)
    monkeypatch.setattr(sys, "argv", ["run_boss_task_list.py", "--pace", "0", "--headless"])

    run_boss_task_list.main()  # must not raise

    assert len(inbox_calls) == 1  # the inbox phase still ran
