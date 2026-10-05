"""One window per task, shared by Launch and Play.

Direct requests, 2026-10-05: for Scope #2, "When I press Launch Mockups I want
that specific part to where the Agent will work over once I click Play"; then
"Same changes for Scope #2 to Scope #3 in regards to launching and playing".
So this is the scope-agnostic mechanism both use:

    open_url()   Launch: start Chromium on the task's page with a DevTools
                 port and its own profile, detached -- or leave it alone if
                 one is already listening, so pressing Launch twice never
                 stacks windows.
    attach()     Play: connect to that browser over CDP and return the tab
                 showing the task's page; None when nothing is listening (or
                 the handshake fails), so the caller launches its own browser
                 exactly as before.

Attaching never owns the browser: closing a connect_over_cdp connection only
disconnects (Playwright's documented behaviour), so the window -- and what
the agent did in it -- stays on screen after the run.

Each task picks its own port, so two tasks' windows can be open at once.
Nothing here knows any task, page or field.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


# Set (to anything) to make attach() always return None -- the test suite
# sets it so no test can ever drive a window the user has open.
NO_ATTACH_ENV = "INTERN_NO_WORKSPACE_ATTACH"


def cdp_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def is_open(port: int, timeout: float = 0.5) -> bool:
    """True when a browser is answering DevTools requests on this port."""
    try:
        with urllib.request.urlopen(cdp_url(port) + "/json/version", timeout=timeout) as r:
            return "Browser" in json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 -- any failure means "not open"
        return False


def chromium_path(preferred: Path | None = None) -> Path:
    """The Chromium to start: `preferred` if it exists, else the one the
    installed Playwright ships -- asked, not guessed, because the build
    folder name (chromium-1208, -1243, ...) differs between machines."""
    if preferred is not None and Path(preferred).exists():
        return Path(preferred)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        return Path(p.chromium.executable_path)


def launch_args(url: str, port: int, profile: Path, chromium: Path, extra=()) -> list:
    return [
        str(chromium),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        *extra,
        url,
    ]


def _targets(port: int) -> list:
    with urllib.request.urlopen(cdp_url(port) + "/json/list", timeout=2) as r:
        return json.loads(r.read().decode("utf-8"))


def _new_tab(port: int, url: str) -> None:
    req = urllib.request.Request(cdp_url(port) + "/json/new?" + url, method="PUT")
    urllib.request.urlopen(req, timeout=5).close()


def _close_tab(port: int, target_id: str) -> None:
    try:
        urllib.request.urlopen(cdp_url(port) + "/json/close/" + target_id, timeout=2).close()
    except Exception:  # noqa: BLE001 -- a stray blank tab is cosmetic
        pass


def ensure_tab(port: int, url: str) -> None:
    """Make sure a tab shows `url`, opening one through DevTools if not."""
    if not any(t.get("type") == "page" and t.get("url", "").startswith(url)
               for t in _targets(port)):
        _new_tab(port, url)


def open_url(url: str, port: int, profile: Path, chromium: Path | None = None,
             extra=(), popen=subprocess.Popen, ready_timeout: float = 20.0):
    """Returns (status, process): ("already-open", None) or ("launched", proc).

    The browser is started on about:blank and the page is then opened as a
    new tab through DevTools, never passed on the command line. Measured on
    Chromium 151 (Playwright 1.62): a page given on the command line makes
    connect_over_cdp hang forever once it is an http:// page -- Playwright
    attaches to a target that reports an empty URL and waits on it -- while
    the same page opened after start attaches in ~1 s. about:blank and
    file:// were unaffected, which is why Scope #2's portal never showed it.

    Already open: just make sure the page has a tab, so pressing Launch
    again after closing the tab brings it back without a second window.
    """
    if is_open(port):
        ensure_tab(port, url)
        return "already-open", None
    chromium = chromium or chromium_path()
    if not Path(chromium).exists():
        raise FileNotFoundError(f"Chromium not found at {chromium} - "
                                "run: python -m playwright install chromium")
    Path(profile).mkdir(parents=True, exist_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = popen(launch_args("about:blank", port, profile, chromium, extra),
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    deadline = time.time() + ready_timeout
    while not is_open(port):
        if time.time() > deadline:
            raise TimeoutError(f"browser did not open its DevTools port {port}")
        time.sleep(0.2)
    blanks = [t["id"] for t in _targets(port)
              if t.get("type") == "page" and t.get("url") == "about:blank"]
    _new_tab(port, url)
    for target_id in blanks:
        _close_tab(port, target_id)
    return "launched", proc


def pick_page(pages, prefer):
    """The tab to work in. `prefer` is a predicate on the tab's URL (or a
    URL prefix string). First preferred tab, else the first tab, else None."""
    test = prefer if callable(prefer) else (lambda u: u.startswith(prefer))
    for page in pages:
        if test(page.url or ""):
            return page
    return pages[0] if pages else None


def attach(playwright, port: int, prefer, timeout_ms: int = 15000):
    """(browser, page) for the open window on this port, or None.

    A browser that answers but cannot be attached to (measured: the CDP
    handshake occasionally took over 3 s on the dev machine) also returns
    None, so Play falls back to its own browser instead of crashing.
    """
    if os.environ.get(NO_ATTACH_ENV) or not is_open(port):
        return None
    try:
        browser = playwright.chromium.connect_over_cdp(cdp_url(port), timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001
        print(f"  could not attach to the open window ({exc.__class__.__name__}); "
              "opening a new one")
        return None
    pages = [pg for ctx in browser.contexts for pg in ctx.pages]
    page = pick_page(pages, prefer)
    if page is None:
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
    try:
        page.bring_to_front()
    except Exception:  # noqa: BLE001 -- cosmetic only
        pass
    return browser, page
