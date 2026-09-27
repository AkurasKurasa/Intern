# Schedule → Real Calendar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the "Schedule" decision create a real Google Calendar event, not just append a line to a text file.

**Architecture:** A new `calendar_client.py` mirrors `gmail_client.py`'s existing mock/real split exactly. `router.py`'s schedule branch gains a second real action (create a calendar event) alongside its existing one (append to `schedule.txt`, unchanged). The human always supplies the real start/end date-time directly through a new UI field — Intern never infers a date from email text. A small, separately-bounded "View Schedule" Notepad button is bundled in since it touches the same area and was already scoped together with this work.

**Tech Stack:** Python (googleapiclient, already a project dependency via `RealGmailClient`), vanilla JS/HTML for the UI, Electron IPC for the Notepad button.

**Spec:** docs/superpowers/specs/2026-08-30-schedule-calendar-and-cold-email-design.md (Part 1: "Schedule → real Calendar event" section)

## Global Constraints

- A calendar event is created **only** when the human provides both a real start AND end date/time through the UI. Never inferred/parsed from email text. A missing date creates no event.
- `schedule_recorder.py`/`schedule.txt` are unchanged — the calendar event is additive, not a replacement for the plain-text log.
- Calendar event creation is wired into `confirm_suggestion()`/`override_decision()` only (Inbox Dispatch, the real page) — **not** into `record_practice_decision()` (Practice Inbox stays data-only for every decision, exactly as Reply/Forward/Flag already are there).
- `RealCalendarClient` reuses the exact same `client_secret.json`/`token.json` OAuth files `RealGmailClient` already uses — `gmail_client.py`'s `SCOPES` list gains the Calendar scope, one shared consent screen.
- Full test suite (`pytest -q` from repo root) must show 0 failed before any task is considered done.

---

### Task 1: `calendar_client.py` — the mock/real Calendar client

**Files:**
- Create: `components/inbox_router/calendar_client.py`
- Test: `tests/test_calendar_client.py`
- Modify: `components/inbox_router/gmail_client.py:31` (the `SCOPES` constant)

**Interfaces:**
- Consumes: nothing from other tasks (this is the foundation task).
- Produces: `CalendarClientBase` (ABC, one abstract method `create_event(summary: str, description: str, start_iso: str, end_iso: str) -> str`), `MockCalendarClient(CalendarClientBase)`, `RealCalendarClient(CalendarClientBase)`, `get_calendar_client(root: str = _THIS_DIR) -> CalendarClientBase`. Task 2 imports `CalendarClientBase`/`MockCalendarClient` from this module.

- [ ] **Step 1: Read `gmail_client.py`'s current `SCOPES` line and `MockGmailClient`/`RealGmailClient` shape**

```bash
grep -n "^SCOPES\|class MockGmailClient\|class RealGmailClient\|def _connect\|def get_gmail_client" components/inbox_router/gmail_client.py
```
This module mirrors that file's exact shape — read the real current `_connect()`/`create_draft()`/`get_gmail_client()` bodies before writing the code below, to confirm nothing has drifted from what's shown here.

- [ ] **Step 2: Add the Calendar scope to `gmail_client.py`'s `SCOPES`**

Change:
```python
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
```
to:
```python
SCOPES = ["https://www.googleapis.com/auth/gmail.modify",
          "https://www.googleapis.com/auth/calendar.events"]
```
This is the ONLY change to `gmail_client.py` in this task — do not touch anything else in that file.

- [ ] **Step 3: Write the failing tests**

