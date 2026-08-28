# Scope #3 Reply Recording via DemoRecorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Electron's real "Start Recording" feature (the same
action-triggered screen+keystroke `DemoRecorder` mechanism Scope #1
already uses) able to record real Scope #3 replies precisely, by
wiring in the already-built-but-unused `WebObserver`, and translate a
finished recording into real `reply_examples.jsonl` rows through the
existing `reply_recorder.py` pipeline.

**Architecture:** `DemoRecorder.__init__` gains a `trace_type == "web"`
branch that swaps its generic `UIAutomationObserver` for a real,
visible `WebObserver` (Playwright), which it launches and navigates to
Inbox Dispatch itself — no attaching to an externally-opened browser.
Everything downstream of `self._observer` (`_request_snapshot()`,
click/keystroke handling, F10 save) is unchanged, since `WebObserver`
returns the same dict shape `UIAutomationObserver` already does. A new
`reply_trace_translator.py` script reads a finished session's
`live_step_NNNN.json` files, finds submitted-reply steps by a
state-transition signal (not pixel matching), and calls the existing
`reply_recorder.record_reply_example()`. Electron's Recording button
infers `trace_type="web"`/`url=<capsule url>` automatically when the
loaded capsule is Inbox Dispatch — no new UI element.

**Tech Stack:** Python (pytest, Playwright, pynput, mss), Electron/Node
(existing `main.js`/`preload.js`/`renderer.js`), no new dependencies —
`playwright` and `pynput` are already project dependencies.

**Spec:** `docs/superpowers/specs/2026-08-28-scope3-reply-recording-via-demorecorder-design.md`

## Global Constraints

- `WebObserver` launches its own browser (`headless=False`) — it never
  attaches to an externally-opened one via CDP. No `--remote-debugging-port`
  flag, no port-picking logic, anywhere in this plan.
- `trace_type="web"` failing to connect raises (`ImportError`/`ValueError`/`RuntimeError`)
  from `DemoRecorder.__init__` — it must never silently fall back to
  `UIAutomationObserver` for an explicit web request.
- `reply_recorder.py`, `reply_features.py`, `reply_model.py`,
  `train_reply_model.py`, `reply_agent.py`, `autonomous_watcher.py` are
  not modified by this plan at all.
- The direct HTTP-based reply capture (`local_ui`'s textarea →
  `/api/confirm`/`/api/override`) keeps working exactly as shipped
  earlier — nothing in this plan removes it.
- Every new/modified Python file follows this project's established
  `sys.path.insert` bootstrap pattern (see any existing file under
  `components/` for the exact shape) — no new packaging mechanism.
- Full test suite (`pytest -q` from repo root) must show 0 failed
  before any task is considered done.

---

### Task 1: Wire `WebObserver` into `DemoRecorder`

**Files:**
- Modify: `components/recorder/recorder.py` (import block ~line 97,
  `DemoRecorder.__init__` ~line 1346-1363, `DemoRecorder.run()`'s
  `finally:` block ~line 1552)
- Test: `tests/test_demo_recorder_web_observer.py` (new)

**Interfaces:**
- Consumes: `components.observers.web_observer.WebObserver` (existing,
  unmodified) — `WebObserver(headless: bool = False)`,
  `.connect(url: Optional[str] = None) -> bool`, `.snapshot() -> dict`,
  `.disconnect() -> None`.
