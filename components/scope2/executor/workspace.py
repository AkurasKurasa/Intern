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

import os
import subprocess
import sys
from pathlib import Path

from executor.scanner import CHROMIUM, MOCKSITE, variant_url

REPO = Path(__file__).resolve().parents[1]

# The mechanism is shared with Scope #3 (components/workspace_browser.py);
# this module only says which page, port and profile are Scope #2's.
if str(REPO.parent) not in sys.path:
    sys.path.insert(0, str(REPO.parent))
import workspace_browser as wb  # noqa: E402

# Off the common 9222 so a developer's own debug Chrome is never mistaken for
# the workspace; Scope #3 uses 9334, so both windows can be open at once.
CDP_PORT = 9333

# Its own profile: a DevTools port cannot be added to a Chromium that is
# already running on the default profile, and this keeps the user's normal
# browser out of it entirely. Gitignored.
PROFILE_DIR = REPO / "data" / "workspace_profile"

pick_page = wb.pick_page


def cdp_url(port: int = CDP_PORT) -> str:
    return wb.cdp_url(port)


def is_open(port: int = CDP_PORT, timeout: float = 0.5) -> bool:
    return wb.is_open(port, timeout)


def chromium_path() -> Path:
    """scanner.CHROMIUM pins one Playwright build folder (chromium-1208),
    absent on machines with a newer Playwright; fall back to asking it."""
    return wb.chromium_path(CHROMIUM)


def launch_args(url: str, port: int = CDP_PORT, profile: Path = PROFILE_DIR,
                chromium: Path = CHROMIUM, extra=()) -> list:
    return wb.launch_args(url, port, profile, chromium, extra)


def open_workspace(variant: str = "v0_base", port: int = CDP_PORT,
                   profile: Path = PROFILE_DIR, chromium: Path | None = None,
                   extra=(), popen=subprocess.Popen):
    """Open the portal the agent will work in. Returns (status, process):
    ("already-open", None) when one is already listening -- pressing Launch
    twice must not stack windows -- or ("launched", proc), detached so it
    outlives the Electron button that started it."""
    if is_open(port):
        return "already-open", None
    return wb.open_url(variant_url(variant), port, profile,
                       chromium or chromium_path(), extra, popen)


def attach(playwright, port: int | None = None, timeout_ms: int = 15000):
    """(browser, page) for the open portal window, or None. `port` defaults
    to CDP_PORT read at call time, so a test can point the runner at its own
    throwaway browser. A failed handshake also returns None, so Play falls
    back to its own browser instead of crashing the run."""
    port = port or CDP_PORT
    if os.environ.get(wb.NO_ATTACH_ENV) or not is_open(port):
        return None
    try:
        return wb.attach(playwright, port, MOCKSITE.as_uri(), timeout_ms)
    except Exception as exc:  # noqa: BLE001
        print(f"  could not attach to the open portal window ({exc.__class__.__name__}); "
              "opening a new one")
        return None
