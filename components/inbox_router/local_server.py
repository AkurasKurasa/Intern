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
from urllib.parse import urlsplit

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

DEFAULT_PORT = 8765
_ALLOWED_ORIGIN_HOSTS = {"localhost", "127.0.0.1"}
_UI_DIR = os.path.join(_THIS_DIR, "local_ui")
_PRACTICE_UI_DIR = os.path.join(_THIS_DIR, "practice_inbox")
_COLD_EMAIL_UI_DIR = os.path.join(_THIS_DIR, "cold_email")

_STATIC_FILES = {
    "/": (_UI_DIR, "index.html", "text/html"),
    "/style.css": (_UI_DIR, "style.css", "text/css"),
    "/app.js": (_UI_DIR, "app.js", "application/javascript"),
    "/practice/": (_PRACTICE_UI_DIR, "index.html", "text/html"),
    "/practice/style.css": (_PRACTICE_UI_DIR, "style.css", "text/css"),
    "/practice/app.js": (_PRACTICE_UI_DIR, "app.js", "application/javascript"),
    "/cold-email/": (_COLD_EMAIL_UI_DIR, "index.html", "text/html"),
    "/cold-email/style.css": (_COLD_EMAIL_UI_DIR, "style.css", "text/css"),
    "/cold-email/app.js": (_COLD_EMAIL_UI_DIR, "app.js", "application/javascript"),
}


def _is_allowed_origin(origin: str) -> bool:
    """Same-origin check by scheme+host, not a hardcoded port -- the local
    server can bind to any port (the real app always uses DEFAULT_PORT, but
    tests spin one up on an OS-assigned port), so pinning this to 8765
    would reject every real POST a browser test makes. Blocks anything
    that isn't plain http(s) on localhost/127.0.0.1, same as the previous
    exact-match set did for the one port it recognized."""
    parts = urlsplit(origin)
    return parts.scheme in ("http", "https") and parts.hostname in _ALLOWED_ORIGIN_HOSTS


def _pick_provider() -> Tuple[str, str]:
    """Same preference order as router.py's own _pick_provider()."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic", os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("GROQ_API_KEY"):
        return "groq", os.environ["GROQ_API_KEY"]
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini", os.environ["GEMINI_API_KEY"]
    return "lmstudio", ""


def build_router(gmail_client=None) -> InboxRouter:
    # Imported here, not at module level: router.py pulls in inbox_agent.py,
    # which does `import torch` at its own module level. That import alone
    # takes several seconds, so keeping it out of local_server.py's module
    # scope is what actually makes serve()'s "bind first" trick work --
    # otherwise the whole torch import chain runs before serve() is even
    # entered, and the socket never binds any earlier than it does today.
    from llm_classifier import LLMClassifier
    from pattern_profile import PatternProfile
    from routing_rules import RuleLayer
    from router import InboxRouter

    if gmail_client is None:
        gmail_client = get_gmail_client()
    profile = PatternProfile()
    rule_layer = RuleLayer(profile)
    provider, api_key = _pick_provider()
    classifier = LLMClassifier(provider=provider, api_key=api_key)
    return InboxRouter(gmail_client, profile, rule_layer, classifier)


def build_cold_email_sender(gmail_client=None):
    from cold_email_sender import ColdEmailSender

    if gmail_client is None:
        gmail_client = get_gmail_client()
    return ColdEmailSender(gmail_client)


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


def handle_request(method: str, path: str, body: bytes, router: InboxRouter, origin: str = None,
                    cold_email_sender=None) -> Tuple[int, dict, bytes, str]:
    """Pure request handler, separated from BaseHTTPRequestHandler so it's
    testable without opening a real socket. Returns
    (status_code, extra_headers, response_body_bytes, content_type)."""
    if method == "POST" and origin is not None and not _is_allowed_origin(origin):
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

    if method == "GET" and path == "/api/inbox/unprocessed":
        payload = json.dumps({"waiting": router.list_unprocessed_stubs()}).encode("utf-8")
        return 200, {}, payload, "application/json"

    if method == "POST" and path == "/api/inbox/process-next":
        entry = router.process_next_unprocessed()
        if entry is None:
            return 200, {}, json.dumps({"done": True}).encode("utf-8"), "application/json"
        payload = json.dumps({"done": False, "entry": entry}).encode("utf-8")
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
        reply_body = data.get("reply_body", "")
        event_start = data.get("event_start", "")
        event_end = data.get("event_end", "")
        forward_to = data.get("forward_to", "")
        router.confirm_suggestion(message_id, decision, reply_body=reply_body,
                                   event_start=event_start, event_end=event_end, forward_to=forward_to)
        return 200, {}, json.dumps({"ok": True}).encode("utf-8"), "application/json"

    if method == "POST" and path == "/api/override":
        data, error = _parse_action_body(body, ("message_id", "new_decision"))
        if error:
            return error
        message_id, new_decision = data["message_id"], data["new_decision"]
        reason = data.get("reason", "")
        reply_body = data.get("reply_body", "")
        event_start = data.get("event_start", "")
        event_end = data.get("event_end", "")
        forward_to = data.get("forward_to", "")
        if not any(e["message_id"] == message_id for e in router.pending_entries()):
            err = json.dumps({"error": f"Unknown or already-handled message_id: {message_id}"}).encode("utf-8")
            return 400, {}, err, "application/json"
        router.override_decision(message_id, new_decision, reason, reply_body=reply_body,
                                  event_start=event_start, event_end=event_end, forward_to=forward_to)
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
        reply_body = data.get("reply_body", "")
        try:
            router.record_practice_decision(data["message_id"], data["decision"], reply_body=reply_body)
        except ValueError as exc:
            err = json.dumps({"error": f"Bad request: {exc}"}).encode("utf-8")
            return 400, {}, err, "application/json"
        return 200, {}, json.dumps({"ok": True}).encode("utf-8"), "application/json"

    if method == "GET" and path == "/cold-email/api/targets":
        targets = cold_email_sender.list_pending_targets()
        payload = json.dumps({"targets": [
            {"name": t.name, "email": t.email, "context_line": t.context_line}
            for t in targets
        ]}).encode("utf-8")
        return 200, {}, payload, "application/json"

    if method == "POST" and path == "/cold-email/api/send":
        data, error = _parse_action_body(body, ("email", "subject", "body"))
        if error:
            return error
        draft_id = cold_email_sender.send_cold_email(data["email"], data["subject"], data["body"])
        if not draft_id:
            err = json.dumps({"error": "Type a subject and a message before sending."}).encode("utf-8")
            return 400, {}, err, "application/json"
        return 200, {}, json.dumps({"ok": True}).encode("utf-8"), "application/json"

    return 404, {}, json.dumps({"error": "Not found"}).encode("utf-8"), "application/json"


def make_handler(router: InboxRouter, cold_email_sender=None):
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
            status, headers, payload, content_type = handle_request(
                method, path, body, router, origin=origin, cold_email_sender=cold_email_sender)
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
    gmail_client = get_gmail_client()
    router = build_router(gmail_client)
    cold_email_sender = build_cold_email_sender(gmail_client)
    httpd.RequestHandlerClass = make_handler(router, cold_email_sender)
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
