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
