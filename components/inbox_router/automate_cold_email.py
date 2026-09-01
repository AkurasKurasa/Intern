"""
components/inbox_router/automate_cold_email.py
===================================================
Scope #3's own version of Scope #1's data-entry list, played the same
way automate_inbox.py plays the regular inbox: opens the real merged
page, drives the real Cold Email sidebar section, and walks through
each real target from the boss' task list (data/task_list.txt) one at
a time -- who they are, what the pre-filled subject would be. It
never sends anything on its own -- Cold Email always needs a real,
human-typed message (the same "never invent text" rule this project
holds everywhere else), so every target is read and shown on the real
page, then left exactly as it was for a human to actually write and
send.

    python automate_cold_email.py                # walks the real task list
    python automate_cold_email.py --limit 2
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from automate_inbox import ensure_server_running, print_countdown, banner, SERVER_URL


def process_one(page, index: int):
    """Reads one target row off the real DOM, opens it, prints the real
    name/email and the pre-filled subject, then goes back without
    sending. Cold Email always needs a human-typed message, so nothing
    here is ever confirmed or removed -- the same row is read every
    time until a human actually sends it themselves, which is why this
    walks by a plain index rather than automate_inbox.py's
    confirm-then-advance shape. Returns a result dict, or None once the
    task list is exhausted."""
    row = page.locator("#coldEmailRowList .row-item").nth(index)
    if row.count() == 0:
        return None

    row.click()
    page.wait_for_selector("#coldEmailDetailView:not([hidden])")

    name = page.locator("#coldEmailTargetName").inner_text()
    email = page.locator("#coldEmailTargetEmail").inner_text()
    subject = page.input_value("#coldEmailSubjectInput")

    print(f"\n  {name} <{email}>")
    print(f"    pre-filled subject: {subject!r}")
    print("    -> left pending -- needs a real message typed by a human")

    page.click("#coldEmailBackBtn")

    return {"name": name, "email": email, "subject": subject,
            "outcome": "left pending -- needs a real message typed by a human"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None,
                    help="only walk the first N targets on the task list")
    ap.add_argument("--pace", type=float, default=1.5,
                    help="seconds to pause on each target so a human can follow it (default: 1.5)")
    ap.add_argument("--headless", action="store_true",
                    help="run without a visible browser window (default: visible)")
    ap.add_argument("--log", type=Path, default=None,
                    help="where to write the run log (default: data/runs/)")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    print_countdown(message="Starting Cold Email -- opening a real browser to walk the boss' task list.")

    started_server = ensure_server_running()

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()
        page.goto(SERVER_URL)
        page.wait_for_selector("#navColdEmail")
        page.click("#navColdEmail")
        page.wait_for_timeout(600)

        banner(1, "Working through the boss' task list")
        while args.limit is None or len(results) < args.limit:
            result = process_one(page, len(results))
            if result is None:
                break
            results.append(result)
            time.sleep(args.pace)

        if not args.headless:
            page.wait_for_timeout(1500)
        browser.close()

    if started_server is not None:
        started_server.terminate()

    banner(2, "Result")
    print(f"  targets walked  {len(results)}")
    for r in results:
        print(f"    {r['name']:<25} {r['email']}")
    if results:
        print(f"\n  {len(results)} target(s) need a real message typed by a human -- "
              f"open {SERVER_URL} yourself (Cold Email section) to write and send them.")
    else:
        print("\n  Nobody left on the task list. Add names to data/task_list.txt.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = args.log or (REPO / "data" / "runs" / f"automate_cold_email_{stamp}.json")
    if not log_path.is_absolute():
        log_path = REPO / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps({
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"  run log         {log_path.relative_to(REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