Create `tests/test_calendar_client.py`:
```python
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from calendar_client import MockCalendarClient


class TestMockCalendarClient:
    def test_create_event_writes_to_mock_file(self, tmp_path):
        client = MockCalendarClient(data_dir=str(tmp_path))
        event_id = client.create_event(
            summary="Vendor call", description="Discuss Q3 proposal",
            start_iso="2026-09-03T14:00:00-07:00", end_iso="2026-09-03T14:30:00-07:00")

        assert event_id
        events_path = tmp_path / "mock_calendar_events.json"
        assert events_path.exists()
        data = json.loads(events_path.read_text(encoding="utf-8"))
        assert len(data["events"]) == 1
        assert data["events"][0]["summary"] == "Vendor call"
        assert data["events"][0]["description"] == "Discuss Q3 proposal"
        assert data["events"][0]["start"] == "2026-09-03T14:00:00-07:00"
        assert data["events"][0]["end"] == "2026-09-03T14:30:00-07:00"
        assert data["events"][0]["event_id"] == event_id

    def test_create_event_multiple_events_all_recorded_with_unique_ids(self, tmp_path):
        client = MockCalendarClient(data_dir=str(tmp_path))
        id1 = client.create_event("First", "d1", "2026-09-01T10:00:00-07:00", "2026-09-01T10:30:00-07:00")
        id2 = client.create_event("Second", "d2", "2026-09-02T10:00:00-07:00", "2026-09-02T10:30:00-07:00")

        assert id1 != id2
        data = json.loads((tmp_path / "mock_calendar_events.json").read_text(encoding="utf-8"))
        assert len(data["events"]) == 2
        assert {e["summary"] for e in data["events"]} == {"First", "Second"}

    def test_create_event_creates_data_dir_if_missing(self, tmp_path):
        data_dir = tmp_path / "nested" / "data"
        client = MockCalendarClient(data_dir=str(data_dir))
        client.create_event("Test", "d", "2026-09-01T10:00:00-07:00", "2026-09-01T10:30:00-07:00")

        assert (data_dir / "mock_calendar_events.json").exists()


class TestGetCalendarClient:
    def test_returns_mock_when_no_credentials_file(self, tmp_path):
        from calendar_client import get_calendar_client, MockCalendarClient
        # No client_secret.json anywhere under tmp_path -- must fall back to mock.
        client = get_calendar_client(root=str(tmp_path))
        assert isinstance(client, MockCalendarClient)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_calendar_client.py -v`
Expected: FAIL with "No module named 'calendar_client'" (or ImportError) — the module doesn't exist yet.

- [ ] **Step 5: Write `calendar_client.py`**

```python
"""
components/inbox_router/calendar_client.py
==============================================
The Schedule decision's real output: a real Google Calendar event, not just
a line in a text file. Mirrors gmail_client.py's own Phase A (mock, no
credentials) / Phase B (real, once credentials/client_secret.json exists)
split exactly -- same reasoning, same shape.

Deliberately narrow: one method, create_event(). No update/delete/list --
this project has never added an "undo" path for a Reply/Forward draft
either, so a calendar event getting created is exactly as final as those
already are. A human can always go edit/cancel it directly in Calendar.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(_THIS_DIR, "data")
DEFAULT_CREDENTIALS_DIR = os.path.join(_THIS_DIR, "credentials")


class CalendarClientBase(ABC):
    @abstractmethod
    def create_event(self, summary: str, description: str,
                      start_iso: str, end_iso: str) -> str:
        """Creates a real calendar event, returns its event id. Only ever
        called by router.py when the human gave both a real start and end
        time -- never with a blank date."""
        ...


class MockCalendarClient(CalendarClientBase):
    """Backs onto a gitignored generated file (mock_calendar_events.json),
    same pattern gmail_client.py's MockGmailClient uses for
    mock_drafts.json -- buildable/testable without any real Google
    account."""

    def __init__(self, data_dir: str = DEFAULT_DATA_DIR) -> None:
        self._data_dir = data_dir
        self._events_path = os.path.join(data_dir, "mock_calendar_events.json")

    def create_event(self, summary: str, description: str,
                      start_iso: str, end_iso: str) -> str:
        events = self._load_events()
        event_id = f"mock-event-{len(events) + 1}"
        events.append({
            "event_id": event_id, "summary": summary, "description": description,
            "start": start_iso, "end": end_iso,
        })
        os.makedirs(self._data_dir, exist_ok=True)
        with open(self._events_path, "w", encoding="utf-8") as f:
            json.dump({"events": events}, f, indent=2)
        return event_id

    def _load_events(self) -> list:
        if not os.path.exists(self._events_path):
            return []
        try:
            with open(self._events_path, "r", encoding="utf-8") as f:
                return json.load(f).get("events", [])
        except Exception:
            return []


class RealCalendarClient(CalendarClientBase):
    """Uses the SAME client_secret.json/token.json OAuth files
    RealGmailClient already uses -- gmail_client.py's SCOPES list (see
    Step 2 above) now includes the Calendar scope alongside Gmail's, so
    one consent screen covers both APIs and both clients share one token
    file."""

    def __init__(self, credentials_dir: str = DEFAULT_CREDENTIALS_DIR) -> None:
        self._client_secret_path = os.path.join(credentials_dir, "client_secret.json")
        self._token_path = os.path.join(credentials_dir, "token.json")
        self._service = None
        print("=" * 70, flush=True)
        print("[Inbox Router] Using REAL Google Calendar -- this calendar is now live.", flush=True)
        print("=" * 70, flush=True)
        self._connect()

    def _connect(self) -> None:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from gmail_client import SCOPES

        creds = None
        if os.path.exists(self._token_path):
            creds = Credentials.from_authorized_user_file(self._token_path, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self._client_secret_path, SCOPES)
                creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(self._token_path), exist_ok=True)
            with open(self._token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        self._service = build("calendar", "v3", credentials=creds)

    def create_event(self, summary: str, description: str,
                      start_iso: str, end_iso: str) -> str:
        event = self._service.events().insert(
            calendarId="primary",
            body={
                "summary": summary, "description": description,
                "start": {"dateTime": start_iso},
                "end": {"dateTime": end_iso},
            },
        ).execute()
        return event["id"]


def get_calendar_client(root: str = _THIS_DIR) -> CalendarClientBase:
    """Same Phase A -> Phase B swap as gmail_client.py's
    get_gmail_client() -- checks the exact same client_secret.json path,
    so Gmail and Calendar always flip from mock to real together."""
    client_secret_path = os.path.join(DEFAULT_CREDENTIALS_DIR, "client_secret.json")
    if os.path.isfile(client_secret_path):
        return RealCalendarClient()
    return MockCalendarClient()
```

