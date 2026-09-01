"""
components/inbox_router/automate_inbox.py
=============================================
Scope #3, end to end -- mirrors components/scope2/automate.py's shape and
its whole point: the earlier local_server.py + local_ui pair could compute
a real decision and display it, but nothing ever CLICKED anything. This
script is what actually operates Inbox Dispatch, the same way a human
triaging their own inbox would -- open the local server's real page in a
real browser, read each email off the real DOM, and click the real button
the decision calls for (Confirm, Override, Archive, Reply). No shortcuts
through the HTTP API from here: every read is a DOM read, every action is
a real Playwright click on the same page a human uses.

    python automate_inbox.py                # dry run -- reads and decides, clicks nothing
    python automate_inbox.py --commit        # actually clicks Confirm for each email
    python automate_inbox.py --commit --limit 5

Stages:

    1  make sure the local server is up (starts it if not)
    2  open the real page in a real (visible by default) browser
    3  for each pending email: read it off the DOM, print the real
       decision + rationale the pipeline already computed, pause so a
       human can follow it, then click Confirm for real (--commit only)
    4  write a run log, same as automate.py
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
SERVER_URL = "http://localhost:8765/"
RULE = "-" * 74


def banner(number, title):
    print(f"\n{RULE}\n {number}. {title}\n{RULE}")


def _flush_safe_print(text: str) -> None:
    """Same guard as automate.py's -- an explicit flush can raise OSError
    on Windows when this is spawned with no console window (Electron's
    Play button, windowsHide=True), even though the write itself already
    succeeded."""
    print(text)
    try:
        sys.stdout.flush()
    except OSError:
        pass


def print_countdown(seconds: int = 5) -> None:
    _flush_safe_print("COUNTDOWN_BEGIN")
    _flush_safe_print("Starting Inbox Dispatch -- opening a real browser to click through it.")
    for i in range(seconds, 0, -1):
        _flush_safe_print(f"COUNTDOWN {i}")
        time.sleep(1)
    _flush_safe_print("COUNTDOWN_END")


def ensure_server_running(timeout_s: float = 20.0) -> subprocess.Popen | None:
    """Starts local_server.py if nothing is answering on SERVER_URL yet.
    Returns the Popen handle if this call started it (so main() can leave
    it running for --show, same as the Electron app already does), or
    None if a server was already up."""
    try:
        urllib.request.urlopen(SERVER_URL, timeout=1)
        return None
    except (urllib.error.URLError, ConnectionError, OSError):
        pass

    proc = subprocess.Popen(
        [sys.executable, "-u", str(REPO / "local_server.py")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(SERVER_URL, timeout=1)
            return proc
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
    raise SystemExit(f"local_server.py didn't come up within {timeout_s:.0f}s")


# Decisions that need real human-typed content (a reply, a forward, a
# schedule note) can never be auto-confirmed here -- this script has no
# real text to type, and confirming with none would either create an
# empty Gmail draft or an empty schedule note, defeating the whole point
# of the text box a human types into on the real page. cold_email has no
# control on this page at all -- it's not a reaction to an existing
# email, it lives on its own separate page. Only leave_alone/flag can be
# confirmed with a single real click, since neither needs typed content --
# clicking the same real icon a human would click for each.
NEEDS_HUMAN_TEXT = {"reply", "forward", "schedule", "cold_email"}
IMMEDIATE_ICON = {"leave_alone": "#archiveBtn", "flag": "#flagBtn"}


def process_one(page, commit: bool, index: int, skipped: int = 0):
    """Reads one pending row off the real DOM, opens it, prints the real
    decision + rationale, then clicks the real icon for that decision
    (Archive for leave_alone, the flag star for flag) if --commit.
    Returns a result dict, or None once the inbox is empty.

    Confirming removes a row from the list, so committed runs read row
    `skipped` -- every row already skipped-in-place (see below) still
    sits above it, and every confirmed row is gone, so `skipped` always
    points at the next row actually needing a decision. A dry run never
    removes or skips anything, so it reads row `index` instead, advancing
    through the list without ever changing it.

    Reply/forward/schedule/cold_email are never auto-confirmed here,
    commit or not -- see NEEDS_HUMAN_TEXT above. Those are left pending --
    in place, not removed -- for a human to actually open and answer
    themselves."""
    row_index = skipped if commit else index
    row = page.locator(".row-item").nth(row_index)
    if row.count() == 0:
        return None

    row.click()
    page.wait_for_selector("#detailView:not([hidden])")

    subject = page.locator("#detailSubject").inner_text()
    sender = page.locator("#detailSender").inner_text()
    decision = page.locator("#detailDecision").inner_text()
    rationale = page.locator("#detailRationale").inner_text()

    print(f"\n  {sender}")
    print(f"  {subject!r}")
    print(f"    decided: {decision}")
    print(f"    because: {rationale}")

    if decision in NEEDS_HUMAN_TEXT:
        page.click("#backBtn")
        outcome = ("left pending -- needs a real reply typed by a human" if decision in ("reply", "forward")
                    else "left pending -- needs real content typed by a human")
    elif commit:
        page.click(IMMEDIATE_ICON[decision])
        page.wait_for_selector("#listView:not([hidden])")
        outcome = "confirmed"
    else:
        page.click("#backBtn")
        outcome = "skipped (dry run)"
    print(f"    -> {outcome}")

    return {"sender": sender, "subject": subject, "decision": decision,
            "rationale": rationale, "outcome": outcome}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true",
                    help="actually click Confirm for each email; the default is a dry run")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N pending emails")
    ap.add_argument("--pace", type=float, default=1.5,
                    help="seconds to pause on each email so a human can follow it (default: 1.5)")
    ap.add_argument("--headless", action="store_true",
                    help="run without a visible browser window (default: visible)")
    ap.add_argument("--log", type=Path, default=None,
                    help="where to write the run log (default: data/runs/)")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    print(f"\n  mode  {'COMMIT' if args.commit else 'dry run'}")
    print_countdown()

    started_server = ensure_server_running()

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()
        page.goto(SERVER_URL)
        page.click("#toolbarRefreshBtn")
        page.wait_for_timeout(600)

        banner(1, "Working through the inbox")
        skipped = 0
        while args.limit is None or len(results) < args.limit:
            result = process_one(page, args.commit, len(results), skipped)
            if result is None:
                break
            results.append(result)
            if result["outcome"] != "confirmed":
                skipped += 1
            time.sleep(args.pace)

        if not args.headless:
            page.wait_for_timeout(1500)
        browser.close()

    if started_server is not None:
        started_server.terminate()

    banner(2, "Result")
    print(f"  emails processed  {len(results)}")
    for r in results:
        print(f"    {r['decision']:<14} {r['subject']}")
    needing_reply = [r for r in results if r["decision"] in ("reply", "forward")]
    if needing_reply:
        print(f"\n  {len(needing_reply)} email(s) need a real reply typed by a human -- "
              f"open http://localhost:8765/ yourself to answer them.")
    if not args.commit:
        print("\n  Nothing was clicked for real. Re-run with --commit to actually confirm each one.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = args.log or (REPO / "data" / "runs" / f"automate_inbox_{stamp}.json")
    if not log_path.is_absolute():
        log_path = REPO / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps({
        "commit": args.commit, "processed_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"  run log           {log_path.relative_to(REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
