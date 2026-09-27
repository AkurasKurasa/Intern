# Scope #3 Local UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Scope #3's Claude-hosted "Inbox Dispatch" mockup (which can never reach the local Python backend due to platform sandboxing) with a small local web server that serves a real, Gmail-styled page whose Confirm/Override actions genuinely call the existing `InboxRouter`/`InboxAgent` pipeline.

**Architecture:** A standalone Python script (`local_server.py`) using only the standard library's `http.server` constructs one real `InboxRouter` and answers a handful of JSON endpoints; a plain HTML/CSS/JS page (no build step, no framework) calls those endpoints with `fetch()`. Electron's existing Play/Launch button starts this server (if not already running) before opening the user's browser to it — no new button, no Electron-embedded UI.

**Tech Stack:** Python standard library (`http.server`, `json`), vanilla HTML/CSS/JS, Node's `child_process.spawn` (already used elsewhere in `main.js`).

**Spec:** `docs/superpowers/specs/2026-08-26-scope3-local-ui-design.md`

## Global Constraints

- Branch: `feature/scope3-record-train-output` (already checked out).
- No new third-party dependencies, Python or JS.
- No change to `InboxAgent`, `RuleLayer`, `LLMClassifier`, `PatternProfile`, or `InboxRouter`'s *existing* behavior — the only exception is one new public method, `InboxRouter.pending_entries()` (Task 1).
- Manual "Refresh" only — no automatic polling on the page (direct decision, not a placeholder).
- Stays outside the Electron application's own window — opens in the user's regular browser via `shell.openExternal`, same as today.
- Full project test suite must stay green after every task.
- Commit after every task, per this project's standing rule to commit every change, not just at the end of a session.

---

### Task 1: `InboxRouter.pending_entries()` and `WorkflowCapsule.local_server`

**Files:**
- Modify: `components/inbox_router/router.py`
- Modify: `components/agent/capsule.py`
- Test: `tests/test_inbox_router.py`
- Test: `tests/test_capsule_launch_command.py`

**Interfaces:**
- Produces: `InboxRouter.pending_entries() -> list[dict]` (each dict is a history entry, same shape `_load_history()` already returns), `WorkflowCapsule.local_server: str = ""`. Both are consumed by Task 2 (`local_server.py` calls `pending_entries()`) and Task 4 (`main.js` reads `capsule.local_server`).

- [ ] **Step 1: Write the failing test for `pending_entries()`**

```python
# In tests/test_inbox_router.py, inside class TestInboxRouterPollOnce (reuse its existing _build helper):
    def test_pending_entries_returns_only_unconfirmed(self, tmp_path):
        router = self._build(tmp_path, inbox=[
            _msg("i1", "stranger@x.com", "totally unrelated"),
            _msg("i2", "stranger@x.com", "also unrelated"),
        ])
        router.poll_once()
        assert len(router.pending_entries()) == 2
        router.confirm_suggestion("i1", "leave_alone")
        pending = router.pending_entries()
        assert len(pending) == 1
        assert pending[0]["message_id"] == "i2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_inbox_router.py::TestInboxRouterPollOnce::test_pending_entries_returns_only_unconfirmed -v`
Expected: FAIL with `AttributeError: 'InboxRouter' object has no attribute 'pending_entries'`

- [ ] **Step 3: Add `pending_entries()` to `InboxRouter`**

In `components/inbox_router/router.py`, add this method right after the existing `_load_history()` method:

```python
    def pending_entries(self) -> list:
        """Every history entry still awaiting a Confirm/Override -- exposed
        as a real public method (rather than reaching into the private
        _load_history()) for local_server.py, a second driver of this same
        class outside router.py's own stdin/stdout protocol."""
        return [e for e in self._load_history() if e.get("status") == "pending"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_inbox_router.py::TestInboxRouterPollOnce::test_pending_entries_returns_only_unconfirmed -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for `WorkflowCapsule.local_server`**

```python
# Append to tests/test_capsule_launch_command.py
class TestLocalServerField:
    def test_defaults_to_empty_string(self):
        capsule = WorkflowCapsule(
            name="x", description="", model_path="", trigger_keywords=[], trigger_apps=[],
        )
        assert capsule.local_server == ""

    def test_round_trips_through_registry_load(self, tmp_path):
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps({"capsules": [{
            "name": "Inbox Dispatch", "description": "", "model_path": "",
            "trigger_keywords": [], "trigger_apps": [], "kind": "url",
            "url": "http://localhost:8765/",
            "local_server": "components/inbox_router/local_server.py",
        }]}), encoding="utf-8")
        registry = CapsuleRegistry(registry_path=str(registry_path))
        capsule = registry.list_capsules()[0]
        assert capsule.local_server == "components/inbox_router/local_server.py"
