# Cold Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "Cold email" a real, working decision instead of a stub that logs "not implemented yet" — a new page lists people from a boss-written task list, and sending creates a real Gmail draft, exactly the way Reply/Forward already do.

**Architecture:** A line/regex parser (`task_list_parser.py`) reads a plain text file for `Name <email>` targets. `cold_email_sender.py` wraps the existing `create_draft()` call and tracks who's already been contacted. A new static page (`cold_email/`) mirrors Practice Inbox's list→detail shape. `local_server.py` gains three new routes and serves the new page. An Electron button opens it, mirroring the existing "Launch mockups"/"View Schedule" buttons exactly.

**Tech Stack:** Python stdlib (`http.server`, `re`, `json`) for the backend, plain HTML/CSS/JS for the page (no framework, matching every other page in this project), Electron IPC for the desktop button.

**Spec:** `docs/superpowers/specs/2026-08-30-schedule-calendar-and-cold-email-design.md` (Part 2, "Cold email", lines 174–274). Part 1 of that spec (Schedule → Calendar) is already shipped; this plan implements Part 2 only.

## Global Constraints

- Calendar events are unrelated to this plan — not touched.
- Cold email's subject line pre-fills from the task list's own real wording; the body is always blank until the human types it. Never a shared template reused across more than one recipient.
- `create_draft()` (existing, `gmail_client.py`) is the only draft-creation path used by Cold email — no new Gmail-writing code.
- Practice Inbox is untouched by this plan and stays data-only for every decision.
- `task_list_parser.py` does line/regex parsing only — never an LLM guess at who's a valid target. A line that doesn't match the expected shape is skipped, never guessed at.
- Blank subject or blank body is always a no-op — never drafts empty/invented content, same honesty guarantee `schedule_recorder.py` and `reply_recorder.py` already hold.
- Full test suite (`pytest -q` from repo root) must show 0 failed before any task in this plan is considered done.

