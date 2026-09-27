# Scope #3: A real, locally-served Inbox Dispatch UI

Date: 2026-08-26

## Context

Direct request: after testing the Record -> Train -> Output pipeline (see `docs/superpowers/specs/2026-08-25-scope3-record-train-output-design.md`), the user tried the existing "Inbox Dispatch" mockup and asked to "finish it" — clarified as: "I need the user interface to test it on, build it or something."

The existing "Inbox Dispatch" capsule (`tasks/registry.json`, `kind: "url"`) points at a Claude-hosted Artifact: a Gmail-styled page whose Confirm/Override buttons only update the page's own local JavaScript state. It has never called into the real Python backend. This was a deliberate choice at the time (a fast way to showcase the UI concept), but it is a hard wall now: a page hosted on Claude's site runs under a strict content-security policy that blocks it from ever reaching a program on the user's own machine — no exception exists for this, so the current mockup can never become real in place.

This spec covers replacing it with a page that actually is real: served from the user's own machine, calling directly into the real `InboxRouter`/`InboxAgent` pipeline already built.

## Goals

- Confirm/Override clicks in the browser page genuinely call `decision_recorder.record_example()` and the real `InboxAgent.decide()` chain — not a JavaScript simulation.
- Keep the exact same one-click launch experience: clicking the existing Play/Launch button for the Inbox Dispatch capsule still just works, with no new button and no change to how the user finds or triggers it.
- Keep the Gmail-styled look of the existing mockup.
- Stay outside the Electron application's own window, per the standing instruction that this UI opens in the user's regular browser, not embedded as an Electron tab.
- No new third-party dependencies — Python's standard library only, matching this project's existing precedent (e.g. `components/scope2/mocksite/serve.py` also uses stdlib `http.server`).

## Non-goals

- Automatic polling for new emails (explicitly deferred — manual Refresh only, per direct decision).
- Any change to `InboxRouter`, `InboxAgent`, `RuleLayer`, `LLMClassifier`, or any other Tasks 1-7 code's *existing* behavior. The one exception, listed explicitly in Component 1 below, is a single new small public method on `InboxRouter` so `local_server.py` never needs to reach into a private (`_`-prefixed) method from outside the class — everything else about `InboxRouter` stays untouched.
- Wiring a real Gmail account — still validated against `MockGmailClient`, same as the rest of Scope #3's Phase A.
- Navigation (still out of scope, per the earlier pipeline spec).

## Architecture

```
User clicks Play/Launch on the Inbox Dispatch capsule (unchanged UI)
        |
        v
main.js's capsule-run / capsule-run-current handlers (MODIFIED)
  - capsule.kind === "url" AND capsule.local_server is set:
      1. If no local server process is already tracked as running,
         spawn it: `python components/inbox_router/local_server.py`
      2. shell.openExternal(capsule.url)  -- unchanged from today
  - capsule.kind === "url" with no local_server field (a genuinely
    external link, the old shape): behaves exactly as today, no change.
        |
        v
components/inbox_router/local_server.py  (NEW)
  - stdlib http.server.HTTPServer + BaseHTTPRequestHandler, no new deps
  - constructs one real InboxRouter at startup: MockGmailClient,
    PatternProfile, RuleLayer, LLMClassifier -- same construction router.py's
    own main() already does, copied not duplicated in spirit (see below)
  - routes:
      GET  /                -> serves components/inbox_router/local_ui/index.html
      GET  /style.css        -> serves the page's stylesheet
      GET  /app.js            -> serves the page's script
      GET  /api/inbox        -> runs router.poll_once(), returns the
                                 router's current pending entries as JSON
      POST /api/confirm      -> body {message_id, decision} -> calls
                                 router.confirm_suggestion(message_id, decision)
      POST /api/override     -> body {message_id, new_decision, reason} ->
                                 calls router.override_decision(...)
        |
        v
components/inbox_router/local_ui/{index.html, style.css, app.js}  (NEW)
  - same Gmail-styled layout as today's mockup, ported not redesigned
  - "Refresh" button calls GET /api/inbox and re-renders the list
  - Confirm/Override buttons call the real POST endpoints, then re-render
    from the response (no separate local state to keep in sync)
```

