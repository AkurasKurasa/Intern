"""
components/excel_observer/excel_observer.py
============================================
Semantic Excel state reader via win32com.

Instead of guessing what is on screen via OCR, ExcelObserver connects directly
to the running Excel process and reads the exact cell values, addresses, column
headers, sheet name, and pixel positions of every visible cell.

The snapshot() output is a state dict fully compatible with the existing trace
format so it drops straight into the transformer pipeline — no changes to the
model or executor required.  The "text" field of every element holds the real
cell value, so the sentence-embedding encoder gives the model genuine semantic
understanding of what each cell contains and means.

Public API
----------
    obs = ExcelObserver()
    ok  = obs.connect()          # attach to the running Excel process
    state = obs.snapshot()       # returns a trace-compatible state dict
    obs.disconnect()

State dict schema
-----------------
{
    "application":        "Microsoft Excel",
    "window_title":       "<workbook name>",
    "screen_resolution":  [w, h],
    "focused_element_id": "cell_<ADDRESS>",
    "elements": [
        {
            "element_id": "cell_B3",
            "type":       "cell" | "header_cell" | "active_cell",
            "bbox":       [x1, y1, x2, y2],   # screen pixel coords
            "text":       "<cell value as string>",
            "confidence": 1.0,
            "enabled":    True,
            "semantic": {
                "address":   "B3",
                "sheet":     "Employee Roster",
                "row":       3,
                "col":       2,
                "is_header": False,
                "is_active": True,
                "formula":   "=SUM(A1:A10)" | "",
                "data_type": "string" | "number" | "date" | "formula" | "empty",
                "column_header": "Last Name",   # value of row-1 in same column
            }
        },
        ...
    ],
    "excel_context": {
        "workbook":     "Q1_2026_Operations.xlsx",
        "sheet":        "Employee Roster",
        "active_cell":  "B3",
        "active_value": "Fitzgerald",
        "active_formula": "",
        "used_range":   "A1:N7",
        "sheet_names":  ["Employee Roster", "Q1 Sales", ...],
    }
}
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ── path setup ────────────────────────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_COMP_DIR = os.path.dirname(_THIS_DIR)
_ROOT     = os.path.dirname(_COMP_DIR)
for _p in (_ROOT, _COMP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import win32com.client
    import win32api
    import win32con
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

# Maximum rows / columns to capture around the active cell
MAX_ROWS = 30
MAX_COLS = 20


# ══════════════════════════════════════════════════════════════════════════════
#  ExcelObserver
# ══════════════════════════════════════════════════════════════════════════════

class ExcelObserver:
    """
    Connects to a running Microsoft Excel instance and snapshots its semantic
    state as a trace-compatible dict on each call to snapshot().
    """

    def __init__(self):
        self._xl:   Optional[Any] = None   # Excel.Application COM object
        self._hwnd: Optional[int] = None   # Excel window handle (for screen coords)

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """
        Attach to an already-running Excel process.
        Returns True on success, False if Excel is not open.
        """
        if not _WIN32_AVAILABLE:
            print("[ExcelObserver] win32com not available — install pywin32.")
            return False
        try:
            self._xl = win32com.client.GetObject(Class="Excel.Application")
            self._xl.Visible = True
            return True
        except Exception as exc:
            print(f"[ExcelObserver] Could not connect to Excel: {exc}")
            self._xl = None
            return False

    def disconnect(self):
        """Release the COM reference."""
        self._xl = None

    @property
    def connected(self) -> bool:
        return self._xl is not None

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """
        Read the current Excel state and return a trace-compatible state dict.
        Falls back to a minimal empty state if Excel is unreachable.
        """
        if not self.connected:
            return _empty_state()

        try:
            return self._read_state()
        except Exception as exc:
            print(f"[ExcelObserver] snapshot() error: {exc}")
            return _empty_state()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _read_state(self) -> Dict[str, Any]:
        xl = self._xl
        wb = xl.ActiveWorkbook
        ws = xl.ActiveSheet
        aw = xl.ActiveWindow

        workbook_name = wb.Name if wb else "Unknown"
        sheet_name    = ws.Name if ws else "Unknown"
        sheet_names   = [s.Name for s in wb.Sheets] if wb else []

        # ── Active cell ───────────────────────────────────────────────────────
        ac          = xl.ActiveCell
        active_addr = ac.Address.replace("$", "")   # e.g. "$B$3" → "B3"
        active_val  = _cell_value_str(ac)
        active_form = ac.Formula if ac.Formula != ac.Value else ""

        # ── Screen resolution ─────────────────────────────────────────────────
        screen_w, screen_h = _screen_resolution()

        # ── Determine the capture range ───────────────────────────────────────
        used  = ws.UsedRange
        ur1   = used.Cells(1, 1)
        first_row = ur1.Row
        first_col = ur1.Column
        last_row  = first_row + used.Rows.Count - 1
        last_col  = first_col + used.Columns.Count - 1

        # Cap to reasonable window
        last_row = min(last_row, first_row + MAX_ROWS - 1)
        last_col = min(last_col, first_col + MAX_COLS - 1)

        # ── Read headers (row 1 of the used range) ────────────────────────────
        headers: Dict[int, str] = {}   # col_index → header text
        for col in range(first_col, last_col + 1):
            cell = ws.Cells(first_row, col)
            headers[col] = _cell_value_str(cell)

        # ── Build element list ────────────────────────────────────────────────
        elements: List[Dict[str, Any]] = []
        elem_counter = 0

        for row in range(first_row, last_row + 1):
            for col in range(first_col, last_col + 1):
                cell = ws.Cells(row, col)
                addr = cell.Address.replace("$", "")   # e.g. "$B$3" → "B3"

                # Pixel bbox via PointsToScreenPixels
                try:
                    px1 = aw.PointsToScreenPixelsX(cell.Left)
                    py1 = aw.PointsToScreenPixelsY(cell.Top)
                    px2 = aw.PointsToScreenPixelsX(cell.Left + cell.Width)
                    py2 = aw.PointsToScreenPixelsY(cell.Top + cell.Height)
                except Exception:
                    px1 = py1 = px2 = py2 = 0

                value      = _cell_value_str(cell)
                formula    = cell.Formula if str(cell.Formula).startswith("=") else ""
                is_header  = (row == first_row)
                is_active  = (addr == active_addr)
                col_header = headers.get(col, "")
                data_type  = _infer_data_type(cell, formula, value)

                elem_type = (
                    "active_cell" if is_active else
                    "header_cell" if is_header else
                    "cell"
                )

                # Text shown to the embedding model:
                # For header cells → the header label itself
                # For data cells → "<column_header>: <value>" so the model
                # understands both what the column means and what's in it.
                if is_header:
                    display_text = value
                elif value:
                    display_text = f"{col_header}: {value}" if col_header else value
                else:
                    display_text = col_header   # empty cell still knows its column

                elem_id = f"cell_{addr}"
                elements.append({
                    "element_id": elem_id,
                    "type":       elem_type,
                    "bbox":       [px1, py1, px2, py2],
                    "text":       display_text,
                    "confidence": 1.0,
                    "enabled":    True,
                    "semantic": {
                        "address":       addr,
                        "sheet":         sheet_name,
                        "row":           row,
                        "col":           col,
                        "is_header":     is_header,
                        "is_active":     is_active,
                        "formula":       formula,
                        "data_type":     data_type,
                        "column_header": col_header,
                        "raw_value":     value,
                    },
                })
                elem_counter += 1

        return {
            "application":        "Microsoft Excel",
            "window_title":       workbook_name,
            "screen_resolution":  [screen_w, screen_h],
            "focused_element_id": f"cell_{active_addr}",
            "elements":           elements,
            "excel_context": {
                "workbook":        workbook_name,
                "sheet":           sheet_name,
                "active_cell":     active_addr,
                "active_value":    active_val,
                "active_formula":  active_form,
                "used_range":      used.Address.replace("$", ""),
                "sheet_names":     sheet_names,
            },
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _cell_value_str(cell) -> str:
    """Return the cell's display value as a plain string."""
    try:
        v = cell.Value
        if v is None:
            return ""
        if isinstance(v, float) and v == int(v):
            return str(int(v))
        return str(v)
    except Exception:
        return ""


def _infer_data_type(cell, formula: str, value: str) -> str:
    if formula:
        return "formula"
    if not value:
        return "empty"
    try:
        float(value.replace(",", ""))
        return "number"
    except ValueError:
        pass
    # rough date check
    if any(sep in value for sep in ("/", "-")) and len(value) <= 10:
        return "date"
    return "string"


def _screen_resolution() -> Tuple[int, int]:
    if _WIN32_AVAILABLE:
        try:
            w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
            h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
            return w, h
        except Exception:
            pass
    return 1920, 1080


def _empty_state() -> Dict[str, Any]:
    return {
        "application":        "Microsoft Excel",
        "window_title":       "",
        "screen_resolution":  list(_screen_resolution()),
        "focused_element_id": None,
        "elements":           [],
        "excel_context":      {},
    }
