"""The shared portal window: Launch opens it, Play fills it.

Direct request: "When I press Launch Mockups I want that specific part to
where the Agent will work over once I click Play." These pin that the runner
works in the browser Launch opened instead of opening a second one, that the
window survives the run, and that with no workspace open nothing changes.

Integration/E2E tests start a real but headless Chromium on a throwaway port,
so nothing appears on screen and the user's own workspace port is untouched.

Run:  python -m pytest tests/scope2/test_workspace.py -q
"""

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent / "components" / "scope2"
sys.path.insert(0, str(REPO))

from executor import workspace  # noqa: E402
from executor.scanner import MOCKSITE, variant_url  # noqa: E402

MAPPING_PATH = REPO / "data" / "mappings" / "v0_handwritten.json"
SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"
APP_MAIN = REPO.parents[1] / "app_electron" / "main.js"

def _has_chromium():
    try:
        return workspace.chromium_path().exists()
    except Exception:  # noqa: BLE001
        return False


needs_chromium = pytest.mark.skipif(not _has_chromium(), reason="Playwright Chromium missing")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def portal_tab_ready(port):
    """The browser answers AND its tab has reached the portal -- right after
    start the tab is briefly about:blank."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=0.5) as r:
            tabs = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return any(t.get("url", "").startswith(MOCKSITE.as_uri()) for t in tabs)


class FakePage:
    def __init__(self, url):
        self.url = url


# ------------------------------------------------------------------ unit


def test_pick_page_prefers_the_portal_tab():
    portal = FakePage(variant_url("v0_base"))
    pages = [FakePage("about:blank"), portal]
    assert workspace.pick_page(pages, MOCKSITE.as_uri()) is portal


def test_pick_page_falls_back_to_any_tab_then_none():
    blank = FakePage("about:blank")
    assert workspace.pick_page([blank], MOCKSITE.as_uri()) is blank
    assert workspace.pick_page([], MOCKSITE.as_uri()) is None


def test_launch_args_open_the_portal_with_a_devtools_port_and_own_profile(tmp_path):
    args = workspace.launch_args("file:///x/index.html", 9444, tmp_path, Path("chrome.exe"))
    assert args[0] == "chrome.exe"
    assert "--remote-debugging-port=9444" in args
    assert f"--user-data-dir={tmp_path}" in args
    assert args[-1] == "file:///x/index.html"


def test_chromium_path_resolves_to_a_real_browser():
    """The pinned build folder is missing on this machine (newer Playwright);
    Launch must still find a browser rather than fail."""
    assert workspace.chromium_path().exists()


def test_is_open_is_false_when_nothing_listens():
    assert workspace.is_open(free_port()) is False


def test_open_workspace_does_not_stack_a_second_window(monkeypatch):
    monkeypatch.setattr(workspace, "is_open", lambda port=None, timeout=0.5: True)
    called = []
    status, proc = workspace.open_workspace(popen=lambda *a, **k: called.append(a))
    assert (status, proc) == ("already-open", None)
    assert called == []


def test_open_workspace_launches_detached_on_the_portal(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "is_open", lambda port=None, timeout=0.5: False)
    seen = {}

    def fake_popen(args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return "proc"

    status, proc = workspace.open_workspace("v2_relabeled", port=9555, profile=tmp_path,
                                            chromium=Path(sys.executable), popen=fake_popen)
    assert (status, proc) == ("launched", "proc")
    assert seen["args"][-1] == variant_url("v2_relabeled")
    assert "--remote-debugging-port=9555" in seen["args"]
    assert seen["kwargs"]["stdin"] == subprocess.DEVNULL


def test_attach_falls_back_when_the_handshake_fails(monkeypatch):
    """A browser that answers but will not attach must not crash Play."""
    monkeypatch.setattr(workspace, "is_open", lambda port=None, timeout=0.5: True)

    class Refusing:
        class chromium:
            @staticmethod
            def connect_over_cdp(*a, **k):
                raise TimeoutError("handshake")

    assert workspace.attach(Refusing) is None


def test_attach_returns_none_without_a_workspace(monkeypatch):
    monkeypatch.setattr(workspace, "CDP_PORT", free_port())
    assert workspace.attach(playwright=None) is None   # never touches playwright


def test_electron_launch_button_opens_the_workspace():
    """Static guard: the Scope #2 Test Tools entry must start the workspace
    (the portal), not only the spreadsheet -- the original complaint."""
    src = APP_MAIN.read_text(encoding="utf-8")
    block = src[src.index('"Sheet-to-Portal Matcher": ['):]
    block = block[:block.index("],")]
    assert "open_workspace.py" in block
    assert "grade_sheet.xlsx" in block


# ------------------------------------------------- integration and E2E


@pytest.fixture
def headless_workspace(tmp_path, monkeypatch):
    """A real Chromium on the portal, headless, on a throwaway port."""
    port = free_port()
    status, proc = workspace.open_workspace(
        "v0_base", port=port, profile=tmp_path / "profile",
        extra=("--headless=new",))
    assert status == "launched"
    deadline = time.time() + 20
    while not portal_tab_ready(port) and time.time() < deadline:
        time.sleep(0.2)
    monkeypatch.setattr(workspace, "CDP_PORT", port)
    yield port
    # Chromium is a process tree; kill all of it, not just the parent.
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)
    else:
        proc.kill()


@needs_chromium
def test_attach_finds_the_portal_tab_launch_opened(headless_workspace):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser, page = workspace.attach(p)
        assert page.url.startswith(MOCKSITE.as_uri())
        browser.close()
    assert workspace.is_open(headless_workspace), "disconnecting must not close the window"


@needs_chromium
@pytest.mark.slow
def test_play_fills_the_window_launch_opened_and_leaves_it_open(headless_workspace, monkeypatch):
    """E2E: the runner, under show (what Play uses), fills the already-open
    portal instead of launching its own, and the window outlives the run."""
    if not SHEET.exists():
        pytest.skip("run data/sheets/make_sheets.py first")
    import executor.runner as runner_module
    from executor.runner import run

    launched = []
    real_attach = workspace.attach
    monkeypatch.setattr(runner_module.workspace, "attach",
                        lambda p: launched.append("attach") or real_attach(p))
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("must not wait on stdin"))
    monkeypatch.setattr(runner_module.time, "sleep",
                        lambda s: pytest.fail("must not hold the process open"))

    log = run("v0_base", MAPPING_PATH, dry_run=True, limit=3, show=True)

    assert launched == ["attach"]
    assert [r.status for r in log.rows] == ["filled"] * 3
    assert workspace.is_open(headless_workspace)

    # And the values are really in THAT window, not in some other browser.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser, page = workspace.attach(p)
        first = log.rows[0]
        label, value = next(iter(first.filled.items()))
        # Typed values live in the inputs' .value, not in the HTML.
        values = page.evaluate(
            "() => [...document.querySelectorAll('input,textarea,select')].map(e => e.value)")
        assert value in values
        browser.close()
