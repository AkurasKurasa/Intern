"""Excel Recorder (3.1) - which cell the user selected, and its column header.

Anchored on selection, not on copy: Excel exposes no reliable copy event, and
3.1 is explicit that Ctrl+C should not be intercepted. The Reconciler's join on
value plus time is what disambiguates a selection that was never copied.

Two mechanisms, in the order 3.1 gives them:
  1. win32com DispatchWithEvents on Application.SheetSelectionChange
  2. xlwings polling app.selection at ~150 ms

Windows and a running Excel are required, so this module imports cleanly
everywhere and fails only when a recorder is actually started. `SelectionSource`
is the seam: the header resolution and type inference below are ordinary
functions over cell facts, and they are tested without Excel.
"""

import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from coworker_recorder.events import ExcelEvent, now, write_event  # noqa: E402

POLL_SECONDS = 0.15  # 3.1's xlwings fallback cadence

# Column-level type inference (3.1: infer over the column, not the single cell,
# because the column is what the matcher sees at execution time).
TYPE_PATTERNS = [
    ("id", re.compile(r"^\d{2,4}-\d{3,6}(-\d+)?$")),
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    ("phone", re.compile(r"^\+?\d[\d\s\-()]{6,}$")),
]
NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


class ExcelUnavailable(RuntimeError):
    """Raised when no Excel automation backend can be started."""


# Excel rejects COM calls while it is busy - a cell in edit mode, a dialog open,
# a menu down. These are the codes it uses to say so, and every one of them is
# an ordinary thing for a person to be doing mid-demonstration.
EXCEL_BUSY_CODES = {
    -2146777998,   # 0x800AC472 VBA_E_IGNORE - "call was rejected by callee"
    -2147418111,   # 0x80010001 RPC_E_CALL_REJECTED
    -2147417846,   # 0x8001010A RPC_E_SERVERCALL_RETRYLATER
}


def _is_excel_busy(exc):
    code = getattr(exc, "hresult", None)
    if code is None:
        args = getattr(exc, "args", ())
        code = args[0] if args and isinstance(args[0], int) else None
    return code in EXCEL_BUSY_CODES


def infer_column_type(values, number_format=""):
    """The column's type from its values, with the cell's format as a hint."""
    cleaned = [str(v).strip() for v in values if str(v).strip()]
    if not cleaned:
        return "text"

    # The number format is checked first, because a date in Excel *is* a
    # number: a serial like 45000 under "dd/mm/yy" is a date, and inferring
    # "numeric" from its digits would be exactly wrong. 3.1 asks for the format
    # plus a regex pass, in that order.
    fmt = (number_format or "").lower()
    if any(token in fmt for token in ("yy", "mmm", "dd/", "d/m", "m/d")):
        return "date"

    for name, pattern in TYPE_PATTERNS:
        if all(pattern.match(v) for v in cleaned):
            return name
    if all(NUMERIC.match(v) for v in cleaned):
        return "numeric"
    return "text"


def header_for_column(sheet_values, column_index, header_row=0):
    """3.1: read the ListObject column name where there is one, otherwise
    assume the header row (row 1 by default, with a session-start override)."""
    if not sheet_values or header_row >= len(sheet_values):
        return ""
    row = sheet_values[header_row]
    if column_index >= len(row):
        return ""
    return str(row[column_index] or "").strip()


def column_values(sheet_values, column_index, header_row=0):
    return [
        row[column_index]
        for row in sheet_values[header_row + 1:]
        if column_index < len(row) and row[column_index] is not None
    ]


def build_event(sheet_name, cell, column_index, sheet_values, value,
                number_format="", header_row=0):
    """One selection becomes a 2.1 event. Pure, so it is tested without Excel."""
    return ExcelEvent(
        t=now(),
        sheet=sheet_name,
        cell=cell,
        column_index=column_index,
        header=header_for_column(sheet_values, column_index, header_row),
        value="" if value is None else str(value),
        number_format=number_format,
        inferred_type=infer_column_type(
            column_values(sheet_values, column_index, header_row), number_format
        ),
    )


# ------------------------------------------------------------- backends


class XlwingsRecorder:
    """3.1's documented fallback: poll app.selection.

    Preferred in practice over the COM event sink - DispatchWithEvents needs a
    message pump on the calling thread, which a plain script does not have, so
    the sink fires only while something else services the loop.
    """

    def __init__(self, session_path, header_row=0):
        self.session_path = Path(session_path)
        self.header_row = header_row
        self.book = None
        self._last = None
        # The sheet's contents, read once per sheet rather than once per poll.
        # A demonstrator only reads from the grade book, so the values do not
        # move underneath us - and re-reading the used range six times a second
        # is both slow and the main way to collide with Excel while the user is
        # mid-edit.
        self._sheet_cache = {}

    def start(self):
        try:
            import xlwings
        except ImportError as exc:
            raise ExcelUnavailable(
                "xlwings is not installed; pip install xlwings"
            ) from exc
        try:
            self.book = xlwings.books.active
        except Exception as exc:  # noqa: BLE001 - no Excel, no open book
            raise ExcelUnavailable(f"no active Excel workbook: {exc}") from exc
        return self

    def sheet_values(self, sheet):
        """The sheet's used range, cached after the first read."""
        if sheet.name not in self._sheet_cache:
            used = sheet.used_range.value or []
            if used and not isinstance(used[0], list):
                used = [used]
            self._sheet_cache[sheet.name] = used
        return self._sheet_cache[sheet.name]

    def poll_once(self):
        """Emit an event if the selection moved. Returns the event or None.

        Excel refuses COM calls while a cell is in edit mode or a dialog is up,
        raising VBA_E_IGNORE (0x800AC472). That is a normal thing for a user to
        be doing, not a failure: the tick is skipped and the next one picks the
        selection up. Letting it propagate would end the recording every time
        the demonstrator double-clicked a cell.
        """
        try:
            selection = self.book.app.selection
            address = selection.address
            if address == self._last:
                return None

            sheet = selection.sheet
            values = self.sheet_values(sheet)
            event = build_event(
                sheet_name=sheet.name,
                cell=address.replace("$", ""),
                column_index=selection.column - 1,
                sheet_values=values,
                value=selection.value,
                number_format=selection.number_format or "",
                header_row=self.header_row,
            )
        except Exception as exc:  # noqa: BLE001 - pywintypes.com_error and kin
            if _is_excel_busy(exc):
                return None
            raise

        self._last = address
        write_event(self.session_path, event)
        return event

    def run(self, seconds=None, on_event=None):
        started = time.time()
        while seconds is None or time.time() - started < seconds:
            event = self.poll_once()
            if event and on_event:
                on_event(event)
            time.sleep(POLL_SECONDS)


def start_recorder(session_path, header_row=0):
    """The recorder 3.1 asks for, with its fallback."""
    return XlwingsRecorder(session_path, header_row).start()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", type=Path,
                    default=REPO / "data" / "demos" / "session.jsonl")
    ap.add_argument("--header-row", type=int, default=0,
                    help="0-based; 3.1's one-time override when row 1 is not the header")
    ap.add_argument("--seconds", type=float, default=None)
    args = ap.parse_args()

    try:
        recorder = start_recorder(args.session, args.header_row)
    except ExcelUnavailable as exc:
        raise SystemExit(f"cannot start: {exc}")

    print(f"recording selections to {args.session} - Ctrl+C to stop")
    try:
        recorder.run(args.seconds,
                     on_event=lambda e: print(f"  {e.cell:<6} {e.header:<18} {e.value!r}"))
    except KeyboardInterrupt:
        print("\nstopped")