Note: `get_calendar_client()`'s `root` parameter is accepted for interface symmetry with a possible future need but the function always resolves credentials from `DEFAULT_CREDENTIALS_DIR` (matching `get_gmail_client()`'s own actual behavior — read that function's real body in Step 1 to confirm this is really how it works before assuming). If the real `get_gmail_client()` behaves differently, match its real behavior instead of what's written here, and note the discrepancy in your report.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_calendar_client.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: 0 failed. (The `SCOPES` change in `gmail_client.py` is additive and inert until `RealGmailClient`/`RealCalendarClient` actually connect, which no test does — this should not affect any existing test.)

- [ ] **Step 8: Commit**

```bash
git add components/inbox_router/calendar_client.py components/inbox_router/gmail_client.py tests/test_calendar_client.py
git commit -m "Add calendar_client.py: real Google Calendar events for Schedule"
```

---

### Task 2: Wire calendar event creation into `router.py`

**Files:**
- Modify: `components/inbox_router/router.py` (imports near the top; `InboxRouter.__init__`; `confirm_suggestion()`; `override_decision()`)
- Test: `tests/test_inbox_router.py`

**Interfaces:**
- Consumes: `calendar_client.CalendarClientBase`, `calendar_client.MockCalendarClient` (Task 1).
- Produces: `InboxRouter.__init__` gains `calendar_client: CalendarClientBase = None` (stored as `self._calendar`, defaulting to a real `MockCalendarClient()` when `None`). `confirm_suggestion()`/`override_decision()` both gain `event_start: str = ""`, `event_end: str = ""` parameters. Task 3 threads these two new parameters through from the HTTP layer.

- [ ] **Step 1: Read the real current `router.py`**

```bash
grep -n "^from schedule_recorder\|def __init__\|def confirm_suggestion\|def override_decision\|elif decision == \"schedule\"\|elif new_decision == \"schedule\"" components/inbox_router/router.py
```
Read the full bodies of `__init__`, `confirm_suggestion()`, and `override_decision()` before editing — the exact current shape matters, and this file has changed multiple times this project.

- [ ] **Step 2: Write the failing tests**

