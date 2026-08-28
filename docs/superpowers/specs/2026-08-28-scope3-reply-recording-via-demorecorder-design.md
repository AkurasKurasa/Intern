# Scope #3 Reply Recording via DemoRecorder — Design

## Context

Direct request: "Also any instance of recording has to go through the
Electron application's recording part." Clarified through follow-up
questions to mean, verbatim: *"Play launches the Agent and then every
recording goes through the Recording thing"* and, when asked whether
"the Recording thing" meant DemoRecorder's literal screen + keystroke
capture (the same mechanism Scope #1 uses) or just its visible
Start/Stop UX with a different capture underneath: **"Literally the
same mechanism — screen + keystrokes."**

This supersedes the reply-capture mechanism shipped earlier the same
day (`scope3_learned_autonomous_reply` step 1): a plain `<textarea>` in
`local_ui`, saved via a direct HTTP POST to `/api/confirm`/`/api/override`,
recorded by `reply_recorder.record_reply_example()`. That mechanism is
NOT deleted — `reply_recorder.py`, `reply_examples.jsonl`'s shape,
`reply_features.py`, `reply_model.py`, `train_reply_model.py`, and
`reply_agent.py` all stay exactly as they are. What changes is *how a
reply_recorder.record_reply_example() call gets triggered* — from a
new source (a translated DemoRecorder session) in addition to the
existing direct one. Nothing already trained or already shipped is
thrown away.

## What already exists, checked directly rather than assumed

- **`components/recorder/recorder.py`'s `ScreenObserver`** (the actual
  class behind Electron's "Start recording" button, launched via
  `app/recorder_bridge.py`'s `DemoRecorder` import) already declares a
  `trace_type: "web" | "excel" | "gui"` parameter — `"web"` is an
  existing, documented, but **entirely unimplemented** value. Its real
  observer-selection chain (`__init__`, lines ~463-494) only ever
  branches to `_vision_observer` → `_excel_observer` (gated on
  `trace_type == "excel"`) → `_uia_observer` (generic Windows
  accessibility tree, works for "all apps" but with no browser-DOM
  awareness) → raw OCR fallback. There is no `_web_observer` anywhere
  in this file today.
- **`components/observers/web_observer/web_observer.py`'s
  `WebObserver`** already exists, fully implemented, and already a
  documented but unwired dependency of `TraceTranslator`'s own
  docstring ("Observers (UIAutomationObserver, WebObserver,
  VisionObserver) handle all screen reading"). It connects to a real
  browser via Playwright (`connect(url)`), and `snapshot()` returns a
  trace-compatible state dict: `elements: [...]`, each with `type`,
  `value` (a textarea's real `input_value()` — this is precisely a
  typed reply's text, read exactly, not OCR'd or reconstructed),
  `label`, `enabled`, `visible`. This is architecturally the same
  precision `automate_inbox.py` already relies on (same Playwright
  dependency), just never connected to the Recorder.
- **`ScreenObserver._capture_loop()`** (lines 861-899) calls whichever
  observer is active's `.snapshot()` once per interval tick and stores
  `(timestamp, frame_image, semantic_state)`. `_translate_and_save()`
  (lines 687+) turns consecutive semantic-state pairs into trace JSON
  files via `TraceTranslator.states_to_trace()` — `state_before`,
  `state_after`, inferred `mouse`/`keyboard`/`action` from
  `_derive_action_from()`, one file per step, all under one
  timestamped session folder.
- **`_derive_action_from()`** (lines 566-680+) already reconstructs
  actual typed text from raw keystroke groups, backspace-aware —
  already fixed for real edge cases (empty/malformed key values, lone
  modifier presses). This machinery is reused as-is, unmodified — it
  already does correctly what a naive from-scratch reply-recording
  translator would otherwise have to redo.
- **`WebObserver._extract_elements()`**'s selector
  (`input, select, textarea, button, a[href], [role=...]`) captures
  the reply `<textarea>` and the Confirm/Override buttons in
  `local_ui/index.html`, but **not** `#detailSubject`/`#detailSender`/
  `#detailBody` — those are a plain `<h1>`/`<span>`/`<pre>`, none of
  which match any selector clause or ARIA role today. This is the one
  real gap between "what WebObserver already captures" and "what a
  translator needs to know which email a reply belongs to."

## Design

### 1. Wire `WebObserver` into `ScreenObserver` for real

Add a fourth observer slot to `ScreenObserver.__init__`, checked
**before** `_uia_observer` (an explicit `trace_type == "web"` request
must not silently fall through to the generic, browser-blind UIA
reader):

```python
self._web_observer: Optional[Any] = None
if (self._vision_observer is None and self._excel_observer is None
        and trace_type == "web" and _WEB_OBSERVER_AVAILABLE):
    obs = _WebObserver()
    if obs.connect(browser_url="http://localhost:9222"):  # attach, don't launch a second browser
        self._web_observer = obs
        print("[ScreenObserver] WebObserver connected — browser DOM semantic mode active.")
    else:
        print("[ScreenObserver] WebObserver could not connect — falling back to UIAutomation.")
```

`_capture_loop()` gains one more `elif` branch, same shape as the
existing `_excel_observer`/`_uia_observer` branches:

```python
elif self._web_observer is not None:
    semantic_state = self._web_observer.snapshot()
    self._frames.append((ts, img, semantic_state))
```

**Attaching to an already-open browser, not launching a new one**:
Inbox Dispatch is already running (Play already starts
`local_server.py` and opens the page). `WebObserver.connect()` accepts
`browser_url` for exactly this — attach via Chrome DevTools Protocol
to the browser tab the user is already looking at and already
interacting with, rather than opening a second, invisible browser the
human never touches. This requires the browser Electron opens for
Inbox Dispatch to be launched with `--remote-debugging-port=9222` (or
an assigned free port passed through) — a small, contained change
where Play currently calls `shell.openExternal` for the `kind: "url"`
capsule.

### 2. Make the reply textarea identify which email it belongs to, without new selector logic

`WebObserver._extract_elements()` already reads an element's `name`
attribute into `name` (used for `label`/`text`) — no change needed to
`web_observer.py` itself. `local_ui/app.js`'s `openMessage()` sets
`replyBody.name = email.message_id` (one new line) whenever a message
opens, so the recorded trace step's textarea element carries the
`message_id` as its `label`/`text` field for free, through
machinery that already exists and is already tested. No new DOM
scraping, no new WebObserver capability, no hardcoded selector for
this specific page's IDs.

### 3. New translator: trace session → real reply examples

New module `components/inbox_router/reply_trace_translator.py`,
manually run the same way `train_reply_model.py`/
`bootstrap_from_sent.py` already are (Record and Train stay separate,
explicit steps — matching this project's own established pattern, not
an automatic hook inside `ScreenObserver.stop()`, which stays entirely
task-agnostic on purpose):

```
python components/inbox_router/reply_trace_translator.py --session-dir <path>
```

For each trace step JSON in the session folder, in order:
- Find the reply `<textarea>` element in `state_after["elements"]`
  (`type == "input"`, `control_type == "textarea"`) — its `value` is
  the exact real typed text at that point, its `label`/`text` is the
  `message_id` from step 2.
- Find whether `#confirmBtn` or `#overrideBtn` was the clicked element
  for this step (via `action`/`mouse` — already-inferred click target,
  same shape `_derive_action_from()` already produces for every other
  Scope #1 trace).
- On a step where Confirm/Override was clicked **and** the textarea's
  `value` is non-empty: look up the real email by `message_id` via
  `InboxRouter`'s already-existing history (`routed_history.json`,
  read through the same `MockGmailClient`/`RealGmailClient.get_message()`
  every other real code path already uses — no new lookup mechanism),
  and call the **existing, unmodified**
  `reply_recorder.record_reply_example(message, reply_body, source="live", path=...)`.

This is the same honesty guarantee `reply_recorder.py` already
enforces (blank/whitespace-only text saves nothing) — a step where the
textarea was empty when Confirm was clicked (e.g. a non-reply
decision) produces no example, exactly like today.

### 4. What does NOT change

- `reply_recorder.py`, `reply_features.py`, `reply_model.py`,
  `train_reply_model.py`, `reply_agent.py`, `autonomous_watcher.py`'s
  auto-draft branch — all untouched. They consume
  `reply_examples.jsonl` regardless of which path wrote to it.
- The direct HTTP-based capture (`local_ui`'s textarea → `/api/confirm`
  → `router.confirm_suggestion(..., reply_body=...)`) stays working
  exactly as shipped earlier today. This design adds a second, now
  *required-by-direct-instruction* path; it does not remove the first.
  A person can still type directly if they want to (e.g., testing,
  or `automate_inbox.py`'s dry-run preview), but the sanctioned way to
  *produce real training data* is now: press Start Recording, use
  Inbox Dispatch normally, press Stop, run the translator.
- `automate_inbox.py`'s reply/forward skip-and-leave-pending fix
  (shipped this session) is unaffected either way — it still never
  blank-confirms, regardless of which recording path eventually
  supplies the real text.

## Error handling

- `WebObserver.connect()` failing (browser not reachable at the given
  CDP port, Playwright not installed) falls through to the existing
  `_uia_observer` branch, exactly the same graceful-degradation shape
  `_excel_observer`'s own connect-failure handling already has one
  level up. Recording still produces *something* (raw OCR/UIA trace),
  it just won't yield precise reply text — a human would notice zero
  new lines in `reply_examples.jsonl` after translating, not a crash.
- `reply_trace_translator.py` on a session with no Confirm/Override
  steps carrying real text: writes nothing, prints a plain count
  ("0 real replies found in this session"), never raises.
- A `message_id` from a trace step that no longer resolves to a real
  message (edge case: history rotated/pruned between recording and
  translating) is skipped with a printed warning, not a crash — same
  defensive shape `record_reply_example`'s own try/except callers
  already use elsewhere in this codebase.

## Testing

- `ScreenObserver.__init__`/`_capture_loop()`: new tests mirroring the
  existing `_excel_observer`-gated-by-`trace_type` tests (if any exist
  today — check first) — `trace_type="web"` selects `_web_observer`
  when available and connectable; falls through to UIA when it isn't;
  never touches `_excel_observer`/`_vision_observer`'s own gating.
- `reply_trace_translator.py`: unit tests against a small, hand-built
  fake session folder (2-3 trace step JSONs matching the real schema
  `states_to_trace()` produces) — confirms it extracts exactly the
  steps where Confirm/Override was clicked with non-empty textarea
  value, skips empty-textarea steps, skips unresolvable message_ids
  without raising, and produces the exact same `reply_examples.jsonl`
  row shape `reply_recorder.record_reply_example()` already writes and
  is already tested against.
- `local_ui/app.js`'s new `replyBody.name = email.message_id` line: one
  small addition to the existing `openMessage()` test coverage (if
  any JS-level coverage exists — this codebase's JS is otherwise
  tested only indirectly through Playwright-driven Python tests like
  `test_automate_inbox.py`, so likely covered the same way: a new
  Playwright-driven assertion that `#replyBody`'s `name` attribute
  equals the open message's id).
- End-to-end smoke: record a short real session against
  `local_server.py`'s mock inbox fixture (same fixture
  `test_automate_inbox.py` already builds), translate it, confirm the
  resulting `reply_examples.jsonl` line's `reply_body` matches exactly
  what was "typed" during the fake recording — the same category of
  real, non-mocked verification `scope3_local_ui`'s own smoke test
  used.

## Two decisions resolved here, not left to the plan

**CDP port: assigned free port, not a hardcoded one.** `9222` is
Chrome's own conventional default remote-debugging port — hardcoding
it risks colliding with some other Chrome-based tool the user might
already have running. `main.js` picks a free port the same trivial way
`local_server.py`'s own `DEFAULT_PORT`-with-fallback pattern already
exists for a comparable problem (`Could not start local server on port
{port} (already running?)`), passes `--remote-debugging-port=<port>`
when launching the browser for the Inbox Dispatch capsule, and threads
that same port to `WebObserver(browser_url=f"http://localhost:{port}")`.

**`trace_type="web"` is inferred, not a new toggle.** `main.js` already
tracks which capsule is currently loaded into the Play slot
(`currentCapsuleName`, per `scope3_mockup_workflow_launcher`). When
Start Recording is pressed while the loaded capsule is specifically
the `kind: "url"` Inbox Dispatch capsule, `recorder_bridge.py`'s
`start_recording()` command carries `trace_type="web"` instead of the
otherwise-hardcoded `"form_filling"`. No new UI element — this mirrors
the same by-capsule-kind branching the Play panel's checkbox-group
show/hide already does today.
