"""The boss' "Check ..." tasks (Scope #3): parse -> answer -> show -> walk.

Direct request 2026-10-05: "Add more tasks for the Scope #3, more cold emails
and general checking." Checking means three things, all chosen by the user:
did someone reply, is there mail about a topic, is the calendar free.

Unit: task_list_parser.parse_check_tasks and checker.run_check, against tiny
fixtures. Integration: local_server's /checks/api/list on a real mock
mailbox. E2E: a real headless browser on the real Inbox Dispatch page walks
the Checks view with automate_checks.process_one.

Run:  python -m pytest tests/test_inbox_checks.py -q
"""
import json
import os
import sys
import threading
from datetime import datetime
from http.server import HTTPServer

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from calendar_client import CalendarClientBase, MockCalendarClient  # noqa: E402
from checker import run_check, topic_words  # noqa: E402
from gmail_client import MockGmailClient  # noqa: E402
from task_list_parser import (  # noqa: E402
    CHECK_REPLY, CHECK_SCHEDULE, CHECK_TOPIC, DEFAULT_TASK_LIST_PATH, CheckTask,
    parse_check_tasks, parse_cold_email_targets,
)

NOW = datetime(2026, 10, 5, 9, 0)

TASK_LIST = """Cold email: Intro
Ann Lee <ann@a.example.com>
Bo Kim <bo@b.example.com>

Check replies:
Ann Lee <ann@a.example.com>
Bo Kim <bo@b.example.com>
not a target line

Check inbox: invoice
Check inbox: the
Check schedule: October 14th at 2pm
Check schedule: October 16th at 10am
Check schedule: next Friday
"""


def _msg(i, sender_email, subject, body, received="2026-10-01T09:00:00-07:00"):
    return {"id": f"m-{i}", "thread_id": f"t-{i}", "sender": f"X <{sender_email}>",
            "sender_email": sender_email, "subject": subject, "snippet": body[:40],
            "body_text": body, "received_at": received, "labels": ["INBOX"]}


@pytest.fixture
def data_dir(tmp_path):
    inbox = [
        _msg(1, "ann@a.example.com", "Re: Intro", "Sounds good."),
        _msg(2, "ann@a.example.com", "Re: Intro (2)", "One more thing.", "2026-10-02T09:00:00-07:00"),
        _msg(3, "billing@v.example.com", "October Invoice", "Attached."),
    ]
    (tmp_path / "mock_inbox.json").write_text(json.dumps({"inbox": inbox, "sent": []}), encoding="utf-8")
    (tmp_path / "mock_calendar_events.json").write_text(json.dumps({"events": [
        {"event_id": "e1", "summary": "Call with Grace", "description": "",
         "start": "2026-10-14T14:00", "end": "2026-10-14T14:30"}]}), encoding="utf-8")
    (tmp_path / "task_list.txt").write_text(TASK_LIST, encoding="utf-8")
    return tmp_path


def _answers(data_dir):
    gmail, cal = MockGmailClient(str(data_dir)), MockCalendarClient(str(data_dir))
    return [run_check(t, gmail, cal, now=NOW) for t in parse_check_tasks(str(data_dir / "task_list.txt"))]


# ------------------------------------------------------------------ parser


def test_parser_reads_every_kind_in_order_and_skips_junk(data_dir):
    tasks = parse_check_tasks(str(data_dir / "task_list.txt"))
    assert [(t.kind, t.query) for t in tasks] == [
        (CHECK_REPLY, "ann@a.example.com"), (CHECK_REPLY, "bo@b.example.com"),
        (CHECK_TOPIC, "invoice"), (CHECK_TOPIC, "the"),
        (CHECK_SCHEDULE, "October 14th at 2pm"), (CHECK_SCHEDULE, "October 16th at 10am"),
        (CHECK_SCHEDULE, "next Friday"),
    ]
    assert tasks[0].name == "Ann Lee"


def test_check_lines_never_become_cold_email_targets(data_dir):
    targets = parse_cold_email_targets(str(data_dir / "task_list.txt"))
    assert [t.email for t in targets] == ["ann@a.example.com", "bo@b.example.com"]