Find `tests/test_inbox_router.py`'s `TestScheduleRecording` class (or whichever class currently covers the `schedule` decision — search `grep -n "class TestScheduleRecording\|def _build" tests/test_inbox_router.py` and read that class's `_build()` helper). Add a `FakeCalendarClient` test double near the top of the file (wherever other small fakes like it already live — search for an existing pattern first):
```python
class FakeCalendarClient:
    def __init__(self):
        self.events = []

    def create_event(self, summary, description, start_iso, end_iso):
        event_id = f"fake-event-{len(self.events) + 1}"
        self.events.append({
            "summary": summary, "description": description,
            "start": start_iso, "end": end_iso, "event_id": event_id,
        })
        return event_id
```
Then extend `TestScheduleRecording`'s `_build()` to accept and thread through an optional `calendar_client` argument (read its real current signature first — it takes `tmp_path` and builds an `InboxRouter(...)`; add `calendar_client=None` as a parameter and pass it into the `InboxRouter(...)` constructor call alongside its existing `schedule_log_path=...` argument), then add these tests to that class:
```python
def test_confirm_schedule_with_dates_creates_a_calendar_event(self, tmp_path):
    calendar = FakeCalendarClient()
    router = self._build(tmp_path, calendar_client=calendar)
    router.poll_once()
    entry_id = router.pending_entries()[0]["message_id"]
    router.confirm_suggestion(entry_id, "schedule", reply_body="Vendor call about Q3.",
                               event_start="2026-09-03T14:00:00-07:00",
                               event_end="2026-09-03T14:30:00-07:00")

    assert len(calendar.events) == 1
    assert calendar.events[0]["description"] == "Vendor call about Q3."
    assert calendar.events[0]["start"] == "2026-09-03T14:00:00-07:00"
    assert calendar.events[0]["end"] == "2026-09-03T14:30:00-07:00"

def test_confirm_schedule_without_dates_creates_no_calendar_event(self, tmp_path):
    calendar = FakeCalendarClient()
    router = self._build(tmp_path, calendar_client=calendar)
    router.poll_once()
    entry_id = router.pending_entries()[0]["message_id"]
    router.confirm_suggestion(entry_id, "schedule", reply_body="Vendor call about Q3.")

    assert calendar.events == []

def test_override_to_schedule_with_dates_creates_a_calendar_event(self, tmp_path):
    calendar = FakeCalendarClient()
    router = self._build(tmp_path, calendar_client=calendar)
    router.poll_once()
    entry_id = router.pending_entries()[0]["message_id"]
    router.override_decision(entry_id, "schedule", reason="needs scheduling",
                              reply_body="Follow-up call.",
                              event_start="2026-09-04T10:00:00-07:00",
                              event_end="2026-09-04T10:30:00-07:00")

    assert len(calendar.events) == 1
    assert calendar.events[0]["description"] == "Follow-up call."

def test_confirm_schedule_calendar_failure_does_not_crash(self, tmp_path):
    class BrokenCalendarClient:
        def create_event(self, *a, **kw):
            raise RuntimeError("calendar API down")
    router = self._build(tmp_path, calendar_client=BrokenCalendarClient())
    router.poll_once()
    entry_id = router.pending_entries()[0]["message_id"]
    # Must not raise -- a calendar failure is logged, not fatal, same as
    # every other real-action failure in this file (draft creation,
    # flag labels).
    router.confirm_suggestion(entry_id, "schedule", reply_body="note",
                               event_start="2026-09-03T14:00:00-07:00",
                               event_end="2026-09-03T14:30:00-07:00")
```
If `_build()`'s real signature or `pending_entries()`'s real shape differs from what's assumed here (e.g. it needs a real inbox message fixture passed in first), adapt these tests to match the real, current helper — read it, don't guess.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_inbox_router.py -k calendar -v`
Expected: FAIL — `InboxRouter.__init__()` doesn't accept `calendar_client` yet, or `confirm_suggestion()`/`override_decision()` don't accept `event_start`/`event_end` yet.

- [ ] **Step 4: Add the import, `__init__` parameter, and wire both methods**

Add near `router.py`'s other local imports (alongside the existing `from schedule_recorder import ...` line):
```python
from calendar_client import CalendarClientBase, MockCalendarClient
```

In `InboxRouter.__init__`'s signature, add one more parameter after `schedule_log_path`:
```python
schedule_log_path: str = DEFAULT_SCHEDULE_LOG_PATH,
calendar_client: CalendarClientBase = None) -> None:
```
And in the body, after the existing `self._schedule_log_path = schedule_log_path` line:
```python
self._calendar = calendar_client if calendar_client is not None else MockCalendarClient()
```

In `confirm_suggestion()`, change the signature to:
```python
def confirm_suggestion(self, message_id: str, decision: str, reply_body: str = "",
                        event_start: str = "", event_end: str = "") -> None:
