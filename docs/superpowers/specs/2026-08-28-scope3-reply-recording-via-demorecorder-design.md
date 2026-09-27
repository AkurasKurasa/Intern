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

**Correction made while writing the plan, not left in from the first
draft:** this design originally targeted `ScreenObserver` — wrong.
`app/recorder_bridge.py`'s `Bridge.start()` (the code Electron's "Start
recording" button actually calls) constructs `recorder.recorder`'s
**`DemoRecorder`** class (line 1320), a separate, more sophisticated
mechanism from `ScreenObserver`: not time-interval polling, but
**action-triggered** — pynput listeners fire on every real mouse click
or keyboard group, and each fires an immediate before/after snapshot
via `self._observer.snapshot()`. `ScreenObserver` exists in the same
file and is real, tested code, but it is not what Electron's Recording
button uses, so it is out of scope for this design entirely.

- **`DemoRecorder.__init__`** (`components/recorder/recorder.py:1346`)
  hard-requires `_UIA_OBSERVER_AVAILABLE` (raises `ImportError`
  otherwise) and unconditionally sets `self._observer =
  _UIAObserver(background_apps={"notepad", ".txt"})`. `trace_type` is
  accepted as a constructor argument but today is **pure metadata** —
  a string written into saved output, never used to select which
  observer runs. There is no per-`trace_type` branching in this class
  at all, and `WebObserver` is not imported anywhere in
  `recorder.py` — not even the lazy `try/except ImportError` pattern
  every other observer (`_ExcelObserver`, `_UIAObserver`,
  `_CVVisionObserver`, lines 69-97) already follows.
- **Every downstream consumer of `self._observer` only ever calls
  `.snapshot()`** (`_request_snapshot()`, line 1421, and its one
  non-subprocess call site `self._observer.snapshot()`, line 1458) and
  treats the result as an opaque dict. `WebObserver.snapshot()`
  already returns, by its own docstring, "the same dict format as
  UIAutomationObserver so the rest of the pipeline needs no changes" —
  confirmed directly against `WebObserver._capture()`'s actual return
  shape (`elements`, `application`, `window_title`, etc.), which
  matches. This means swapping `self._observer` to a `WebObserver`
  instance for `trace_type == "web"` is a genuinely small, contained
  change — nothing downstream of the assignment needs to know or care
  which concrete class it's holding.
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
- **`WebObserver._extract_elements()`**'s selector
  (`input, select, textarea, button, a[href], [role=...]`) captures
  the reply `<textarea>` and the Confirm/Override buttons in
  `local_ui/index.html`, but **not** `#detailSubject`/`#detailSender`/
  `#detailBody` — those are a plain `<h1>`/`<span>`/`<pre>`, none of
  which match any selector clause or ARIA role today. This is the one
  real gap between "what WebObserver already captures" and "what a
  translator needs to know which email a reply belongs to."

## Design

### 1. Give `DemoRecorder` a real `trace_type == "web"` path

**Import**, mirroring the exact existing lazy pattern (lines 69-97)
for the other three observers:

```python
try:
    from observers.web_observer import WebObserver as _WebObserver
    _WEB_OBSERVER_AVAILABLE = True
except ImportError:
    try:
        from components.observers.web_observer import WebObserver as _WebObserver
        _WEB_OBSERVER_AVAILABLE = True
    except ImportError:
        _WEB_OBSERVER_AVAILABLE = False
```

**`DemoRecorder.__init__`** (`recorder.py:1346`) gains one new
parameter, `url: str = ""` — the page to open and record against, only
meaningful when `trace_type == "web"`. The existing unconditional
`_UIA_OBSERVER_AVAILABLE` guard and unconditional `_UIAObserver(...)`
construction both become the **non-web** branch of a new if/else — the
`"web"` branch requires `_WEB_OBSERVER_AVAILABLE` and a non-empty
`url` instead, and constructs a real, visible (`headless=False`)
`WebObserver`, connecting it immediately:

