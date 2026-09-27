# Scope #3: Practice Inbox (Record-step demonstration environment)

Date: 2026-08-26

## Context

Direct request, after realizing a real gap: Scope #1 (the insurance form) and Scope #2 (the mock portal) both have a genuine "practice target app" — an environment a person freely acts on, generating raw demonstrations. Scope #3 never had this. "Inbox Dispatch" (the real local UI built earlier today) is not it: it shows an AI-generated suggestion and asks the person to Confirm or Override it — that's the **Output/deployment** step, not the **Record** step. There was no page where a person just looks at an email and decides, on their own judgment, what to do with it — the actual behavioral-cloning demonstration this whole pipeline is supposed to learn from.

This spec adds that missing piece.

## Goals

- A person can freely browse the same mock inbox and, for each email, pick an action themselves — no AI suggestion, no rationale, nothing to confirm — exactly the same demonstration relationship Scope #1's form and Scope #2's portal already have.
- Every picked action becomes a real row in `training_examples.jsonl`, via the exact same `decision_recorder.record_example()` function every other path already uses. `train_inbox_agent.py` needs zero changes.
- Reachable the same way Scope #1/#2's practice apps are: the existing "Launch mockups" button, currently hidden for Inbox Dispatch (since it never had anything to launch there) — un-hidden and wired to this.
- No new server process — reuse `local_server.py`, already spawned by the existing Play mechanism.

## Non-goals

