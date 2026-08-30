# Schedule → Real Calendar, and Cold Email — Design

## Context

Direct instruction, after extensive back-and-forth clarifying it: Scope #3's six
decisions (Reply, Forward, Schedule, Cold email, Flag, Leave alone) are not UI
labels — each one must correspond to a real, concrete action, or the project
is lying about what it does. A prior pass already made **Flag** real (applies
an actual Gmail label, not just a recorded decision — see
`docs/superpowers/specs/2026-08-28-scope3-redefined-choices-and-schedule-design.md`
and its plan). **Reply**/**Forward** were already real before that (real Gmail
drafts via `create_draft()`). **Leave alone** doing nothing is confirmed
correct — that IS the action. Two are not yet real:

- **Schedule** currently only appends a line to a plain text file
  (`schedule.txt`) — nothing you could act on later without manually opening
  that file. Direct instruction: it should create a real **Google Calendar
  event**, because "a plain text file you have to remember to check" isn't
  actually using the schedule, just storing it.
- **Cold email** currently does nothing at all (a stub that logs "not
  implemented yet"). Direct instruction, settled over several exchanges:
  - Who to email comes from a **task-list document** (a boss-style file,
    matching the `example_boss_task_list.txt` mockup discussed earlier this
    project, now becoming a real file the code actually reads) — not typed in
    ad hoc, not inferred.
  - What to write is **fully typed by the human, per person** — no shared
    template reused across recipients. Same "never invent, your real words
    only" rule this project holds everywhere else.
  - The subject line pre-fills from the task list's own wording (the boss's
    real words), editable, rather than the user typing it from nothing or
    Intern inventing one.

Both features share a root cause worth naming: they're the two decisions
whose real-world action isn't "do something to an existing Gmail message" —
Schedule's real destination is a different Google product (Calendar), and
Cold email's target isn't in the inbox at all (a brand-new contact, sourced
from a document, not a message). That's why both were left as stubs/logs
originally, and why both need slightly more machinery than Flag's one-line
label fix.

## What already exists (unchanged by this spec)

- `components/inbox_router/gmail_client.py` — `GmailClientBase` /
  `MockGmailClient` / `RealGmailClient`, the Phase A (mock, no credentials) /
  Phase B (real, `credentials/client_secret.json` present) swap. `create_draft()`
  already exists and is reused as-is by this spec — no new draft-creation
  code, Cold email is just a new caller of the same method Reply/Forward use.
  `apply_flag_label()` (added by the prior plan) is the direct precedent for
  this spec's new `calendar_client.py`: same mock/real split, same
  find-or-create-then-cache pattern for the real side.
- `components/inbox_router/schedule_recorder.py` — `record_schedule_entry()`,
  appends the human's real note to `schedule.txt`, blank-note-saves-nothing
  guarantee. **Stays exactly as it is** — the plain-text log remains a cheap,
  free audit trail alongside the new real Calendar event, not replaced by it.
- `components/inbox_router/router.py` — `confirm_suggestion()`/
  `override_decision()`, the only place real Gmail-side actions (draft
  creation, flag labels) happen; `record_practice_decision()` on the same
  class, deliberately data-only (no real actions), used by Practice Inbox.
- `components/inbox_router/local_server.py` — HTTP layer over `InboxRouter`,
  serves `local_ui/` (Inbox Dispatch, the real page) and `practice_inbox/`
  (data-only) as static files plus JSON API routes.
- `tasks/registry.json`'s `Inbox Dispatch` entry — `kind: "script"`,
  `entrypoint: automate_inbox.py`, but also carries `url`/`local_server`
  fields purely so Electron's "Launch mockups" button can open
  `{url}practice/` in the system browser. This spec adds one more use of that
  same `url`/`local_server` pair.

## Design

### 1. Schedule → real Calendar event

**New module: `components/inbox_router/calendar_client.py`**, mirroring
`gmail_client.py`'s shape exactly:

```python
class CalendarClientBase(ABC):
    @abstractmethod
    def create_event(self, summary: str, description: str,
                      start_iso: str, end_iso: str) -> str:
        """Creates a real calendar event, returns its event id."""
        ...

class MockCalendarClient(CalendarClientBase):
    # Appends to a local JSON file (mock_calendar_events.json), same
    # gitignored-generated-file pattern as mock_drafts.json.

class RealCalendarClient(CalendarClientBase):
    # Google Calendar API (googleapiclient, already a project dependency
    # via RealGmailClient). Uses the SAME client_secret.json/token.json
    # OAuth files Gmail already uses -- Calendar's scope
    # ("https://www.googleapis.com/auth/calendar.events") gets ADDED to
    # gmail_client.py's SCOPES list, one shared consent screen, one token
    # file, both APIs. (Concrete effect: since this project has never
    # actually completed real OAuth yet -- no client_secret.json exists
    # today -- there's no existing user to re-consent. This is a
    # forward-looking note, not a migration.)

def get_calendar_client(root=...) -> CalendarClientBase:
    # Same is-the-credentials-file-present check as get_gmail_client().
```

**The human provides the date/time directly — Intern never parses it out of
email text.** This is the same principle as "never invent content" applied to
dates: guessing a date from free text ("Sept 3rd" — which year? whose
timezone? is "next Tuesday" relative to today or the email's received date?)
is exactly the kind of AI interpretation this project has ruled out
everywhere else, and a wrong calendar event is worse than none. So the
Schedule reply-box gains a real `<input type="datetime-local">` alongside the
existing note textarea — both required for a calendar event to be created;
the plain-text log (`schedule.txt`) still records with just the note, exactly
as it does today, regardless of whether a date was given.

**Router wiring** (`router.py`): `InboxRouter.__init__` gains an injectable
`calendar_client: CalendarClientBase = None` param (defaults to
`get_calendar_client()`, same injection pattern `reply_examples_path`/
`schedule_log_path` already use for testability). `confirm_suggestion()`/
`override_decision()`'s existing `elif decision == "schedule" and message is
not None:` branch gains a second real action, alongside the unchanged
`record_schedule_entry()` call:

```python
elif decision == "schedule" and message is not None:
    if reply_body.strip():
        try:
            record_schedule_entry(message, reply_body, path=self._schedule_log_path)
        except Exception as exc:
            emit("inbox_log", line=f"Failed to record schedule entry: {exc}", level="err")
    if event_start and event_end:
        try:
            self._calendar.create_event(
                summary=message.subject, description=reply_body,
                start_iso=event_start, end_iso=event_end)
        except Exception as exc:
            emit("inbox_log", line=f"Failed to create calendar event: {exc}", level="err")
```

Both methods gain an `event_start: str = ""`, `event_end: str = ""` parameter
(ISO datetimes). A blank/missing date creates no event — same honesty
guarantee as blank reply text creating no draft. **Not threaded into
`record_practice_decision()`** — Practice Inbox stays data-only for every
decision, exactly as established for Reply/Forward/Flag; only Inbox Dispatch
creates real calendar events.

**HTTP layer** (`local_server.py`): `/api/confirm` and `/api/override`
parse two more optional body fields, `event_start`/`event_end`, defaulting to
`""`, passed straight through — same pattern `reply_body` already uses.

**UI** (`local_ui/index.html` + `app.js`): the existing `#replyBoxWrap` gains
two more fields, shown only when the active decision is `schedule` (reply/
forward never show them):
```html
<div id="scheduleDatesWrap" class="schedule-dates-wrap" hidden>
  <label>Starts <input type="datetime-local" id="eventStart"></label>
  <label>Ends <input type="datetime-local" id="eventEnd"></label>
</div>
```
`refreshReplyBoxVisibility()`'s existing suggested/override check gains a
matching `scheduleDatesWrap.hidden = decision !== "schedule"` line.
`submitDecision()`/`confirmCurrent()` include `event_start`/`event_end` in
their POST bodies only when `decision === "schedule"`.

**A small, separate, bounded addition bundled into the same plan since it
touches the same area: "View Schedule" button.** `schedule.txt` stays a
plain file, so a one-click way to actually read it matters more, not less,
now that it's a secondary record instead of the primary one. Mirrors the
existing `TEST_MOCKUPS`/`type: "notepad"` mechanism `main.js`'s
`test-launch-mockups` handler already has (used for other capsules) — a new
small IPC handler, `view-schedule`, spawns `notepad.exe` on
`components/inbox_router/data/schedule.txt`'s real path. One button in
`local_ui/index.html`'s sidebar (or wherever the working implementer finds a
natural home reading the current layout), wired the same way
`btnLaunchMockups` already is.

### 2. Cold email

**The task list becomes a real, committed-format file:**
`components/inbox_router/data/task_list.txt`. Deliberately simple,
line-based, no free-text parsing:
```
Cold email:
Dana Whitfield <dana.whitfield@northline.example.com>
Marcus Oyelaran <m.oyelaran@delridge.example.com>
Priya Ramaswami <priya@ramaswami-consulting.example.com>
```
A "Cold email:" heading line, followed by one `Name <email>` line per
target, until a blank line or a different heading (this project only builds
the "Cold email:" section for now — a future task-list-driven plan for the
other decision types, if ever built, would extend the same file/parser, not
replace it).

**New module: `components/inbox_router/task_list_parser.py`**:
```python
@dataclass
class ColdEmailTarget:
    name: str
    email: str
    context_line: str  # the heading text above this target's section --
                        # becomes the pre-filled (editable) subject.

def parse_cold_email_targets(path: str = DEFAULT_TASK_LIST_PATH) -> List[ColdEmailTarget]:
    ...
```
Regex/line-based parsing only (`Name <email>` via a simple pattern) — no LLM,
no guessing. A line that doesn't match the pattern is skipped, not guessed at.

**New state file**: `components/inbox_router/data/cold_email_state.json`,
`{"contacted_emails": [...]}`, exactly mirroring `mock_state.json`'s
`processed_ids` shape and purpose — once a target's been emailed, they drop
off the pending list.

**New module: `components/inbox_router/cold_email_sender.py`**:
```python
class ColdEmailSender:
    def __init__(self, gmail_client: GmailClientBase,
                 task_list_path: str = DEFAULT_TASK_LIST_PATH,
                 state_path: str = DEFAULT_COLD_EMAIL_STATE_PATH) -> None: ...

    def list_pending_targets(self) -> List[ColdEmailTarget]:
        """Every target in the task list not already in contacted_emails."""

    def send_cold_email(self, email: str, subject: str, body: str) -> str:
        """Creates a real Gmail draft (via the SAME create_draft() Reply/
        Forward already use -- no new send/draft code), marks this email
        contacted. Blank subject/body is a no-op, same honesty guarantee
        as everywhere else -- never draft empty/invented content."""
```
Deliberately **does not** call `decision_recorder.record_example()` — unlike
Reply/Forward/Schedule/Flag, there is no "decision" being learned here (the
task list already decided WHO; there is no ambiguous inbox message being
classified). Keeping this out avoids conflating "task-list-driven execution"
with "inbox-triage decision learning," which the plan that introduced the
6-choice set was careful to keep cold-start-safe and untouched. `cold_email`
remains a selectable option inside Inbox Dispatch/Practice Inbox too (for the
rare case an *existing* inbox email genuinely calls for reaching out to a new
third party it mentions) — picking it there keeps today's "not implemented
yet" log message, since that path has no target/subject/body flow at all.
This spec's real, working Cold email lives only on the new page below.

**New page: `components/inbox_router/cold_email/`** (`index.html`,
`style.css`, `app.js`), structurally mirroring `practice_inbox/`'s shape
(list view → detail view → compose → confirm) but listing task-list targets
instead of inbox messages:
- List view: one row per pending target (name, email, context line as the
  preview text) — no AI suggestion anywhere, matching Practice Inbox's own
  "no suggestion" precedent, since there's nothing to suggest here either.
- Detail view: target's name/email shown read-only; **subject** field
  pre-filled from `context_line`, editable; **body** textarea, blank,
  required; **Confirm** button.
- Confirming calls the send route, then removes that target from the list
  (mirrors `closeMessage()`+`loadInbox()`'s existing refresh pattern).

**New routes in `local_server.py`**:
```
GET  /cold-email/               -> serve cold_email/index.html
GET  /cold-email/style.css      -> serve cold_email/style.css
GET  /cold-email/app.js         -> serve cold_email/app.js
GET  /cold-email/api/targets    -> {"targets": [...]}
POST /cold-email/api/send       -> {email, subject, body} -> {"ok": true}
```
`handle_request()` gains a `ColdEmailSender` instance (constructed once,
same lifetime as the `InboxRouter` it's threaded alongside — `serve()`
constructs both from the same `GmailClientBase`).

**Reaching the page from Electron**: extends the exact mechanism
`test-launch-mockups`'s Inbox Dispatch branch already uses
(`capsule.local_server && capsule.url` → `ensureLocalServerRunning()` +
`shell.openExternal(...)`) — a second small IPC handler,
`launch-cold-email`, doing the same `ensureLocalServerRunning(capsule.
local_server)` then `shell.openExternal(`${capsule.url}cold-email/`)`. A
second small button next to the existing "Launch mockups" button in
`renderer.js`'s Play panel, shown under the same condition (`capsule.
local_server && capsule.url`) — visible for Inbox Dispatch, invisible for
capsules that don't carry those fields.

## Global Constraints (binding on the implementation plan)

- Calendar events are created **only** when the human provides both a real
  start and end date/time through the UI. Never inferred/parsed from email
  text. A missing date creates no event — the plain-text log still records
  the note regardless.
- Cold email's subject line pre-fills from the task list's own real wording;
  the body is always blank until the human types it. Never a shared
  template reused across more than one recipient in this spec.
- `create_draft()` (existing) is the only draft-creation path used by Cold
  email — no new Gmail-writing code.
- Practice Inbox is untouched by this spec and stays data-only for every
  decision — no real drafts, no calendar events, no flag labels, no cold
  emails, regardless of what's typed there.
- `schedule_recorder.py`/`schedule.txt` are unchanged — the Calendar event
  is additive, not a replacement.
- `task_list_parser.py` does line/regex parsing only — never an LLM guess at
  who's a valid target.
- Full test suite (`pytest -q` from repo root) must show 0 failed before any
  task in the resulting plan is considered done.

## What's explicitly out of scope for this spec

- Any other section of the task list besides "Cold email:" (e.g., a future
  "Schedule whatever needs scheduling" section driving Schedule from the
  task list too) — not requested, not designed here.
- Feeding Cold email decisions into `decision_recorder`/training at all —
  deliberately excluded above, see reasoning.
- Editing/canceling a calendar event once created, or editing/withdrawing a
  cold email draft once created — both are exactly as final as an existing
  Reply/Forward draft is today (a human can still go edit it in Gmail/
  Calendar directly; this project doesn't add a special undo path anywhere
  else either).
- Real OAuth setup/testing against a live Google account — this project has
  never completed that step for Gmail either; this spec's real-client code
  paths are written and unit-testable via dependency injection the same way
  `RealGmailClient` already is, but exercising them against a live Google
  Calendar is the user's own future action, same as real Gmail always has
  been.
