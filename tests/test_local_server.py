import json
import os
import subprocess
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
import reply_recorder


def _write_fixture(data_dir, inbox=None, sent=None):
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "mock_inbox.json"), "w", encoding="utf-8") as f:
        json.dump({"inbox": inbox or [], "sent": sent or []}, f)


def _msg(mid, sender_email, subject, body="body text"):
    return {
        "id": mid, "thread_id": mid, "sender": sender_email, "sender_email": sender_email,
        "subject": subject, "snippet": "", "body_text": body, "received_at": "2026-08-26T00:00:00Z",
    }


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


def _build_router(tmp_path, inbox=None, calendar_client=None):
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
                        examples_path=str(tmp_path / "training_examples.jsonl"),
                        reply_examples_path=str(tmp_path / "reply_examples.jsonl"),
                        schedule_log_path=str(tmp_path / "schedule.txt"),
                        calendar_client=calendar_client)


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


class TestHandleRequestInboxUnprocessed:
    """The autonomous-run pair: peek at what's waiting, then classify
    exactly one at a time through the real pipeline -- so a UI can drive
    the same reasoning poll_once() does, but visibly, one step at a time,
    instead of dumping the whole inbox pre-decided."""

    def test_get_unprocessed_lists_waiting_mail_with_no_decision(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        status, _headers, body, content_type = ls.handle_request("GET", "/api/inbox/unprocessed", b"", router)
        assert status == 200
        assert content_type == "application/json"
        payload = json.loads(body)
        assert payload["waiting"] == [{
            "message_id": "i1", "subject": "unrelated",
            "sender": "stranger@x.com", "sender_email": "stranger@x.com",
        }]
        assert "decision" not in payload["waiting"][0]

    def test_post_process_next_classifies_one_via_the_real_pipeline(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        status, _headers, body, content_type = ls.handle_request("POST", "/api/inbox/process-next", b"", router)
        assert status == 200
        assert content_type == "application/json"
        payload = json.loads(body)
        assert payload["done"] is False
        assert payload["entry"]["message_id"] == "i1"
        assert payload["entry"]["decision"] == "flag"  # no LLM configured -> flag
        # The one message just processed no longer shows up as waiting.
        status, _headers, body, _ct = ls.handle_request("GET", "/api/inbox/unprocessed", b"", router)
        assert json.loads(body)["waiting"] == []

    def test_post_process_next_reports_done_when_inbox_is_empty(self, tmp_path):
        router = _build_router(tmp_path, inbox=[])
        status, _headers, body, _ct = ls.handle_request("POST", "/api/inbox/process-next", b"", router)
        assert status == 200
        assert json.loads(body) == {"done": True}


class TestHandleRequestConfirm:
    def test_post_confirm_records_a_real_decision(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        body = json.dumps({"message_id": "i1", "decision": "leave_alone"}).encode("utf-8")
        status, _headers, resp_body, content_type = ls.handle_request("POST", "/api/confirm", body, router)
        assert status == 200
        assert json.loads(resp_body) == {"ok": True}
        assert router.pending_entries() == []

    def test_post_confirm_reply_with_real_text_creates_a_draft_with_that_text(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        body = json.dumps({"message_id": "i1", "decision": "reply",
                            "reply_body": "Thanks, I'll get back to you."}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/api/confirm", body, router)
        assert status == 200
        assert json.loads(resp_body) == {"ok": True}
        examples = reply_recorder.load_reply_examples(path=router._reply_examples_path)
        assert examples[0]["reply_body"] == "Thanks, I'll get back to you."

    def test_post_override_forward_with_typed_recipient_addresses_the_draft(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        body = json.dumps({"message_id": "i1", "new_decision": "forward",
                            "reply_body": "FYI.", "forward_to": "colleague@example.com"}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/api/override", body, router)
        assert status == 200
        assert json.loads(resp_body) == {"ok": True}
        drafts = json.loads((tmp_path / "data" / "mock_drafts.json").read_text())["drafts"]
        assert drafts[0]["to"] == "colleague@example.com"

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

    def test_post_confirm_non_dict_json_returns_400(self, tmp_path):
        # Regression test: body is valid JSON (5) but not an object. json.loads()
        # succeeds, but indexing data["message_id"] raises TypeError, which must
        # be caught alongside JSONDecodeError and KeyError.
        router = _build_router(tmp_path)
        status, _headers, _body, _ct = ls.handle_request("POST", "/api/confirm", b"5", router)
        assert status == 400

    def test_post_confirm_from_disallowed_origin_returns_403(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        body = json.dumps({"message_id": "i1", "decision": "leave_alone"}).encode("utf-8")
        status, _headers, _body, _ct = ls.handle_request("POST", "/api/confirm", body, router, origin="http://evil.example.com")
        assert status == 403

    def test_post_confirm_from_localhost_on_a_non_default_port_is_allowed(self, tmp_path):
        # Regression: the origin allowlist used to be an exact-match set
        # hardcoded to DEFAULT_PORT (8765). The real app always runs on
        # that port, but a test server binds an OS-assigned random one --
        # every POST a real browser made against it was silently rejected
        # as "Origin not allowed", which is exactly what broke the
        # autonomous-run feature the first time it was driven by an actual
        # browser instead of a bodyless GET. Any localhost/127.0.0.1 origin
        # must be accepted regardless of port.
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        body = json.dumps({"message_id": "i1", "decision": "leave_alone"}).encode("utf-8")
        status, _headers, _body, _ct = ls.handle_request(
            "POST", "/api/confirm", body, router, origin="http://127.0.0.1:54321")
        assert status == 200

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


class TestHandleRequestOverride:
    def test_post_override_records_the_new_decision(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        body = json.dumps({"message_id": "i1", "new_decision": "reply", "reason": "actually needs one"}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/api/override", body, router)
        assert status == 200
        assert json.loads(resp_body) == {"ok": True}

    def test_post_override_to_reply_with_real_text_creates_a_draft(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        router.poll_once()
        body = json.dumps({"message_id": "i1", "new_decision": "reply",
                            "reply_body": "Sure, sounds good."}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/api/override", body, router)
        assert status == 200
        examples = reply_recorder.load_reply_examples(path=router._reply_examples_path)
        assert examples[0]["reply_body"] == "Sure, sounds good."

    def test_post_override_unknown_message_id_returns_400(self, tmp_path):
        router = _build_router(tmp_path)
        body = json.dumps({"message_id": "i1", "new_decision": "reply"}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/api/override", body, router)
        assert status == 400
        assert json.loads(resp_body)["error"]

    def test_post_api_override_schedule_with_dates_creates_calendar_event(self, tmp_path):
        calendar = FakeCalendarClient()
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "vendor call")],
                                calendar_client=calendar)
        router.poll_once()
        body = json.dumps({
            "message_id": "i1", "new_decision": "schedule",
            "reply_body": "Vendor call Sept 3rd.",
            "event_start": "2026-09-03T14:00:00-07:00",
            "event_end": "2026-09-03T14:30:00-07:00",
        }).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/api/override", body, router)

        assert status == 200
        assert json.loads(resp_body) == {"ok": True}
        assert len(calendar.events) == 1
        assert calendar.events[0]["start"] == "2026-09-03T14:00:00-07:00"

    def test_post_api_override_schedule_without_dates_creates_no_event(self, tmp_path):
        calendar = FakeCalendarClient()
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "vendor call")],
                                calendar_client=calendar)
        router.poll_once()
        body = json.dumps({"message_id": "i1", "new_decision": "schedule", "reply_body": "note"}).encode("utf-8")
        status, _headers, _resp_body, _ct = ls.handle_request("POST", "/api/override", body, router)

        assert status == 200
        assert calendar.events == []


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

    def test_original_index_html_still_serves_after_static_files_widened(self, tmp_path):
        # Regression guard for the _STATIC_FILES shape change in this task --
        # confirms widening the map to (directory, filename, content_type)
        # didn't break the three pre-existing entries.
        router = _build_router(tmp_path)
        status, _headers, body, content_type = ls.handle_request("GET", "/", b"", router)
        assert status == 200
        assert content_type == "text/html"
        assert b"<html" in body.lower()


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

    def test_post_practice_record_with_reply_body_records_real_content(self, tmp_path):
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "hello")])
        body = json.dumps({"message_id": "i1", "decision": "reply", "reply_body": "Sounds good."}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/practice/api/record", body, router)
        assert status == 200
        assert json.loads(resp_body) == {"ok": True}
        examples = reply_recorder.load_reply_examples(path=str(tmp_path / "reply_examples.jsonl"))
        assert len(examples) == 1
        assert examples[0]["reply_body"] == "Sounds good."

    def test_post_practice_record_without_reply_body_still_works(self, tmp_path):
        # reply_body is optional -- omitting it entirely (not just sending
        # blank) must not break the existing decision-only flow.
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "hello")])
        body = json.dumps({"message_id": "i1", "decision": "flag"}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/practice/api/record", body, router)
        assert status == 200
        assert json.loads(resp_body) == {"ok": True}
        assert not (tmp_path / "reply_examples.jsonl").exists()

    def test_post_practice_record_invalid_decision_returns_400_not_a_dropped_connection(self, tmp_path):
        # decision_recorder.record_example() validates against
        # DECISIONS_ORDER and raises ValueError on an unrecognized
        # decision (e.g. a stale "route_scope1"). Before this test was
        # added, that ValueError propagated straight out of
        # handle_request() uncaught, which the real HTTP server turns
        # into a dropped connection instead of a clean error response --
        # found by hand-testing the real running server against an
        # invalid decision.
        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "hello")])
        body = json.dumps({"message_id": "i1", "decision": "route_scope1"}).encode("utf-8")
        status, _headers, resp_body, _ct = ls.handle_request("POST", "/practice/api/record", body, router)
        assert status == 400
        assert "route_scope1" in json.loads(resp_body)["error"]


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