```python
if trace_type == "web":
    if not _WEB_OBSERVER_AVAILABLE:
        raise ImportError("WebObserver not found in components/observers/.")
    if not url:
        raise ValueError('trace_type="web" requires a url to record against.')
    self._observer = _WebObserver(headless=False)
    if not self._observer.connect(url=url):
        raise RuntimeError(f"WebObserver could not connect to {url!r}.")
else:
    if not _UIA_OBSERVER_AVAILABLE:
        raise ImportError("UIAutomationObserver not found in components/observers/.")
    try:
        self._observer = _UIAObserver(background_apps={"notepad", ".txt"})
    except TypeError:
        self._observer = _UIAObserver()
```

Nothing else in `DemoRecorder` changes — `_request_snapshot()`,
`_on_click()`/`_on_key_press()`, the action queue, F10 save — all
already call `self._observer.snapshot()` generically and are
unaffected by which concrete class that is (see the compatible-dict-
shape finding above).

**Cleanup**: `run()`'s existing `finally:` block (~line 1552, where
listeners are stopped and pending actions flushed) gains one line —
`if trace_type == "web": self._observer.disconnect()` — so the
recording browser window closes itself once F10/Stop ends the
session, rather than lingering as an orphaned window.

**Launches its own browser — does not attach to one Electron already
opened.** An earlier draft of this design (written before finding that
`DemoRecorder`, not `ScreenObserver`, is the real target) planned to
attach via Chrome DevTools Protocol to whatever browser Play's
`shell.openExternal` had already opened. That's wrong on inspection:
`shell.openExternal` hands a URL to the OS's default browser handler —
Electron gets no launch flags, no `--remote-debugging-port`, and no
guarantee the result is even Chromium. `WebObserver.connect(url=...)`
with no `browser_url` already does exactly the right thing with zero
new code: it launches its **own** real, visible Chromium via
Playwright (the identical mechanism `automate_inbox.py` already uses
today) and navigates straight to the given `url`. No CDP port, no
flag-threading through `main.js`, no dependency on however the OS's
default browser happens to be configured. The human interacts with
that window; the raw screen+keystroke capture (pynput, system-wide)
records it exactly the same as any other window.

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
an automatic hook inside `DemoRecorder`, which stays entirely
task-agnostic on purpose):

```
python components/inbox_router/reply_trace_translator.py --session-dir <path>
```

**Each saved step is `live_step_NNNN.json`** (`recorder.py:2234-2243`,
confirmed by reading the actual write, not assumed from
`ScreenObserver`'s different shape): `{"trace_id", "timestamp",
"duration", "type", "state": <before>, "mouse", "keyboard",
"next_state": <after>}` — `state`/`next_state` here, **not**
`state_before`/`state_after` (that naming belongs to a different,
unused-for-this-feature class). Both `state` and `next_state` are
`_fmt_state()`-projected dicts with an `elements` list whose per-element
shape (`element_id`, `type`, `control_type`, `bbox`, `text`, `value`,
`label`) is identical whether it came from `UIAutomationObserver` or
`WebObserver` — confirmed above.

**Click-target matching by pixel position doesn't work here — checked,
not assumed.** `_on_click()` (`recorder.py:1841`) records raw
`click_pos` in absolute physical screen coordinates from pynput.
`WebObserver._extract_elements()`'s `bbox` comes from Playwright's
`bounding_box()`, which is relative to the page **viewport**, not the
screen — the two are not directly comparable without also knowing the
browser window's on-screen position and chrome height, which nothing
here currently tracks. Rather than solve that coordinate-system
problem, the translator uses a **state-transition signal instead of a
click-target lookup**, avoiding it entirely:

For each step, in order, look at `next_state["elements"]` for the
reply `<textarea>` (`control_type == "textarea"`) — its `value` is the
exact real typed text at that point (Playwright's own
`input_value()`, not reconstructed from keystrokes), its `label`/`text`
is the `message_id` from Design §2. A step counts as **a submitted
reply** when:
1. This step's `state["elements"]` has that textarea with a non-empty
   `value`, **and**
2. The *next* step's `state["elements"]` no longer contains that same
   `message_id`-labeled textarea at all (Confirm/Override closes the
   detail view and returns to the list — the textarea element simply
   isn't present in the next snapshot), **or** this is the last step
   in the session (Stop was pressed right after submitting).

On a step matching that pattern: look up the real email by
`message_id` via `InboxRouter`'s already-existing history
(`routed_history.json`, read through the same
`MockGmailClient`/`RealGmailClient.get_message()` every other real
code path already uses — no new lookup mechanism), and call the
**existing, unmodified**
`reply_recorder.record_reply_example(message, reply_body, source="live", path=...)`.

This is the same honesty guarantee `reply_recorder.py` already
enforces (blank/whitespace-only text saves nothing) — a step where the
textarea was empty produces no example, exactly like today.

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

- `WebObserver.connect()` failing (Playwright not installed, or the
  browser fails to launch) raises `RuntimeError` from
  `DemoRecorder.__init__` immediately — it does **not** silently fall
  back to `UIAutomationObserver`. An explicit `trace_type="web"`
  request that can't actually observe the web page would otherwise
  record a UIA trace of some unrelated foreground window and call it a
  "web recording" — worse than failing loudly, since it produces a
  session that looks complete but translates to zero real reply
  examples with no explanation why. `Bridge.start()` catches this the
  same way it already catches any other recorder construction failure
  (`except Exception as exc: emit("error", message=f"Failed to start
  recorder: {exc}")`, `recorder_bridge.py:150-152` — no new error
  path needed).