## Components

### 1. `components/inbox_router/router.py` (one small addition)

Add one public method to `InboxRouter`, next to its existing `_load_history()`:

```python
def pending_entries(self) -> list:
    """Every history entry still awaiting a Confirm/Override -- the one
    piece of InboxRouter's internal history-reading logic local_server.py
    (a driver outside this class) needs, exposed properly instead of
    reaching into the private _load_history()."""
    return [e for e in self._load_history() if e.get("status") == "pending"]
```

No existing method's behavior changes; this only exposes a read that was already being computed the same way `_find_history_entry()` does today.

### 2. `components/inbox_router/local_server.py` (new)

A standalone script, same category as `router.py` itself but a different driver of the same `InboxRouter` class — `router.py` drives it via stdin/stdout for Electron; this drives it via HTTP for a browser. Both are legitimate, independent front ends onto the same backend object; neither needs to know the other exists.

Construction mirrors `router.py`'s `main()` exactly (same `get_gmail_client()`, `PatternProfile()`, `RuleLayer(profile)`, `_pick_provider()` + `LLMClassifier(...)`), but does **not** call `run_forever()` — no stdin thread, no polling loop, no `emit()`-based event stream. The HTTP handler calls `InboxRouter`'s existing public methods directly:

```python
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self._serve_file("index.html", "text/html")
        elif self.path == "/style.css":
            self._serve_file("style.css", "text/css")
        elif self.path == "/app.js":
            self._serve_file("app.js", "application/javascript")
        elif self.path == "/api/inbox":
            router.poll_once()
            self._json(200, {"pending": router.pending_entries()})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/confirm":
            router.confirm_suggestion(body["message_id"], body["decision"])
            self._json(200, {"ok": True})
        elif self.path == "/api/override":
            router.override_decision(body["message_id"], body["new_decision"], body.get("reason", ""))
            self._json(200, {"ok": True})
        else:
            self.send_error(404)
```

(Exact method bodies are illustrative; the implementation plan will pin down every helper — `_serve_file`, `_json`, port selection, and the "don't start a second server if one is already listening" check via a simple `try: bind` / `except OSError: assume already running` pattern.)

`GET /api/inbox` calling `poll_once()` is the "Refresh" semantics: no background thread, no timer — a request to this endpoint is the entire trigger.

### 3. `components/inbox_router/local_ui/{index.html, style.css, app.js}` (new)

Ported from the existing Claude Artifact's HTML/CSS/JS (same visual design — row list, click-to-open message view, Confirm/Override buttons inside the opened message), with the JavaScript layer changed from local-array mutation to real `fetch()` calls:

- On load and on "Refresh" click: `fetch("/api/inbox")`, render the returned list.
- Confirm button: `fetch("/api/confirm", {method: "POST", body: JSON.stringify({message_id, decision})})`, then re-fetch `/api/inbox` to reflect the real updated state.
- Override button: same shape, posting to `/api/override`.

No local simulation state survives this port — every displayed value comes from a real server response.

### 4. `tasks/registry.json` (modified)

Inbox Dispatch's entry:
```json
{
  "name": "Inbox Dispatch",
  "description": "Real, locally-served email triage UI for Scope #3 (Inbox Router)",
  "model_path": "",
  "trigger_keywords": [],
  "trigger_apps": [],
  "trace_dir": "",
  "created": "2026-08-23T00:00:00",
  "emoji": "📬",
  "kind": "url",
  "url": "http://localhost:8765/",
  "local_server": "components/inbox_router/local_server.py"
}
```

`local_server` is a new, optional `WorkflowCapsule` field (empty string default) — only set for a `kind="url"` capsule whose URL needs a locally-spawned process first. A genuinely external `kind="url"` link (if one is ever added later) simply leaves this field empty, and the existing `shell.openExternal()`-only behavior applies unchanged — this keeps the feature generic rather than hardcoded to Inbox Dispatch specifically, matching the project's "don't hard-code for tasks" rule.

### 5. `components/agent/capsule.py` (modified)

Add `local_server: str = ""` to `WorkflowCapsule`, same pattern as the existing `url: str = ""` field — a plain optional dataclass field, no behavior of its own (the behavior lives in `main.js`, which already owns all `kind="url"` launch logic).

