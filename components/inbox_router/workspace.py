"""
components/inbox_router/workspace.py
====================================
The Inbox Dispatch window the user opens with Launch, and Play then works in.

Direct request 2026-10-05: "Same changes for Scope #2 to Scope #3 in regards
to launching and playing." Before this, Launch opened the PRACTICE inbox
(/practice/, the page for recording demonstrations) in the default browser,
while Play's automate_inbox.py launched its own Chromium on the main page --
two windows, and the one Launch opened was not the one the agent used.

Now Launch opens the main page in a Chromium with a DevTools port, and the
automation scripts attach to it (components/workspace_browser.py, shared
with Scope #2). No window open: they launch their own, as before.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR.parent))
import workspace_browser as wb  # noqa: E402

# Scope #2 uses 9333; a different port so both windows can be open at once.
CDP_PORT = 9334
PROFILE_DIR = _THIS_DIR / "data" / "workspace_profile"


def is_main_page(url: str, server_url: str) -> bool:
    """The agent's page is the server's root -- not /practice/ (recording) or
    any other path the same server also serves."""
    a, b = urlsplit(url), urlsplit(server_url)
    same_server = a.port == b.port and a.hostname in ("127.0.0.1", "localhost")
    return same_server and a.path in ("", "/")


def open_workspace(server_url: str, port: int | None = None, profile: Path = PROFILE_DIR,
                   chromium: Path | None = None, extra=(), popen=subprocess.Popen):
    """Launch: open the main inbox page. Returns (status, process)."""
    return wb.open_url(server_url, port or CDP_PORT, profile, chromium, extra, popen)


def attach(playwright, server_url: str, port: int | None = None):
    """Play: (browser, page) on the open inbox window, or None. `port`
    defaults to CDP_PORT read at call time so a test can redirect it."""
    return wb.attach(playwright, port or CDP_PORT,
                     lambda url: is_main_page(url, server_url))


def open_page(playwright, server_url: str, headless: bool):
    """What every Scope #3 script does to get its page: the user's open
    window when there is one (never when headless -- a measurement or test
    run stays isolated), else a fresh maximised browser as before.

    Returns (browser, page, attached). Closing an attached browser only
    disconnects, so the user's window stays open."""
    attached = None if headless else attach(playwright, server_url)
    if attached:
        browser, page = attached
        print("  working in the inbox window you opened")
        return browser, page, True
    # Maximised, with the page filling the whole window, so every target
    # the pointer moves to is large and already on screen.
    browser = playwright.chromium.launch(headless=headless, args=["--start-maximized"])
    page = browser.new_context(no_viewport=True).new_page()
    return browser, page, False