- `reply_trace_translator.py` on a session with no Confirm/Override
  steps carrying real text: writes nothing, prints a plain count
  ("0 real replies found in this session"), never raises.
- A `message_id` from a trace step that no longer resolves to a real
  message (edge case: history rotated/pruned between recording and
  translating) is skipped with a printed warning, not a crash — same
  defensive shape `record_reply_example`'s own try/except callers
  already use elsewhere in this codebase.

## Testing

- `DemoRecorder.__init__`: new tests (check for existing
  `DemoRecorder.__init__` test coverage first, mirror its style) —
  `trace_type="web"` with a reachable `url` selects a `WebObserver` as
  `self._observer`; `trace_type="web"` with no `url` raises
  `ValueError`; `trace_type="web"` when `WebObserver.connect()` fails
  raises `RuntimeError` (does not silently fall back — an explicit
  request for web recording that can't actually record web state
  should fail loudly, not quietly produce a UIA trace of the wrong
  window); every non-"web" `trace_type` behaves exactly as today
  (`_UIAObserver` constructed, unaffected by the new branch).
- `reply_trace_translator.py`: unit tests against a small, hand-built
  fake session folder (2-3 `live_step_NNNN.json` files matching the
  real schema `DemoRecorder` actually writes, §3) — confirms it
  extracts exactly the steps matching the submitted-reply
  state-transition pattern (non-empty textarea value, then that
  textarea's `message_id` absent from the next step), skips
  empty-textarea steps, skips a textarea that's still present in the
  next step (detail view stayed open — nothing was submitted), skips
  unresolvable `message_id`s without raising, and produces the exact
  same `reply_examples.jsonl` row shape
  `reply_recorder.record_reply_example()` already writes and is
  already tested against.
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

## One decision resolved here, not left to the plan

**`trace_type="web"` (and the recording `url`) is inferred, not a new
toggle.** `main.js` already tracks which capsule is currently loaded
into the Play slot (`currentCapsuleName`, per
`scope3_mockup_workflow_launcher`). When Start Recording is pressed
while the loaded capsule is specifically the `kind: "url"` Inbox
Dispatch capsule, the `start` command sent to `recorder_bridge.py`
carries `trace_type="web"` and `url=capsule.url`; `Bridge.start()`
(`app/recorder_bridge.py:141`, the real method — its current signature
is `start(self, output_dir: str | None = None)`, gaining `trace_type:
str = "form_filling"` and `url: str = ""` params) passes both straight
through to `DemoRecorder(output_dir=self._out_dir,
trace_type=trace_type, url=url)` in place of today's hardcoded
`trace_type="form_filling"` call. No new UI element — this mirrors the
same by-capsule-kind branching the Play panel's checkbox-group
show/hide already does today. `Bridge.start()` must call
`ensure_local_server_running()` (reusing `automate_inbox.py`'s
existing helper of the same shape, not a second copy of it) before
constructing `DemoRecorder` with `trace_type="web"`, so the page
actually exists for `WebObserver` to navigate to.