```

Check the top of `tests/test_capsule_launch_command.py` for its existing imports (`WorkflowCapsule`, `CapsuleRegistry`, `json`) — reuse them; don't re-import if already present.

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_capsule_launch_command.py::TestLocalServerField -v`
Expected: FAIL with `TypeError: WorkflowCapsule.__init__() got an unexpected keyword argument 'local_server'` (first test) or similar

- [ ] **Step 7: Add the field to `WorkflowCapsule`**

In `components/agent/capsule.py`, add this field right after the existing `url: str = ""` field:

```python
    # kind="url" only: the relative path to a Python script that must be
    # running (as a local HTTP server) before this capsule's url is opened.
    # "" means the url needs nothing local-served -- either a genuinely
    # external link, or (today) unused for every non-url kind. Added for
    # Inbox Dispatch's local, real UI (as opposed to its earlier
    # Claude-hosted mockup, which could never reach the local backend).
    local_server:     str = ""
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_capsule_launch_command.py::TestLocalServerField -v`
Expected: PASS

- [ ] **Step 9: Run the full Scope #3 + capsule test files, then commit**

Run: `pytest tests/test_inbox_router.py tests/test_capsule_launch_command.py -v`
Expected: PASS (all tests, including the 2 new ones)

```bash
git add components/inbox_router/router.py components/agent/capsule.py tests/test_inbox_router.py tests/test_capsule_launch_command.py
git commit -m "Add InboxRouter.pending_entries() and WorkflowCapsule.local_server field"
```

---

### Task 2: `local_server.py` — the real backend-facing HTTP server

**Files:**
- Create: `components/inbox_router/local_server.py`
- Test: `tests/test_local_server.py`

**Interfaces:**
- Consumes: `InboxRouter.pending_entries()` (Task 1), `InboxRouter.{poll_once, confirm_suggestion, override_decision}` (pre-existing), `gmail_client.get_gmail_client`, `pattern_profile.PatternProfile`, `routing_rules.RuleLayer`, `llm_classifier.LLMClassifier` (all pre-existing).
- Produces: `handle_request(method: str, path: str, body: bytes, router: InboxRouter) -> tuple[int, dict, bytes, str]` (status, extra headers, response body bytes, content-type), `build_router() -> InboxRouter`, `make_handler(router) -> type`, `serve(port: int = DEFAULT_PORT) -> None`, `DEFAULT_PORT = 8765`. Task 3's static files are served through the same `handle_request` (no separate interface). Task 4 doesn't import this module directly — it spawns it as a subprocess by path.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_local_server.py
import json
import os
import sys
import urllib.request
import urllib.error
from http.server import HTTPServer
import threading

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage, MockGmailClient
from llm_classifier import LLMClassifier
from pattern_profile import PatternProfile
from routing_rules import RuleLayer
from router import InboxRouter
import local_server as ls


def _write_fixture(data_dir, inbox=None, sent=None):
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "mock_inbox.json"), "w", encoding="utf-8") as f:
        json.dump({"inbox": inbox or [], "sent": sent or []}, f)


def _msg(mid, sender_email, subject, body="body text"):
    return {
        "id": mid, "thread_id": mid, "sender": sender_email, "sender_email": sender_email,
        "subject": subject, "snippet": "", "body_text": body, "received_at": "2026-08-26T00:00:00Z",
    }


def _build_router(tmp_path, inbox=None):
    data_dir = tmp_path / "data"
    _write_fixture(str(data_dir), inbox=inbox or [])
    client = MockGmailClient(data_dir=str(data_dir))
    profile = PatternProfile(path=str(data_dir / "profile.json"))
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"capsules": []}), encoding="utf-8")
    rules = RuleLayer(profile, registry_path=str(registry_path))
    classifier = LLMClassifier(provider="none")
    history_path = str(data_dir / "routed_history.json")
    return InboxRouter(client, profile, rules, classifier, history_path=history_path,
                        inbox_checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"),
                        examples_path=str(tmp_path / "training_examples.jsonl"))


