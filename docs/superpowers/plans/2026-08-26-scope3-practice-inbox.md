# Scope #3 Practice Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Scope #3 the practice/demonstration environment Scope #1 (the insurance form) and Scope #2 (the mock portal) already have — a page where a person freely decides what to do with an email, with no AI suggestion involved, generating real training examples.

**Architecture:** Two new public methods on the existing `InboxRouter` (no change to its existing behavior), two new routes on the existing `local_server.py` (same running process, no second server), one new plain HTML/CSS/JS page, and the existing "Launch mockups" button (currently hidden for Inbox Dispatch) wired to open it.

**Tech Stack:** Python standard library only (no new dependencies), vanilla HTML/CSS/JS, the existing Electron `main.js`/`renderer.js`.

**Spec:** `docs/superpowers/specs/2026-08-26-scope3-practice-inbox-design.md`

## Global Constraints

- Branch: `feature/scope3-record-train-output` (already checked out).
- No new third-party dependencies, Python or JS.
- No change to `InboxAgent`, `RuleLayer`, `LLMClassifier`, `PatternProfile`, `train_inbox_agent.py`, `decision_recorder.py`, or `local_ui/` (Inbox Dispatch's own page) — purely additive alongside them.
- `InboxRouter` gains exactly two new public methods (`list_practice_inbox`, `record_practice_decision`) — no change to any of its existing methods' behavior.
- No AI reasoning (`RuleLayer`/`LLMClassifier`) anywhere in the practice-inbox flow — a practice decision is a raw human demonstration, recorded exactly like every other example via `decision_recorder.record_example()`.
- Full project test suite must stay green after every task.
- Commit after every task, per this project's standing rule.

---

### Task 1: `InboxRouter.list_practice_inbox()` and `record_practice_decision()`

**Files:**
- Modify: `components/inbox_router/router.py`
- Test: `tests/test_inbox_router.py`

**Interfaces:**
- Produces: `InboxRouter.list_practice_inbox() -> list` (list of `EmailMessage` objects), `InboxRouter.record_practice_decision(message_id: str, decision: str) -> None`. Both consumed by Task 2 (`local_server.py`'s new routes call them directly).

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_inbox_router.py, as a new class (reuses the existing
# _write_fixture/_msg module-level helpers already at the top of the file):
class TestPracticeInbox:
    def _build(self, tmp_path, inbox=None, sent=None):
        _write_fixture(tmp_path / "data", inbox=inbox or [], sent=sent or [])
        client = MockGmailClient(data_dir=str(tmp_path / "data"))
        profile = PatternProfile(path=str(tmp_path / "data" / "profile.json"))
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps({"capsules": []}), encoding="utf-8")
        rules = RuleLayer(profile, registry_path=str(registry_path))
        classifier = LLMClassifier(provider="none")
        history_path = str(tmp_path / "data" / "routed_history.json")
        return InboxRouter(client, profile, rules, classifier, history_path=history_path,
                            inbox_checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"),
                            examples_path=str(tmp_path / "data" / "training_examples.jsonl"))

    def test_list_practice_inbox_returns_all_messages_unfiltered(self, tmp_path):
        router = self._build(tmp_path, inbox=[
            _msg("i1", "stranger@x.com", "first"),
            _msg("i2", "stranger@x.com", "second"),
        ])
        # Mark one as already processed via the real triage flow -- practice
        # mode must still show it, unlike poll_once()'s unprocessed-only view.
        router.poll_once()
        messages = router.list_practice_inbox()
        assert {m.id for m in messages} == {"i1", "i2"}

    def test_record_practice_decision_writes_a_real_example(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "stranger@x.com", "hello", body="real body text")])
        router.record_practice_decision("i1", "reply")

        examples = load_examples(path=str(tmp_path / "data" / "training_examples.jsonl"))
        assert len(examples) == 1
        assert examples[0]["message_id"] == "i1"
        assert examples[0]["decision"] == "reply"
        assert examples[0]["source"] == "live"
        assert examples[0]["body_text"] == "real body text"

    def test_record_practice_decision_updates_pattern_profile(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "boss@work.com", "hello")])
        router.record_practice_decision("i1", "reply")

        pattern = router._profile.pattern_for("boss@work.com")
        assert pattern is not None
        assert pattern.reply_count == 1

    def test_record_practice_decision_unknown_message_id_does_not_raise(self, tmp_path):
        router = self._build(tmp_path)
        router.record_practice_decision("does-not-exist", "reply")  # must not raise
        examples = load_examples(path=str(tmp_path / "data" / "training_examples.jsonl"))
        assert examples == []