```
And extend the existing `elif decision == "schedule" and message is not None:` branch — keep its existing `record_schedule_entry(...)` call exactly as it is, and add the calendar call right after it, still inside the same `elif`:
```python
elif decision == "schedule" and message is not None:
    if reply_body.strip():
        try:
            record_schedule_entry(message, reply_body, path=self._schedule_log_path)
        except Exception as exc:
            emit("inbox_log", line=f"Failed to record schedule entry: {exc}", level="err")
    if event_start.strip() and event_end.strip():
        try:
            self._calendar.create_event(summary=message.subject, description=reply_body,
                                         start_iso=event_start, end_iso=event_end)
        except Exception as exc:
            emit("inbox_log", line=f"Failed to create calendar event: {exc}", level="err")
```

Do the identical change in `override_decision()`: signature gains `event_start: str = "", event_end: str = ""`, and its own `elif new_decision == "schedule" and message is not None:` branch gets the same second `if event_start.strip() and event_end.strip():` block appended after its existing `record_schedule_entry(...)` call.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_inbox_router.py -k calendar -v`
Expected: PASS, all 4 new tests.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: 0 failed. Every existing caller of `InboxRouter(...)`/`confirm_suggestion()`/`override_decision()` must still work unchanged, since the two new parameters both default to safe no-op values (`None` → a real `MockCalendarClient()`, `""` → no event created).

- [ ] **Step 7: Commit**

```bash
git add components/inbox_router/router.py tests/test_inbox_router.py
git commit -m "Wire calendar event creation into confirm_suggestion/override_decision"
```

---

### Task 3: Thread `event_start`/`event_end` through the HTTP layer

**Files:**
- Modify: `components/inbox_router/local_server.py`
- Test: `tests/test_local_server.py`

**Interfaces:**
- Consumes: `InboxRouter.confirm_suggestion(event_start=, event_end=)` / `override_decision(event_start=, event_end=)` (Task 2).
- Produces: nothing new for later tasks — Task 4 (the UI) talks to these same `/api/confirm`/`/api/override` routes, which already exist; it just starts sending two more optional fields in the POST body.

- [ ] **Step 1: Read the real current `/api/confirm`/`/api/override` handlers**

```bash
grep -n "POST.*api/confirm\|POST.*api/override" components/inbox_router/local_server.py
```
Read both full blocks in `handle_request()` before editing.

- [ ] **Step 2: Write the failing tests**

Find `tests/test_local_server.py`'s `_build_router()` helper (search `grep -n "def _build_router" tests/test_local_server.py`) and extend it to accept and thread through an optional `calendar_client=None` parameter into its `InboxRouter(...)` construction, same as Task 2 did for `tests/test_inbox_router.py`. Reuse the same `FakeCalendarClient` shape from Task 2 (define it in this file too, or import it if the two test files already share fixtures — check first with `grep -n "^import\|^from" tests/test_local_server.py` to see if there's an existing shared-fixtures module; if not, just define a local copy here, matching this file's existing style of not importing fixtures from other test files).

```python
def test_post_api_confirm_schedule_with_dates_creates_calendar_event(self, tmp_path):
    calendar = FakeCalendarClient()
    router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "vendor call")],
                            calendar_client=calendar)
    router.poll_once()
    body = json.dumps({
        "message_id": "i1", "decision": "schedule",
        "reply_body": "Vendor call Sept 3rd.",
        "event_start": "2026-09-03T14:00:00-07:00",
        "event_end": "2026-09-03T14:30:00-07:00",
    }).encode("utf-8")
    status, _headers, resp_body, _ct = ls.handle_request("POST", "/api/confirm", body, router)

    assert status == 200
    assert json.loads(resp_body) == {"ok": True}
    assert len(calendar.events) == 1
    assert calendar.events[0]["start"] == "2026-09-03T14:00:00-07:00"

def test_post_api_confirm_schedule_without_dates_creates_no_event(self, tmp_path):
    calendar = FakeCalendarClient()
    router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "vendor call")],
                            calendar_client=calendar)
    router.poll_once()
    body = json.dumps({"message_id": "i1", "decision": "schedule", "reply_body": "note"}).encode("utf-8")
    status, _headers, _resp_body, _ct = ls.handle_request("POST", "/api/confirm", body, router)

    assert status == 200
    assert calendar.events == []
```
Adjust the exact `_build_router`/`_msg` call shapes to match this file's real current helpers if they differ from what's shown here (read them first, per Step 1's instruction — don't guess).

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_local_server.py -k calendar -v`
Expected: FAIL — `_build_router()` doesn't accept `calendar_client` yet, and/or the confirm route doesn't pass `event_start`/`event_end` through.

- [ ] **Step 4: Update the two route handlers**

In the `/api/confirm` block, right after the existing `reply_body = data.get("reply_body", "")` line, add:
```python
event_start = data.get("event_start", "")
event_end = data.get("event_end", "")
```
and change the existing call:
```python
router.confirm_suggestion(message_id, decision, reply_body=reply_body)
```
to:
```python
router.confirm_suggestion(message_id, decision, reply_body=reply_body,
                           event_start=event_start, event_end=event_end)
