"""Record a demonstration session end to end (Milestone 4).

Drives the portal with Playwright while the real content script records, and
synthesises the Excel side from the sheet the values came from - the selection
events a user would generate by clicking each source cell before pasting it.

This is not a substitute for the win32com recorder. It is the reproducible half:
it exercises the browser recorder, the Reconciler and the confirmation gate on
real DOM events without needing a live Excel on the machine running the tests.
A real session replaces the synthesised half and nothing else changes.

Usage:
    python recorder/demo_session.py --rows 2
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from executor.scanner import CHROMIUM, variant_url  # noqa: E402
import pandas as pd  # noqa: E402
from executor.sheet_reader import read_sheet  # noqa: E402
from coworker_recorder.confirm import ACCEPT, Decision, apply_decisions, proposals, render  # noqa: E402
from coworker_recorder.events import BrowserEvent, ExcelEvent, write_session  # noqa: E402
from coworker_recorder.reconciler import reconcile, summarise  # noqa: E402

CONTENT_JS = (REPO / "coworker_recorder" / "extension" / "content.js").read_text(encoding="utf-8")
SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"
DEMOS = REPO / "data" / "demos"

# Which sheet column feeds which portal control, for the demonstration only.
# The whole point of the experiment is that the system induces this; here it is
# the script standing in for a human who knows where they are pasting from.
DEMO_COLUMNS = [
    ("PROGRAM", "course", 5),
    ("YEAR LEVEL", "year", 8),
    ("FINAL GRADE", "grade", 9),
]
PASS_MARK = 75

# V6b runs the 1.00-5.00 scale, where 1.00 is highest and 3.00 is the passing
# mark. The sheet is always 0-100, so a demonstration on that variant converts
# as the encoder would - and the induced rule must come out with the opposite
# operator, which is the whole point of the variant.
SCALE_1_5 = {"v6b_scale"}


def to_scale_1_5(grade):
    """0-100 onto 1.00-5.00: 100 -> 1.00, 75 -> 3.00, below 75 -> worse than 3."""
    if grade >= PASS_MARK:
        return round(1.0 + (100.0 - grade) / (100.0 - PASS_MARK) * 2.0, 2)
    return round(min(5.0, 3.0 + (PASS_MARK - grade) / PASS_MARK * 2.0), 2)

# Synthetic clock. Rows are spaced well inside the Reconciler's 60 s window but
# far enough apart that one row's selections cannot match another row's writes.
START = datetime(2026, 8, 5, 10, 0, 0, tzinfo=timezone.utc)
ROW_SECONDS = 30
WRITE_DELAY = 5


def excel_events_for(record, when, scale_1_5=False):
    """The selections a user makes while copying one row, one cell at a time."""
    events = []
    for offset, (header, key, column_index) in enumerate(DEMO_COLUMNS):
        value = record[header]
        if key == "grade" and scale_1_5:
            value = to_scale_1_5(float(value))
        elif isinstance(value, float) and value.is_integer():
            value = int(value)
        events.append(ExcelEvent(
            t=(when + timedelta(seconds=offset * 2)).isoformat(timespec="milliseconds"),
            sheet="SUMMARY",
            cell=f"{chr(ord('A') + column_index)}{record.name + 15}",
            column_index=column_index,
            header=header,
            value=str(value),
            inferred_type="text" if header == "PROGRAM" else "numeric",
        ))
    return events


def select_rows(df, rows, balanced=True):
    """Pick the demonstrated rows.

    Balanced by default: a demonstration in which every student passed cannot
    constrain a threshold at all, and 3.8 would rightly refuse to induce one.
    A demonstrator who understands the task shows at least one failing row.
    """
    if not balanced:
        return df.head(rows)

    passing = df[df["FINAL GRADE"] >= PASS_MARK]
    failing = df[df["FINAL GRADE"] < PASS_MARK]
    if failing.empty or passing.empty:
        return df.head(rows)

    take_fail = max(1, rows // 3)
    chosen = pd.concat([passing.head(rows - take_fail), failing.head(take_fail)])
    return chosen.sort_index()


def record(rows=2, variant="v0_base", out=None, balanced=True):
    from playwright.sync_api import sync_playwright

    df, _ = read_sheet(SHEET, "SUMMARY", 11, "STUDENT NUMBER")
    df = select_rows(df, rows, balanced).reset_index(drop=True)

    excel_events, browser_raw = [], []
    clock = START
    rows = len(df)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=str(CHROMIUM) if CHROMIUM.exists() else None
        )
        page = browser.new_page()
        try:
            page.goto(variant_url(variant), wait_until="load")
            page.wait_for_selector("#records-body tr")
            page.evaluate(CONTENT_JS)

            for index, (_, sheet_row) in enumerate(df.iterrows()):
                excel_events += excel_events_for(sheet_row, clock, variant in SCALE_1_5)
                clock += timedelta(seconds=10)

                row = page.locator("#records-body tr").nth(index)
                for header, key, _ in DEMO_COLUMNS:
                    value = sheet_row[header]
                    if key == "grade" and variant in SCALE_1_5:
                        value = to_scale_1_5(float(value))
                    elif isinstance(value, float) and value.is_integer():
                        value = int(value)
                    row.locator(f"[data-key={key}] input").fill(str(value))

                # The derived field: chosen by the demonstrator, with no source
                # cell behind it. That absence is the whole signal.
                passed = sheet_row["FINAL GRADE"] >= PASS_MARK
                options = page.eval_on_selector(
                    "#records-body [data-key=remarks] select",
                    "el => Array.from(el.options).map(o => o.value).filter(Boolean)")
                remark = options[0] if passed else options[1]
                row.locator("[data-key=remarks] select").select_option(remark)

            page.locator("h1").click()
            browser_raw = page.evaluate("() => window.__demo")
        finally:
            browser.close()

    # Both sides are restamped onto one synthetic clock, keyed by row: the
    # Excel selections for a row, then the writes for that row a few seconds
    # later. Real wall-clock times from Playwright are milliseconds apart and
    # would put every row inside every other row's window.
    row_start = {row: START + timedelta(seconds=row * ROW_SECONDS)
                 for row in range(rows)}

    for index, event in enumerate(excel_events):
        row, offset = divmod(index, len(DEMO_COLUMNS))
        event.t = (row_start[row] + timedelta(seconds=offset)).isoformat(
            timespec="milliseconds")

    browser_events = [BrowserEvent(**raw) for raw in browser_raw]
    by_row = {}
    for event in browser_events:
        by_row.setdefault(event.row, []).append(event)
    for row, events in by_row.items():
        events.sort(key=lambda e: e.seq)
        for offset, event in enumerate(events):
            event.t = (
                row_start[row] + timedelta(seconds=WRITE_DELAY + offset)
            ).isoformat(timespec="milliseconds")

    out = out or (DEMOS / f"{variant}_{rows}rows.jsonl")
    write_session(out, excel_events + browser_events)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=2)
    ap.add_argument("--variant", default="v0_base")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--unbalanced", action="store_true",
                    help="take the first N rows even if they share one outcome")
    args = ap.parse_args()

    path = record(args.rows, args.variant, args.out, balanced=not args.unbalanced)
    print(f"recorded {path.relative_to(REPO)}")

    from coworker_recorder.events import read_session

    excel_events, browser_events = read_session(path)
    print(f"  {len(excel_events)} excel selections, {len(browser_events)} browser writes")

    result = reconcile(excel_events, browser_events)
    print()
    print(summarise(result))
    print(render(result))

    confirmed = apply_decisions(
        result, [Decision(p["target_label"], ACCEPT) for p in proposals(result)]
    )
    print(f"{confirmed.accepted} accepted, {confirmed.corrections} corrected -> "
          f"{len(confirmed.pairs)} confirmed pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
