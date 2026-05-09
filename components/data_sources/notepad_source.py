"""
components/data_sources/notepad_source.py
==========================================
NotepadDataSource — reads field values from an open Notepad (or text file)
window via Win32 WM_GETTEXT, bypassing the 2000-char UIA cap.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

from .base import DataSource

logger = logging.getLogger(__name__)

# ── Field-line matching helpers ───────────────────────────────────────────────
# Used by peek() / peek_next_after() / agent._peek_notepad to resolve a
# field-name to its value in the source text. Substring matching ("Name" hits
# "First Name" / "Last Name") was producing wrong values silently. These
# helpers prefer exact key match (`<field>:`), with substring as a guarded
# fallback (whole-word only).

def _split_kv(line: str) -> Optional[Tuple[str, str]]:
    """Strip brackets from key + trailing [hint] / ← markers from value."""
    if ":" not in line:
        return None
    key, _, val = line.partition(":")
    key = re.sub(r"[\[\]]", "", key).strip()
    if not key:
        return None
    val = re.sub(r"\s+\[[^\]]*\]\s*$", "", val)
    val = re.sub(r"\s*←.*", "", val).strip()
    return (key, val)


def _exact_match(line: str, field_name: str) -> Optional[str]:
    """Return value if line's key equals field_name (case-insensitive)."""
    kv = _split_kv(line.strip())
    if kv and kv[0].lower() == field_name.lower().strip():
        return kv[1]
    return None


def _find_field_line(lines, field_name: str) -> Optional[Tuple[int, str]]:
    """
    Return (line_index, value) for first line whose key matches field_name.
    Pass 1: exact key match. Pass 2: whole-word substring in key.
    Returns None if not found.
    """
    fl = field_name.lower().strip()
    for i, line in enumerate(lines):
        val = _exact_match(line, field_name)
        if val is not None:
            return (i, val)
    for i, line in enumerate(lines):
        kv = _split_kv(line.strip())
        if not kv:
            continue
        key_lc = kv[0].lower()
        if re.search(rf"\b{re.escape(fl)}\b", key_lc):
            return (i, kv[1])
    return None


# Shared record parser (same logic as agent.py _parse_records).
def _parse_records(raw_text: str) -> dict:
    """
    Parse a multi-record intake text into {record_num: {field: value}}.
    Expects records delimited by lines containing 'RECORD N OF M'.
    Returns {} if no record headers are found.
    """
    parts = re.split(r"={3,}[\s\S]*?RECORD\s+(\d+)\s+OF\s+\d+[\s\S]*?={3,}", raw_text)
    if len(parts) <= 1:
        return {}
    records = {}
    i = 1
    while i + 1 < len(parts):
        rn   = int(parts[i])
        body = parts[i + 1]
        data = {}
        current_section = ""
        for line in body.splitlines():
            stripped = line.strip()
            sec_m = re.match(r'^\[\s*(.+?)\s*\]$', stripped)
            if sec_m:
                sect = sec_m.group(1)
                if re.match(r'(?:Driver|Vehicle)\s+\d+', sect):
                    current_section = sect
                else:
                    current_section = ""
                continue
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                key = re.sub(r"[\[\]]", "", key).strip()
                val = re.sub(r"\s*\[.*", "", val)
                val = re.sub(r"\s*←.*", "", val).strip()
                if not key:
                    continue
                cleaned = val.lower().strip()
                if current_section:
                    full_key = f"{current_section} {key}"
                    if cleaned in {"(none)", "none"}:
                        data[full_key] = ""
                    elif val and cleaned != "":
                        data[full_key] = val
                else:
                    if key in data:
                        continue
                    if cleaned in {"(none)", "none"}:
                        data[key] = ""
                    elif val and cleaned != "":
                        data[key] = val
        records[rn] = data
        i += 2
    return records


