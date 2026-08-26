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
_ALLOWED_ORIGINS = {f"http://localhost:{DEFAULT_PORT}", f"http://127.0.0.1:{DEFAULT_PORT}"}
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


def _parse_action_body(body: bytes, required_keys) -> Tuple[dict, tuple]:
    """Parse a POST body as JSON and confirm it has every key in
    required_keys. Returns (data, None) on success, or (None, error_tuple)
    on failure -- the caller returns error_tuple directly as its own
    (status, headers, body, content_type) response."""
    try:
        data = json.loads(body or b"{}")
        for key in required_keys:
            _ = data[key]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        err = json.dumps({"error": f"Bad request: {exc}"}).encode("utf-8")
        return None, (400, {}, err, "application/json")
    return data, None


def handle_request(method: str, path: str, body: bytes, router: InboxRouter, origin: str = None) -> Tuple[int, dict, bytes, str]:
    """Pure request handler, separated from BaseHTTPRequestHandler so it's
    testable without opening a real socket. Returns
    (status_code, extra_headers, response_body_bytes, content_type)."""
    if method == "POST" and origin is not None and origin not in _ALLOWED_ORIGINS:
        err = json.dumps({"error": "Origin not allowed"}).encode("utf-8")
        return 403, {}, err, "application/json"

    if method == "GET" and path in _STATIC_FILES:
        directory, filename, content_type = _STATIC_FILES[path]
        file_path = os.path.join(directory, filename)
        if not os.path.isfile(file_path):
            return 404, {}, b"Not found", "text/plain"
        with open(file_path, "rb") as f:
            return 200, {}, f.read(), content_type

    if method == "GET" and path == "/api/inbox":
        router.poll_once()
        payload = json.dumps({"pending": router.pending_entries()}).encode("utf-8")
        return 200, {}, payload, "application/json"

    if method == "POST" and path == "/api/confirm":
        data, error = _parse_action_body(body, ("message_id", "decision"))
        if error:
            return error
        message_id, decision = data["message_id"], data["decision"]
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
        data, error = _parse_action_body(body, ("message_id", "new_decision"))
        if error:
            return error
        message_id, new_decision = data["message_id"], data["new_decision"]
        reason = data.get("reason", "")
        if not any(e["message_id"] == message_id for e in router.pending_entries()):
            err = json.dumps({"error": f"Unknown or already-handled message_id: {message_id}"}).encode("utf-8")
            return 400, {}, err, "application/json"
        router.override_decision(message_id, new_decision, reason)
        return 200, {}, json.dumps({"ok": True}).encode("utf-8"), "application/json"

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
            self._respond("POST", self.path, body, self.headers.get("Origin"))

        def _respond(self, method, path, body, origin=None):
            status, headers, payload, content_type = handle_request(method, path, body, router, origin=origin)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def serve(port: int = DEFAULT_PORT) -> None:
    # Bind and start listening FIRST, before the slow torch/sentence-encoder
    # import chain build_router() triggers -- a browser connecting during
    # that window gets its TCP connection accepted into the OS backlog
    # (instead of "connection refused"), even though nothing is ready to
    # actually answer yet. The real handler class is installed once ready,
    # before serve_forever() starts processing any request.
    try:
        httpd = HTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    except OSError as exc:
        print(f"Could not start local server on port {port} (already running?): {exc}")
        return
    print(f"Inbox Dispatch local server listening on http://localhost:{port}/")
    router = build_router()
    httpd.RequestHandlerClass = make_handler(router)
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