class TestBulkBarHiddenAttribute:
    """Regression for a CSS-specificity bug found in local_ui/style.css:
    `.bulk-bar { display: flex }` is a class selector, which ties in
    specificity with the browser's own `[hidden] { display: none }` rule --
    and author styles win a specificity tie over the UA stylesheet. So the
    bulk-select bar rendered visible even with its `hidden` attribute set,
    on a page that had never had any rows selected. Fixed with an explicit
    `.bulk-bar[hidden] { display: none }` override -- the same fix pattern
    already used for `#ppTestGroup[hidden]` in the Electron app's CSS
    earlier in this project."""

    def test_bulk_bar_is_actually_invisible_with_nothing_selected(self, tmp_path):
        pytest.importorskip("playwright.sync_api")
        from playwright.sync_api import sync_playwright

        router = _build_router(tmp_path, inbox=[_msg("i1", "stranger@x.com", "unrelated")])
        handler_cls = ls.make_handler(router)
        httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/")
                page.wait_for_selector("#rowList .row-item")

                assert page.eval_on_selector("#bulkBar", "el => el.hidden") is True
                assert page.eval_on_selector("#bulkBar", "el => getComputedStyle(el).display") == "none"

                page.locator(".row-checkbox").first.check()
                page.wait_for_timeout(100)
                assert page.eval_on_selector("#bulkBar", "el => el.hidden") is False
                assert page.eval_on_selector("#bulkBar", "el => getComputedStyle(el).display") == "flex"

                browser.close()
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