### 6. `app_electron/main.js` (modified)

`capsule-run` and `capsule-run-current` handlers: before the existing `if (capsule.url) shell.openExternal(capsule.url);` line, add — only when `capsule.local_server` is set — a check-and-spawn step:

```javascript
if (capsule.local_server) {
  ensureLocalServerRunning(capsule.local_server);
}
if (capsule.url) shell.openExternal(capsule.url);
```

`ensureLocalServerRunning(scriptPath)` tracks a single module-level handle (e.g. `let localServerProcess = null;`) — if already set and the process is still alive, do nothing; otherwise `spawn(pythonExe, [path.join(REPO_ROOT, scriptPath)], { detached: true, stdio: "ignore" })` (same `spawn`/`resolvePython()` pattern `main.js` already uses for the "Launch mockups" feature) and store the handle. No explicit stop path is needed for this pass — the server is a lightweight local process that can keep running across multiple Launch clicks without harm (repeated `poll_once()` calls are idempotent, per the existing `mark_processed()` design).

## Data flow

1. User clicks Play/Launch on Inbox Dispatch → `main.js` spawns `local_server.py` if not already running, then opens the browser to `http://localhost:8765/`.
2. Page loads, immediately calls `GET /api/inbox` → server runs `router.poll_once()` against `MockGmailClient` → returns pending entries.
3. User clicks Confirm/Override → real `POST` request → `InboxRouter`'s real methods run → `decision_recorder.record_example()` fires exactly as it does through the Electron-driven path (Task 7's wiring, untouched) → training data accumulates for real.
4. Page re-fetches `/api/inbox` to show the updated state.

This means: using this UI is now functionally identical to using the Electron app's own Inbox tab, for the purpose of the Record step — either one works, and both write to the same real `training_examples.jsonl`.

## Error handling

- Port already in use (a previous server instance still running): `HTTPServer` binding failure — script should catch `OSError` and log a clear message rather than crashing with a raw traceback, since `main.js`'s "don't spawn a second one" check may race a manually-started instance.
- A confirm/override for an unknown `message_id`: `InboxRouter`'s existing methods already `emit("inbox_error", ...)` — that side effect is harmless here (goes to the server process's own stdout, not the page), but the HTTP handler must still return a clear error status (`400`) rather than a generic `200 {"ok": true}`, so the page can show a real error instead of silently pretending success.
- Malformed JSON body on a POST: return `400` with a short message, don't crash the handler thread.

## Testing

TDD throughout:
- `local_server.py`'s route-handling logic factored so it's testable without opening a real socket where practical (e.g., a small internal function that takes a parsed request and the `InboxRouter` instance and returns a response tuple) — mirroring how this project already separates protocol-handling from business logic elsewhere (e.g., `router.py`'s own `_classify_and_record()` vs. its stdin-loop wrapper).
- Where a real socket is the only honest way to test (actual HTTP semantics, headers, status codes), spin up the server on an ephemeral port (`port=0`) in the test and hit it with `urllib.request` — same category as this project's own precedent of preferring real behavior over mocks.
- Cover: `GET /api/inbox` returns real pending entries after a real `poll_once()`; `POST /api/confirm` actually writes to `decision_recorder`'s file and updates `routed_history.json`; unknown `message_id` returns `400`; malformed JSON returns `400` without crashing.
- `app_electron/main.js`'s `ensureLocalServerRunning` — no existing JS test suite exists for `main.js` in this project (confirmed: no test files reference it); manual verification (launch, confirm a second Launch click doesn't spawn a duplicate process) is the established precedent for this file, matching how the earlier `kind="url"` feature and the `ppTestGroup` CSS fix were both verified.

## File layout summary

```
components/inbox_router/
  router.py               (modified: + pending_entries() public method)
  local_server.py         (new)
  local_ui/
    index.html            (new)
    style.css             (new)
    app.js                (new)
components/agent/capsule.py    (modified: + local_server field)
app_electron/main.js           (modified: ensureLocalServerRunning + 2 call sites)
tasks/registry.json             (modified: Inbox Dispatch's url + local_server)
tests/
  test_local_server.py    (new)
```
