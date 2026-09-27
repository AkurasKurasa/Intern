"""
components/inbox_router/automate_cold_email.py
===================================================
Scope #3's own version of Scope #1's data-entry list, played the same
way automate_inbox.py plays the regular inbox: opens the real merged
page, drives the real Cold Email sidebar section, and walks through
each real target from the boss' task list (data/task_list.txt) one at
a time -- who they are, what the pre-filled subject would be.

Dry run (default) never sends anything -- Cold Email normally needs a
real, human-typed message (the same "never invent text" rule this
project holds everywhere else for Reply/Forward/Schedule), so every
target is just read and shown, then left exactly as it was.

--commit is the one deliberate, explicit exception in this whole
project: direct instruction, "Break that rule for Scope #3." With
--commit, cold_email_llm.generate_cold_email() asks LM Studio for a
real subject/body for each target, types it into the real page, and
clicks Send for real -- an actual Gmail draft gets created. Every
other decision type (Reply, Forward, Schedule) is untouched by this;
this flag only ever affects Cold Email.

    python automate_cold_email.py                # dry run -- reads and shows, creates nothing
    python automate_cold_email.py --commit        # asks LM Studio for real text and creates a real draft
    python automate_cold_email.py --commit --limit 2
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Same fix as automate_inbox.py, same reason: a real Windows console
# defaults to a codepage (cp1252) that can't encode characters a real
# LLM response routinely contains (curly quotes, em dashes), and this
# script prints LM Studio's generated subject/body the same way.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from automate_inbox import ensure_server_running, print_countdown, banner, SERVER_URL
from cold_email_llm import generate_cold_email


def process_one(page, commit: bool, index: int):
    """Reads one target row off the real DOM, opens it, prints the real
    name/email and the pre-filled subject.

    Dry run: goes back without sending -- nothing is ever confirmed or
    removed, so the same row is read every time until a human actually
    sends it themselves.

    --commit: asks LM Studio for a real subject/body, types it into the
    real page's own fields, and clicks Send for real. A successful send
    removes the target from the list (same as the human flow), so the
    caller must re-read row `index` on the NEXT call rather than
    advancing past it -- mirrors automate_inbox.py's own
    confirm-then-re-read-the-same-slot shape, for the same reason: the
    list just got shorter.

    Returns a result dict, or None once the task list is exhausted."""
    row = page.locator("#coldEmailRowList .row-item").nth(index)
    if row.count() == 0:
        return None

    row.click()
    page.wait_for_selector("#coldEmailDetailView:not([hidden])")

    name = page.locator("#coldEmailTargetName").inner_text()
    email = page.locator("#coldEmailTargetEmail").inner_text()
    prefilled_subject = page.input_value("#coldEmailSubjectInput")

    print(f"\n  {name} <{email}>")

    if not commit:
        print(f"    pre-filled subject: {prefilled_subject!r}")
        print("    -> left pending -- needs a real message typed by a human")
        page.click("#coldEmailBackBtn")
        return {"name": name, "email": email, "subject": prefilled_subject, "body": "",
                "outcome": "left pending -- needs a real message typed by a human"}

    subject, body = generate_cold_email(name, prefilled_subject)
    if not subject or not body:
        print("    -> LM Studio unavailable (or gave an empty response) -- left pending")
        page.click("#coldEmailBackBtn")
        return {"name": name, "email": email, "subject": prefilled_subject, "body": "",
                "outcome": "left pending -- LM Studio unavailable"}

    print(f"    LM Studio subject: {subject!r}")
    print(f"    LM Studio body: {body!r}")

    page.fill("#coldEmailSubjectInput", subject)
    page.fill("#coldEmailBodyInput", body)
    page.click("#coldEmailSendBtn")
    page.wait_for_selector("#coldEmailListView:not([hidden])")
    # "drafted", not "sent" -- gmail_client.py deliberately has no
    # send()/send_message() method anywhere in this project. Clicking
    # this button really calls create_draft(): a real draft is created
    # and saved (verify in data/mock_drafts.json), but nothing here ever
    # delivers an email anywhere.
    print("    -> drafted")

    return {"name": name, "email": email, "subject": subject, "body": body, "outcome": "drafted"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true",
                    help="ask LM Studio for real text and actually click Send; the default is a dry run")
    ap.add_argument("--limit", type=int, default=None,
                    help="only walk the first N targets on the task list")
    ap.add_argument("--pace", type=float, default=1.5,
                    help="seconds to pause on each target so a human can follow it (default: 1.5)")
    ap.add_argument("--headless", action="store_true",
                    help="run without a visible browser window (default: visible)")
    ap.add_argument("--log", type=Path, default=None,
                    help="where to write the run log (default: data/runs/)")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright, Error as PlaywrightError

    print(f"\n  mode  {'COMMIT (LM Studio writes, real draft created)' if args.commit else 'dry run'}")
    print_countdown(message="Starting Cold Email -- opening a real browser to walk the boss' task list.")

    started_server = ensure_server_running()

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()
        page.goto(SERVER_URL)
        # There's no button or tab for Cold Email anywhere in this page's
        # markup at all -- direct instruction: "if it's a cold email I
        # want the Agent to reply on its own without a need for the Cold
        # Email tab or label thing." A human never sees or clicks
        # anything to reach this section, so neither does this script --
        # it calls the page's own setView("cold_email") function
        # directly, the exact same function a (removed) nav click used
        # to call, just invoked as real JS instead of a DOM interaction.
        # Same real bug, same fix as automate_inbox.py's own slow-poll
        # race: a fixed 600ms wait here can race ahead of the real
        # /cold-email/api/targets fetch, reading 0 rows even with real
        # targets waiting -- wait for the actual response instead.
        with page.expect_response(lambda r: "/cold-email/api/targets" in r.url and r.request.method == "GET",
                                   timeout=60_000):
            page.evaluate("setView('cold_email')")
        page.wait_for_timeout(200)

        banner(1, "Working through the boss' task list")
        skipped = 0
        try:
            while args.limit is None or len(results) < args.limit:
                row_index = skipped if args.commit else len(results)
                result = process_one(page, args.commit, row_index)
                if result is None:
                    break
                results.append(result)
                if result["outcome"] != "drafted":
                    skipped += 1
                time.sleep(args.pace)
        except PlaywrightError as exc:
            # The browser/page can close out from under this loop for
            # reasons entirely outside this script's own control (the OS
            # reclaiming it, another heavy process crowding it out) --
            # nothing here ever closes it itself. Whatever was already
            # sent before that point is real and already recorded in
            # `results`; report that honestly instead of dying with a raw
            # traceback and losing the summary of real work already done.
            print(f"\n  Browser closed unexpectedly mid-run ({exc.__class__.__name__}) -- "
                  f"stopping here with what was already completed.")

        try:
            if not args.headless:
                page.wait_for_timeout(1500)
            browser.close()
        except PlaywrightError:
            pass  # already gone -- nothing left to close

    if started_server is not None:
        started_server.terminate()

    banner(2, "Result")
    print(f"  targets walked  {len(results)}")
    for r in results:
        print(f"    {r['outcome']:<12} {r['name']:<25} {r['email']}")
    needing_human = [r for r in results if r["outcome"] != "drafted"]
    if needing_human:
        print(f"\n  {len(needing_human)} target(s) still need a real message -- "
              f"open {SERVER_URL} yourself (Cold Email section) to write and save a draft for them.")
    if not results:
        print("\n  Nobody left on the task list. Add names to data/task_list.txt.")
    if not args.commit:
        print("\n  Nothing was sent for real. Re-run with --commit to actually send (via LM Studio).")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = args.log or (REPO / "data" / "runs" / f"automate_cold_email_{stamp}.json")
    if not log_path.is_absolute():
        log_path = REPO / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps({
        "commit": args.commit, "processed_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"  run log         {log_path.relative_to(REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