- Produces: `DemoRecorder(output_dir: str = "data/demos/human",
  trace_type: str = "form_filling", url: str = "")` — the `url` param
  is new; `self._observer` is a `WebObserver` instance when
  `trace_type == "web"`, otherwise unchanged (`UIAutomationObserver`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_demo_recorder_web_observer.py
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import recorder.recorder as recorder_module


class _FakeMouseKeyboard:
    """Stands in for pynput's real Listener -- no global input hooks."""
    def __init__(self, *a, **k):
        pass
    def start(self):
        pass
    def stop(self):
        pass
    def is_alive(self):
        return True
    def join(self, timeout=None):
        pass


class _FakeWebObserver:
    def __init__(self, headless=False):
        self.headless = headless
        self.connected_to = None
        self.disconnected = False

    def connect(self, url=None):
        self.connected_to = url
        return True

    def snapshot(self):
        return {"application": "browser", "elements": []}

    def disconnect(self):
        self.disconnected = True


class _FakeWebObserverThatFailsToConnect(_FakeWebObserver):
    def connect(self, url=None):
        self.connected_to = url
        return False


def _patch_pynput(monkeypatch):
    monkeypatch.setattr(recorder_module, "_pynput_mouse", MagicMock(Listener=_FakeMouseKeyboard))
    monkeypatch.setattr(recorder_module, "_pynput_keyboard", MagicMock(Listener=_FakeMouseKeyboard))


class TestWebTraceType:
    def test_web_trace_type_constructs_web_observer(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _FakeWebObserver)

        rec = recorder_module.DemoRecorder(
            output_dir=str(tmp_path), trace_type="web", url="http://localhost:8765/")

        assert isinstance(rec._observer, _FakeWebObserver)
        assert rec._observer.connected_to == "http://localhost:8765/"

    def test_web_trace_type_without_url_raises(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _FakeWebObserver)

        with pytest.raises(ValueError):
            recorder_module.DemoRecorder(output_dir=str(tmp_path), trace_type="web", url="")

    def test_web_trace_type_when_unavailable_raises_import_error(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", False)

        with pytest.raises(ImportError):
            recorder_module.DemoRecorder(
                output_dir=str(tmp_path), trace_type="web", url="http://localhost:8765/")

    def test_web_trace_type_when_connect_fails_raises_runtime_error_not_fallback(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _FakeWebObserverThatFailsToConnect)

        with pytest.raises(RuntimeError):
            recorder_module.DemoRecorder(
                output_dir=str(tmp_path), trace_type="web", url="http://localhost:8765/")

    def test_non_web_trace_type_is_unaffected(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _FakeWebObserver)

        rec = recorder_module.DemoRecorder(output_dir=str(tmp_path), trace_type="form_filling")

        assert not isinstance(rec._observer, _FakeWebObserver)
        assert hasattr(rec._observer, "snapshot")


class TestWebObserverCleanup:
    def test_run_disconnects_web_observer_on_stop(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _FakeWebObserver)

        rec = recorder_module.DemoRecorder(
            output_dir=str(tmp_path), trace_type="web", url="http://localhost:8765/")
        rec._quit_event.set()  # run() exits its wait() immediately
        rec.run(auto_start=False)

        assert rec._observer.disconnected is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_demo_recorder_web_observer.py -v`
Expected: FAIL — `AttributeError: module 'recorder.recorder' has no
attribute '_WEB_OBSERVER_AVAILABLE'` (or similar; the import/branch
doesn't exist yet).

- [ ] **Step 3: Add the lazy `WebObserver` import**

In `components/recorder/recorder.py`, immediately after the existing
`_CVVisionObserver` import block (ends ~line 97), add:

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

- [ ] **Step 4: Give `DemoRecorder.__init__` the `url` param and the `trace_type=="web"` branch**

Find this in `DemoRecorder.__init__` (~line 1346-1363):

```python
    def __init__(self, output_dir: str = "data/demos/human", trace_type: str = "form_filling"):
        if not _PYNPUT_AVAILABLE:
            raise ImportError("pynput is required. Install with: pip install pynput")
        if not _UIA_OBSERVER_AVAILABLE:
            raise ImportError("UIAutomationObserver not found in components/observers/.")

        from datetime import datetime as _dt
        _session_ts   = _dt.now().strftime("%Y%m%d_%H%M%S")
        _intern_dir   = _INTERN_DIR
        self.output_dir = os.path.join(_intern_dir, output_dir, f"session_{_session_ts}")
        self.trace_type = trace_type

        # Option A: only walk the foreground form + Notepad source window.
        # ~10x faster snapshots than walking every visible window.
        try:
            self._observer = _UIAObserver(background_apps={"notepad", ".txt"})
        except TypeError:
            self._observer = _UIAObserver()  # older signature fallback
```

Replace it with:

```python
    def __init__(self, output_dir: str = "data/demos/human", trace_type: str = "form_filling",
                 url: str = ""):
        if not _PYNPUT_AVAILABLE:
            raise ImportError("pynput is required. Install with: pip install pynput")

        from datetime import datetime as _dt
        _session_ts   = _dt.now().strftime("%Y%m%d_%H%M%S")
        _intern_dir   = _INTERN_DIR
        self.output_dir = os.path.join(_intern_dir, output_dir, f"session_{_session_ts}")
        self.trace_type = trace_type

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
            # Option A: only walk the foreground form + Notepad source window.
            # ~10x faster snapshots than walking every visible window.
            try:
                self._observer = _UIAObserver(background_apps={"notepad", ".txt"})
            except TypeError:
                self._observer = _UIAObserver()  # older signature fallback
```

- [ ] **Step 5: Add web-observer cleanup to `run()`'s `finally:` block**

Find the `finally:` block inside `DemoRecorder.run()` (~line 1552,
starts `self._recording = False`). Immediately after the
`try: listeners["k"].stop() ... except Exception: pass` lines, add:

```python
            if self.trace_type == "web":
                try:
                    self._observer.disconnect()
                except Exception:
                    pass
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_demo_recorder_web_observer.py -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Run the full suite to check for regressions**

Run: `python -m pytest -q`
Expected: same pass count as before this task, plus these 6, 0 failed.

- [ ] **Step 8: Commit**

```bash
git add components/recorder/recorder.py tests/test_demo_recorder_web_observer.py
git commit -m "Give DemoRecorder a real trace_type=web path via WebObserver"
```

---

### Task 2: Tag the reply textarea with the open message's id

**Files:**
- Modify: `components/inbox_router/local_ui/app.js` (`openMessage()`,
  ~line 154-165 per the current file)
- Test: `tests/test_automate_inbox.py` (extend — this file already has
  a real-Playwright-browser fixture pattern to mirror)

**Interfaces:**
- Consumes: nothing new.
- Produces: `#replyBody`'s `name` attribute equals the open message's
  `message_id` whenever a message is open. This is what
  `reply_trace_translator.py` (Task 3) reads via `WebObserver`'s
  already-existing `label`/`text` extraction (`name` attribute →
  `handle.get_attribute("name")`, already read by
  `WebObserver._extract_elements()` — no change needed there).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_automate_inbox.py` (uses the existing `real_page`
fixture already defined in that file):

```python
class TestReplyTextareaCarriesMessageId:
    def test_reply_body_name_attribute_matches_open_message_id(self, real_page):
        real_page.locator(".row-item").nth(0).click()
        real_page.wait_for_selector("#detailView:not([hidden])")

        name_attr = real_page.locator("#replyBody").get_attribute("name")

        assert name_attr == "i1"  # real_page's fixture opens messages i1/i2/i3 in order
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_automate_inbox.py::TestReplyTextareaCarriesMessageId -v`
Expected: FAIL — `assert None == "i1"` (no `name` attribute set today).

- [ ] **Step 3: Set `replyBody.name` in `openMessage()`**

In `components/inbox_router/local_ui/app.js`, find `openMessage()`:

```javascript
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
  replyBody.value = "";
  overrideSelect.value = "route_scope1";
  refreshReplyBoxVisibility();
  listView.hidden = true;
  detailView.hidden = false;
}
```

Add one line, `replyBody.name = email.message_id;`, right after
`replyBody.value = "";`:

```javascript
  replyBody.value = "";
  replyBody.name = email.message_id;
  overrideSelect.value = "route_scope1";
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_automate_inbox.py::TestReplyTextareaCarriesMessageId -v`
Expected: PASS.

- [ ] **Step 5: Run the file's full suite (this file spins up real Playwright browsers — confirm nothing else broke)**

Run: `python -m pytest tests/test_automate_inbox.py -q`
Expected: all tests pass (existing + 1 new).

- [ ] **Step 6: Commit**

```bash
git add components/inbox_router/local_ui/app.js tests/test_automate_inbox.py
git commit -m "Tag the reply textarea with the open message's id, for trace translation"
```

---

### Task 3: `reply_trace_translator.py` — trace session to real reply examples

**Files:**
- Create: `components/inbox_router/reply_trace_translator.py`
- Test: `tests/test_reply_trace_translator.py`

**Interfaces:**
- Consumes: `reply_recorder.record_reply_example(message: EmailMessage,
  reply_body: str, source: str, path: str) -> None` (existing,
  unmodified); `InboxRouter`/`MockGmailClient.get_message(message_id:
  str) -> Optional[EmailMessage]` (existing).
- Produces: `translate_session(session_dir: str, gmail_client,
  reply_examples_path: str) -> int` (returns count of examples
  written) — the function a CLI `main()` and tests both call.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reply_trace_translator.py
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components" / "inbox_router"))

from gmail_client import EmailMessage, MockGmailClient
import reply_recorder as rr
import reply_trace_translator as translator


def _step(step_idx, textarea_value="", message_id="", include_textarea=True):
    elements = []
    if include_textarea:
        elements.append({
            "element_id": "web_0", "type": "input", "control_type": "textarea",
            "text": message_id, "value": textarea_value, "label": message_id,
            "enabled": True, "visible": True, "source": "web",
        })
    return {
        "trace_id": f"live_step_{step_idx:04d}", "timestamp": "2026-08-28T00:00:00",
        "duration": 1.0, "type": "web",
        "state": {"application": "browser", "elements": elements},
        "mouse": {}, "keyboard": {},
        "next_state": {"application": "browser", "elements": elements},
    }


def _write_session(tmp_path, steps):
    session_dir = tmp_path / "session_20260828_000000"
    session_dir.mkdir()
    for i, step in enumerate(steps):
        (session_dir / f"live_step_{i:04d}.json").write_text(json.dumps(step), encoding="utf-8")
    return str(session_dir)


def _build_gmail_client(tmp_path, inbox):
    data_dir = tmp_path / "data"
    os.makedirs(data_dir, exist_ok=True)
    (data_dir / "mock_inbox.json").write_text(json.dumps({"inbox": inbox, "sent": []}), encoding="utf-8")
    return MockGmailClient(data_dir=str(data_dir))


def _msg(mid, sender_email, subject, body="body text"):
    return {
        "id": mid, "thread_id": mid, "sender": f"Someone <{sender_email}>", "sender_email": sender_email,
        "subject": subject, "snippet": "", "body_text": body, "received_at": "2026-08-27T00:00:00Z",
        "labels": ["INBOX"],
    }


class TestTranslateSession:
    def test_submitted_reply_is_recorded(self, tmp_path):
        # Step 0: textarea has real text for message m1.
        # Step 1: that textarea is gone -- Confirm/Override closed the detail view.
        steps = [
            _step(0, textarea_value="Thanks, that works for me.", message_id="m1"),
            _step(1, include_textarea=False),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 1
        examples = rr.load_reply_examples(examples_path)
        assert len(examples) == 1
        assert examples[0]["message_id"] == "m1"
        assert examples[0]["reply_body"] == "Thanks, that works for me."
        assert examples[0]["source"] == "live"

    def test_last_step_in_session_with_text_still_counts(self, tmp_path):
        # No "next" step at all -- Stop was pressed right after submitting.
        steps = [_step(0, textarea_value="Sure, call me in 10.", message_id="m1")]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "quick question")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 1

    def test_textarea_still_present_in_next_step_is_not_counted(self, tmp_path):
        # Detail view stayed open -- nothing was submitted yet.
        steps = [
            _step(0, textarea_value="typing...", message_id="m1"),
            _step(1, textarea_value="typing... more", message_id="m1"),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 0
        assert rr.load_reply_examples(examples_path) == []

    def test_empty_textarea_produces_no_example(self, tmp_path):
        steps = [
            _step(0, textarea_value="", message_id="m1"),
            _step(1, include_textarea=False),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 0

    def test_unresolvable_message_id_is_skipped_without_raising(self, tmp_path):
        steps = [
            _step(0, textarea_value="Thanks!", message_id="does-not-exist"),
            _step(1, include_textarea=False),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[])  # empty inbox -- nothing resolves
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)  # must not raise

        assert count == 0

    def test_no_step_files_returns_zero(self, tmp_path):
        session_dir = tmp_path / "empty_session"
        session_dir.mkdir()
        gmail_client = _build_gmail_client(tmp_path, inbox=[])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(str(session_dir), gmail_client, examples_path)

        assert count == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_reply_trace_translator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reply_trace_translator'`.

- [ ] **Step 3: Write `reply_trace_translator.py`**

```python
"""
components/inbox_router/reply_trace_translator.py
======================================================
Reads a finished DemoRecorder session (trace_type="web",
live_step_NNNN.json files) and pulls out real reply examples --
(which email, what was typed) -- recorded through the exact same
reply_recorder.record_reply_example() the direct HTTP-based capture
already uses. Manually run, same as train_reply_model.py:

    python components/inbox_router/reply_trace_translator.py --session-dir <path>

A step counts as a submitted reply when its own textarea value is
non-empty AND that same message_id-labeled textarea is absent from
the next step (Confirm/Override closed the detail view) -- or it's
the last step in the session (Stop was pressed right after
submitting). This avoids matching a raw click position against a DOM
element's bounding box: pynput's click_pos is in absolute screen
coordinates, WebObserver's bbox is viewport-relative, and the two
aren't directly comparable without also knowing the browser window's
on-screen position -- a problem this state-transition check sidesteps
entirely.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from typing import Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
for _p in (_ROOT, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import get_gmail_client
from reply_recorder import DEFAULT_REPLY_EXAMPLES_PATH, record_reply_example


def _find_reply_textarea(state: dict) -> Optional[dict]:
    for el in state.get("elements", []):
        if el.get("control_type") == "textarea":
            return el
    return None


def _load_steps(session_dir: str) -> list:
    paths = sorted(glob.glob(os.path.join(session_dir, "live_step_*.json")))
    steps = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            steps.append(json.load(f))
    return steps


def translate_session(session_dir: str, gmail_client, reply_examples_path: str = DEFAULT_REPLY_EXAMPLES_PATH) -> int:
    """Reads every live_step_NNNN.json in session_dir, in order, and
    records one reply example per submitted-reply step found. Returns
    how many were written. Never raises on an unresolvable message_id
    or a missing/empty session -- both are normal, loggable outcomes."""
    steps = _load_steps(session_dir)
    written = 0

    for i, step in enumerate(steps):
        state = step.get("state", {})
        textarea = _find_reply_textarea(state)
        if textarea is None:
            continue
        reply_body = (textarea.get("value") or "").strip()
        if not reply_body:
            continue
        message_id = textarea.get("label") or textarea.get("text") or ""
        if not message_id:
            continue

        is_last_step = (i == len(steps) - 1)
        if not is_last_step:
            next_state = steps[i + 1].get("state", {})
            next_textarea = _find_reply_textarea(next_state)
            still_open = (
                next_textarea is not None
                and (next_textarea.get("label") or next_textarea.get("text")) == message_id
            )
            if still_open:
                continue  # detail view stayed open -- nothing submitted yet

        message = gmail_client.get_message(message_id)
        if message is None:
            print(f"  [skip] step {i}: message_id {message_id!r} did not resolve to a real message")
            continue

        record_reply_example(message, reply_body, source="live", path=reply_examples_path)
        written += 1
        print(f"  [recorded] {message_id}: {reply_body[:60]!r}")

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate a recorded DemoRecorder session into real reply examples.")
    parser.add_argument("--session-dir", required=True, help="Path to a session_<timestamp> folder")
    parser.add_argument("--examples-path", default=DEFAULT_REPLY_EXAMPLES_PATH)
    args = parser.parse_args()

    gmail_client = get_gmail_client()
    count = translate_session(args.session_dir, gmail_client, args.examples_path)
    print(f"\n{count} real reply example(s) written to {args.examples_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_reply_trace_translator.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: 0 failed.

- [ ] **Step 6: Commit**

```bash
git add components/inbox_router/reply_trace_translator.py tests/test_reply_trace_translator.py
git commit -m "Add reply_trace_translator.py: recorded sessions to real reply examples"
```

---

### Task 4: `Bridge.start()` accepts `trace_type`/`url` and threads them to `DemoRecorder`

**Files:**
- Modify: `app/recorder_bridge.py` (`Bridge.start()` ~line 141-152,
  the `cmd == "start"` dispatch ~line 478-479)
- Test: `tests/test_recorder_bridge_web_recording.py` (new)

**Interfaces:**
- Consumes: `automate_inbox.ensure_server_running(timeout_s: float =
  20.0) -> subprocess.Popen | None` (existing, imported not
  duplicated); `DemoRecorder(output_dir, trace_type, url)` (Task 1).
- Produces: `Bridge.start(self, output_dir: str | None = None,
  trace_type: str = "form_filling", url: str = "") -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_recorder_bridge_web_recording.py
"""
Bridge.start() threading trace_type/url through to DemoRecorder, and
calling ensure_server_running() first when trace_type="web" -- so the
page actually exists before WebObserver tries to navigate to it.
Same subprocess.Popen-is-never-real approach as
test_recorder_bridge_capsule_run.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import recorder_bridge as rb


class _FakeRecorder:
    def __init__(self, output_dir="", trace_type="form_filling", url=""):
        self.output_dir = output_dir
        self.trace_type = trace_type
        self.url = url
        self._steps = []

    def run(self):
        pass


def test_start_passes_trace_type_and_url_to_demo_recorder(monkeypatch):
    captured = {}

    def fake_demo_recorder(output_dir="", trace_type="form_filling", url=""):
        captured.update(output_dir=output_dir, trace_type=trace_type, url=url)
        return _FakeRecorder(output_dir, trace_type, url)

    monkeypatch.setattr(rb, "DemoRecorder", fake_demo_recorder)
    monkeypatch.setattr(rb, "ensure_server_running", lambda: None)
    bridge = rb.Bridge()

    bridge.start(trace_type="web", url="http://localhost:8765/")

    assert captured["trace_type"] == "web"
    assert captured["url"] == "http://localhost:8765/"


def test_start_defaults_match_existing_behavior(monkeypatch):
    captured = {}

    def fake_demo_recorder(output_dir="", trace_type="form_filling", url=""):
        captured.update(output_dir=output_dir, trace_type=trace_type, url=url)
        return _FakeRecorder(output_dir, trace_type, url)

    monkeypatch.setattr(rb, "DemoRecorder", fake_demo_recorder)
    bridge = rb.Bridge()

    bridge.start()

    assert captured["trace_type"] == "form_filling"
    assert captured["url"] == ""


def test_start_with_web_trace_type_ensures_server_running_first(monkeypatch):
    calls = []
    monkeypatch.setattr(rb, "ensure_server_running", lambda: calls.append("ensured"))
    monkeypatch.setattr(rb, "DemoRecorder", lambda output_dir="", trace_type="form_filling", url="": _FakeRecorder())
    bridge = rb.Bridge()

    bridge.start(trace_type="web", url="http://localhost:8765/")

    assert calls == ["ensured"]


def test_start_with_non_web_trace_type_does_not_touch_server(monkeypatch):
    calls = []
    monkeypatch.setattr(rb, "ensure_server_running", lambda: calls.append("ensured"))
    monkeypatch.setattr(rb, "DemoRecorder", lambda output_dir="", trace_type="form_filling", url="": _FakeRecorder())
    bridge = rb.Bridge()

    bridge.start()

    assert calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_recorder_bridge_web_recording.py -v`
Expected: FAIL — `TypeError: start() got an unexpected keyword argument 'trace_type'`.

- [ ] **Step 3: Import `ensure_server_running` and update `Bridge.start()`**

In `app/recorder_bridge.py`, near the top where other project imports
live (check the existing `from ... import ...` block, same area
`DemoRecorder` is already imported from), add:

```python
from automate_inbox import ensure_server_running
```

Then find `Bridge.start()`:

```python
    def start(self, output_dir: str | None = None) -> None:
        if self._running:
            emit("error", message="Already recording.")
            return
        if output_dir:
            self._out_dir = output_dir if os.path.isabs(output_dir) else os.path.join(_ROOT, output_dir)

        try:
            self._recorder = DemoRecorder(output_dir=self._out_dir, trace_type="form_filling")
        except Exception as exc:
            emit("error", message=f"Failed to start recorder: {exc}")
            return
```

Replace with:

```python
    def start(self, output_dir: str | None = None, trace_type: str = "form_filling",
              url: str = "") -> None:
        if self._running:
            emit("error", message="Already recording.")
            return
        if output_dir:
            self._out_dir = output_dir if os.path.isabs(output_dir) else os.path.join(_ROOT, output_dir)

        if trace_type == "web":
            ensure_server_running()

        try:
            self._recorder = DemoRecorder(output_dir=self._out_dir, trace_type=trace_type, url=url)
        except Exception as exc:
            emit("error", message=f"Failed to start recorder: {exc}")
            return
```

- [ ] **Step 4: Thread `trace_type`/`url` through the `cmd == "start"` dispatch**

Find:

```python
            if cmd == "start":
                self.start(msg.get("output_dir"))
```

Replace with:

```python
            if cmd == "start":
                self.start(msg.get("output_dir"), msg.get("trace_type", "form_filling"),
                           msg.get("url", ""))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_recorder_bridge_web_recording.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: 0 failed. In particular confirm
`tests/test_recorder_bridge_inbox_router.py` and any other
`test_recorder_bridge_*.py` files still pass — `Bridge.start()`'s
signature changed, but every new param has a default matching the old
hardcoded behavior.

- [ ] **Step 7: Commit**

```bash
git add app/recorder_bridge.py tests/test_recorder_bridge_web_recording.py
git commit -m "Bridge.start() threads trace_type/url through to DemoRecorder"
```

---

### Task 5: Electron infers `trace_type="web"` when Inbox Dispatch is the loaded capsule

**Files:**
- Modify: `app_electron/preload.js` (`recorderAPI.start`, line 4)
- Modify: `app_electron/main.js` (`recorder-start` handler, ~line 643)
- Test: manual verification only (see Step 4) — this layer is pure
  Electron IPC glue with no existing JS test harness in this project;
  Tasks 1-4's Python tests already cover every byte of logic this task
  threads values through.

**Interfaces:**
- Consumes: `currentCapsuleName` (existing `main.js` module-level
  variable), `listCapsules()` (existing `main.js` function).
- Produces: nothing new consumed elsewhere — this is the top of the
  chain.

- [ ] **Step 1: Update `preload.js`'s `recorderAPI.start`**

Find:

```javascript
  start: (outputDir) => ipcRenderer.invoke("recorder-start", outputDir),
```

Replace with:

```javascript
  start: (outputDir) => ipcRenderer.invoke("recorder-start", outputDir),
  // trace_type/url are never passed by the renderer directly -- main.js
  // infers them from whichever capsule is currently loaded (see
  // "recorder-start" handler), so Start Recording needs no new button.
```

(No signature change needed here — the renderer's call site,
`window.recorderAPI.start(outDirInput.value.trim() || null)`, doesn't
change. `main.js`'s handler is where the inference happens, since it
already has `currentCapsuleName` in scope and the renderer doesn't.)

- [ ] **Step 2: Update `main.js`'s `recorder-start` handler**

Find:

```javascript
ipcMain.handle("recorder-start", (_evt, outputDir) => {
  queueOrSend({ cmd: "start", output_dir: outputDir || null });
});
```

Replace with:

```javascript
ipcMain.handle("recorder-start", (_evt, outputDir) => {
  const capsule = currentCapsuleName
    ? listCapsules().find((c) => c.name === currentCapsuleName)
    : null;
  const isWebCapsule = !!capsule && capsule.kind === "url";
  queueOrSend({
    cmd: "start", output_dir: outputDir || null,
    trace_type: isWebCapsule ? "web" : "form_filling",
    url: isWebCapsule ? capsule.url : "",
  });
});
```

- [ ] **Step 3: Syntax-check both files**

Run: `node --check app_electron/main.js && node --check app_electron/preload.js`
Expected: both exit 0, no output.

- [ ] **Step 4: Manual verification (only user-run per this project's
      "only the user runs live tasks" rule — this step is a checklist
      for them, not something to execute automatically)**

1. `npm start` in `app_electron/`.
2. Load the "Inbox Dispatch" capsule into the Play slot.
3. Click "Start recording" — a real, visible Chromium window should
   open navigated to `http://localhost:8765/` (not the Recorder's
   usual target).
4. Open an email, type a real reply, click Confirm.
5. Click "Stop recording" (or F10) — the browser window should close
   itself.
6. Run `python components/inbox_router/reply_trace_translator.py
   --session-dir <the session folder just printed>` and confirm it
   reports "1 real reply example(s) written."
7. Confirm `components/inbox_router/data/reply_examples.jsonl` has a
   new line with the exact real text typed in step 4.

- [ ] **Step 5: Commit**

```bash
git add app_electron/preload.js app_electron/main.js
git commit -m "Infer trace_type=web recording automatically for the Inbox Dispatch capsule"
```

---

### Task 6: Update DEVELOPERS.md and the Task Tree

**Files:**
- Modify: `DEVELOPERS.md` (extend the `scope3_learned_autonomous_reply`
  entry, or add a new dated sub-entry immediately after it — match
  whichever this project's convention favors by looking at how the
  same entry was already extended once today)
- Modify: `treetask/index.html` (same node, mirrored)

**Interfaces:** none — documentation only.

- [ ] **Step 1: Read the current `scope3_learned_autonomous_reply` entries in both files**

`grep -n "scope3_learned_autonomous_reply" DEVELOPERS.md treetask/index.html`

- [ ] **Step 2: Append a dated note to both** covering: the direct
      instruction ("recording must go through Electron's Recording
      feature"), the `ScreenObserver`-vs-`DemoRecorder` correction
      made while writing the plan (a real, worth-recording finding —
      this project's own convention throughout today's other entries
      already records corrections like this, not just successes), the
      `WebObserver` wiring, `reply_trace_translator.py`, and the final
      test counts once Tasks 1-5 are done and the full suite has been
      run once at the end.

- [ ] **Step 3: Verify `treetask/index.html`'s script block still parses**

Run:
```bash
node -e "
const fs = require('fs');
const html = fs.readFileSync('treetask/index.html', 'utf8');
const m = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
m.forEach((x,i)=>{ try { new Function(x[1]); console.log(i,'OK'); } catch(e){ console.log(i,'ERR',e.message); } });
"
```
Expected: `0 OK`.

- [ ] **Step 4: Commit and push**

```bash
git add DEVELOPERS.md treetask/index.html
git commit -m "Sync Task Tree and DEVELOPERS.md with reply recording via DemoRecorder"
git push
```