```

Do the identical change in the `/api/override` block: add the same two `data.get(...)` lines after its own `reply_body = data.get("reply_body", "")` line, and extend its `router.override_decision(...)` call with `event_start=event_start, event_end=event_end`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_local_server.py -k calendar -v`
Expected: PASS, both new tests.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: 0 failed.

- [ ] **Step 7: Commit**

```bash
git add components/inbox_router/local_server.py tests/test_local_server.py
git commit -m "Thread event_start/event_end through /api/confirm and /api/override"
```

---

### Task 4: UI — date/time fields on the Schedule reply box

**Files:**
- Modify: `components/inbox_router/local_ui/index.html`
- Modify: `components/inbox_router/local_ui/app.js`
- Modify: `components/inbox_router/local_ui/style.css`

**Interfaces:**
- Consumes: `/api/confirm` and `/api/override` now accept optional `event_start`/`event_end` fields in their POST body (Task 3).
- Produces: nothing for later tasks in this plan.

There is no existing JavaScript test harness anywhere in this repo (no `*.test.js` files at all) — building one from scratch for this task is disproportionate to the change. Verification for this task is: the `node --check` syntax command below, plus careful manual reading during task review (the reviewer should trace through `refreshReplyBoxVisibility()`/`submitDecision()`/`confirmCurrent()`/`openMessage()` by hand against the DOM structure, the same way a human would). This is a deliberate, stated scope decision, not a skipped step.

- [ ] **Step 1: Read the real current files**

```bash
grep -n "replyBoxWrap\|replyBody\|overrideSelect" components/inbox_router/local_ui/index.html
grep -n "isReplyLike\|refreshReplyBoxVisibility\|function submitDecision\|function confirmCurrent\|function openMessage\|function closeMessage" components/inbox_router/local_ui/app.js
```
Read the full current bodies of every function named above before editing.

- [ ] **Step 2: Add the date/time fields to `index.html`**

Inside the existing `<div id="replyBoxWrap" class="reply-box-wrap" hidden>` block, right after the existing `<textarea id="replyBody" ...>` line, add:
```html
<div id="scheduleDatesWrap" class="schedule-dates-wrap" hidden>
  <label class="schedule-date-label">Starts
    <input type="datetime-local" id="eventStart" class="schedule-date-input">
  </label>
  <label class="schedule-date-label">Ends
    <input type="datetime-local" id="eventEnd" class="schedule-date-input">
  </label>
</div>
```
Do not touch anything else in this file.

- [ ] **Step 3: Add matching CSS to `style.css`**

Find the existing `.reply-textarea` rule (`grep -n "\.reply-textarea" components/inbox_router/local_ui/style.css`) and add these rules right after it:
```css
.schedule-dates-wrap { margin-top: 8px; display: flex; gap: 16px; }
.schedule-dates-wrap[hidden] { display: none; }
.schedule-date-label { font-size: 13px; color: #5f6368; display: flex; flex-direction: column; gap: 4px; }
.schedule-date-input {
  font-family: inherit; font-size: 14px; color: #202124;
  border: 1px solid #dadce0; border-radius: 4px; padding: 6px 8px;
}
.schedule-date-input:focus { outline: none; border-color: #1a73e8; }
```

- [ ] **Step 4: Wire `app.js`**

Add two new element references near the top, alongside the existing `const replyBody = document.getElementById("replyBody");` line:
```js
const scheduleDatesWrap = document.getElementById("scheduleDatesWrap");
const eventStart = document.getElementById("eventStart");
const eventEnd = document.getElementById("eventEnd");
```

