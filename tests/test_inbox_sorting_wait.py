"""After Launch resets the practice data, the first inbox load sorts all 30
emails before answering (~45 s measured). Found 2026-10-05: the page looked
empty the whole time ("There are no pending emails whatsoever"), and Play's
60 s wait for that load could time out and skip the inbox phase.

Run:  python -m pytest tests/test_inbox_sorting_wait.py -q
"""
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ENTRY = {"message_id": "m1", "sender": "Ann <a@e.com>", "sender_email": "a@e.com",
         "subject": "Hello", "body_text": "Hi", "decision": "reply", "rationale": "r",
         "confidence": 0.9, "layer": "rule", "status": "pending"}


class _SlowRouter:
    """Sorting takes a while, like the real first load after a reset."""

    def __init__(self, delay):
        self.delay = delay

    def poll_once(self):
        time.sleep(self.delay)

    def pending_entries(self):
        return [ENTRY]

    def __getattr__(self, name):
        return lambda *a, **k: []


def test_page_says_it_is_sorting_then_shows_the_emails():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    from local_server import make_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(_SlowRouter(3.0), None, None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(f"http://127.0.0.1:{httpd.server_address[1]}/", wait_until="domcontentloaded")
        pg.wait_for_selector("#emptyState:not([hidden])", timeout=2000)
        assert "sorting your inbox" in pg.locator("#emptyState").inner_text()
        pg.wait_for_selector("#rowList .row-item", timeout=10000)
        assert pg.locator("#emptyState").is_hidden()
        b.close()
    httpd.shutdown()


def test_play_waits_long_enough_for_the_first_sort():
    import automate_inbox
    assert automate_inbox.INBOX_LOAD_TIMEOUT_MS >= 180_000
    for name in ("automate_inbox.py", "run_boss_task_list.py"):
        src = Path(_INBOX_DIR, name).read_text(encoding="utf-8")
        assert "timeout=INBOX_LOAD_TIMEOUT_MS" in src, name


def test_electron_keeps_the_inbox_server_output():
    src = Path(_ROOT, "app_electron", "main.js").read_text(encoding="utf-8")
    block = src[src.index("async function ensureLocalServerRunning"):]
    block = block[:block.index("function startBridge")]
    assert "inbox_server.log" in block
    assert 'stdio: "ignore"' not in block
