"""
components/inbox_router/run_boss_task_list.py
==================================================
The single combined entry point for "everything the boss needs done":
walks the boss' task list (Cold Email) and then works the WHOLE
regular inbox (Reply/Forward/Schedule/Leave alone), in ONE real
browser session -- one window, opened once, not two.

Direct requests, in order:
1. "I need the boss' task list to contain all we need to do: cold
   email, forward, reply, and everything else." Confirmed via
   AskUserQuestion: keep the Agent deciding reply/forward/schedule from
   real inbox mail exactly as it already does (that's the actual
   thesis claim -- it decides from how this user has really behaved,
   not a fixed instruction list) -- just make ONE command run both
   halves.
2. First version of this script launched each half as its own
   subprocess, which meant its own separate browser window per half.
   Direct correction: "I want it to be one seamless thing, it can't
   open the web browser again and again." Rewritten to drive a single
   Playwright browser/page through both phases -- Cold Email first,
   then the same page switches view and walks the inbox, never
   closing and reopening a window in between.
3. The first single-window version still capped each phase at
   whatever --limit was passed for the demo. Direct correction: "It
   didn't go through the whole inbox... It has to navigate all the
   mail in the inbox." --limit now defaults to None (walk everything)
   -- it's still available as an explicit override, but nothing here
   silently stops early by default.

This changes no decision logic anywhere -- reply/forward/schedule/
leave_alone are still decided exactly as automate_inbox.py already
decides them, cold email exactly as automate_cold_email.py already
walks it. This file only reuses each module's own process_one() against
one shared page instead of running two separate scripts.

    python run_boss_task_list.py                        # dry run, everything
    python run_boss_task_list.py --commit                # real actions, everything
    python run_boss_task_list.py --commit --auto-draft-reply
    python run_boss_task_list.py --commit --limit 5       # cap each phase, for a quick look
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from automate_inbox import (
    ensure_server_running, print_countdown, banner, SERVER_URL,
    process_one as inbox_process_one,
    _color, _BLUE, _RED, _BOLD,
)
from automate_cold_email import process_one as cold_email_process_one


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true",
                    help="actually act for real in both phases; the default is a dry run of both")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap on how many items EACH phase processes -- omit to walk everything (default)")
    ap.add_argument("--pace", type=float, default=1.5,
                    help="seconds to pause on each item so a human can follow it (default: 1.5)")
    ap.add_argument("--headless", action="store_true",
                    help="run without a visible browser window (default: visible)")
    ap.add_argument("--auto-draft-reply", action="store_true",
                    help="for 'reply'/'forward' inbox decisions, generate real text via LM Studio and "
                         "draft it for real -- see automate_inbox.py's own --help for the full contract. "
                         "Has no effect on the Cold Email phase, which already always drafts under --commit.")
    ap.add_argument("--log", type=Path, default=None,
                    help="where to write the combined run log (default: data/runs/)")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright, Error as PlaywrightError

    print(f"\n  mode  {'COMMIT' if args.commit else 'dry run'}")
    print_countdown(message="Starting the boss' full task list -- one browser: Cold Email, then the whole inbox.")

    started_server = ensure_server_running()

    cold_email_results: list = []
    inbox_results: list = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()
        page.goto(SERVER_URL)

        # ---- Phase 1: Cold Email (the boss' own task list) --------------
        banner(1, "Cold Email -- the boss' own task list")
        with page.expect_response(lambda r: "/cold-email/api/targets" in r.url and r.request.method == "GET",
                                   timeout=60_000):
            page.evaluate("setView('cold_email')")
        page.wait_for_timeout(200)

        skipped = 0
        try:
            while args.limit is None or len(cold_email_results) < args.limit:
                row_index = skipped if args.commit else len(cold_email_results)
                result = cold_email_process_one(page, args.commit, row_index)
                if result is None:
                    break
                cold_email_results.append(result)
                if result["outcome"] != "drafted":
                    skipped += 1
                time.sleep(args.pace)
        except PlaywrightError as exc:
            print(f"\n  Browser closed unexpectedly during Cold Email ({exc.__class__.__name__}) -- "
                  f"moving on to the inbox with what was already done.")

        # ---- Phase 2: the whole regular inbox, same page, same window ---
        # setView('inbox') alone triggers no fetch -- it just re-renders
        # whatever was already loaded before Cold Email started. The real
        # refresh click is what actually re-fetches current pending mail,
        # same as automate_inbox.py's own main() does on its own.
        banner(2, "Regular Inbox -- Reply / Forward / Schedule / Leave alone")
        page.evaluate("setView('inbox')")
        with page.expect_response(lambda r: "/api/inbox" in r.url and r.request.method == "GET",
                                   timeout=60_000):
            page.click("#toolbarRefreshBtn")
        page.wait_for_timeout(200)

        skipped = 0
        try:
            while args.limit is None or len(inbox_results) < args.limit:
                result = inbox_process_one(page, args.commit, len(inbox_results), skipped,
                                            dwell_ms=int(args.pace * 1000),
                                            auto_draft_reply=args.auto_draft_reply)
                if result is None:
                    break
                inbox_results.append(result)
                if not result["outcome"].startswith("confirmed"):
                    skipped += 1
                time.sleep(args.pace)
        except PlaywrightError as exc:
            print(f"\n  Browser closed unexpectedly during the inbox ({exc.__class__.__name__}) -- "
                  f"stopping here with what was already completed.")

        try:
            if not args.headless:
                page.wait_for_timeout(1500)
            browser.close()
        except PlaywrightError:
            pass  # already gone -- nothing left to close

    if started_server is not None:
        started_server.terminate()

    banner(3, "Combined Result")
    drafted = sum(1 for r in cold_email_results if r["outcome"] == "drafted")
    confirmed = sum(1 for r in inbox_results if r["outcome"].startswith("confirmed"))
    print(f"  Cold Email   {_color(str(len(cold_email_results)), _BOLD)} walked, "
          f"{_color(str(drafted), _BLUE)} drafted")
    print(f"  Inbox        {_color(str(len(inbox_results)), _BOLD)} processed, "
          f"{_color(str(confirmed), _BLUE)} completed for real")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = args.log or (REPO / "data" / "runs" / f"run_boss_task_list_{stamp}.json")
    if not log_path.is_absolute():
        log_path = REPO / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps({
        "commit": args.commit, "processed_at": datetime.now(timezone.utc).isoformat(),
        "cold_email_results": cold_email_results, "inbox_results": inbox_results,
    }, indent=2), encoding="utf-8")
    print(f"  run log      {log_path.relative_to(REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