In `refreshReplyBoxVisibility()`, add one line after the existing `replyBoxWrap.hidden = ...` line:
```js
function refreshReplyBoxVisibility() {
  const email = pendingEmails.find((e) => e.message_id === openMessageId);
  const suggested = email ? email.decision : "";
  replyBoxWrap.hidden = !(isReplyLike(suggested) || isReplyLike(overrideSelect.value));
  scheduleDatesWrap.hidden = !(suggested === "schedule" || overrideSelect.value === "schedule");
}
```

In `submitDecision(newDecision, reason, successMessage)`, extend the POST body (read the real current body first — it should currently include `message_id`, `new_decision`, `reason`, `reply_body`) to also send:
```js
event_start: newDecision === "schedule" ? eventStart.value : "",
event_end: newDecision === "schedule" ? eventEnd.value : "",
```

In `confirmCurrent()`, extend its POST body the same way, using `email.decision` in place of `newDecision`:
```js
event_start: email.decision === "schedule" ? eventStart.value : "",
event_end: email.decision === "schedule" ? eventEnd.value : "",
```

In `openMessage(messageId)`, right after the existing `replyBody.value = "";` line, add:
```js
eventStart.value = "";
eventEnd.value = "";
```
so a newly-opened message never carries over a stale date from whatever was previously open.

- [ ] **Step 5: Verify syntax**

Run: `node --check components/inbox_router/local_ui/app.js`
Expected: no output (success).

- [ ] **Step 6: Manually trace the logic (documented in your report, not run as an automated test)**

In your report, walk through and confirm by reading the code: (a) opening a `schedule`-suggested email shows the date fields; (b) opening a `reply`-suggested email does NOT show the date fields; (c) selecting "Schedule" from the override dropdown on a non-schedule email shows the date fields; (d) confirming/overriding to `schedule` with both fields filled sends `event_start`/`event_end` in the POST body; (e) confirming/overriding to anything else sends empty strings for both, regardless of what's still sitting in the (hidden) date inputs.

- [ ] **Step 7: Run the full Python suite** (nothing here should have changed, but confirm)

Run: `python -m pytest -q`
Expected: 0 failed.

- [ ] **Step 8: Commit**

```bash
git add components/inbox_router/local_ui/index.html components/inbox_router/local_ui/app.js components/inbox_router/local_ui/style.css
git commit -m "Add Schedule date/time fields to Inbox Dispatch's reply box"
```

---

### Task 5: "View Schedule" Notepad button

**Files:**
- Modify: `app_electron/main.js`
- Modify: `app_electron/preload.js`
- Modify: `app_electron/renderer/index.html`
- Modify: `app_electron/renderer/renderer.js`

**Interfaces:**
- Consumes: nothing from earlier tasks in this plan — independent of Tasks 1-4.
- Produces: nothing for later tasks.

