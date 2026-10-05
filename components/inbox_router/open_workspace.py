"""Open the Inbox Dispatch window the agent will work in (the Launch button).

    python open_workspace.py

Starts the local server first if it is not running, then opens its main page
in a Chromium the automation scripts attach to (see workspace.py). Exits as
soon as the browser is started; the browser itself is detached and stays.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from automate_inbox import SERVER_URL, ensure_server_running  # noqa: E402
from workspace import open_workspace  # noqa: E402


def main():
    ensure_server_running()
    status, _ = open_workspace(SERVER_URL)
    print(f"inbox workspace {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
