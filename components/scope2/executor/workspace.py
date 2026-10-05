"""The portal window the user opens, and the agent then works in.

Direct request: "When I press Launch Mockups I want that specific part to
where the Agent will work over once I click Play." Before this, Launch opened
only the spreadsheet and Play opened its OWN Playwright browser, so the window
the user prepared was never the one the agent touched -- the same shape as
Scope #1, where the user opens the form and the agent then acts on that
window, was missing here.

So one window, shared by both buttons:

    open_workspace()   Launch: start Chromium with a DevTools port on the
                       portal, or leave it alone if it is already running.
    attach()           Play: connect to that browser over CDP and hand back
                       its portal tab; None when nothing is listening, so the
                       runner falls back to launching its own browser exactly
                       as before.

Attaching never owns the browser: closing the Playwright connection only
disconnects (Playwright's documented behaviour for connect_over_cdp), so the
filled portal stays on screen for the user after the run.

Nothing here names a field, a column or a variant beyond the URL it is given.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from executor.scanner import CHROMIUM, MOCKSITE, variant_url

REPO = Path(__file__).resolve().parents[1]

# Off the common 9222 so a developer's own debug Chrome is never mistaken for
# the workspace.
CDP_PORT = 9333

# Its own profile: a DevTools port cannot be added to a Chromium that is
# already running on the default profile, and this keeps the user's normal
# browser out of it entirely. Gitignored.
PROFILE_DIR = REPO / "data" / "workspace_profile"


def cdp_url(port: int = CDP_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def is_open(port: int = CDP_PORT, timeout: float = 0.5) -> bool:
    """True when a browser is answering DevTools requests on this port."""
    try:
        with urllib.request.urlopen(cdp_url(port) + "/json/version", timeout=timeout) as r:
            return "Browser" in json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 -- any failure means "not open"
        return False


def chromium_path() -> Path:
    """The Chromium to start. scanner.CHROMIUM pins one Playwright build
    folder (chromium-1208); a machine with a newer Playwright has a different
    folder, and the runner already copes by letting Playwright pick. Launch
    needs a real path, so ask Playwright the same question instead of
    guessing a folder name."""
    if CHROMIUM.exists():
        return CHROMIUM
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        return Path(p.chromium.executable_path)


def launch_args(url: str, port: int = CDP_PORT, profile: Path = PROFILE_DIR,
                chromium: Path = CHROMIUM, extra=()) -> list:
    return [
        str(chromium),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        *extra,
        url,
    ]


def open_workspace(variant: str = "v0_base", port: int = CDP_PORT,
                   profile: Path = PROFILE_DIR, chromium: Path | None = None,
                   extra=(), popen=subprocess.Popen):
    """Open the portal the agent will work in. Returns (status, process).

    status is "already-open" (process None) when a workspace browser is
    already listening -- pressing Launch twice must not stack windows -- or
    "launched". The process is detached so it outlives the Electron button
    that started it.
    """
    if is_open(port):
        return "already-open", None
    chromium = chromium or chromium_path()
    if not Path(chromium).exists():
        raise FileNotFoundError(f"Chromium not found at {chromium} - "
                                "run: python -m playwright install chromium")
    Path(profile).mkdir(parents=True, exist_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = popen(launch_args(variant_url(variant), port, profile, chromium, extra),
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    return "launched", proc


def pick_page(pages, prefer_prefix: str):
    """The tab the agent should work in: the first one already showing the
    portal, else the first tab at all, else None."""
    for page in pages:
        if (page.url or "").startswith(prefer_prefix):
            return page
    return pages[0] if pages else None


def attach(playwright, port: int | None = None, timeout_ms: int = 15000):
    """(browser, page) for the open workspace, or None if there is none.

    `port` defaults to the module's CDP_PORT read at call time, so a test can
    point the runner at its own throwaway browser.

    A browser that answers but cannot be attached to (measured: the CDP
    handshake occasionally took over 3 s on this machine) also returns None,
    so Play falls back to its own browser instead of crashing the run.
    """
    port = port or CDP_PORT
    if not is_open(port):
        return None
    try:
        browser = playwright.chromium.connect_over_cdp(cdp_url(port), timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001
        print(f"  could not attach to the open portal window ({exc.__class__.__name__}); "
              "opening a new one")
        return None
    pages = [pg for ctx in browser.contexts for pg in ctx.pages]
    page = pick_page(pages, MOCKSITE.as_uri())
    if page is None:
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
    try:
        page.bring_to_front()
    except Exception:  # noqa: BLE001 -- cosmetic only
        pass
    return browser, page