No new automated tests for this task (Electron IPC + a native Notepad launch — same category of thing this project already doesn't unit-test, e.g. the existing `test-launch-mockups` handler has no test either). Verification is: `node --check` on every edited `.js` file, plus a real, hands-on smoke test described in Step 6 below.

- [ ] **Step 1: Read the real current files**

```bash
grep -n "REPO_ROOT\|ipcMain.handle(\"test-launch-mockups\"" app_electron/main.js
grep -n "contextBridge.exposeInMainWorld(\"capsulesAPI\"" app_electron/preload.js
grep -n "ppTestGroup\|btnLaunchMockups" app_electron/renderer/index.html app_electron/renderer/renderer.js
```
Read the full `test-launch-mockups` handler in `main.js`, the full `capsulesAPI` object in `preload.js`, and the full `#ppTestGroup` block in `index.html` plus its wiring in `renderer.js`, before editing any of them.

- [ ] **Step 2: Add the IPC handler in `main.js`**

Add this new handler right after the existing `ipcMain.handle("test-launch-mockups", ...)` block (`main.js` already has `fs`, `path`, `spawn` required at the top of the file — confirm this in Step 1's read, don't add duplicate requires):
```js
ipcMain.handle("view-schedule", () => {
  const scheduleDir = path.join(REPO_ROOT, "components", "inbox_router", "data");
  const schedulePath = path.join(scheduleDir, "schedule.txt");
  if (!fs.existsSync(schedulePath)) {
    fs.mkdirSync(scheduleDir, { recursive: true });
    fs.writeFileSync(schedulePath, "");
  }
  const child = spawn("notepad.exe", [schedulePath], { detached: true, stdio: "ignore" });
  child.unref();
  return { ok: true };
});
```

- [ ] **Step 3: Expose it in `preload.js`**

In the existing `contextBridge.exposeInMainWorld("capsulesAPI", { ... })` object, add one more line, right after the existing `launchTestMockups: (capsuleName) => ipcRenderer.invoke("test-launch-mockups", capsuleName),` line:
```js
viewSchedule: () => ipcRenderer.invoke("view-schedule"),
```

- [ ] **Step 4: Add the button in `index.html`**

Inside the existing `<div id="ppTestGroup" hidden>` block, right after the existing `<button ... id="btnLaunchMockups" ...>...</button>` closing tag, add:
```html
<button class="btn btn-ghost btn-sm" id="btnViewSchedule" type="button">
  <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/></svg>
  View Schedule
</button>
```
This button lives inside the SAME `#ppTestGroup` div as "Launch mockups" — it inherits that div's existing show/hide rule (visible only when the loaded capsule has `local_server`/`url` fields, which today means Inbox Dispatch specifically). Do not add a new visibility condition; do not touch anything else in this file.

- [ ] **Step 5: Wire the click handler in `renderer.js`**

Add a new element reference alongside the existing `const btnLaunchMockups = document.getElementById("btnLaunchMockups");` line:
```js
const btnViewSchedule = document.getElementById("btnViewSchedule");
```
Add a new click listener alongside the existing `btnLaunchMockups.addEventListener("click", ...)` block:
```js
btnViewSchedule.addEventListener("click", async () => {
  try {
    await window.capsulesAPI.viewSchedule();
  } catch (e) {
    capsuleLog(`Couldn't open schedule: ${e.message || e}`, "err");
  }
});
```
If `capsuleLog(...)` isn't the real name of this file's logging helper, use whatever the real one is (read it from the existing `btnLaunchMockups` handler's own catch block in Step 1).

- [ ] **Step 6: Syntax-check every edited JS file**

```bash
node --check app_electron/main.js
node --check app_electron/preload.js
node --check app_electron/renderer/renderer.js
```
Expected: no output from any of the three (success).

Then, in your report, describe (you cannot literally click a real Electron window yourself — this is the user's own future smoke test, not something to attempt here) exactly what a human running the app would see: loading the Inbox Dispatch workflow into the Play panel should reveal a "View Schedule" button next to "Launch mockups"; clicking it should open Notepad on the real `schedule.txt` file, creating it empty first if it doesn't exist yet.

- [ ] **Step 7: Run the full Python suite** (nothing Python changed, but confirm nothing else broke)

Run: `python -m pytest -q`
Expected: 0 failed.

- [ ] **Step 8: Commit**

```bash
git add app_electron/main.js app_electron/preload.js app_electron/renderer/index.html app_electron/renderer/renderer.js
git commit -m "Add 'View Schedule' button that opens schedule.txt in Notepad"
```

---

### Task 6: Sync DEVELOPERS.md and the Task Tree

**Files:**
- Modify: `DEVELOPERS.md`
- Modify: `treetask/index.html`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Find the existing Scope #3 entry area**

```bash
grep -n "scope3_redefined_choices_and_schedule\|Scope #3" DEVELOPERS.md | head -10
```

- [ ] **Step 2: Add a new, dated sub-entry** covering: Schedule now creates a real Google Calendar event (via the new `calendar_client.py`, mirroring `gmail_client.py`'s mock/real split) in addition to the existing plain-text log, which is unchanged; the human always supplies the real date/time directly through the UI, never inferred from the email; and the small "View Schedule" Notepad button. Reference the real commit range this task's own `git log` shows once Tasks 1-5 are done (read it, don't guess at SHAs).

- [ ] **Step 3: Mirror the same content into `treetask/index.html`**'s matching node (find it via `grep -n "scope3_redefined_choices_and_schedule" treetask/index.html`; append a new dated paragraph to that node's existing `desc`, don't replace it).

- [ ] **Step 4: Verify the script block still parses**

```bash
node -e "const fs=require('fs');const html=fs.readFileSync('treetask/index.html','utf8');const m=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];m.forEach((x,i)=>{try{new Function(x[1]);console.log(i,'OK');}catch(e){console.log(i,'ERR',e.message);}});"
```
Expected: every entry `OK`.

- [ ] **Step 5: Commit and push**

```bash
git add DEVELOPERS.md treetask/index.html
git commit -m "Sync Task Tree and DEVELOPERS.md with Schedule's real Calendar output"
git push
```