def test_real_task_list_has_three_cold_sections_and_all_check_kinds():
    targets = parse_cold_email_targets(DEFAULT_TASK_LIST_PATH)
    assert len(targets) >= 10
    assert len({t.context_line for t in targets}) == 3
    kinds = {t.kind for t in parse_check_tasks(DEFAULT_TASK_LIST_PATH)}
    assert kinds == {CHECK_REPLY, CHECK_TOPIC, CHECK_SCHEDULE}


# ----------------------------------------------------------------- checker


def test_every_answer_on_the_fixture(data_dir):
    got = [(r.answer, len(r.evidence)) for r in _answers(data_dir)]
    assert got == [
        ("yes", 2),        # Ann wrote twice
        ("no", 0),         # Bo never wrote
        ("yes", 1),        # "invoice" matches "October Invoice", any case
        ("unclear", 0),    # "the" is not a topic
        ("busy", 1),       # the 2pm call
        ("free", 0),
        ("unclear", 0),    # no date + clock time: never guessed
    ]


def test_reply_evidence_is_newest_first(data_dir):
    reply = _answers(data_dir)[0]
    assert reply.evidence[0]["subject"] == "Re: Intro (2)"


def test_schedule_overlap_catches_a_meeting_that_starts_inside_the_slot(data_dir):
    task = CheckTask(CHECK_SCHEDULE, "October 14th at 1:45pm")
    r = run_check(task, MockGmailClient(str(data_dir)), MockCalendarClient(str(data_dir)), now=NOW)
    assert r.answer == "busy"


def test_a_calendar_that_cannot_be_read_is_unclear_not_free(data_dir):
    class WriteOnly(CalendarClientBase):
        def create_event(self, *a):
            return ""

    task = CheckTask(CHECK_SCHEDULE, "October 16th at 10am")
    r = run_check(task, MockGmailClient(str(data_dir)), WriteOnly(), now=NOW)
    assert r.answer == "unclear"


def test_checks_never_write_anything(data_dir):
    before = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    _answers(data_dir)
    after = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert after == before


def test_topic_words_drop_filler():
    assert topic_words("Any mail about the contract renewal?") == ["contract", "renewal"]


# ------------------------------------------------------------- integration


def test_server_route_answers_the_task_list(data_dir):
    from local_server import ChecksService, handle_request

    svc = ChecksService(MockGmailClient(str(data_dir)), MockCalendarClient(str(data_dir)),
                        str(data_dir / "task_list.txt"))
    status, _, body, ctype = handle_request("GET", "/checks/api/list", b"", router=None,
                                            checks_service=svc)
    assert status == 200 and ctype == "application/json"
    checks = json.loads(body)["checks"]
    assert [c["index"] for c in checks] == list(range(7))
    assert checks[0]["label"] == "Did Ann Lee reply?"


def test_server_route_without_a_service_is_503():
    from local_server import handle_request
    status, *_ = handle_request("GET", "/checks/api/list", b"", router=None)
    assert status == 503


# ---------------------------------------------------------------------- E2E


class _QuietRouter:
    """The page loads the inbox on start; this E2E only exercises Checks."""

    def __getattr__(self, name):
        return lambda *a, **k: []


@pytest.fixture
def live_page(data_dir):
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    from local_server import ChecksService, make_handler

    svc = ChecksService(MockGmailClient(str(data_dir)), MockCalendarClient(str(data_dir)),
                        str(data_dir / "task_list.txt"))
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(_QuietRouter(), None, svc))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001
            httpd.shutdown()
            pytest.skip(f"no Chromium: {exc}")
        page = browser.new_page()
        page.goto(url)
        yield page
        browser.close()
    httpd.shutdown()


def test_agent_walks_every_check_on_the_real_page(live_page):
    from automate_checks import open_checks_view, process_one

    open_checks_view(live_page)
    results = []
    while (r := process_one(live_page, len(results))) is not None:
        results.append(r)

    assert [r["answer"] for r in results] == ["yes", "no", "yes", "unclear", "busy", "free", "unclear"]
    assert results[0]["label"] == "Did Ann Lee reply?"
    assert results[0]["evidence_count"] == 2
    assert results[4]["evidence_count"] == 1
    # Back on the list, nothing changed by walking it.
    assert live_page.locator("#checksRowList .row-item").count() == 7