class TestModuleImportStaysLight:
    """Regression: local_server.py used to import `from router import
    InboxRouter` at module level. router.py imports inbox_agent.py, which
    does `import torch` at ITS module level -- so the whole torch import
    chain used to run before serve() (and therefore HTTPServer()) was ever
    reached, silently defeating serve()'s documented "bind the socket
    before the slow import chain runs" design. A browser opening
    http://localhost:8765/ during that 8-12+ second window saw "connection
    refused", not a slow-loading page, because nothing was listening yet.
    Fixed by moving the heavy imports inside build_router(), which only
    runs after HTTPServer() has already bound and started listening.

    Runs in a fresh subprocess: this test file's own top-level `from
    router import InboxRouter` (needed by other tests) already pulls torch
    into THIS process, so an in-process check here would be meaningless."""

    def test_importing_local_server_does_not_import_torch(self):
        script = (
            "import sys; "
            f"sys.path.insert(0, {_ROOT!r}); "
            f"sys.path.insert(0, {_INBOX_DIR!r}); "
            "import local_server; "
            "print('torch' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False", (
            f"importing local_server pulled in torch -- "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )


class TestScheduleSingleWhenField:
    """Regression + coverage for collapsing Schedule's two datetime-local
    fields (Starts/Ends) into one. A human deciding to schedule something
    only ever picks one moment ("schedule this for 4pm") -- asking for a
    second, separate end time made the UI ask a question nobody actually
    has an answer to when they're reading an email. The end time is now
    computed client-side (app.js's addMinutes()) as start + 30 minutes,
    never typed by the human."""

    def test_picking_one_when_value_creates_a_30_minute_calendar_event(self, tmp_path):
        pytest.importorskip("playwright.sync_api")
        from playwright.sync_api import sync_playwright

        calendar = FakeCalendarClient()
        router = _build_router(tmp_path, inbox=[_msg("i1", "boss@x.com", "Kickoff call")],
                                calendar_client=calendar)
        handler_cls = ls.make_handler(router)
        httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/")
                page.wait_for_selector("#rowList .row-item")
                page.locator(".row-item").first.click()
                page.wait_for_selector("#scheduleBtn")
                page.click("#scheduleBtn")
                page.wait_for_selector("#scheduleDatesWrap:not([hidden])")
                # Exactly one datetime field is shown now -- confirms the old
                # two-field Starts/Ends form is actually gone, not just that
                # a new field happens to also work.
                assert page.locator("#scheduleDatesWrap input[type=datetime-local]").count() == 1
                page.fill("#eventWhen", "2026-09-03T14:00")
                page.fill("#replyBody", "Kickoff call with the client")
                page.click("#sendBtn")
                page.wait_for_timeout(300)
                browser.close()
        finally:
            httpd.shutdown()
            thread.join(timeout=5)

        assert len(calendar.events) == 1
        event = calendar.events[0]
        assert event["start"] == "2026-09-03T14:00"
        assert event["end"] == "2026-09-03T14:30"

    def test_leaving_when_empty_refuses_to_send(self, tmp_path):
        pytest.importorskip("playwright.sync_api")
        from playwright.sync_api import sync_playwright

        calendar = FakeCalendarClient()
        router = _build_router(tmp_path, inbox=[_msg("i1", "boss@x.com", "Kickoff call")],
                                calendar_client=calendar)
        handler_cls = ls.make_handler(router)
        httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/")
                page.wait_for_selector("#rowList .row-item")
                page.locator(".row-item").first.click()
                page.wait_for_selector("#scheduleBtn")
                page.click("#scheduleBtn")
                page.wait_for_selector("#scheduleDatesWrap:not([hidden])")
                page.fill("#replyBody", "Kickoff call with the client")
                page.click("#sendBtn")
                page.wait_for_timeout(200)
                assert "pick when" in page.eval_on_selector("#detailStatus", "el => el.textContent").lower()
                browser.close()
        finally:
            httpd.shutdown()
            thread.join(timeout=5)

        assert calendar.events == []