```

Check the top of `tests/test_inbox_router.py` for its existing imports (`json`, `MockGmailClient`, `PatternProfile`, `RuleLayer`, `LLMClassifier`, `InboxRouter`) — reuse them. Add `from decision_recorder import load_examples` if not already imported.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inbox_router.py::TestPracticeInbox -v`
Expected: FAIL with `AttributeError: 'InboxRouter' object has no attribute 'list_practice_inbox'`

- [ ] **Step 3: Add the two methods to `InboxRouter`**

In `components/inbox_router/router.py`, add these two methods right after the existing `pending_entries()` method:

```python
    def list_practice_inbox(self) -> list:
        """Every mock inbox message available to practice-demonstrate on,
        unfiltered by processed state -- unlike poll_once()'s
        list_inbox_unprocessed(), practice mode is meant to be repeatable,
        not a one-shot triage queue. Wraps the same list_recent_inbox()
        bootstrap() already uses for a wide lookback window."""
        since_iso = "2020-01-01T00:00:00+00:00"  # effectively "everything" for the mock fixture
        return self._gmail.list_recent_inbox(since_iso)

    def record_practice_decision(self, message_id: str, decision: str) -> None:
        """A raw human demonstration -- no AI suggestion involved anywhere,
        the opposite of confirm_suggestion()/override_decision(). Fetches
        the real message and records it exactly like every other recorded
        example, via the same decision_recorder.record_example() call.
        Also folds into the sender-pattern profile the same way a real
        confirm does, since a genuine demonstration is at least as strong
        a signal as a confirm."""
        message = self._gmail.get_message(message_id)
        if message is None:
            emit("inbox_error", message=f"Unknown message id: {message_id}")
            return
        record_example(message, decision, source="live", path=self._examples_path)
        self._profile.record_confirmed_decision(message, decision)
```