class NotepadDataSource(DataSource):
    """
    Reads field values from an open Notepad (or plain text file) window.

    Parameters
    ----------
    window_title_fragment : str
        A fragment of the Notepad window title (e.g. "data_entry_intake" or "Data Entry For GUI").
    """

    _EDIT_CLASSES = {"Edit", "RichEditD2DPT", "RichEdit20W", "RICHEDIT50W", "RICHEDIT60W"}

    def __init__(self, window_title_fragment: str = "") -> None:
        self._title_fragment = window_title_fragment
        self._cache: Dict[str, str] = {}
        self._record_num: int = 1

    # ── DataSource interface ─────────────────────────────────────────────────

    def lookup(self, field_name: str, section: str = "") -> Optional[str]:
        """Return the cached value for field_name (with optional section prefix)."""
        _skip_vals = {"(none)", "none", "(leave blank)", "n/a", "yes (check)",
                      "leave blank — liability only", "leave blank — owned outright"}

        def _get(key: str) -> Optional[str]:
            kl = key.lower()
            v = self._cache.get(key) or next(
                (rv for rk, rv in self._cache.items() if rk.lower() == kl), None
            )
            if not v or v.lower().strip("()") in _skip_vals:
                return None
            return v

        if section:
            val = _get(f"{section} {field_name}")
            if val:
                return val
        return _get(field_name)

    def get_all(self) -> Dict[str, str]:
        return dict(self._cache)

    def refresh(self, record_num: int) -> None:
        """Re-read Notepad and populate the cache for the given record number."""
        self._record_num = record_num
        full_text = self.read_full_text()
        if not full_text:
            logger.warning("NotepadDataSource.refresh: no text found — cache unchanged")
            return

        records = _parse_records(full_text)
        if records:
            rec = records.get(record_num, records.get(min(records), {}))
            self._cache = rec
            logger.info("NotepadDataSource: refreshed %d field(s) for record %d",
                        len(rec), record_num)
        else:
            # Plain key:value text
            data: Dict[str, str] = {}
            for line in full_text.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = re.sub(r"[\[\]]", "", key).strip()
                    val = re.sub(r"\s*\[.*", "", val)
                    val = re.sub(r"\s*←.*", "", val).strip()
                    if (key and key not in data and val
                            and val.lower() not in {"(none)", "none", "(leave blank)"}):
                        data[key] = val
            self._cache = data
            logger.info("NotepadDataSource: refreshed (plain text) %d field(s)", len(data))

    # ── Win32 helpers ────────────────────────────────────────────────────────

    def _find_notepad_hwnd(self, state: Optional[Dict[str, Any]] = None) -> int:
        """
        Find the Notepad / text-file window HWND.
        Priority:
          1. Match window title from UIA background elements (if state provided).
          2. Use _title_fragment to search via FindWindow.
          3. Enumerate all top-level windows by class/title heuristic.
        Returns 0 if not found.
        """
        try:
            import win32gui

            np_hwnd = 0

            # Strategy 1: from UIA state
            if state:
                for e in state.get("elements", []):
                    if e.get("window_role") != "background":
                        continue
                    wt = (e.get("window_title") or "").strip()
                    if ".txt" in wt or "notepad" in wt.lower():
                        np_hwnd = win32gui.FindWindow(None, wt)
                        if not np_hwnd:
                            np_hwnd = win32gui.FindWindow(None, wt + " - Notepad")
                        if np_hwnd:
                            return np_hwnd

            # Strategy 2: use title fragment
            if self._title_fragment:
                np_hwnd = win32gui.FindWindow(None, self._title_fragment)
                if not np_hwnd:
                    np_hwnd = win32gui.FindWindow(None, self._title_fragment + " - Notepad")
                if np_hwnd:
                    return np_hwnd

            # Strategy 3: enumerate all top-level windows
            def _find_np(hwnd, _):
                nonlocal np_hwnd
                if np_hwnd:
                    return
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title = win32gui.GetWindowText(hwnd)
                cls   = win32gui.GetClassName(hwnd)
                if cls == "Notepad" or ".txt" in title or "notepad" in title.lower():
                    np_hwnd = hwnd

            win32gui.EnumWindows(_find_np, None)
            return np_hwnd
        except Exception:
            return 0

    def _find_edit_hwnd(self, np_hwnd: int) -> int:
        """Find the edit control child of a Notepad window."""
        try:
            import win32gui
            edit_hwnd = 0

            def _find_edit(hwnd, _):
                nonlocal edit_hwnd
                if edit_hwnd:
                    return
                try:
                    if win32gui.GetClassName(hwnd) in self._EDIT_CLASSES:
                        edit_hwnd = hwnd
                except Exception:
                    pass

            win32gui.EnumChildWindows(np_hwnd, _find_edit, None)

            # Win11 Notepad nests the editor deeper
            if not edit_hwnd:
                child = win32gui.GetWindow(np_hwnd, 5)  # GW_CHILD
                while child and not edit_hwnd:
                    win32gui.EnumChildWindows(child, _find_edit, None)
                    child = win32gui.GetWindow(child, 2)  # GW_HWNDNEXT

            return edit_hwnd
        except Exception:
            return 0

    def read_full_text(self, state: Optional[Dict[str, Any]] = None) -> str:
        """
        Read the complete text from the Notepad window via Win32 WM_GETTEXT.
        Bypasses the 2000-char UIA cap.
        Returns empty string if not available.
        """
        try:
            import win32gui
            import win32api
            import ctypes

            WM_GETTEXTLENGTH = 0x000E
            WM_GETTEXT       = 0x000D

            np_hwnd = self._find_notepad_hwnd(state)
            if not np_hwnd:
                return ""

            edit_hwnd = self._find_edit_hwnd(np_hwnd)
            if not edit_hwnd:
                return ""

            length = win32api.SendMessage(edit_hwnd, WM_GETTEXTLENGTH, 0, 0)
            if length <= 0:
                return ""
            buf = ctypes.create_unicode_buffer(length + 2)
            ctypes.windll.user32.SendMessageW(edit_hwnd, WM_GETTEXT, length + 1, buf)
            return buf.value
        except Exception:
            return ""

    def peek(self, field_name: str, state: Dict[str, Any],
             visual_reader: Optional[Any] = None,
             visual_cache: Optional[Dict[str, str]] = None,
             step_delay: float = 1.2) -> Optional[str]:
        """
        Scroll Notepad to the line containing field_name, extract its value
        into the cache, and return it (or None if not found).

        When visual_reader is provided and field not found via Win32 text:
          falls back to VLM screenshot scan.
        """
        if field_name in self._cache:
            return self._cache.get(field_name)

        try:
            import pyautogui
            import win32gui
            import win32api

            EM_GETFIRSTVISIBLELINE = 0x00CE
            EM_LINESCROLL          = 0x00B6

            # Collect raw text and background window title
            elements = state.get("elements", [])
            bg_window_title = ""
            raw_text = ""
            for e in elements:
                if e.get("window_role") != "background":
                    continue
                win_title = (e.get("window_title") or "").strip()
                blob      = (e.get("value") or "").strip()
                if not bg_window_title:
                    if ".txt" in win_title or "notepad" in win_title.lower():
                        bg_window_title = win_title
                if blob and len(blob) > len(raw_text):
                    raw_text = blob

            # Prefer Win32 full text
            full_np_text = self.read_full_text(state)
            if full_np_text:
                raw_text = full_np_text

            logger.info("NotepadDataSource.peek: field=%r  raw_text_len=%d",
                        field_name, len(raw_text))
            if not raw_text:
                return None

            lines = raw_text.splitlines()
            hit = _find_field_line(lines, field_name)
            target_line = hit[0] if hit else 0

            np_hwnd   = self._find_notepad_hwnd(state)
            edit_hwnd = self._find_edit_hwnd(np_hwnd) if np_hwnd else 0

            if edit_hwnd:
                first_visible = win32api.SendMessage(edit_hwnd, EM_GETFIRSTVISIBLELINE, 0, 0)
                delta = target_line - first_visible - 3
                if delta != 0:
                    win32api.SendMessage(edit_hwnd, EM_LINESCROLL, 0, delta)

            # Extract value from Win32 text directly (exact key match preferred)
            found_val = None
            if hit and hit[1]:
                found_val = hit[1]
                self._cache[field_name] = found_val
                if visual_cache is not None:
                    visual_cache[field_name] = found_val
                logger.info("NotepadDataSource.peek: Win32 text found field=%r value=%r",
                            field_name, found_val)

            if found_val:
                return found_val

            # VLM fallback
            if visual_reader and np_hwnd:
                form_hwnd = win32gui.GetForegroundWindow()
                try:
                    visual_reader._force_foreground(np_hwnd)
                    time.sleep(0.4)
                    # Move mouse over Notepad — pyautogui.scroll targets cursor window
                    try:
                        rect = win32gui.GetWindowRect(np_hwnd)
                        pyautogui.moveTo((rect[0]+rect[2])//2, (rect[1]+rect[3])//2,
                                         duration=0.1)
                    except Exception:
                        pass

                    seen_hashes: set = set()
                    fl_lower = field_name.lower()

                    for attempt in range(10):
                        screenshot = pyautogui.screenshot()
                        img_hash   = hash(screenshot.tobytes())

                        if img_hash not in seen_hashes:
                            seen_hashes.add(img_hash)
                            new_fields = visual_reader._extract_from_screenshot(screenshot)
                            for k, v in new_fields.items():
                                norm = k.strip()
                                if norm and norm not in self._cache:
                                    self._cache[norm] = str(v).strip()
                                    if visual_cache is not None:
                                        visual_cache[norm] = str(v).strip()
                                if norm.lower() == fl_lower:
                                    found_val = str(v).strip()

                        if found_val:
                            logger.info("NotepadDataSource.peek: VLM found field=%r value=%r (attempt %d)",
                                        field_name, found_val, attempt + 1)
                            break

                        logger.info("NotepadDataSource.peek: VLM scrolling for field=%r (attempt %d)",
                                    field_name, attempt + 1)
                        pyautogui.scroll(-10)
                        time.sleep(0.35)

                    if not found_val:
                        logger.warning("NotepadDataSource.peek: VLM exhausted — field=%r not found",
                                       field_name)
                finally:
                    try:
                        win32gui.SetForegroundWindow(form_hwnd)
                    except Exception:
                        pass
            elif np_hwnd and not visual_reader:
                # Non-VLM mode: hover mouse over Notepad (visual UX only)
                rect = win32gui.GetWindowRect(np_hwnd)
                cx   = (rect[0] + rect[2]) / 2
                cy   = (rect[1] + rect[3]) / 2
                orig = pyautogui.position()
                pyautogui.moveTo(cx, cy, duration=0.25)
                time.sleep(0.4)
                pyautogui.moveTo(orig.x, orig.y, duration=0.2)

            return found_val

        except Exception:
            return None

    def peek_next_after(self, field_name: str,
                        state: Optional[Dict[str, Any]] = None) -> Optional[Tuple[str, str]]:
        """
        Return (key, value) of the first field that appears AFTER field_name
        in the Notepad text and has NOT yet been cached.
        Used to resolve duplicate-UIA-label fields.
        Returns None if not found.
        """
        full_text = self.read_full_text(state)
        if not full_text:
            return None
        lines = full_text.splitlines()
        # Locate target line via exact key match first (whole-word fallback inside _find_field_line)
        hit = _find_field_line(lines, field_name)
        if not hit:
            return None
        target_idx = hit[0]
        for line in lines[target_idx + 1:]:
            kv = _split_kv(line.strip())
            if not kv:
                continue
            k, v = kv
            if k and v and k not in self._cache:
                logger.info("NotepadDataSource.peek_next_after: '%s' → next uncached field='%s' value=%r",
                            field_name, k, v)
                return (k, v)
        return None
