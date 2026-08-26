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
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
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
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
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