class TestHandleRequestInbox:
    def test_get_api_inbox_returns_pending_after_poll(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        status, headers, body, content_type = ls.handle_request("GET", "/api/inbox", b"", router)
        assert status == 200
        assert content_type == "application/json"
        payload = json.loads(body)
        assert len(payload["pending"]) == 1
        assert payload["pending"][0]["message_id"] == "i1"

    def test_unknown_path_returns_404(self, tmp_path):
        router = _build_router(tmp_path)
        status, _headers, _body, _ct = ls.handle_request("GET", "/nope", b"", router)
        assert status == 404


class TestHandleRequestConfirm:
    def test_post_confirm_records_a_real_decision(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        body = json.dumps({"message_id": "i1", "decision": "leave_alone"}).encode("utf-8")
        status, _headers, resp_body, content_type = ls.handle_request("POST", "/api/confirm", body, router)
        assert status == 200
        assert json.loads(resp_body) == {"ok": True}
        assert router.pending_entries() == []

    def test_post_confirm_malformed_json_returns_400(self, tmp_path):
        router = _build_router(tmp_path)
        status, _headers, _body, _ct = ls.handle_request("POST", "/api/confirm", b"not json", router)
        assert status == 400

    def test_post_confirm_missing_field_returns_400(self, tmp_path):
        router = _build_router(tmp_path)
        body = json.dumps({"message_id": "i1"}).encode("utf-8")  # missing "decision"
        status, _headers, _body, _ct = ls.handle_request("POST", "/api/confirm", body, router)
        assert status == 400

    def test_post_confirm_unknown_message_id_returns_400(self, tmp_path):
        # No poll_once() ever ran, so "i1" is not a pending entry --
        # confirm_suggestion() itself would just log-and-return None either
        # way, so the handler must check pending_entries() itself rather
        # than trusting a call that can't distinguish success from failure.
        router = _build_router(tmp_path)
        body = json.dumps({"message_id": "i1", "decision": "leave_alone"}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/api/confirm", body, router)
        assert status == 400
        assert json.loads(resp_body)["error"]


class TestHandleRequestOverride:
    def test_post_override_records_the_new_decision(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        body = json.dumps({"message_id": "i1", "new_decision": "reply", "reason": "actually needs one"}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/api/override", body, router)
        assert status == 200
        assert json.loads(resp_body) == {"ok": True}

    def test_post_override_unknown_message_id_returns_400(self, tmp_path):
        router = _build_router(tmp_path)
        body = json.dumps({"message_id": "i1", "new_decision": "reply"}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/api/override", body, router)
        assert status == 400
        assert json.loads(resp_body)["error"]


class TestBuildRouter:
    def test_returns_a_real_inbox_router(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ls, "get_gmail_client", lambda: _build_router(tmp_path)._gmail)
        router = ls.build_router()
        assert isinstance(router, InboxRouter)


class TestServeOverHTTP:
    """The one test that actually opens a real socket -- confirms real
    HTTP semantics (status line, headers) that calling handle_request()
    directly can't verify."""

    def test_get_api_inbox_over_real_http(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        handler_cls = ls.make_handler(router)
        httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/inbox") as resp:
                assert resp.status == 200
                payload = json.loads(resp.read())
                assert len(payload["pending"]) == 1
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_local_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'local_server'`

- [ ] **Step 3: Write the implementation**

```python
# components/inbox_router/local_server.py
"""
components/inbox_router/local_server.py
===========================================
A second, independent driver of the same InboxRouter class router.py
already defines -- router.py drives it over stdin/stdout for Electron's
own Inbox tab; this drives it over plain HTTP for a real browser page
(Scope #3's "Inbox Dispatch" capsule). Neither knows the other exists;
both call the same public InboxRouter methods, so nothing about the
pipeline itself changes here.

Usage:
    python components/inbox_router/local_server.py
    (then open http://localhost:8765/ in a browser)
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
_COMP = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Same hand-rolled .env loader as router.py -- no python-dotenv anywhere
# in this project.
_env_path = os.path.join(_ROOT, ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from gmail_client import get_gmail_client
from llm_classifier import LLMClassifier
from pattern_profile import PatternProfile
from routing_rules import RuleLayer
from router import InboxRouter

DEFAULT_PORT = 8765
_UI_DIR = os.path.join(_THIS_DIR, "local_ui")

_STATIC_FILES = {
    "/": ("index.html", "text/html"),
    "/style.css": ("style.css", "text/css"),
    "/app.js": ("app.js", "application/javascript"),
}


def _pick_provider() -> Tuple[str, str]:
    """Same preference order as router.py's own _pick_provider()."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic", os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("GROQ_API_KEY"):
        return "groq", os.environ["GROQ_API_KEY"]
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini", os.environ["GEMINI_API_KEY"]
    return "lmstudio", ""


def build_router() -> InboxRouter:
    gmail_client = get_gmail_client()
    profile = PatternProfile()
    rule_layer = RuleLayer(profile)
    provider, api_key = _pick_provider()
    classifier = LLMClassifier(provider=provider, api_key=api_key)
    return InboxRouter(gmail_client, profile, rule_layer, classifier)


def handle_request(method: str, path: str, body: bytes, router: InboxRouter) -> Tuple[int, dict, bytes, str]:
    """Pure request handler, separated from BaseHTTPRequestHandler so it's
    testable without opening a real socket. Returns
    (status_code, extra_headers, response_body_bytes, content_type)."""
    if method == "GET" and path in _STATIC_FILES:
        filename, content_type = _STATIC_FILES[path]
        file_path = os.path.join(_UI_DIR, filename)
        if not os.path.isfile(file_path):
            return 404, {}, b"Not found", "text/plain"
        with open(file_path, "rb") as f:
            return 200, {}, f.read(), content_type

    if method == "GET" and path == "/api/inbox":
        router.poll_once()
        payload = json.dumps({"pending": router.pending_entries()}).encode("utf-8")
        return 200, {}, payload, "application/json"

    if method == "POST" and path == "/api/confirm":
        try:
            data = json.loads(body or b"{}")
            message_id = data["message_id"]
            decision = data["decision"]
        except (json.JSONDecodeError, KeyError) as exc:
            err = json.dumps({"error": f"Bad request: {exc}"}).encode("utf-8")
            return 400, {}, err, "application/json"
        # confirm_suggestion() itself returns None either way -- it can't
        # tell the caller whether message_id was actually found, it just
        # logs and returns on an unknown one. Check pending_entries() (the
        # one public read InboxRouter already exposes) ourselves first, so
        # an unknown id gets a real 400 instead of a misleading 200.
        if not any(e["message_id"] == message_id for e in router.pending_entries()):
            err = json.dumps({"error": f"Unknown or already-handled message_id: {message_id}"}).encode("utf-8")
            return 400, {}, err, "application/json"
        router.confirm_suggestion(message_id, decision)
        return 200, {}, json.dumps({"ok": True}).encode("utf-8"), "application/json"

    if method == "POST" and path == "/api/override":
        try:
            data = json.loads(body or b"{}")
            message_id = data["message_id"]
            new_decision = data["new_decision"]
            reason = data.get("reason", "")
        except (json.JSONDecodeError, KeyError) as exc:
            err = json.dumps({"error": f"Bad request: {exc}"}).encode("utf-8")
            return 400, {}, err, "application/json"
        if not any(e["message_id"] == message_id for e in router.pending_entries()):
            err = json.dumps({"error": f"Unknown or already-handled message_id: {message_id}"}).encode("utf-8")
            return 400, {}, err, "application/json"
        router.override_decision(message_id, new_decision, reason)
        return 200, {}, json.dumps({"ok": True}).encode("utf-8"), "application/json"

    return 404, {}, json.dumps({"error": "Not found"}).encode("utf-8"), "application/json"


def make_handler(router: InboxRouter):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # keep stdout quiet -- this is a background helper process

        def do_GET(self):
            self._respond("GET", self.path, b"")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            self._respond("POST", self.path, body)

        def _respond(self, method, path, body):
            status, headers, payload, content_type = handle_request(method, path, body, router)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def serve(port: int = DEFAULT_PORT) -> None:
    router = build_router()
    handler_cls = make_handler(router)
    try:
        httpd = HTTPServer(("127.0.0.1", port), handler_cls)
    except OSError as exc:
        print(f"Could not start local server on port {port} (already running?): {exc}")
        return
    print(f"Inbox Dispatch local server listening on http://localhost:{port}/")
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_local_server.py -v`
Expected: PASS (all 9 tests). Note: `local_ui/index.html`/`style.css`/`app.js` don't exist yet (Task 3), so `_STATIC_FILES` requests aren't tested here — Task 3 covers those.

- [ ] **Step 5: Commit**

```bash
git add components/inbox_router/local_server.py tests/test_local_server.py
git commit -m "Add Scope #3 local HTTP server driving the real InboxRouter pipeline"
```

---

### Task 3: The real, locally-served Gmail-styled page

**Files:**
- Create: `components/inbox_router/local_ui/index.html`
- Create: `components/inbox_router/local_ui/style.css`
- Create: `components/inbox_router/local_ui/app.js`
- Test: `tests/test_local_server.py` (append)

**Interfaces:**
- Consumes: `local_server.py`'s `/`, `/style.css`, `/app.js`, `/api/inbox`, `/api/confirm`, `/api/override` endpoints (Task 2), via `fetch()` from `app.js`.
- Produces: nothing further tasks import — this is the leaf of the plan's file tree.

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_local_server.py
class TestStaticFiles:
    def test_index_html_is_served(self, tmp_path):
        router = _build_router(tmp_path)
        status, _headers, body, content_type = ls.handle_request("GET", "/", b"", router)
        assert status == 200
        assert content_type == "text/html"
        assert b"<html" in body.lower()

    def test_style_css_is_served(self, tmp_path):
        router = _build_router(tmp_path)
        status, _headers, _body, content_type = ls.handle_request("GET", "/style.css", b"", router)
        assert status == 200
        assert content_type == "text/css"

    def test_app_js_is_served(self, tmp_path):
        router = _build_router(tmp_path)
        status, _headers, body, content_type = ls.handle_request("GET", "/app.js", b"", router)
        assert status == 200
        assert content_type == "application/javascript"
        assert b"/api/inbox" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_local_server.py::TestStaticFiles -v`
Expected: FAIL with 404s (the three files don't exist yet under `local_ui/`)

- [ ] **Step 3: Write `index.html`**

```html
<!-- components/inbox_router/local_ui/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Inbox Dispatch</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <header class="topbar">
    <span class="brand">📬 Inbox Dispatch</span>
    <button id="refreshBtn" class="btn btn-primary">Refresh</button>
  </header>

  <main>
    <div id="listView">
      <p id="emptyState" class="empty-state" hidden>No pending emails. Click Refresh to check again.</p>
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
      <p class="detail-rationale" id="detailRationale"></p>
      <pre class="detail-body" id="detailBody"></pre>

      <div class="decision-bar">
        <span>Suggested: <strong id="detailDecision"></strong></span>
        <button id="confirmBtn" class="btn btn-primary">Confirm</button>
      </div>
      <div class="override-bar">
        <select id="overrideSelect">
          <option value="route_scope1">Route to Scope #1</option>
          <option value="route_scope2">Route to Scope #2</option>
          <option value="reply">Reply</option>
          <option value="forward">Forward</option>
          <option value="flag">Flag</option>
          <option value="leave_alone">Leave alone</option>
        </select>
        <button id="overrideBtn" class="btn btn-ghost">Override</button>
      </div>
      <p id="detailStatus" class="status-line"></p>
    </div>
  </main>

  <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 4: Write `style.css`**

```css
/* components/inbox_router/local_ui/style.css */
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
.btn-primary { background: #d93025; color: #fff; }
.btn-primary:hover { background: #b3261e; }
.btn-ghost { background: #fff; color: #d93025; border: 1px solid #d93025; }
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
.row-badge {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  background: #e8eaed; color: #444;
}
.empty-state { color: #70757a; padding: 24px 8px; }

.detail-header { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.avatar {
  width: 36px; height: 36px; border-radius: 50%;
  background: #1a73e8; color: #fff; display: flex;
  align-items: center; justify-content: center; font-weight: 500;
}
.detail-sender { font-weight: 500; }
.detail-subject { font-size: 18px; margin-top: 2px; }
.detail-rationale { color: #5f6368; font-style: italic; }
.detail-body {
  white-space: pre-wrap; font-family: inherit; font-size: 14px;
  background: #f8f9fa; padding: 12px; border-radius: 8px;
}
.decision-bar, .override-bar {
  display: flex; align-items: center; gap: 12px; margin: 12px 0;
}
.status-line { color: #188038; font-size: 13px; min-height: 18px; }
```

- [ ] **Step 5: Write `app.js`**

```javascript
// components/inbox_router/local_ui/app.js
let pendingEmails = [];
let openMessageId = null;

const rowList = document.getElementById("rowList");
const emptyState = document.getElementById("emptyState");
const listView = document.getElementById("listView");
const detailView = document.getElementById("detailView");
const detailStatus = document.getElementById("detailStatus");

async function loadInbox() {
  detailStatus.textContent = "";
  const resp = await fetch("/api/inbox");
  const data = await resp.json();
  pendingEmails = data.pending || [];
  renderList();
}

function renderList() {
  rowList.innerHTML = "";
  emptyState.hidden = pendingEmails.length > 0;
  pendingEmails.forEach((email) => {
    const li = document.createElement("li");
    li.className = "row-item";
    li.innerHTML = `
      <span class="row-sender">${escapeHtml(email.sender || email.sender_email || "")}</span>
      <span class="row-subject">${escapeHtml(email.subject || "")}</span>
      <span class="row-badge">${escapeHtml(email.decision || "")}</span>
    `;
    li.addEventListener("click", () => openMessage(email.message_id));
    rowList.appendChild(li);
  });
}

function openMessage(messageId) {
  const email = pendingEmails.find((e) => e.message_id === messageId);
  if (!email) return;
  openMessageId = messageId;
  document.getElementById("detailAvatar").textContent =
    (email.sender || email.sender_email || "?").charAt(0).toUpperCase();
  document.getElementById("detailSender").textContent = email.sender || email.sender_email || "";
  document.getElementById("detailSubject").textContent = email.subject || "";
  document.getElementById("detailRationale").textContent = email.rationale || "";
  document.getElementById("detailBody").textContent = email.body_text || "(no body available)";
  document.getElementById("detailDecision").textContent = email.decision || "";
  listView.hidden = true;
  detailView.hidden = false;
}

function closeMessage() {
  openMessageId = null;
  detailView.hidden = true;
  listView.hidden = false;
}

async function confirmCurrent() {
  const email = pendingEmails.find((e) => e.message_id === openMessageId);
  if (!email) return;
  await fetch("/api/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: openMessageId, decision: email.decision }),
  });
  detailStatus.textContent = "Confirmed.";
  await loadInbox();
  closeMessage();
}

async function overrideCurrent() {
  const newDecision = document.getElementById("overrideSelect").value;
  await fetch("/api/override", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: openMessageId, new_decision: newDecision, reason: "manual override" }),
  });
  detailStatus.textContent = "Overridden.";
  await loadInbox();
  closeMessage();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

document.getElementById("refreshBtn").addEventListener("click", loadInbox);
document.getElementById("backBtn").addEventListener("click", closeMessage);
document.getElementById("confirmBtn").addEventListener("click", confirmCurrent);
document.getElementById("overrideBtn").addEventListener("click", overrideCurrent);

loadInbox();
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_local_server.py -v`
Expected: PASS (all tests, including the new `TestStaticFiles` class)

- [ ] **Step 7: Commit**

```bash
git add components/inbox_router/local_ui/
git commit -m "Add the real, locally-served Gmail-styled Inbox Dispatch page"
```

---

### Task 4: Wire it into the existing Play/Launch button

**Files:**
- Modify: `app_electron/main.js`
- Modify: `tasks/registry.json`

**Interfaces:**
- Consumes: `WorkflowCapsule.local_server` (Task 1, read from the registry JSON directly by `main.js`, not imported as a Python type).
- Produces: nothing further tasks consume — this is the plan's final functional wiring.

- [ ] **Step 1: Add `ensureLocalServerRunning` and wire it into both launch points**

In `app_electron/main.js`, near the top where other module-level state lives (search for `let currentCapsuleName` to find that area), add:

```javascript
let localServerProcess = null;

function ensureLocalServerRunning(scriptPath) {
  if (localServerProcess && localServerProcess.exitCode === null) {
    return; // already running
  }
  const pythonExe = resolvePython();
  const fullPath = path.join(REPO_ROOT, scriptPath);
  localServerProcess = spawn(pythonExe, [fullPath], {
    cwd: REPO_ROOT, detached: true, stdio: "ignore", windowsHide: true,
  });
  localServerProcess.unref();
}
```

Then change the existing `capsule-run` handler from:

```javascript
ipcMain.handle("capsule-run", (_evt, capsuleName) => {
  // kind="url" isn't a subprocess at all -- there's nothing for
  // recorder_bridge.py/Python to run, so this short-circuits before ever
  // reaching queueOrSend(). Scope #3's mockup was deliberately built
  // OUTSIDE the Electron app (direct request), so "Play" for it just opens
  // the real browser to that page.
  const capsule = listCapsules().find((c) => c.name === capsuleName);
  if (capsule && capsule.kind === "url") {
    if (capsule.url) shell.openExternal(capsule.url);
    return { opened: true };
  }
  queueOrSend({ cmd: "run_capsule", capsule_name: capsuleName });
  return { opened: false };
});
```

to:

```javascript
ipcMain.handle("capsule-run", (_evt, capsuleName) => {
  // kind="url" isn't a subprocess at all -- there's nothing for
  // recorder_bridge.py/Python to run, so this short-circuits before ever
  // reaching queueOrSend(). Scope #3's Inbox Dispatch page is deliberately
  // built OUTSIDE the Electron app (direct request), so "Play" for it just
  // opens the real browser to that page -- ensuring its local server is
  // running first, when the capsule declares one.
  const capsule = listCapsules().find((c) => c.name === capsuleName);
  if (capsule && capsule.kind === "url") {
    if (capsule.local_server) ensureLocalServerRunning(capsule.local_server);
    if (capsule.url) shell.openExternal(capsule.url);
    return { opened: true };
  }
  queueOrSend({ cmd: "run_capsule", capsule_name: capsuleName });
  return { opened: false };
});
```

And change the existing `capsule-run-current` handler from:

```javascript
ipcMain.handle("capsule-run-current", () => {
  if (!currentCapsuleName) return;
  const capsule = listCapsules().find((c) => c.name === currentCapsuleName);
  if (capsule && capsule.kind === "url") {
    if (capsule.url) shell.openExternal(capsule.url);
    return;
  }
  queueOrSend({ cmd: "run_capsule", capsule_name: currentCapsuleName });
});
```

to:

```javascript
ipcMain.handle("capsule-run-current", () => {
  if (!currentCapsuleName) return;
  const capsule = listCapsules().find((c) => c.name === currentCapsuleName);
  if (capsule && capsule.kind === "url") {
    if (capsule.local_server) ensureLocalServerRunning(capsule.local_server);
    if (capsule.url) shell.openExternal(capsule.url);
    return;
  }
  queueOrSend({ cmd: "run_capsule", capsule_name: currentCapsuleName });
});
```

- [ ] **Step 2: Validate `main.js`'s syntax**

Run: `node --check app_electron/main.js`
Expected: no output (success) — `node --check` prints nothing and exits 0 on valid syntax

- [ ] **Step 3: Update the registry entry**

In `tasks/registry.json`, find the `"Inbox Dispatch"` capsule entry and replace its `"url"` value and add a `"local_server"` field, so the full entry reads:

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

(Keep every other capsule entry in the file exactly as-is — only this one entry's `"description"` and `"url"` change, and `"local_server"` is newly added.)

- [ ] **Step 4: Validate the registry JSON parses**

Run: `python -c "import json; json.load(open('tasks/registry.json'))" && echo OK`
Expected: `OK`

- [ ] **Step 5: Run the full Python test suite**

Run: `pytest -q`
Expected: PASS, 0 failed (registry.json's `url`/`local_server` change doesn't affect any Python test's assertions, since none of them assert on Inbox Dispatch's specific `url` value)

- [ ] **Step 6: Commit**

```bash
git add app_electron/main.js tasks/registry.json
git commit -m "Wire Inbox Dispatch's Play button to launch the real local server"
```

---

### Task 5: End-to-end verification, docs sync, push

**Files:**
- Modify: `DEVELOPERS.md`
- Modify: `treetask/index.html`

- [ ] **Step 1: Run the real server by hand and confirm it serves the page**

Run: `python components/inbox_router/local_server.py &` (background it, or open a second terminal), then:

```bash
curl -s http://localhost:8765/ | head -5
curl -s http://localhost:8765/api/inbox
```

Expected: the first command prints the start of `index.html`'s HTML; the second prints a JSON object with a `"pending"` key (an array — possibly empty, possibly populated from `mock_inbox.json`, depending on what's already in `routed_history.json`/`mock_state.json` on disk). Stop the server afterward (`kill %1` or close the terminal).

- [ ] **Step 2: Launch the Electron app and confirm the real click-path works**

Since this project's standing rule reserves real GUI-automation runs (`run_task.py`) for the user, but launching and clicking within the Electron app itself is not that category (no automated mouse/keyboard driving a separate target application), this step may be performed directly: start the app (`cd app_electron && npm start`), load the "Inbox Dispatch" capsule into the Play panel, click Play, and confirm the browser opens to `http://localhost:8765/` showing real pending emails (not the old Claude-hosted mockup). Click Confirm on one, then check `components/inbox_router/data/training_examples.jsonl` gained one new line — this is the same file Task 7 of the earlier Record/Train/Output plan wired up, so a successful append here confirms the whole chain end-to-end, not just this plan's own pieces in isolation.

- [ ] **Step 3: Run the full project test suite**

Run: `pytest -q`
Expected: PASS, 0 failed (report the real final passed/skipped/failed counts here once run — do not guess them)

- [ ] **Step 4: Update `DEVELOPERS.md`**

Find the `scope3_mockup_workflow_launcher` entry (added for the earlier `kind="url"` capsule feature) and add a new entry immediately after it, before `scope3_record_train_output_pipeline` (or wherever the Scope #3 entries currently end), following the same style:

```markdown
- [x] `scope3_local_ui` — **Added 2026-08-26, direct request** ("I need the user interface to test it on, build it or something", after discovering the Claude-hosted "Inbox Dispatch" mockup can never reach the local backend -- content-security-policy sandboxing on hosted pages has no exception for localhost). Replaced the mockup with a real, locally-served page: `components/inbox_router/local_server.py` (stdlib `http.server`, no new dependencies) constructs one real `InboxRouter` and answers `/api/inbox`, `/api/confirm`, `/api/override` by calling its existing public methods directly -- a second, independent driver of the exact same class `router.py` already drives over stdin/stdout for Electron's own Inbox tab, so nothing about the Record -> Train -> Output pipeline itself changed. `components/inbox_router/local_ui/{index.html,style.css,app.js}` is a plain, dependency-free Gmail-styled page whose Confirm/Override buttons make real `fetch()` calls -- no local JavaScript simulation survives. The existing Play/Launch button for the Inbox Dispatch capsule now starts this server first (if not already running, tracked via one module-level process handle in `main.js`) before opening the browser, so nothing new appears in the UI -- the same click just does something real now. One new public method, `InboxRouter.pending_entries()`, was the only change to any pre-existing pipeline file. Manual Refresh only, no auto-polling (direct decision). TDD: `tests/test_local_server.py` (12 tests, including one real-socket test verifying actual HTTP semantics, and dedicated coverage for an unknown `message_id` returning a real 400 rather than a misleading 200), plus 3 new tests for `pending_entries()`/`WorkflowCapsule.local_server`. Full suite: report the real number here once Step 3 runs, not assumed.
```

(Replace the final sentence's instruction with the actual passed/skipped/failed numbers once Step 3's real output is known.)

- [ ] **Step 5: Mirror the same content into `treetask/index.html`**

Find the `scope3` hub's `items` array (search for `scope3_mockup_workflow_launcher`) and insert a new node object right after it, following the exact same `{id, t, done, desc}` shape neighboring nodes use, condensed from Step 4's `DEVELOPERS.md` prose the same way `scope3_mock_pattern_fixture_fix`'s node condenses its own longer `DEVELOPERS.md` entry.

- [ ] **Step 6: Validate the Task Tree's JS still parses**

Run:
```bash
node -e "
const fs = require('fs');
const html = fs.readFileSync('treetask/index.html', 'utf8');
const m = html.match(/<script>([\s\S]*)<\/script>/);
if (!m) { console.error('NO SCRIPT BLOCK FOUND'); process.exit(1); }
new Function(m[1]);
console.log('OK - script block parses cleanly');
"
```
Expected: `OK - script block parses cleanly`

- [ ] **Step 7: Commit and push**

```bash
git add DEVELOPERS.md treetask/index.html
git commit -m "Sync Task Tree and DEVELOPERS.md with Scope #3's real local UI"
git push origin feature/scope3-record-train-output
```
