"""Scope #3 Launch/Play share one window, like Scope #2.

Direct request 2026-10-05: "Same changes for Scope #2 to Scope #3 in regards
to launching and playing." Launch used to open the PRACTICE inbox in the
default browser while Play launched its own Chromium on the main page. Now
Launch opens the main page with a DevTools port and the scripts attach.

Also covers the shared mechanism, components/workspace_browser.py, which
Scope #2's executor/workspace.py now delegates to.

Run:  python -m pytest tests/test_inbox_workspace.py -q
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer as HTTPServer
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
_COMPONENTS = os.path.join(_ROOT, "components")
for _p in (_ROOT, _INBOX_DIR, _COMPONENTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import workspace_browser as wb  # noqa: E402
import workspace as inbox_ws  # noqa: E402

APP_MAIN = Path(_ROOT) / "app_electron" / "main.js"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakePage:
    def __init__(self, url):
        self.url = url


# ------------------------------------------------------------- shared, unit


def test_pick_page_accepts_a_prefix_or_a_predicate():
    a, b = FakePage("about:blank"), FakePage("http://x/one")
    assert wb.pick_page([a, b], "http://x/") is b
    assert wb.pick_page([a, b], lambda u: u.endswith("one")) is b
    assert wb.pick_page([a], "http://x/") is a
    assert wb.pick_page([], "http://x/") is None


def test_shared_attach_returns_none_when_nothing_listens():
    assert wb.attach(playwright=None, port=free_port(), prefer="x") is None


def test_shared_attach_falls_back_when_the_handshake_fails(monkeypatch):
    monkeypatch.setattr(wb, "is_open", lambda port, timeout=0.5: True)

    class Refusing:
        class chromium:
            @staticmethod
            def connect_over_cdp(*a, **k):
                raise TimeoutError("handshake")

    assert wb.attach(Refusing, 1, "x") is None


def test_open_url_never_stacks_a_second_window(monkeypatch):
    monkeypatch.setattr(wb, "is_open", lambda port, timeout=0.5: True)
    monkeypatch.setattr(wb, "_targets", lambda port: [{"type": "page", "url": "http://x/"}])
    called = []
    assert wb.open_url("http://x/", 1, Path("."), popen=lambda *a, **k: called.append(a)) == ("already-open", None)
    assert called == []


# ------------------------------------------------------------ Scope #3, unit


def test_main_page_is_the_server_root_not_practice_or_another_server():
    server = "http://127.0.0.1:8765/"
    assert inbox_ws.is_main_page("http://127.0.0.1:8765/", server)
    assert inbox_ws.is_main_page("http://localhost:8765", server)
    assert not inbox_ws.is_main_page("http://127.0.0.1:8765/practice/", server)
    assert not inbox_ws.is_main_page("http://127.0.0.1:9999/", server)


def test_scope3_uses_its_own_port_so_both_scopes_can_be_open():
    sys.path.insert(0, os.path.join(_ROOT, "components", "scope2"))
    from executor import workspace as scope2_ws
    assert inbox_ws.CDP_PORT != scope2_ws.CDP_PORT


def test_open_workspace_launches_blank_then_opens_the_main_page_as_a_tab(monkeypatch, tmp_path):
    seen = {"tabs": []}
    states = iter([False, True])
    monkeypatch.setattr(wb, "is_open", lambda port, timeout=0.5: next(states, True))
    monkeypatch.setattr(wb, "_targets", lambda port: [])
    monkeypatch.setattr(wb, "_new_tab", lambda port, url: seen["tabs"].append(url))

    def fake_popen(args, **kwargs):
        seen["args"] = args
        return "proc"

    status, proc = inbox_ws.open_workspace("http://127.0.0.1:8765/", profile=tmp_path,
                                           chromium=Path(sys.executable), popen=fake_popen)
    assert (status, proc) == ("launched", "proc")
    assert seen["args"][-1] == "about:blank"
    assert seen["tabs"] == ["http://127.0.0.1:8765/"]
    assert f"--remote-debugging-port={inbox_ws.CDP_PORT}" in seen["args"]


def test_launch_again_reopens_a_closed_tab_without_a_second_window(monkeypatch):
    tabs = []
    monkeypatch.setattr(wb, "is_open", lambda port, timeout=0.5: True)
    monkeypatch.setattr(wb, "_targets", lambda port: [{"type": "page", "url": "about:blank"}])
    monkeypatch.setattr(wb, "_new_tab", lambda port, url: tabs.append(url))
    assert wb.open_url("http://x/", 1, Path("."), popen=lambda *a, **k: pytest.fail("no 2nd window"))         == ("already-open", None)
    assert tabs == ["http://x/"]


def test_chromium_command_line_http_page_is_never_used():
    """Regression guard for the measured hang: open_url must not pass the
    page on the command line."""
    args = []
    try:
        wb.open_url("http://x/", 1, Path("."), chromium=Path(sys.executable),
                    popen=lambda a, **k: args.extend(a) or "p", ready_timeout=0.01)
    except TimeoutError:
        pass
    assert "http://x/" not in args


def test_headless_runs_never_attach(monkeypatch):
    monkeypatch.setattr(inbox_ws, "attach", lambda *a, **k: pytest.fail("headless must not attach"))

    class Launches:
        class chromium:
            @staticmethod
            def launch(headless, args):
                class B:
                    def new_context(self, **k):
                        class C:
                            def new_page(self):
                                return "fresh"
                        return C()
                return B()

    _, page, attached = inbox_ws.open_page(Launches, "http://127.0.0.1:8765/", headless=True)
    assert (page, attached) == ("fresh", False)


def test_electron_launch_opens_the_inbox_workspace_not_the_practice_page():
    src = APP_MAIN.read_text(encoding="utf-8")
    block = src[src.index('ipcMain.handle("launch-test-tools"'):]
    block = block[:block.index("const targets = TEST_MOCKUPS")]
    assert "open_workspace.py" in block and "inbox_router" in block
    assert "practice/" not in block.replace("not the practice page", "")


def test_every_scope3_script_gets_its_page_through_the_workspace():
    for name in ("automate_inbox.py", "automate_cold_email.py", "run_boss_task_list.py"):
        src = Path(_INBOX_DIR, name).read_text(encoding="utf-8")
        assert "open_page(p, SERVER_URL, args.headless)" in src, name
        assert "p.chromium.launch(" not in src, name


# -------------------------------------------------- integration and E2E


class _QuietRouter:
    def __getattr__(self, name):
        return lambda *a, **k: []


def _chromium_ok():
    try:
        return wb.chromium_path().exists()
    except Exception:  # noqa: BLE001
        return False


needs_chromium = pytest.mark.skipif(not _chromium_ok(), reason="Playwright Chromium missing")


@pytest.fixture
def inbox_window(tmp_path, monkeypatch):
    """A real inbox page server + a real headless Chromium opened by Launch's
    own code on it, with a decoy /practice/ tab opened FIRST."""
    from local_server import ChecksService, make_handler
    from calendar_client import MockCalendarClient
    from gmail_client import MockGmailClient

    (tmp_path / "mock_inbox.json").write_text(json.dumps({"inbox": [], "sent": []}), encoding="utf-8")
    (tmp_path / "task_list.txt").write_text("Check inbox: invoice\n", encoding="utf-8")
    svc = ChecksService(MockGmailClient(str(tmp_path)), MockCalendarClient(str(tmp_path)),
                        str(tmp_path / "task_list.txt"))
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(_QuietRouter(), None, svc))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"

    port = free_port()
    monkeypatch.setattr(inbox_ws, "CDP_PORT", port)
    monkeypatch.delenv("INTERN_NO_WORKSPACE_ATTACH", raising=False)
    status, proc = wb.open_url(url + "practice/", port, tmp_path / "profile",
                               extra=("--headless=new",))
    assert status == "launched"
    deadline = time.time() + 20
    while not wb.is_open(port) and time.time() < deadline:
        time.sleep(0.2)
    yield url, port
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()
    httpd.shutdown()


@needs_chromium
@pytest.mark.slow
def test_play_works_in_the_launched_window_on_the_main_page(inbox_window):
    from playwright.sync_api import sync_playwright
    from automate_checks import open_checks_view, process_one

    url, port = inbox_window
    with sync_playwright() as p:
        # Launch opened the practice tab; the main page is opened beside it,
        # as a user might. Play must pick the main page, not the decoy.
        b0 = p.chromium.connect_over_cdp(wb.cdp_url(port))
        b0.contexts[0].new_page().goto(url)
        b0.close()

        browser, page, attached = inbox_ws.open_page(p, url, headless=False)
        assert attached is True
        assert inbox_ws.is_main_page(page.url, url)
        open_checks_view(page)
        result = process_one(page, 0)
        assert result["answer"] == "no"
        browser.close()

    assert wb.is_open(port), "disconnecting must leave the user's window open"


def test_the_test_suite_can_never_attach_to_a_users_window(monkeypatch):
    """Found 2026-10-05: with the user's real portal window open, a
    show-mode test attached to it and filled a row there. conftest.py sets
    the switch for every test; with a window 'open', attach still refuses
    without ever touching Playwright."""
    assert os.environ.get(wb.NO_ATTACH_ENV)
    monkeypatch.setattr(wb, "is_open", lambda port, timeout=0.5: True)
    assert wb.attach(playwright=None, port=1, prefer="x") is None


def test_play_only_picks_rows_from_the_inbox_list():
    """Found 2026-10-05: automate_inbox.py located rows with a page-wide
    '.row-item', but the Cold Email phase leaves its (hidden) rows on the
    same page, so once the inbox ran out the agent could try to click a
    hidden cold-email row and stall. Rows must come from #rowList only."""
    src = Path(_INBOX_DIR, "automate_inbox.py").read_text(encoding="utf-8")
    assert 'page.locator("#rowList .row-item")' in src
    assert 'page.locator(".row-item")' not in src