- No RuleLayer/LLMClassifier/InboxAgent involvement anywhere in this flow — this is pure human demonstration, the opposite of the review-a-suggestion flow Inbox Dispatch already provides.
- No change to Inbox Dispatch itself, `InboxAgent`, `train_inbox_agent.py`, or anything already built — purely additive.
- Repeated practice on the same fixed mock inbox is fine (unlike the real triage flow, this doesn't need `mark_processed()`/pending-state tracking) — every email stays available to practice on again.

## Architecture

```
"Launch mockups" button, un-hidden for Inbox Dispatch
        |
        v
main.js: same ensureLocalServerRunning() + TEST_MOCKUPS-style entry,
         opens http://localhost:8765/practice/
        |
        v
local_server.py (extended, same running process)
  GET  /practice/           -> practice_inbox/index.html
  GET  /practice/style.css  -> practice_inbox/style.css
  GET  /practice/app.js     -> practice_inbox/app.js
  GET  /practice/api/inbox  -> InboxRouter.list_practice_inbox()
  POST /practice/api/record -> InboxRouter.record_practice_decision(...)
        |
        v
router.py (InboxRouter gains 2 new public methods, no existing behavior changed)
  list_practice_inbox() -> list[EmailMessage]      (wraps self._gmail.list_recent_inbox())
  record_practice_decision(message_id, decision)   (fetches the real EmailMessage,
                                                      calls decision_recorder.record_example()
                                                      directly, source="live")
        |
        v
components/inbox_router/data/training_examples.jsonl   (same file, same shape, unchanged)
```

## Components

### 1. `components/inbox_router/router.py` (two new public methods)

```python
def list_practice_inbox(self) -> list:
    """Every mock inbox message available to practice-demonstrate on,
    unfiltered by processed state -- unlike poll_once()'s
    list_inbox_unprocessed(), practice mode is meant to be repeatable, not
    a one-shot triage queue. Wraps the same list_recent_inbox() bootstrap()
    already uses for a wide lookback window."""
    since_iso = "2020-01-01T00:00:00+00:00"  # effectively "everything" for the mock fixture
    return self._gmail.list_recent_inbox(since_iso)

def record_practice_decision(self, message_id: str, decision: str) -> None:
    """A raw human demonstration -- no AI suggestion involved at any point,
    the opposite of confirm_suggestion()/override_decision(). Fetches the
    real message and records it exactly like every other recorded example,
    via the same decision_recorder.record_example() call. Also folds into
    the sender-pattern profile the same way a real confirm does, since a
    genuine demonstration is at least as strong a signal as a confirm."""
    message = self._gmail.get_message(message_id)
    if message is None:
        emit("inbox_error", message=f"Unknown message id: {message_id}")
        return
    record_example(message, decision, source="live")
    self._profile.record_confirmed_decision(message, decision)
```

(`record_example` and `emit` are already imported/defined in `router.py` — `record_example` via the existing `from decision_recorder import DEFAULT_EXAMPLES_PATH, record_example` import, `emit` is the module's own existing function.)

### 2. `components/inbox_router/local_server.py` (new routes, same file)

Extend `_STATIC_FILES` with the practice page's three files, and add two new branches to `handle_request()`:

```python
_PRACTICE_UI_DIR = os.path.join(_THIS_DIR, "practice_inbox")

_STATIC_FILES = {
    "/": ("index.html", "text/html"),
    "/style.css": ("style.css", "text/css"),
    "/app.js": ("app.js", "application/javascript"),
    "/practice/": ("index.html", "text/html"),
    "/practice/style.css": ("style.css", "text/css"),
    "/practice/app.js": ("app.js", "application/javascript"),
}
```

(The handler needs to know WHICH directory a given static path serves from — `/` and `/practice/`-prefixed paths resolve to two different directories. The plan will make `_STATIC_FILES` map each path to `(directory, filename, content_type)` instead of just `(filename, content_type)`, updating the one existing static-file branch in `handle_request()` accordingly — a small, mechanical widening, not a behavior change for the three existing entries.)

New API branches, same shape as the existing `/api/inbox`/`/api/confirm`:
```python
    if method == "GET" and path == "/practice/api/inbox":
        messages = router.list_practice_inbox()
        payload = json.dumps({"messages": [
            {"message_id": m.id, "subject": m.subject, "sender": m.sender,
             "sender_email": m.sender_email, "body_text": m.body_text}
            for m in messages
        ]}).encode("utf-8")
        return 200, {}, payload, "application/json"

    if method == "POST" and path == "/practice/api/record":
        data, error = _parse_action_body(body, ("message_id", "decision"))
        if error:
            return error
        router.record_practice_decision(data["message_id"], data["decision"])
        return 200, {}, json.dumps({"ok": True}).encode("utf-8"), "application/json"
```

(Reuses the existing `_parse_action_body` helper — no new duplication.)

### 3. `components/inbox_router/practice_inbox/{index.html,style.css,app.js}` (new)

Same Gmail-styled visual family as `local_ui/` (same CSS variables/layout conventions), but a materially different interaction: no suggested decision shown anywhere, no Confirm/Override — just the email and six plain action buttons (Reply, Forward, Flag, Route to Scope #1, Route to Scope #2, Leave Alone). Picking one calls `POST /practice/api/record` with `{message_id, decision}` and returns to the list — the email stays available to practice on again (no removal from the list, since this isn't a processed-state queue).

### 4. `app_electron/main.js` (un-hide + wire "Launch mockups")

Two changes:
- `TEST_MOCKUPS` gains an `"Inbox Dispatch"` entry — but unlike the existing `form_filling`/`"Sheet-to-Portal Matcher"` entries (which spawn separate real apps), this one needs the local server running first, then opens a browser URL, mirroring the Play-button `ensureLocalServerRunning` + `shell.openExternal` pattern rather than the `spawn`/`shell.openPath` pattern the other two entries use. The plan will give `test-launch-mockups`'s handler a capsule-aware branch (checking `capsule.local_server` the same way `capsule-run` already does) rather than only ever consulting the flat `TEST_MOCKUPS` dict, since Inbox Dispatch's "practice target" isn't a `{type, script/target}` pair the way the other two are — it's a URL on the already-running local server.
- `renderer.js`'s `ppTestGroup.hidden = capsule.kind === "url";` (added earlier today specifically because Inbox Dispatch had nothing to launch) needs to become conditional on whether the capsule now has a practice target — i.e., `hidden = capsule.kind === "url" && !capsule.local_server` stops being sufficient once Inbox Dispatch DOES have something to launch again; the plan will make this explicit rather than leave the now-stale comment/condition in place.

### 5. `tasks/registry.json`

No change needed to the "Inbox Dispatch" entry's existing fields — `local_server` already points at `local_server.py`, which is the same process the practice routes are added to.

## Data flow

1. Un-hidden "Launch mockups" click → `main.js` ensures `local_server.py` is running (same mechanism as Play) → opens `http://localhost:8765/practice/`.
2. Page loads → `GET /practice/api/inbox` → real mock messages, no decisions attached.
3. Person opens one, picks an action → `POST /practice/api/record` → `InboxRouter.record_practice_decision()` → `decision_recorder.record_example()` → a real row in `training_examples.jsonl`.
4. `python components/inbox_router/train_inbox_agent.py` (unchanged) trains on it.
5. Inbox Dispatch (unchanged) is where the trained model's suggestions get reviewed in real use.

## Error handling

- `record_practice_decision()` on an unknown `message_id`: mirrors `confirm_suggestion()`'s existing pattern — `emit("inbox_error", ...)`, returns without raising; the HTTP layer's existing pattern (checking before calling) is not needed here the same way, since there's no "already handled" state to protect against — a genuinely unknown id is the only failure mode, and `get_message()` returning `None` is already the correct signal.
- Malformed JSON / missing keys on `POST /practice/api/record`: reuses the existing `_parse_action_body` 400 path, unchanged.

## Testing

TDD throughout:
- `tests/test_inbox_router.py`: `list_practice_inbox()` returns real messages from a fixture (not just pending ones — confirm it includes something `poll_once()` would have marked processed); `record_practice_decision()` writes a real row via `decision_recorder.load_examples()` and updates the pattern profile; unknown `message_id` emits an error and doesn't raise.
- `tests/test_local_server.py`: `GET /practice/api/inbox` returns real messages; `POST /practice/api/record` writes a real example; malformed body still 400s via the existing shared helper; the widened `_STATIC_FILES` shape still serves all five existing files correctly (three original + two practice-page paths at minimum, `index.html`/`style.css`/`app.js` each in their own directory).
- Full project suite must stay green after every task, per this project's standing rule.

## File layout summary

```
components/inbox_router/
  router.py                 (modified: + list_practice_inbox(), + record_practice_decision())
  local_server.py           (modified: widened _STATIC_FILES shape, + 2 new routes)
  practice_inbox/
    index.html               (new)
    style.css                (new)
    app.js                   (new)
app_electron/
  main.js                    (modified: TEST_MOCKUPS/test-launch-mockups gains a
                              capsule-aware branch for Inbox Dispatch)
  renderer/renderer.js       (modified: ppTestGroup hidden-condition updated)
tests/
  test_inbox_router.py       (extended: list_practice_inbox/record_practice_decision tests)
  test_local_server.py       (extended: /practice/* route tests)
```
