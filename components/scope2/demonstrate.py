"""Watch a demonstration, then learn from it.

    python demonstrate.py

Opens your grade sheet in Excel and the portal in Chrome, and watches while you
fill in a few students by hand. When you stop it, it joins what it saw in Excel
to what it saw in the browser, tells you what it thinks you were doing, and
asks you to confirm.

This is the half of the system a person actually touches. Everything after it -
learning the mapping, inducing the Remarks rule, filling the remaining students -
is `automate.py`.

Both recorders are the real ones: the Excel side is recorder/excel_recorder.py
polling the live selection, and the browser side is the extension's own
content.js injected into the page. Nothing about the demonstration is simulated.
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from executor.scanner import CHROMIUM, variant_url  # noqa: E402
from coworker_recorder.confirm import (  # noqa: E402
    ACCEPT, CORRECT, REJECT, Decision, apply_decisions, proposals, render,
)
from coworker_recorder.events import BrowserEvent, write_session  # noqa: E402
from coworker_recorder.excel_recorder import ExcelUnavailable, start_recorder  # noqa: E402
from coworker_recorder.reconciler import reconcile, summarise  # noqa: E402

SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"
DEMOS = REPO / "data" / "demos"
CONTENT_JS = (REPO / "coworker_recorder" / "extension" / "content.js").read_text(encoding="utf-8")

POLL_SECONDS = 0.15
HEADER_ROW = 11          # the sheet's headers are on row 12, 0-based here

RULE = "-" * 74


def open_browser(variant, base_url):
    """A real Chrome window you drive yourself, with the recorder injected.

    Playwright is used only to launch it and to read the recorder's buffer back
    out; every click and keystroke in the session is yours.
    """
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
    except Exception:  # noqa: BLE001 - Chrome not registered with Playwright
        browser = playwright.chromium.launch(
            executable_path=str(CHROMIUM) if CHROMIUM.exists() else None,
            headless=False)

    page = browser.new_page(no_viewport=True)
    page.goto(variant_url(variant, base_url), wait_until="load")
    page.wait_for_selector("#records-body tr")
    page.evaluate(CONTENT_JS)
    return playwright, browser, page


def describe(event):
    if event["source"] == "excel":
        return f"    excel    {event['cell']:<8} {event['header']:<16} {event['value']!r}"
    resolved = BrowserEvent(**event)
    return (f"    browser  row {event['row']:<4} "
            f"{resolved.resolved.label[:28]:<30} {event['value']!r}")


def watch(page, excel_recorder, session_path):
    """Poll both sides until interrupted. Returns (excel_events, raw_browser).

    The browser buffer is kept from the last successful poll rather than read
    once at the end. Ctrl+C in the terminal reaches the whole process group, so
    Chrome is often already gone by the time the loop unwinds - and reading it
    then throws away the entire demonstration for the sake of the last 150ms.
    """
    excel_events, seen_browser, latest = [], 0, []

    print(f"\n{RULE}\n RECORDING - do a few students, then press Ctrl+C here\n{RULE}")
    print("  Click the cell in Excel, copy it, paste it into the portal.")
    print("  Do that for Course, Year and Grade, then pick Remarks yourself.")
    print("  Six students is the number this portal needs; fewer will not")
    print("  pin down the Remarks rule.\n")

    try:
        while True:
            if excel_recorder is not None:
                event = excel_recorder.poll_once()
                if event is not None:
                    excel_events.append(event)
                    print(describe({"source": "excel", "cell": event.cell,
                                    "header": event.header, "value": event.value}))

            try:
                latest = page.evaluate("() => window.__demo || []")
            except Exception:  # noqa: BLE001 - the window may have been closed
                print("\n  browser closed - keeping what was recorded")
                break

            while seen_browser < len(latest):
                print(describe(latest[seen_browser]))
                seen_browser += 1

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\n  stopped")

    return excel_events, latest


def readiness(session_path, result):
    """Have you shown it enough? Returns (ready, lines to print).

    Two different capabilities need different amounts of demonstration, and
    they stop needing more at different times:

      the mapping  - settles quickly; a field with a reconciled pair is learned.
      the rule     - needs both outcomes present, and needs enough rows that
                     only one field still separates them. On this portal Year
                     separates Passed from Failed by coincidence up to five
                     rows, and only Grade survives at six.

    So "enough" is whichever of the two is still short, and that is worth
    saying out loud rather than leaving to a guess.
    """
    from rules.detect import (
        STATUS_AMBIGUOUS, STATUS_NO_DRIVER, STATUS_OK, STATUS_ONE_CLASS,
    )
    from rules.induce_from_session import induce_from_session

    rows = sorted({p.row for p in result.pairs})
    lines = [f"  rows demonstrated   {len(rows)}"]

    mapped = sorted({p.target_label for p in result.pairs})
    lines.append(f"  fields learned      {', '.join(mapped) if mapped else 'none'}")

    if not mapped:
        return False, lines + [
            "",
            "  NOT READY - nothing reconciled. Values pasted into the portal did",
            "  not match any cell selected in Excel shortly before. Check that",
            "  Excel was open and that you clicked the source cell before copying.",
        ]

    if len(rows) < 2:
        return False, lines + [
            "",
            "  NOT READY - one row is not enough on a sheet portal. A cell's",
            "  label covers its column and its student at once, and the column's",
            "  own name only emerges by comparing rows. Do at least one more.",
        ]

    entries, _ = induce_from_session(session_path, auto_confirm=False)
    if not entries:
        return True, lines + [
            "",
            "  READY - every field was copied from a column; no rule to induce.",
        ]

    for entry in entries:
        field, detection = entry["field"], entry["detection"]
        rule = entry["rule"]

        if detection.status == STATUS_ONE_CLASS:
            return False, lines + [
                f"  rule for {field}      cannot be worked out yet",
                "",
                "  NOT READY - every student you demonstrated got the same result.",
                "  Show one that FAILED (a grade under 75 - Bernardo, Elaine S.",
                "  has 72) so it can see where the boundary is.",
            ]

        if detection.status == STATUS_AMBIGUOUS:
            drivers = ", ".join(d.driver_label for d in detection.drivers)
            return False, lines + [
                f"  rule for {field}      ambiguous",
                "",
                f"  NOT READY - {drivers} all explain the result equally well so",
                "  far. Two or three more students, with a mix of pass and fail,",
                "  should separate them.",
            ]

        if detection.status == STATUS_NO_DRIVER:
            return False, lines + [
                f"  rule for {field}      no driver found",
                "",
                f"  NOT READY - nothing you filled in explains {field}. If it is",
                "  not decided by another field on the form, it is outside what",
                "  this system induces.",
            ]

        if detection.status == STATUS_OK and rule is not None:
            lines.append(f"  rule for {field}      {rule.describe()}")

    return True, lines + ["", "  READY - you can stop. Everything below is automatic."]


def confirm_interactively(result):
    print(render(result))
    decisions = []
    for proposal in proposals(result):
        label, header = proposal["target_label"], proposal["source_header"]
        answer = input(f"  {header} -> {label}?  "
                       f"[Enter = correct / n = wrong / or type the right "
                       f"column name]: ").strip()

        # "yes" is the obvious thing to type at a yes/no prompt, and reading it
        # as a column named "yes" silently corrupts the training data. Accept
        # the words people actually use, and only treat an answer as a
        # correction when it is not one of them.
        if answer == "" or answer.lower() in ("y", "yes", "yep", "ok", "correct"):
            decisions.append(Decision(label, ACCEPT))
        elif answer.lower() in ("n", "no", "nope", "wrong"):
            decisions.append(Decision(label, REJECT))
        else:
            decisions.append(Decision(label, CORRECT, answer))
    return apply_decisions(result, decisions)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", default="v0_base")
    ap.add_argument("--sheet", type=Path, default=SHEET)
    ap.add_argument("--base-url", default="http://127.0.0.1:8765")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--no-excel", action="store_true",
                    help="skip the Excel watcher (browser events only)")
    ap.add_argument("--yes", action="store_true",
                    help="accept every proposal without asking")
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_path = args.out or (DEMOS / f"live_{args.variant}_{stamp}.jsonl")

    print(f"\n  sheet   {args.sheet}")
    print(f"  portal  {args.base_url}/{args.variant}/index.html")
    print(f"  session {session_path.name}")

    # ---- Excel -------------------------------------------------------
    excel_recorder = None
    if not args.no_excel:
        print(f"\n{RULE}\n 1. Excel\n{RULE}")
        print("  Open this file in Excel and go to the SUMMARY tab:")
        print(f"    {args.sheet}")
        input("\n  Press Enter once it is open and in front of you... ")
        try:
            excel_recorder = start_recorder(session_path, header_row=HEADER_ROW)
            print("  watching the Excel selection")
        except ExcelUnavailable as exc:
            print(f"  could not attach to Excel: {exc}")
            print("  continuing without it - the browser side still records, but")
            print("  nothing will reconcile, so every field will look derived.")

    # ---- browser -----------------------------------------------------
    print(f"\n{RULE}\n 2. Browser\n{RULE}")
    print("  Opening Chrome with the recorder loaded. Use it normally.")
    playwright, browser, page = open_browser(args.variant, args.base_url)

    # ---- record ------------------------------------------------------
    try:
        excel_events, raw_browser = watch(page, excel_recorder, session_path)
    finally:
        try:
            browser.close()
            playwright.stop()
        except Exception:  # noqa: BLE001 - the window may already be gone
            pass

    browser_events = [BrowserEvent(**raw) for raw in raw_browser]
    if not browser_events:
        raise SystemExit("\n  nothing was recorded in the browser - "
                         "was anything typed into the portal?")

    write_session(session_path, excel_events + browser_events)
    print(f"\n  wrote {session_path.relative_to(REPO)}  "
          f"({len(excel_events)} excel, {len(browser_events)} browser)")

    # ---- reconcile ---------------------------------------------------
    print(f"\n{RULE}\n 3. What I think you were doing\n{RULE}")
    from coworker_recorder.events import read_session

    excel_back, browser_back = read_session(session_path)
    result = reconcile(excel_back, browser_back)
    print(summarise(result))

    if not result.pairs:
        print("\n  Nothing reconciled. The usual cause is that the Excel side")
        print("  was not recording - check that Excel was open and in focus.")
        return 1

    confirmed = (apply_decisions(result,
                                 [Decision(p["target_label"], ACCEPT)
                                  for p in proposals(result)])
                 if args.yes else confirm_interactively(result))

    print(f"\n  {confirmed.accepted} accepted, {confirmed.corrections} corrected, "
          f"{confirmed.rejections} rejected -> {len(confirmed.pairs)} confirmed pairs")

    # ---- have you shown it enough? -----------------------------------
    print(f"\n{RULE}\n 4. Have you shown it enough?\n{RULE}")
    ready, lines = readiness(session_path, result)
    for line in lines:
        print(line)

    if not ready:
        print("\n  Run demonstrate.py again and do the students you did this")
        print("  time PLUS the extra ones, in a single session. Each run writes")
        print("  its own file and automate.py reads one file, so a second run")
        print("  replaces this demonstration rather than adding to it.")
        return 1

    print(f"\n{RULE}\n Next\n{RULE}")
    print("  Fill in the remaining students from what you just showed it:\n")
    print(f"    python automate.py --session {session_path.relative_to(REPO)}")
    print(f"    python automate.py --session {session_path.relative_to(REPO)} --commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