**Ruling on an ambiguity the spec left open** (recorded here per this project's "every choice gets recorded" rule): the spec's `context_line` field is described as "the heading text above this target's section," but its own example shows one bare `Cold email:` heading (no trailing text) over three targets — which would make every target's pre-filled subject identical and empty. For a heading to produce a *useful* per-batch subject, this plan defines the heading's exact regex as `^Cold email:\s*(.*)$` — any text after the colon becomes `context_line` for every target until the next blank line or a different heading. The spec's own literal example (heading with nothing after the colon) still parses correctly under this rule; it just produces `context_line == ""` for that batch, which the UI treats as an empty (still editable) subject field, not an error.

---

### Task 1: Task list parser

**Files:**
- Create: `components/inbox_router/task_list_parser.py`
- Create: `components/inbox_router/data/task_list.txt`
- Test: `tests/test_task_list_parser.py`

**Interfaces:**
- Produces: `ColdEmailTarget` dataclass (`name: str`, `email: str`, `context_line: str`) and `parse_cold_email_targets(path: str = DEFAULT_TASK_LIST_PATH) -> List[ColdEmailTarget]`, both importable from `task_list_parser`. `DEFAULT_TASK_LIST_PATH` is also exported. Task 2 imports all three names.

- [ ] **Step 1: Write the committed fixture file**

Create `components/inbox_router/data/task_list.txt` with exactly this content (synthetic names/emails, matching this project's existing synthetic-fixture convention, e.g. `mock_inbox.json`):

```
Cold email:
Dana Whitfield <dana.whitfield@northline.example.com>
Marcus Oyelaran <m.oyelaran@delridge.example.com>
Priya Ramaswami <priya@ramaswami-consulting.example.com>
```

This file is committed (real, synthetic, checked-in data) — do NOT add it to `.gitignore`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_task_list_parser.py`:

```python
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from task_list_parser import ColdEmailTarget, DEFAULT_TASK_LIST_PATH, parse_cold_email_targets


def _write(tmp_path, content):
    path = tmp_path / "task_list.txt"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_parses_the_committed_example_task_list():
    targets = parse_cold_email_targets(DEFAULT_TASK_LIST_PATH)
    assert targets == [
        ColdEmailTarget(name="Dana Whitfield", email="dana.whitfield@northline.example.com", context_line=""),
        ColdEmailTarget(name="Marcus Oyelaran", email="m.oyelaran@delridge.example.com", context_line=""),
        ColdEmailTarget(name="Priya Ramaswami", email="priya@ramaswami-consulting.example.com", context_line=""),
    ]


def test_heading_with_context_text_becomes_the_pre_filled_subject(tmp_path):
    path = _write(tmp_path, "Cold email: Q3 partnership outreach\nDana Whitfield <dana@x.example.com>\n")
    targets = parse_cold_email_targets(path)
    assert targets == [ColdEmailTarget(name="Dana Whitfield", email="dana@x.example.com",
                                        context_line="Q3 partnership outreach")]


def test_malformed_target_line_is_skipped_not_guessed_at(tmp_path):
    path = _write(tmp_path, "Cold email:\nnot a valid target line\nDana Whitfield <dana@x.example.com>\n")
    targets = parse_cold_email_targets(path)
    assert targets == [ColdEmailTarget(name="Dana Whitfield", email="dana@x.example.com", context_line="")]


def test_blank_line_ends_the_section(tmp_path):
    path = _write(tmp_path, "Cold email:\nDana Whitfield <dana@x.example.com>\n\nMarcus Oyelaran <m@x.example.com>\n")
    targets = parse_cold_email_targets(path)
    assert targets == [ColdEmailTarget(name="Dana Whitfield", email="dana@x.example.com", context_line="")]


def test_a_different_heading_ends_the_section(tmp_path):
    path = _write(tmp_path, "Cold email:\nDana Whitfield <dana@x.example.com>\nOther section:\nMarcus Oyelaran <m@x.example.com>\n")
    targets = parse_cold_email_targets(path)
    assert targets == [ColdEmailTarget(name="Dana Whitfield", email="dana@x.example.com", context_line="")]


def test_two_separate_headings_each_get_their_own_context(tmp_path):
    path = _write(tmp_path,
        "Cold email: Conference follow-up\nDana Whitfield <dana@x.example.com>\n"
        "\n"
        "Cold email: Referral thank-you\nMarcus Oyelaran <m@x.example.com>\n")
    targets = parse_cold_email_targets(path)
    assert targets == [
        ColdEmailTarget(name="Dana Whitfield", email="dana@x.example.com", context_line="Conference follow-up"),
        ColdEmailTarget(name="Marcus Oyelaran", email="m@x.example.com", context_line="Referral thank-you"),
    ]


def test_missing_file_returns_empty_list(tmp_path):
    assert parse_cold_email_targets(str(tmp_path / "does_not_exist.txt")) == []


def test_no_heading_at_all_returns_no_targets(tmp_path):
    path = _write(tmp_path, "Dana Whitfield <dana@x.example.com>\n")
    assert parse_cold_email_targets(path) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_task_list_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'task_list_parser'`

- [ ] **Step 3: Write the implementation**

Create `components/inbox_router/task_list_parser.py`:

```python
"""
components/inbox_router/task_list_parser.py
=================================================
Reads components/inbox_router/data/task_list.txt -- a boss-style, plain
text file listing who Cold email should reach out to. Line/regex parsing
only, matching this project's "never guess, never invent" rule: a line
that doesn't match the expected shape is skipped, never interpreted by
an LLM.

File format:
    Cold email: <optional free text, becomes the pre-filled subject>
    Name <email@example.com>
    Name <email@example.com>

    Cold email: <a different context line>
    Name <email@example.com>

A "Cold email:" heading starts a new section; every following
"Name <email>" line until a blank line or a different heading belongs to
that heading's context_line. A line inside a section that isn't a valid
"Name <email>" line is skipped, not guessed at -- it does not end the
section.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TASK_LIST_PATH = os.path.join(_THIS_DIR, "data", "task_list.txt")

_HEADING_RE = re.compile(r"^Cold email:\s*(.*)$")
_ANY_HEADING_RE = re.compile(r".*:\s*$")
_TARGET_RE = re.compile(r"^(.+?)\s*<([^<>@\s]+@[^<>\s]+)>\s*$")


@dataclass
class ColdEmailTarget:
    name: str
    email: str
    context_line: str


def parse_cold_email_targets(path: str = DEFAULT_TASK_LIST_PATH) -> List[ColdEmailTarget]:
    if not os.path.isfile(path):
        return []
    targets: List[ColdEmailTarget] = []
    in_section = False
    context_line = ""
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            stripped = raw_line.strip()
            heading_match = _HEADING_RE.match(stripped)
            if heading_match:
                in_section = True
                context_line = heading_match.group(1).strip()
                continue
            if not stripped:
                in_section = False
                continue
            if _ANY_HEADING_RE.match(stripped):
                in_section = False
                continue
            if not in_section:
                continue
            target_match = _TARGET_RE.match(stripped)
            if not target_match:
                continue
            name, email = target_match.group(1).strip(), target_match.group(2).strip()
            targets.append(ColdEmailTarget(name=name, email=email, context_line=context_line))
    return targets
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_task_list_parser.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add components/inbox_router/task_list_parser.py components/inbox_router/data/task_list.txt tests/test_task_list_parser.py
git commit -m "Add task_list_parser.py: read Cold Email targets from a plain text task list"
```

---

### Task 2: Cold email sender

**Files:**
- Create: `components/inbox_router/cold_email_sender.py`
- Test: `tests/test_cold_email_sender.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `ColdEmailTarget`, `DEFAULT_TASK_LIST_PATH`, `parse_cold_email_targets` from `task_list_parser` (Task 1). `GmailClientBase.create_draft(to: str, subject: str, body: str, thread_id: str = "") -> str` from `gmail_client.py` (existing).
- Produces: `ColdEmailSender` class with `__init__(self, gmail_client, task_list_path=DEFAULT_TASK_LIST_PATH, state_path=DEFAULT_COLD_EMAIL_STATE_PATH)`, `list_pending_targets(self) -> List[ColdEmailTarget]`, `send_cold_email(self, email: str, subject: str, body: str) -> str` (returns the draft id, or `""` on a no-op blank subject/body). `DEFAULT_COLD_EMAIL_STATE_PATH` is also exported. Task 3 imports `ColdEmailSender` and constructs it in `local_server.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cold_email_sender.py`:

```python
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cold_email_sender import ColdEmailSender


class _FakeGmailClient:
    def __init__(self):
        self.drafts = []

    def create_draft(self, to, subject, body, thread_id=""):
        draft_id = f"fake-draft-{len(self.drafts) + 1}"
        self.drafts.append({"to": to, "subject": subject, "body": body, "thread_id": thread_id})
        return draft_id


def _write_task_list(tmp_path, content):
    path = tmp_path / "task_list.txt"
    path.write_text(content, encoding="utf-8")
    return str(path)


def _sender(tmp_path, task_list_content, gmail=None):
    task_list_path = _write_task_list(tmp_path, task_list_content)
    state_path = str(tmp_path / "cold_email_state.json")
    return ColdEmailSender(gmail or _FakeGmailClient(), task_list_path=task_list_path, state_path=state_path), state_path


_ONE_TARGET = "Cold email:\nDana Whitfield <dana@x.example.com>\n"
_TWO_TARGETS = "Cold email:\nDana Whitfield <dana@x.example.com>\nMarcus Oyelaran <marcus@x.example.com>\n"


def test_list_pending_targets_returns_everyone_not_yet_contacted(tmp_path):
    sender, _ = _sender(tmp_path, _TWO_TARGETS)
    pending = sender.list_pending_targets()
    assert [t.email for t in pending] == ["dana@x.example.com", "marcus@x.example.com"]


def test_list_pending_targets_excludes_already_contacted_emails(tmp_path):
    sender, state_path = _sender(tmp_path, _TWO_TARGETS)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"contacted_emails": ["dana@x.example.com"]}, f)
    pending = sender.list_pending_targets()
    assert [t.email for t in pending] == ["marcus@x.example.com"]


def test_send_cold_email_creates_a_real_draft_and_marks_contacted(tmp_path):
    gmail = _FakeGmailClient()
    sender, state_path = _sender(tmp_path, _ONE_TARGET, gmail=gmail)
    draft_id = sender.send_cold_email("dana@x.example.com", "Hi Dana", "Reaching out about a partnership.")
    assert draft_id == "fake-draft-1"
    assert gmail.drafts == [{"to": "dana@x.example.com", "subject": "Hi Dana",
                              "body": "Reaching out about a partnership.", "thread_id": ""}]
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    assert state["contacted_emails"] == ["dana@x.example.com"]
    assert sender.list_pending_targets() == []


def test_send_cold_email_with_blank_subject_is_a_no_op(tmp_path):
    gmail = _FakeGmailClient()
    sender, state_path = _sender(tmp_path, _ONE_TARGET, gmail=gmail)
    draft_id = sender.send_cold_email("dana@x.example.com", "   ", "A real message.")
    assert draft_id == ""
    assert gmail.drafts == []
    assert not os.path.exists(state_path)


def test_send_cold_email_with_blank_body_is_a_no_op(tmp_path):
    gmail = _FakeGmailClient()
    sender, state_path = _sender(tmp_path, _ONE_TARGET, gmail=gmail)
    draft_id = sender.send_cold_email("dana@x.example.com", "A real subject", "   ")
    assert draft_id == ""
    assert gmail.drafts == []
    assert not os.path.exists(state_path)


def test_contacted_email_matching_is_case_insensitive(tmp_path):
    sender, state_path = _sender(tmp_path, _ONE_TARGET)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"contacted_emails": ["DANA@x.example.com"]}, f)
    assert sender.list_pending_targets() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cold_email_sender.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cold_email_sender'`

- [ ] **Step 3: Write the implementation**

Create `components/inbox_router/cold_email_sender.py`:

```python
"""
components/inbox_router/cold_email_sender.py
=================================================
Output step for the "cold email" decision -- Reply/Forward's create_draft()
call, reused as-is, for a brand-new contact sourced from a task list
document instead of an existing inbox thread. Deliberately does NOT call
decision_recorder.record_example(): unlike Reply/Forward/Schedule/Flag,
there's no inbox-triage decision being learned here -- the task list
already decided WHO, there's no ambiguous message being classified.
"""
from __future__ import annotations

import json
import os
from typing import List

from task_list_parser import ColdEmailTarget, DEFAULT_TASK_LIST_PATH, parse_cold_email_targets

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COLD_EMAIL_STATE_PATH = os.path.join(_THIS_DIR, "data", "cold_email_state.json")


class ColdEmailSender:
    def __init__(self, gmail_client,
                 task_list_path: str = DEFAULT_TASK_LIST_PATH,
                 state_path: str = DEFAULT_COLD_EMAIL_STATE_PATH) -> None:
        self._gmail = gmail_client
        self._task_list_path = task_list_path
        self._state_path = state_path

    def list_pending_targets(self) -> List[ColdEmailTarget]:
        targets = parse_cold_email_targets(self._task_list_path)
        contacted = set(self._load_state().get("contacted_emails", []))
        return [t for t in targets if t.email.strip().lower() not in contacted]

    def send_cold_email(self, email: str, subject: str, body: str) -> str:
        subject = (subject or "").strip()
        body = (body or "").strip()
        if not subject or not body:
            return ""  # never draft empty/invented content
        draft_id = self._gmail.create_draft(to=email, subject=subject, body=body, thread_id="")
        state = self._load_state()
        contacted = set(state.get("contacted_emails", []))
        contacted.add(email.strip().lower())
        state["contacted_emails"] = sorted(contacted)
        self._save_state(state)
        return draft_id

    def _load_state(self) -> dict:
        if not os.path.exists(self._state_path):
            return {}
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_state(self, state: dict) -> None:
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cold_email_sender.py -v`
Expected: 6 passed

- [ ] **Step 5: Add the generated state file to `.gitignore`**

In `.gitignore`, in the "Inbox Router (Scope #3)" section, right after the existing `components/inbox_router/data/schedule.txt` line, add:

```
components/inbox_router/data/cold_email_state.json
```

- [ ] **Step 6: Commit**

```bash
git add components/inbox_router/cold_email_sender.py tests/test_cold_email_sender.py .gitignore
git commit -m "Add cold_email_sender.py: real Gmail drafts for Cold Email targets"
```

---

### Task 3: Cold Email page + local_server.py wiring

**Files:**
- Create: `components/inbox_router/cold_email/index.html`
- Create: `components/inbox_router/cold_email/style.css`
- Create: `components/inbox_router/cold_email/app.js`
- Modify: `components/inbox_router/local_server.py`
- Test: `tests/test_local_server.py`

**Interfaces:**
- Consumes: `ColdEmailSender` from `cold_email_sender.py` (Task 2). `get_gmail_client()` from `gmail_client.py` (existing).
- Produces: `local_server.py` gains `build_cold_email_sender(gmail_client=None) -> ColdEmailSender`; `build_router(gmail_client=None) -> InboxRouter` (extended with an optional param, backward compatible with every existing no-arg call site); `handle_request(method, path, body, router, origin=None, cold_email_sender=None)` (new trailing keyword param, backward compatible with every existing positional call site); `make_handler(router, cold_email_sender=None)` (same). Routes: `GET /cold-email/`, `GET /cold-email/style.css`, `GET /cold-email/app.js`, `GET /cold-email/api/targets`, `POST /cold-email/api/send`.

- [ ] **Step 1: Write the failing HTTP-level tests**

In `tests/test_local_server.py`, add near the top (after the existing `from router import InboxRouter` / `import local_server as ls` imports, alongside `_msg`/`FakeCalendarClient`):

```python
from cold_email_sender import ColdEmailSender


class _FakeGmailClientForColdEmail:
    def __init__(self):
        self.drafts = []

    def create_draft(self, to, subject, body, thread_id=""):
        draft_id = f"fake-cold-draft-{len(self.drafts) + 1}"
        self.drafts.append({"to": to, "subject": subject, "body": body, "thread_id": thread_id})
        return draft_id


def _write_task_list(tmp_path, content):
    path = tmp_path / "task_list.txt"
    path.write_text(content, encoding="utf-8")
    return str(path)


def _build_cold_email_sender(tmp_path, content="Cold email: Q3 outreach\nDana Whitfield <dana@x.example.com>\n",
                              gmail=None):
    task_list_path = _write_task_list(tmp_path, content)
    state_path = str(tmp_path / "cold_email_state.json")
    return ColdEmailSender(gmail or _FakeGmailClientForColdEmail(), task_list_path=task_list_path,
                            state_path=state_path)
```

Then add a new test class, anywhere after `TestHandleRequestOverride` and before `TestBuildRouter`:

```python
class TestColdEmailRoutes:
    def test_get_targets_lists_everyone_not_contacted(self, tmp_path):
        router = _build_router(tmp_path)
        sender = _build_cold_email_sender(tmp_path)
        status, _headers, resp_body, _ct = ls.handle_request(
            "GET", "/cold-email/api/targets", b"", router, cold_email_sender=sender)
        assert status == 200
        assert json.loads(resp_body) == {"targets": [
            {"name": "Dana Whitfield", "email": "dana@x.example.com", "context_line": "Q3 outreach"},
        ]}

    def test_post_send_creates_draft_and_removes_target_from_pending_list(self, tmp_path):
        router = _build_router(tmp_path)
        gmail = _FakeGmailClientForColdEmail()
        sender = _build_cold_email_sender(tmp_path, gmail=gmail)
        body = json.dumps({"email": "dana@x.example.com", "subject": "Hi Dana",
                            "body": "Reaching out about a partnership."}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request(
            "POST", "/cold-email/api/send", body, router, cold_email_sender=sender)
        assert status == 200
        assert json.loads(resp_body) == {"ok": True}
        assert gmail.drafts == [{"to": "dana@x.example.com", "subject": "Hi Dana",
                                  "body": "Reaching out about a partnership.", "thread_id": ""}]

        status2, _headers2, resp_body2, _ct2 = ls.handle_request(
            "GET", "/cold-email/api/targets", b"", router, cold_email_sender=sender)
        assert json.loads(resp_body2) == {"targets": []}

    def test_post_send_with_blank_body_returns_400_and_creates_no_draft(self, tmp_path):
        router = _build_router(tmp_path)
        gmail = _FakeGmailClientForColdEmail()
        sender = _build_cold_email_sender(tmp_path, gmail=gmail)
        body = json.dumps({"email": "dana@x.example.com", "subject": "Hi Dana", "body": "   "}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request(
            "POST", "/cold-email/api/send", body, router, cold_email_sender=sender)
        assert status == 400
        assert gmail.drafts == []

    def test_cold_email_index_html_is_served(self, tmp_path):
        router = _build_router(tmp_path)
        status, _headers, body, content_type = ls.handle_request("GET", "/cold-email/", b"", router)
        assert status == 200
        assert content_type == "text/html"
        assert b"<html" in body.lower()

    def test_cold_email_style_css_is_served(self, tmp_path):
        router = _build_router(tmp_path)
        status, _headers, _body, content_type = ls.handle_request("GET", "/cold-email/style.css", b"", router)
        assert status == 200
        assert content_type == "text/css"

    def test_cold_email_app_js_is_served(self, tmp_path):
        router = _build_router(tmp_path)
        status, _headers, body, content_type = ls.handle_request("GET", "/cold-email/app.js", b"", router)
        assert status == 200
        assert content_type == "application/javascript"
        assert b"/cold-email/api" in body


class TestColdEmailPageRealBrowser:
    """Real-browser regression, matching TestScheduleSingleWhenField's own
    pattern: proves the actual served page really talks to the actual
    routes above, not just that the routes work in isolation."""

    def test_sending_a_cold_email_creates_a_real_draft_through_the_real_page(self, tmp_path):
        pytest.importorskip("playwright.sync_api")
        from playwright.sync_api import sync_playwright

        router = _build_router(tmp_path)
        gmail = _FakeGmailClientForColdEmail()
        sender = _build_cold_email_sender(tmp_path, gmail=gmail)
        handler_cls = ls.make_handler(router, cold_email_sender=sender)
        httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/cold-email/")
                page.wait_for_selector("#rowList .row-item")
                page.locator(".row-item").first.click()
                page.wait_for_selector("#detailView:not([hidden])")
                assert page.input_value("#subjectInput") == "Q3 outreach"
                page.fill("#bodyInput", "Reaching out about a partnership.")
                page.click("#sendBtn")
                page.wait_for_timeout(300)
                assert page.locator("#rowList .row-item").count() == 0
                browser.close()
        finally:
            httpd.shutdown()
            thread.join(timeout=5)

        assert gmail.drafts == [{"to": "dana@x.example.com", "subject": "Q3 outreach",
                                  "body": "Reaching out about a partnership.", "thread_id": ""}]

    def test_sending_with_no_message_typed_is_refused_client_side(self, tmp_path):
        pytest.importorskip("playwright.sync_api")
        from playwright.sync_api import sync_playwright

        router = _build_router(tmp_path)
        gmail = _FakeGmailClientForColdEmail()
        sender = _build_cold_email_sender(tmp_path, gmail=gmail)
        handler_cls = ls.make_handler(router, cold_email_sender=sender)
        httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/cold-email/")
                page.wait_for_selector("#rowList .row-item")
                page.locator(".row-item").first.click()
                page.wait_for_selector("#detailView:not([hidden])")
                page.click("#sendBtn")
                page.wait_for_timeout(200)
                assert "type a message" in page.eval_on_selector("#detailStatus", "el => el.textContent").lower()
                browser.close()
        finally:
            httpd.shutdown()
            thread.join(timeout=5)

        assert gmail.drafts == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_local_server.py -k ColdEmail -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'cold_email_sender'` at collection, or `TypeError: handle_request() got an unexpected keyword argument 'cold_email_sender'` once Task 2's import resolves — either way, red before the page/wiring exist).

- [ ] **Step 3: Create the page — `components/inbox_router/cold_email/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Cold Email</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/cold-email/style.css">
</head>
<body>
  <div class="gmail-shell">
    <header class="topbar">
      <div class="logo">
        <span class="logo-mark">&#128233;</span>
        <span class="logo-text">Cold Email</span>
      </div>
    </header>

    <div class="body-row">
      <nav class="sidebar">
        <button id="refreshBtn" class="compose-btn">
          <span class="compose-icon">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>
          </span>
          Refresh
        </button>
        <div class="nav-item nav-item-active">
          <span class="nav-label">Task list</span>
          <span class="nav-count" id="targetCount"></span>
        </div>
      </nav>

      <main class="main-pane">
        <div id="listView">
          <p id="emptyState" class="empty-state" hidden>Nobody left on the task list. Add names to data/task_list.txt.</p>
          <ul id="rowList" class="row-list"></ul>
        </div>

        <div id="detailView" class="detail-view" hidden>
          <div class="detail-toolbar">
            <button id="backBtn" class="toolbar-icon-btn" title="Back to list">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
            </button>
          </div>
          <h1 class="detail-subject" id="detailTargetName"></h1>
          <p class="detail-target-email" id="detailTargetEmail"></p>

          <div class="compose-form">
            <label class="compose-label">Subject
              <input type="text" id="subjectInput" class="compose-input">
            </label>
            <textarea id="bodyInput" class="reply-textarea" placeholder="Type your message -- this exact text is what gets sent, nothing is written for you."></textarea>
            <button id="sendBtn" class="btn btn-primary send-btn">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
              Send
            </button>
          </div>
          <p id="detailStatus" class="status-line"></p>
        </div>
      </main>
    </div>
  </div>

  <div id="snackbar" class="snackbar" hidden></div>

  <script src="/cold-email/app.js"></script>
</body>
</html>
```

- [ ] **Step 4: Create `components/inbox_router/cold_email/style.css`**

```css
/* components/inbox_router/cold_email/style.css */
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  font-family: "Roboto", Arial, sans-serif;
  background: #ffffff;
  color: #202124;
}

.gmail-shell { display: flex; flex-direction: column; height: 100vh; }

.topbar {
  display: flex; align-items: center; gap: 16px;
  height: 64px; padding: 0 16px; flex-shrink: 0;
  border-bottom: 1px solid #e0e0e0;
}
.logo { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.logo-mark { font-size: 22px; }
.logo-text { font-size: 20px; color: #5f6368; letter-spacing: 0.1px; }

.body-row { display: flex; flex: 1; min-height: 0; }

.sidebar { width: 232px; flex-shrink: 0; padding: 16px 8px; overflow-y: auto; }
.compose-btn {
  display: flex; align-items: center; gap: 12px;
  background: #fff; color: #444; border: none;
  border-radius: 16px; padding: 14px 20px;
  font-size: 14px; font-weight: 500; font-family: inherit;
  box-shadow: 0 1px 3px 1px rgba(60,64,67,.15), 0 1px 2px rgba(60,64,67,.3);
  cursor: pointer; margin: 4px 8px 16px; width: 100%;
}
.compose-btn:hover { box-shadow: 0 1px 3px 1px rgba(60,64,67,.25), 0 2px 6px 2px rgba(60,64,67,.15); }
.compose-icon { color: #1a73e8; display: flex; align-items: center; }
.nav-item {
  display: flex; align-items: center; gap: 16px; width: 100%;
  height: 32px; padding: 0 20px 0 24px; margin-right: 16px;
  border-radius: 0 16px 16px 0; font-size: 14px; color: #202124;
}
.nav-item-active { background: #fce8e6; color: #d93025; font-weight: 700; }
.nav-label { flex: 1; }
.nav-count { font-size: 12px; }

.main-pane { flex: 1; min-width: 0; overflow-y: auto; }

.row-list { list-style: none; margin: 0; padding: 0; }
.row-item {
  display: flex; gap: 12px; align-items: center;
  padding: 0 16px; height: 44px; cursor: pointer;
  border-bottom: 1px solid #f1f1f1; position: relative;
}
.row-item:hover {
  box-shadow: 0 1px 2px 0 rgba(60,64,67,.30), 0 1px 3px 1px rgba(60,64,67,.15);
  border-bottom-color: transparent; border-radius: 8px; z-index: 1; background: #fff;
}
.row-sender { width: 200px; flex-shrink: 0; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row-snippet { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row-subject { font-weight: 700; }
.row-preview { color: #5f6368; font-weight: 400; }
.empty-state { color: #70757a; padding: 24px; }

.detail-toolbar {
  display: flex; align-items: center; gap: 4px;
  padding: 8px 16px; border-bottom: 1px solid #f1f1f1; margin-bottom: 16px;
}
.toolbar-icon-btn {
  width: 36px; height: 36px; border-radius: 50%; border: none;
  background: transparent; color: #5f6368; cursor: pointer;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}
.toolbar-icon-btn:hover { background: #f1f3f4; }
.detail-view { padding: 0 24px 24px; max-width: 760px; }
.detail-subject { font-size: 22px; font-weight: 400; margin: 8px 0 4px; }
.detail-target-email { color: #5f6368; font-size: 13px; margin: 0 0 20px; }

.compose-form { display: flex; flex-direction: column; gap: 12px; }
.compose-label { font-size: 13px; color: #5f6368; display: flex; flex-direction: column; gap: 4px; }
.compose-input {
  font-family: inherit; font-size: 14px; color: #202124;
  border: 1px solid #dadce0; border-radius: 4px; padding: 8px 10px;
}
.compose-input:focus { outline: none; border-color: #1a73e8; }
.reply-textarea {
  width: 100%; min-height: 160px; resize: vertical;
  font-family: inherit; font-size: 14px; color: #202124;
  border: 1px solid #dadce0; border-radius: 8px; padding: 12px;
  box-sizing: border-box;
}
.reply-textarea:focus { outline: none; border-color: #1a73e8; }
.btn {
  border: none; border-radius: 18px; padding: 8px 20px;
  font-size: 14px; cursor: pointer; font-family: inherit;
  display: inline-flex; align-items: center; gap: 8px; width: fit-content;
}
.btn-primary { background: #d93025; color: #fff; }
.btn-primary:hover { background: #b3261e; }
.status-line { color: #188038; font-size: 13px; min-height: 18px; padding: 4px 0 0; }

.snackbar {
  position: fixed; left: 24px; bottom: 24px; z-index: 100;
  background: #323232; color: #fff; font-size: 14px;
  padding: 14px 24px; border-radius: 4px;
  box-shadow: 0 3px 5px -1px rgba(0,0,0,.2), 0 6px 10px 0 rgba(0,0,0,.14);
  animation: snackbar-in 0.15s ease-out;
}
.snackbar[hidden] { display: none; }
@keyframes snackbar-in {
  from { transform: translateY(16px); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}
```

- [ ] **Step 5: Create `components/inbox_router/cold_email/app.js`**

```js
// components/inbox_router/cold_email/app.js
let targets = [];
let openEmail = null;
let snackbarTimer = null;

const rowList = document.getElementById("rowList");
const emptyState = document.getElementById("emptyState");
const listView = document.getElementById("listView");
const detailView = document.getElementById("detailView");
const detailStatus = document.getElementById("detailStatus");
const targetCount = document.getElementById("targetCount");
const snackbar = document.getElementById("snackbar");
const subjectInput = document.getElementById("subjectInput");
const bodyInput = document.getElementById("bodyInput");

function showSnackbar(message) {
  clearTimeout(snackbarTimer);
  snackbar.textContent = message;
  snackbar.hidden = false;
  snackbarTimer = setTimeout(() => { snackbar.hidden = true; }, 4000);
}

async function loadTargets() {
  detailStatus.textContent = "";
  try {
    const resp = await fetch("/cold-email/api/targets");
    if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
    const data = await resp.json();
    targets = data.targets || [];
    renderList();
  } catch (e) {
    targets = [];
    rowList.innerHTML = "";
    emptyState.hidden = false;
    emptyState.textContent = "Can't reach the local server -- is it running? Try refreshing in a few seconds.";
  }
}

function renderList() {
  rowList.innerHTML = "";
  targetCount.textContent = targets.length > 0 ? String(targets.length) : "";
  emptyState.hidden = targets.length > 0;
  if (targets.length === 0) {
    emptyState.textContent = "Nobody left on the task list. Add names to data/task_list.txt.";
  }
  targets.forEach((target) => {
    const li = document.createElement("li");
    li.className = "row-item";
    li.innerHTML = `
      <span class="row-sender">${escapeHtml(target.name)}</span>
      <span class="row-snippet">
        <span class="row-subject">${escapeHtml(target.email)}</span>
        <span class="row-preview"> - ${escapeHtml(target.context_line || "")}</span>
      </span>
    `;
    li.addEventListener("click", () => openTarget(target.email));
    rowList.appendChild(li);
  });
}

function openTarget(email) {
  const target = targets.find((t) => t.email === email);
  if (!target) return;
  openEmail = email;
  document.getElementById("detailTargetName").textContent = target.name;
  document.getElementById("detailTargetEmail").textContent = target.email;
  subjectInput.value = target.context_line || "";
  bodyInput.value = "";
  detailStatus.textContent = "";
  listView.hidden = true;
  detailView.hidden = false;
}

function closeTarget() {
  openEmail = null;
  listView.hidden = false;
  detailView.hidden = true;
}

async function sendPending() {
  if (!openEmail) return;
  if (!subjectInput.value.trim()) {
    detailStatus.textContent = "Error: type a subject.";
    subjectInput.focus();
    return;
  }
  if (!bodyInput.value.trim()) {
    detailStatus.textContent = "Error: type a message.";
    bodyInput.focus();
    return;
  }
  const resp = await fetch("/cold-email/api/send", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: openEmail, subject: subjectInput.value, body: bodyInput.value }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    detailStatus.textContent = `Error: ${err.error || "request failed"}`;
    return;
  }
  showSnackbar("Sent.");
  await loadTargets();
  closeTarget();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

document.getElementById("refreshBtn").addEventListener("click", loadTargets);
document.getElementById("backBtn").addEventListener("click", closeTarget);
document.getElementById("sendBtn").addEventListener("click", sendPending);

loadTargets();
```

- [ ] **Step 6: Wire `local_server.py`**

Modify `components/inbox_router/local_server.py`:

Replace lines 46–56 (the `_UI_DIR`/`_PRACTICE_UI_DIR`/`_STATIC_FILES` block):

```python
_UI_DIR = os.path.join(_THIS_DIR, "local_ui")
_PRACTICE_UI_DIR = os.path.join(_THIS_DIR, "practice_inbox")
_COLD_EMAIL_UI_DIR = os.path.join(_THIS_DIR, "cold_email")

_STATIC_FILES = {
    "/": (_UI_DIR, "index.html", "text/html"),
    "/style.css": (_UI_DIR, "style.css", "text/css"),
    "/app.js": (_UI_DIR, "app.js", "application/javascript"),
    "/practice/": (_PRACTICE_UI_DIR, "index.html", "text/html"),
    "/practice/style.css": (_PRACTICE_UI_DIR, "style.css", "text/css"),
    "/practice/app.js": (_PRACTICE_UI_DIR, "app.js", "application/javascript"),
    "/cold-email/": (_COLD_EMAIL_UI_DIR, "index.html", "text/html"),
    "/cold-email/style.css": (_COLD_EMAIL_UI_DIR, "style.css", "text/css"),
    "/cold-email/app.js": (_COLD_EMAIL_UI_DIR, "app.js", "application/javascript"),
}
```

Replace lines 81–98 (`def build_router() -> InboxRouter:` through the end of that function) with:

```python
def build_router(gmail_client=None) -> InboxRouter:
    # Imported here, not at module level: router.py pulls in inbox_agent.py,
    # which does `import torch` at its own module level. That import alone
    # takes several seconds, so keeping it out of local_server.py's module
    # scope is what actually makes serve()'s "bind first" trick work --
    # otherwise the whole torch import chain runs before serve() is even
    # entered, and the socket never binds any earlier than it does today.
    from llm_classifier import LLMClassifier
    from pattern_profile import PatternProfile
    from routing_rules import RuleLayer
    from router import InboxRouter

    if gmail_client is None:
        gmail_client = get_gmail_client()
    profile = PatternProfile()
    rule_layer = RuleLayer(profile)
    provider, api_key = _pick_provider()
    classifier = LLMClassifier(provider=provider, api_key=api_key)
    return InboxRouter(gmail_client, profile, rule_layer, classifier)


def build_cold_email_sender(gmail_client=None):
    from cold_email_sender import ColdEmailSender

    if gmail_client is None:
        gmail_client = get_gmail_client()
    return ColdEmailSender(gmail_client)
```

Replace lines 116–119 (the `def handle_request(...)` signature and its docstring) with:

```python
def handle_request(method: str, path: str, body: bytes, router: InboxRouter, origin: str = None,
                    cold_email_sender=None) -> Tuple[int, dict, bytes, str]:
    """Pure request handler, separated from BaseHTTPRequestHandler so it's
    testable without opening a real socket. Returns
    (status_code, extra_headers, response_body_bytes, content_type)."""
```

Insert, right before the final `return 404, {}, json.dumps({"error": "Not found"}).encode("utf-8"), "application/json"` line (currently line 207):

```python
    if method == "GET" and path == "/cold-email/api/targets":
        targets = cold_email_sender.list_pending_targets()
        payload = json.dumps({"targets": [
            {"name": t.name, "email": t.email, "context_line": t.context_line}
            for t in targets
        ]}).encode("utf-8")
        return 200, {}, payload, "application/json"

    if method == "POST" and path == "/cold-email/api/send":
        data, error = _parse_action_body(body, ("email", "subject", "body"))
        if error:
            return error
        draft_id = cold_email_sender.send_cold_email(data["email"], data["subject"], data["body"])
        if not draft_id:
            err = json.dumps({"error": "Type a subject and a message before sending."}).encode("utf-8")
            return 400, {}, err, "application/json"
        return 200, {}, json.dumps({"ok": True}).encode("utf-8"), "application/json"

```

Replace lines 210–232 (`def make_handler(router: InboxRouter):` through `return Handler`) with:

```python
def make_handler(router: InboxRouter, cold_email_sender=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # keep stdout quiet -- this is a background helper process

        def do_GET(self):
            self._respond("GET", self.path, b"")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            self._respond("POST", self.path, body, self.headers.get("Origin"))

        def _respond(self, method, path, body, origin=None):
            status, headers, payload, content_type = handle_request(
                method, path, body, router, origin=origin, cold_email_sender=cold_email_sender)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(payload)

    return Handler
```

Replace lines 235–250 (`def serve(port: int = DEFAULT_PORT) -> None:` through `httpd.serve_forever()`) with:

```python
def serve(port: int = DEFAULT_PORT) -> None:
    # Bind and start listening FIRST, before the slow torch/sentence-encoder
    # import chain build_router() triggers -- a browser connecting during
    # that window gets its TCP connection accepted into the OS backlog
    # (instead of "connection refused"), even though nothing is ready to
    # actually answer yet. The real handler class is installed once ready,
    # before serve_forever() starts processing any request.
    try:
        httpd = HTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    except OSError as exc:
        print(f"Could not start local server on port {port} (already running?): {exc}")
        return
    print(f"Inbox Dispatch local server listening on http://localhost:{port}/")
    gmail_client = get_gmail_client()
    router = build_router(gmail_client)
    cold_email_sender = build_cold_email_sender(gmail_client)
    httpd.RequestHandlerClass = make_handler(router, cold_email_sender)
    httpd.serve_forever()
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_local_server.py -k ColdEmail -v`
Expected: all new tests pass (7 HTTP-level + 2 real-browser).

Run: `pytest tests/test_local_server.py -v`
Expected: every existing test in this file still passes unchanged (confirms the `build_router()`/`handle_request()`/`make_handler()` signature changes are genuinely backward compatible).

- [ ] **Step 8: Commit**

```bash
git add components/inbox_router/cold_email/ components/inbox_router/local_server.py tests/test_local_server.py
git commit -m "Wire Cold Email into local_server.py: new page + /cold-email/api routes"
```

---

### Task 4: Electron "Launch Cold Email" button

**Files:**
- Modify: `app_electron/main.js`
- Modify: `app_electron/preload.js`
- Modify: `app_electron/renderer/index.html`
- Modify: `app_electron/renderer/renderer.js`

**Interfaces:**
- Consumes: `listCapsules()`, `ensureLocalServerRunning(scriptPath)`, `shell.openExternal` (all existing, `main.js`). `currentCapsule` (existing global, `renderer.js`).
- Produces: IPC channel `launch-cold-email` (main.js handler → `{ok: true}` or `{ok: false, error}`); `window.capsulesAPI.launchColdEmail(capsuleName)` (preload.js); `#btnLaunchColdEmail` button (index.html); click handler + visibility toggle (renderer.js).

There is no existing JavaScript test harness anywhere in this repo (no `*.test.js` files at all) — building one from scratch for this task is disproportionate to the change, matching the precedent set by this project's own `test-launch-mockups`/`view-schedule` handlers, neither of which has a test either. Verification for this task is `node --check` on every edited `.js` file, plus careful manual reading during task review.

- [ ] **Step 1: Add the IPC handler to `app_electron/main.js`**

Find the existing `ipcMain.handle("view-schedule", () => { ... });` block (around lines 767–777 — it opens the local `data/schedule.txt` in Notepad). Right after that block's closing `});`, insert:

```js
ipcMain.handle("launch-cold-email", (_evt, capsuleName) => {
  // Same mechanism as test-launch-mockups above, just a different page on
  // the same local server -- Cold Email is its own page, not a Test-section
  // mockup, but it needs the exact same "make sure the server's running,
  // then open a browser tab" two steps.
  const capsule = listCapsules().find((c) => c.name === capsuleName);
  if (capsule && capsule.local_server && capsule.url) {
    ensureLocalServerRunning(capsule.local_server);
    shell.openExternal(`${capsule.url}cold-email/`);
    return { ok: true };
  }
  return { ok: false, error: "This task has no Cold Email page." };
});
```

- [ ] **Step 2: Expose it in `app_electron/preload.js`**

Right after the existing `launchTestMockups: (capsuleName) => ipcRenderer.invoke("test-launch-mockups", capsuleName),` line, insert:

```js
  launchColdEmail: (capsuleName) => ipcRenderer.invoke("launch-cold-email", capsuleName),
```

- [ ] **Step 3: Add the button to `app_electron/renderer/index.html`**

Right after the existing `<button class="btn btn-ghost btn-sm" id="btnViewSchedule" type="button">...</button>` block (ends right before `</div>` that closes `#ppTestGroup`), insert:

```html
        <button class="btn btn-ghost btn-sm" id="btnLaunchColdEmail" type="button" hidden>
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 6l-10 7L2 6"/><rect x="2" y="4" width="20" height="16" rx="2"/></svg>
          Launch Cold Email
        </button>
```

This button starts `hidden` (unlike its two siblings, which are always visible whenever the Test section shows) — per the spec, it should only show for a capsule that actually carries `local_server`/`url` (Inbox Dispatch), not for every task. Step 4 below toggles that.

- [ ] **Step 4: Wire it up in `app_electron/renderer/renderer.js`**

Right after the existing `const btnViewSchedule = document.getElementById("btnViewSchedule");` line (line 309), insert:

```js
const btnLaunchColdEmail = document.getElementById("btnLaunchColdEmail");
```

In `loadCapsuleIntoSlot`, right after the existing `ppTestGroup.hidden = capsule.kind === "url" && !capsule.local_server;` line (line 600), insert:

```js
  btnLaunchColdEmail.hidden = !(capsule.local_server && capsule.url);
```

In `clearPlaySlot`, right after the existing `ppTestGroup.hidden = true;` line (line 662), insert:

```js
  btnLaunchColdEmail.hidden = true;
```

Right after the existing `btnViewSchedule.addEventListener(...)` block (ends at line 716 with `});`), insert:

```js
btnLaunchColdEmail.addEventListener("click", async () => {
  if (!currentCapsule) return;
  try {
    const result = await window.capsulesAPI.launchColdEmail(currentCapsule.name);
    if (!result.ok) capsuleLog(result.error, "err");
  } catch (e) {
    capsuleLog(`Couldn't open Cold Email: ${e.message || e}`, "err");
  }
});
```

- [ ] **Step 5: Syntax-check every edited file**

Run:
```bash
node --check app_electron/main.js
node --check app_electron/preload.js
node --check app_electron/renderer/renderer.js
```
Expected: no output, exit code 0 for all three.

- [ ] **Step 6: Commit**

```bash
git add app_electron/main.js app_electron/preload.js app_electron/renderer/index.html app_electron/renderer/renderer.js
git commit -m "Add 'Launch Cold Email' button to the Electron Play panel"
```

---

## Final Verification (after all 4 tasks)

1. `pytest -q` from repo root — must show 0 failed.
2. `git status` — confirm nothing unintended was staged.
3. Sync `DEVELOPERS.md`'s `scope3_gmail_style_actions` entry (or a new dated extension of it, matching this project's own pattern of continuing one node rather than forking) and `treetask/index.html`'s matching node with what was actually built, including the heading-parsing ruling recorded above.
4. Push.