(`record_example`, `emit`, and `self._examples_path` all already exist in this file — `record_example` via the existing `from decision_recorder import DEFAULT_EXAMPLES_PATH, record_example` import at the top, `emit` as the module's own function, `self._examples_path` set in `__init__` from Task 1 of the earlier local-UI plan.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_inbox_router.py::TestPracticeInbox -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Run the full suite and commit**

Run: `pytest -q`
Expected: PASS, 0 failed

```bash
git add components/inbox_router/router.py tests/test_inbox_router.py
git commit -m "Add InboxRouter.list_practice_inbox() and record_practice_decision()"
```

---

### Task 2: `local_server.py` — practice inbox routes

**Files:**
- Modify: `components/inbox_router/local_server.py`
- Test: `tests/test_local_server.py`

**Interfaces:**
- Consumes: `InboxRouter.list_practice_inbox()`, `InboxRouter.record_practice_decision()` (Task 1).
- Produces: two new routes, `GET /practice/api/inbox` and `POST /practice/api/record`, plus three new static-file paths (`/practice/`, `/practice/style.css`, `/practice/app.js`) — all consumed by Task 3 (the practice page itself, served from these routes) and manually by Task 4/5 (the Launch-mockups wiring points a browser at `/practice/`).

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_local_server.py (reuses the existing _build_router/_msg
# helpers already defined in that file):
class TestPracticeInboxRoutes:
    def test_get_practice_api_inbox_returns_all_messages(self, tmp_path):
        router = _build_router(tmp_path, inbox=[
            _msg("i1", "stranger@x.com", "first"),
            _msg("i2", "stranger@x.com", "second"),
        ])
        status, _headers, body, content_type = ls.handle_request("GET", "/practice/api/inbox", b"", router)
        assert status == 200
        assert content_type == "application/json"
        payload = json.loads(body)
        assert {m["message_id"] for m in payload["messages"]} == {"i1", "i2"}

    def test_post_practice_record_writes_a_real_example(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "hello")])
        body = json.dumps({"message_id": "i1", "decision": "leave_alone"}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/practice/api/record", body, router)
        assert status == 200
        assert json.loads(resp_body) == {"ok": True}
        assert len(router.list_practice_inbox()) == 1  # message still there, not removed

    def test_post_practice_record_malformed_json_returns_400(self, tmp_path):
        router = _build_router(tmp_path)
        status, _headers, _body, _ct = ls.handle_request("POST", "/practice/api/record", b"not json", router)
        assert status == 400


class TestPracticeStaticFiles:
    def test_practice_index_html_is_served(self, tmp_path):
        router = _build_router(tmp_path)
        status, _headers, body, content_type = ls.handle_request("GET", "/practice/", b"", router)
        assert status == 200
        assert content_type == "text/html"
        assert b"<html" in body.lower()

    def test_practice_style_css_is_served(self, tmp_path):
        router = _build_router(tmp_path)
        status, _headers, _body, content_type = ls.handle_request("GET", "/practice/style.css", b"", router)
        assert status == 200
        assert content_type == "text/css"

    def test_practice_app_js_is_served(self, tmp_path):
        router = _build_router(tmp_path)
        status, _headers, body, content_type = ls.handle_request("GET", "/practice/app.js", b"", router)
        assert status == 200
        assert content_type == "application/javascript"
        assert b"/practice/api" in body

    def test_original_index_html_still_serves_after_static_files_widened(self, tmp_path):
        # Regression guard for the _STATIC_FILES shape change in this task --
        # confirms widening the map to (directory, filename, content_type)
        # didn't break the three pre-existing entries.
        router = _build_router(tmp_path)
        status, _headers, body, content_type = ls.handle_request("GET", "/", b"", router)
        assert status == 200
        assert content_type == "text/html"
        assert b"<html" in body.lower()
```

Check the top of `tests/test_local_server.py` for its existing imports (`ls` as the `local_server` module alias, `_build_router`, `_msg`, `json`) — reuse them.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_local_server.py::TestPracticeInboxRoutes tests/test_local_server.py::TestPracticeStaticFiles -v`
Expected: FAIL — the practice routes don't exist yet (404s), and `test_original_index_html_still_serves_after_static_files_widened` should currently PASS already (it's a regression guard for a change not yet made) — that's fine, it stays passing throughout this task.

- [ ] **Step 3: Widen `_STATIC_FILES` and add the two new routes**

In `components/inbox_router/local_server.py`, change:

```python
_UI_DIR = os.path.join(_THIS_DIR, "local_ui")

_STATIC_FILES = {
    "/": ("index.html", "text/html"),
    "/style.css": ("style.css", "text/css"),
    "/app.js": ("app.js", "application/javascript"),
}
```

to:

```python
_UI_DIR = os.path.join(_THIS_DIR, "local_ui")
_PRACTICE_UI_DIR = os.path.join(_THIS_DIR, "practice_inbox")

_STATIC_FILES = {
    "/": (_UI_DIR, "index.html", "text/html"),
    "/style.css": (_UI_DIR, "style.css", "text/css"),
    "/app.js": (_UI_DIR, "app.js", "application/javascript"),
    "/practice/": (_PRACTICE_UI_DIR, "index.html", "text/html"),
    "/practice/style.css": (_PRACTICE_UI_DIR, "style.css", "text/css"),
    "/practice/app.js": (_PRACTICE_UI_DIR, "app.js", "application/javascript"),
}
```

Change the static-file branch inside `handle_request()` from:

```python
    if method == "GET" and path in _STATIC_FILES:
        filename, content_type = _STATIC_FILES[path]
        file_path = os.path.join(_UI_DIR, filename)
        if not os.path.isfile(file_path):
            return 404, {}, b"Not found", "text/plain"
        with open(file_path, "rb") as f:
            return 200, {}, f.read(), content_type
```

to:

```python
    if method == "GET" and path in _STATIC_FILES:
        directory, filename, content_type = _STATIC_FILES[path]
        file_path = os.path.join(directory, filename)
        if not os.path.isfile(file_path):
            return 404, {}, b"Not found", "text/plain"
        with open(file_path, "rb") as f:
            return 200, {}, f.read(), content_type
```

Then add two new branches to `handle_request()`, right after the existing `/api/override` branch and before the final `return 404, ...` fallback:

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_local_server.py -v`
Expected: PASS (all tests in the file, including the new ones — note `TestPracticeStaticFiles`'s `index.html`/`style.css`/`app.js` tests for `/practice/*` paths will still 404 at this point since `practice_inbox/` doesn't have real files yet — Task 3 creates them. Only run `TestPracticeInboxRoutes` and `test_original_index_html_still_serves_after_static_files_widened` as the actual gate for this task; the other `TestPracticeStaticFiles` tests are written now but expected to stay red until Task 3, same pattern the earlier local-UI plan used for its own static files.)

Run instead, to confirm just this task's real scope: `pytest tests/test_local_server.py::TestPracticeInboxRoutes tests/test_local_server.py::TestPracticeStaticFiles::test_original_index_html_still_serves_after_static_files_widened -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite and commit**

Run: `pytest -q`
Expected: 3 failures (`test_practice_index_html_is_served`, `test_practice_style_css_is_served`, `test_practice_app_js_is_served` — these need `practice_inbox/`'s real files, which Task 3 creates), 0 other failures. This is a deliberately staged red state, not a regression — the earlier local-UI plan used this exact same pattern (a task's own static-file tests intentionally left red until the next task completes them). Note the 3 expected failures explicitly in your commit message or report so it's clear they're expected, not a surprise.

```bash
git add components/inbox_router/local_server.py tests/test_local_server.py
git commit -m "Add Scope #3 practice-inbox routes to local_server.py"
```

---

### Task 3: The practice inbox page

**Files:**
- Create: `components/inbox_router/practice_inbox/index.html`
- Create: `components/inbox_router/practice_inbox/style.css`
- Create: `components/inbox_router/practice_inbox/app.js`

**Interfaces:**
- Consumes: `local_server.py`'s `/practice/`, `/practice/style.css`, `/practice/app.js`, `/practice/api/inbox`, `/practice/api/record` routes (Task 2), via `fetch()` from `app.js`.
- Produces: nothing further tasks import — this is the plan's front-end leaf.

- [ ] **Step 1: Confirm the two static-file tests from Task 2 currently fail**

Run: `pytest tests/test_local_server.py::TestPracticeStaticFiles -v`
Expected: `test_practice_index_html_is_served`, `test_practice_style_css_is_served`, `test_practice_app_js_is_served` FAIL (404s); `test_original_index_html_still_serves_after_static_files_widened` PASSES (already green from Task 2).

- [ ] **Step 2: Write `index.html`**

```html
<!-- components/inbox_router/practice_inbox/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Inbox Practice</title>
  <link rel="stylesheet" href="/practice/style.css">
</head>
<body>
  <header class="topbar">
    <span class="brand">📥 Inbox Practice</span>
    <button id="refreshBtn" class="btn btn-primary">Refresh</button>
  </header>

  <main>
    <div id="listView">
      <p id="emptyState" class="empty-state" hidden>No emails to practice on. Click Refresh.</p>
      <ul id="rowList" class="row-list"></ul>
    </div>

    <div id="detailView" class="detail-view" hidden>
      <button id="backBtn" class="btn-back">&larr; Back</button>
      <div class="detail-header">
        <div class="avatar" id="detailAvatar"></div>
        <div>
          <div class="detail-sender" id="detailSender"></div>
          <div class="detail-subject" id="detailSubject"></div>
        </div>
      </div>
      <pre class="detail-body" id="detailBody"></pre>

      <p class="detail-hint">What would you do with this email?</p>
      <div class="action-grid">
        <button class="btn btn-action" data-decision="reply">Reply</button>
        <button class="btn btn-action" data-decision="forward">Forward</button>
        <button class="btn btn-action" data-decision="route_scope1">Route to Scope #1</button>
        <button class="btn btn-action" data-decision="route_scope2">Route to Scope #2</button>
        <button class="btn btn-action" data-decision="flag">Flag</button>
        <button class="btn btn-action" data-decision="leave_alone">Leave alone</button>
      </div>
      <p id="detailStatus" class="status-line"></p>
    </div>
  </main>

  <script src="/practice/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Write `style.css`**

```css
/* components/inbox_router/practice_inbox/style.css */
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: Roboto, "Segoe UI", Arial, sans-serif;
  background: #ffffff;
  color: #202124;
}
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 20px; border-bottom: 1px solid #e0e0e0;
}
.brand { font-size: 18px; font-weight: 500; }
.btn {
  border: none; border-radius: 18px; padding: 8px 20px;
  font-size: 14px; cursor: pointer;
}
.btn-primary { background: #1a73e8; color: #fff; }
.btn-primary:hover { background: #1558b0; }
.btn-action {
  background: #fff; color: #1a73e8; border: 1px solid #1a73e8;
  padding: 10px 14px;
}
.btn-action:hover { background: #f0f6ff; }
.btn-back { background: none; border: none; font-size: 14px; cursor: pointer; color: #1a73e8; margin-bottom: 12px; }

main { max-width: 760px; margin: 0 auto; padding: 12px 20px; }

.row-list { list-style: none; margin: 0; padding: 0; }
.row-item {
  display: flex; gap: 12px; align-items: baseline;
  padding: 12px 8px; border-bottom: 1px solid #f1f1f1; cursor: pointer;
}
.row-item:hover { background: #f8f8f8; }
.row-sender { width: 180px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row-subject { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty-state { color: #70757a; padding: 24px 8px; }

.detail-header { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.avatar {
  width: 36px; height: 36px; border-radius: 50%;
  background: #1a73e8; color: #fff; display: flex;
  align-items: center; justify-content: center; font-weight: 500;
}
.detail-sender { font-weight: 500; }
.detail-subject { font-size: 18px; margin-top: 2px; }
.detail-body {
  white-space: pre-wrap; font-family: inherit; font-size: 14px;
  background: #f8f9fa; padding: 12px; border-radius: 8px;
}
.detail-hint { font-weight: 500; margin: 16px 0 8px; }
.action-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
}
.status-line { color: #188038; font-size: 13px; min-height: 18px; margin-top: 12px; }
```

- [ ] **Step 4: Write `app.js`**

```javascript
// components/inbox_router/practice_inbox/app.js
let inboxMessages = [];
let openMessageId = null;

const rowList = document.getElementById("rowList");
const emptyState = document.getElementById("emptyState");
const listView = document.getElementById("listView");
const detailView = document.getElementById("detailView");
const detailStatus = document.getElementById("detailStatus");

async function loadInbox() {
  detailStatus.textContent = "";
  try {
    const resp = await fetch("/practice/api/inbox");
    if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
    const data = await resp.json();
    inboxMessages = data.messages || [];
    emptyState.textContent = "No emails to practice on. Click Refresh.";
    renderList();
  } catch (e) {
    inboxMessages = [];
    rowList.innerHTML = "";
    emptyState.hidden = false;
    emptyState.textContent = "Can't reach the local server -- is it running? Try refreshing in a few seconds.";
  }
}

function renderList() {
  rowList.innerHTML = "";
  emptyState.hidden = inboxMessages.length > 0;
  inboxMessages.forEach((email) => {
    const li = document.createElement("li");
    li.className = "row-item";
    li.innerHTML = `
      <span class="row-sender">${escapeHtml(email.sender || email.sender_email || "")}</span>
      <span class="row-subject">${escapeHtml(email.subject || "")}</span>
    `;
    li.addEventListener("click", () => openMessage(email.message_id));
    rowList.appendChild(li);
  });
}

function openMessage(messageId) {
  const email = inboxMessages.find((e) => e.message_id === messageId);
  if (!email) return;
  openMessageId = messageId;
  document.getElementById("detailAvatar").textContent =
    (email.sender || email.sender_email || "?").charAt(0).toUpperCase();
  document.getElementById("detailSender").textContent = email.sender || email.sender_email || "";
  document.getElementById("detailSubject").textContent = email.subject || "";
  document.getElementById("detailBody").textContent = email.body_text || "(no body available)";
  listView.hidden = true;
  detailView.hidden = false;
}

function closeMessage() {
  openMessageId = null;
  detailView.hidden = true;
  listView.hidden = false;
}

async function recordDecision(decision) {
  if (!openMessageId) return;
  try {
    const resp = await fetch("/practice/api/record", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: openMessageId, decision }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      detailStatus.textContent = `Error: ${err.error || "record failed"}`;
      return;
    }
    closeMessage();
  } catch (e) {
    detailStatus.textContent = "Error: could not reach the server.";
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

document.getElementById("refreshBtn").addEventListener("click", loadInbox);
document.getElementById("backBtn").addEventListener("click", closeMessage);
document.querySelectorAll(".btn-action").forEach((btn) => {
  btn.addEventListener("click", () => recordDecision(btn.dataset.decision));
});

loadInbox();
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_local_server.py -v`
Expected: PASS (every test in the file, including all of `TestPracticeStaticFiles`)

- [ ] **Step 6: Commit**

```bash
git add components/inbox_router/practice_inbox/
git commit -m "Add the Scope #3 practice inbox page (Record-step demonstration UI)"
```

---

### Task 4: Wire "Launch mockups" for Inbox Dispatch

**Files:**
- Modify: `app_electron/main.js`
- Modify: `app_electron/renderer/renderer.js`

**Interfaces:**
- Consumes: nothing new from earlier tasks directly (this task wires the Electron UI to the already-running `local_server.py`'s `/practice/` route via a plain URL, not a Python import).

- [ ] **Step 1: Update `main.js`'s `test-launch-mockups` handler**

In `app_electron/main.js`, change:

```javascript
ipcMain.handle("test-launch-mockups", (_evt, capsuleName) => {
  const targets = TEST_MOCKUPS[capsuleName];
  if (!targets) {
    return { ok: false, error: `No test mockups defined for '${capsuleName}'.` };
  }
```

to:

```javascript
ipcMain.handle("test-launch-mockups", (_evt, capsuleName) => {
  // Inbox Dispatch's practice target isn't a {type, script/target} pair
  // like form_filling/Sheet-to-Portal Matcher's real, separate apps below
  // -- it's a page on the SAME local server the Play button already starts
  // (see ensureLocalServerRunning), just a different URL path. Checked
  // first, ahead of the flat TEST_MOCKUPS lookup.
  const capsule = listCapsules().find((c) => c.name === capsuleName);
  if (capsule && capsule.kind === "url" && capsule.local_server) {
    ensureLocalServerRunning(capsule.local_server);
    shell.openExternal(`${capsule.url}practice/`);
    return { ok: true, opened: ["practice inbox"] };
  }

  const targets = TEST_MOCKUPS[capsuleName];
  if (!targets) {
    return { ok: false, error: `No test mockups defined for '${capsuleName}'.` };
  }
```

(Leave the rest of the handler — the `for (const t of targets)` loop and everything after it — exactly as-is. `listCapsules`, `ensureLocalServerRunning`, and `shell` are all already defined/imported earlier in this same file.)

- [ ] **Step 2: Validate `main.js`'s syntax**

Run: `node --check app_electron/main.js`
Expected: no output (success)

- [ ] **Step 3: Update `renderer.js`'s `ppTestGroup` visibility condition**

In `app_electron/renderer/renderer.js`, change:

```javascript
  ppTestGroup.hidden = capsule.kind === "url";
```

to:

```javascript
  // A kind="url" capsule with a local_server (e.g. Inbox Dispatch) now DOES
  // have something to launch here -- its own practice page, on the same
  // server Play already starts. Only a genuinely external kind="url" link
  // with no local_server stays hidden, since that case still has nothing
  // for this button to open.
  ppTestGroup.hidden = capsule.kind === "url" && !capsule.local_server;
```

- [ ] **Step 4: Validate `renderer.js`'s syntax**

Run: `node --check app_electron/renderer/renderer.js`
Expected: no output (success)

- [ ] **Step 5: Run the full Python suite and commit**

Run: `pytest -q`
Expected: PASS, 0 failed (this task touches no Python files, so the count should be unchanged from Task 3's end state)

```bash
git add app_electron/main.js app_electron/renderer/renderer.js
git commit -m "Wire Launch mockups to open the Scope #3 practice inbox"
```

---

### Task 5: End-to-end verification, docs sync, push

**Files:**
- Modify: `DEVELOPERS.md`
- Modify: `treetask/index.html`

- [ ] **Step 1: Server smoke test**

Launch `python components/inbox_router/local_server.py` in the background, then:

```bash
curl -s http://localhost:8765/practice/ | head -5
curl -s http://localhost:8765/practice/api/inbox
```

Expected: the first prints the start of the practice page's HTML; the second prints a JSON object with a `"messages"` array (possibly empty depending on what's in the mock fixture). Stop the server afterward.

- [ ] **Step 2: Run the full project test suite**

Run: `pytest -q`
Expected: PASS, 0 failed — report the real final summary line, not a guess.

- [ ] **Step 3: Update `DEVELOPERS.md`**

Find the most recent Scope #3 entry (the local-UI one added earlier today, likely named `scope3_local_ui`) and add a new entry immediately after it, following the same style:

```markdown
- [x] `scope3_practice_inbox` — **Added 2026-08-26, direct request** (found while testing the local UI: Scope #1's insurance form and Scope #2's mock portal both give a person something to freely act on to generate real demonstrations; Scope #3 never had that -- Inbox Dispatch is the review-a-suggestion step, not the record-a-demonstration step). Adds the missing Record-step target: `InboxRouter` gains two new public methods, `list_practice_inbox()` and `record_practice_decision()`, with zero change to any of its existing behavior. `local_server.py` (the same process Inbox Dispatch already runs) gains two routes, `/practice/api/inbox` and `/practice/api/record`, plus three new static-file paths for a new page, `components/inbox_router/practice_inbox/{index.html,style.css,app.js}` -- Gmail-styled like Inbox Dispatch, but with no AI suggestion shown anywhere: a person just picks an action (reply/forward/route to Scope #1/route to Scope #2/flag/leave alone) on their own judgment, which calls `decision_recorder.record_example()` directly -- the exact same function every other recorded example already goes through, so `train_inbox_agent.py` needed zero changes. The existing "Launch mockups" button (hidden for Inbox Dispatch since the earlier `kind="url"` capsule feature, since it had nothing to launch) is un-hidden and wired to open this page, reusing the same `ensureLocalServerRunning()` mechanism Play already uses -- no new UI element, no second server process. TDD throughout: [N] new tests across `test_inbox_router.py`/`test_local_server.py`. Full suite: [report the real passed/skipped/failed numbers here once Step 2 runs].
```

(Fill in the real test count and full-suite numbers from your own Step 2 run — do not guess them.)

- [ ] **Step 4: Mirror into `treetask/index.html`**

Find the scope3 hub's `items` array (search for the most recent Scope #3 node id, e.g. `scope3_local_ui`) and insert a new node right after it, in the same `{id, t, done, desc}` shape as its neighbors, condensed the same way those neighboring nodes condense their own `DEVELOPERS.md` source.

- [ ] **Step 5: Validate the Task Tree's JS still parses**

Run:
```
node -e "const fs = require('fs'); const html = fs.readFileSync('treetask/index.html', 'utf8'); const m = html.match(/<script>([\s\S]*)<\/script>/); if (!m) { console.error('NO SCRIPT BLOCK FOUND'); process.exit(1); } new Function(m[1]); console.log('OK - script block parses cleanly');"
```
Expected: `OK - script block parses cleanly`

- [ ] **Step 6: Commit and push**

```bash
git add DEVELOPERS.md treetask/index.html
git commit -m "Sync Task Tree and DEVELOPERS.md with the Scope #3 practice inbox"
git push origin feature/scope3-record-train-output
```
