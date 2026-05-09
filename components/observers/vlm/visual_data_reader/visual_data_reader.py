"""
visual_data_reader.py
=====================
Reads source data from ANY visible window (Notepad, PDF, Excel, Web, etc.)
by taking screenshots and using Gemini Flash vision to extract key-value data.

Replaces the Win32 EM_GETLINE approach so the agent actually *sees* the screen
instead of reading memory directly — works for any document format.

Usage:
    reader = VisualDataReader(api_key=os.environ["GEMINI_API_KEY"])
    reader.pre_scan("Data Entry For GUI - Notepad")   # scrolls + reads
    value = reader.lookup("First Name")               # → "John"
"""

from __future__ import annotations

import io
import json
import logging
import time
from difflib import get_close_matches
from typing import Optional

import pyautogui
import win32api
import win32con
import win32gui
import win32process

logger = logging.getLogger("observers.visual_data_reader")


class VisualDataReader:
    """
    Pre-scans a visible source window using a vision model (Groq or Gemini),
    extracts all key-value data, and provides fast cache lookups.
    """

    def __init__(self, api_key: str, groq_api_key: str = ""):
        self._gemini_key = api_key
        self._groq_key   = groq_api_key
        # Flat cache for backward-compat lookup() / get_all().
        self._cache: dict[str, str] = {}
        # Per-record cache populated by pre_scan: {record_num: {field: value}}.
        self._record_cache: dict[int, dict[str, str]] = {}

        # Prefer Groq if key provided; fall back to Gemini
        if groq_api_key:
            import openai
            self._client  = openai.OpenAI(
                api_key  = groq_api_key,
                base_url = "https://api.groq.com/openai/v1",
            )
            self._backend = "groq"
            logger.info("VisualDataReader: using Groq vision backend (llama-4-scout)")
        else:
            from google import genai
            self._client  = genai.Client(api_key=api_key)
            self._backend = "gemini"
            logger.info("VisualDataReader: using Gemini vision backend")

    # ── Public API ────────────────────────────────────────────────────────────

    def pre_scan(self, source_window_title: str) -> dict[str, str]:
        """
        Bring the source window to the foreground, scroll through it from
        top to bottom taking screenshots, and extract all data via the VLM.

        Returns the FLAT {field: value} cache for backward compatibility.
        Per-record results are stored in self._record_cache as
        {record_num: {field: value}}, populated by detecting the
        '__record__' marker the prompt asks the VLM to emit when it sees
        a 'RECORD N OF M' header.
        """
        hwnd = self._find_window(source_window_title)
        if not hwnd:
            logger.warning("VisualDataReader: window %r not found — skipping scan", source_window_title)
            return {}

        logger.info("VisualDataReader: pre-scanning %r ...", source_window_title)

        # Bring to front using thread-attach workaround for Windows focus restrictions
        self._force_foreground(hwnd)
        time.sleep(0.5)

        # Find Notepad's edit control hwnd so we can scroll it via Win32
        # EM_LINESCROLL — bypasses pyautogui's "scroll-targets-cursor-window"
        # quirk that was breaking the cross-record pre-scan entirely.
        edit_hwnd = self._find_edit_hwnd(hwnd)
        EM_GETFIRSTVISIBLELINE = 0x00CE
        EM_LINESCROLL          = 0x00B6
        EM_GETLINECOUNT        = 0x00BA

        # Scroll to top via EM_LINESCROLL (negative delta, large)
        if edit_hwnd:
            try:
                cur_first = win32api.SendMessage(edit_hwnd, EM_GETFIRSTVISIBLELINE, 0, 0)
                if cur_first > 0:
                    win32api.SendMessage(edit_hwnd, EM_LINESCROLL, 0, -cur_first)
                    time.sleep(0.2)
            except Exception:
                pass
        else:
            # Fallback to keyboard if no edit hwnd
            pyautogui.hotkey("ctrl", "Home")
            time.sleep(0.3)
            try:
                rect = win32gui.GetWindowRect(hwnd)
                pyautogui.moveTo((rect[0]+rect[2])//2, (rect[1]+rect[3])//2, duration=0.1)
            except Exception:
                pass

        seen_hashes: set[int] = set()
        consecutive_empty = 0
        current_record: int = 1   # default bucket if no header seen yet
        SCROLL_LINES = 20         # how many lines to advance per iteration
        max_iterations = 200      # safety bound (200 * 20 = 4000 lines)
        last_first_visible = -1

        for _iter in range(max_iterations):
            screenshot = pyautogui.screenshot()
            img_hash   = hash(screenshot.tobytes())

            if img_hash not in seen_hashes:
                seen_hashes.add(img_hash)
                extracted = self._extract_from_screenshot(screenshot)

                # Detect record header — VLM emits '__record__' when it sees 'RECORD N OF M'.
                rec_marker = extracted.pop("__record__", None)
                if rec_marker:
                    try:
                        new_rec = int(str(rec_marker).strip())
                        if new_rec >= 1:
                            current_record = new_rec
                            logger.info("VisualDataReader: now reading RECORD %d", current_record)
                    except (ValueError, TypeError):
                        pass

                bucket = self._record_cache.setdefault(current_record, {})
                new_count = 0
                for k, v in extracted.items():
                    normalised = k.strip()
                    if not normalised:
                        continue
                    val_str = str(v).strip()
                    if normalised not in bucket:
                        bucket[normalised] = val_str
                        new_count += 1
                    if normalised not in self._cache:
                        self._cache[normalised] = val_str

                logger.info("VisualDataReader: record=%d  extracted %d new field(s)  "
                            "(record total=%d  flat total=%d)",
                            current_record, new_count, len(bucket), len(self._cache))

                if new_count == 0:
                    consecutive_empty += 1
                    if consecutive_empty >= 4:
                        break
                else:
                    consecutive_empty = 0

            # ── Scroll Notepad via Win32 EM_LINESCROLL ──
            if edit_hwnd:
                try:
                    win32api.SendMessage(edit_hwnd, EM_LINESCROLL, 0, SCROLL_LINES)
                    time.sleep(0.25)
                    cur_first = win32api.SendMessage(edit_hwnd, EM_GETFIRSTVISIBLELINE, 0, 0)
                    if cur_first == last_first_visible:
                        # Scroll didn't advance — at end of document
                        logger.info("VisualDataReader: EM_LINESCROLL no longer advancing — end of doc")
                        break
                    last_first_visible = cur_first
                except Exception as e:
                    logger.warning("VisualDataReader: EM_LINESCROLL failed (%s) — falling back to mouse scroll", e)
                    pyautogui.scroll(-15)
                    time.sleep(0.35)
            else:
                pyautogui.scroll(-15)
                time.sleep(0.35)

        logger.info("VisualDataReader: scan complete — %d total field(s) cached across %d record(s)",
                    len(self._cache), len(self._record_cache))
        for _rn in sorted(self._record_cache):
            logger.info("  record %d: %d field(s)", _rn, len(self._record_cache[_rn]))
        return dict(self._cache)

    def get_record_cache(self) -> dict[int, dict[str, str]]:
        """Return the per-record cache populated by pre_scan."""
        return {rn: dict(d) for rn, d in self._record_cache.items()}

    def rescan_after_scroll(
        self, source_window_title: str, line_advance: int = 12
    ) -> dict[str, str]:
        """
        Scroll the source Notepad +line_advance lines via EM_LINESCROLL, take
        ONE screenshot, ask the VLM to extract what is now visible, return
        the flat {field: value} dict for that screen.

        Used by callers that experienced a cache miss — looks at the next
        chunk of the source document to see if the missing field appears.
        """
        import win32api

        EM_LINESCROLL = 0x00B6

        hwnd = self._find_window(source_window_title)
        if not hwnd:
            return {}
        edit_hwnd = self._find_edit_hwnd(hwnd)
        if not edit_hwnd:
            return {}

        form_hwnd = win32gui.GetForegroundWindow()
        try:
            self._force_foreground(hwnd)
            time.sleep(0.25)
            win32api.SendMessage(edit_hwnd, EM_LINESCROLL, 0, line_advance)
            time.sleep(0.30)
            shot = pyautogui.screenshot()
            extracted = self._extract_from_screenshot(shot)
            extracted.pop("__record__", None)
            out: dict[str, str] = {}
            for k, v in extracted.items():
                nk = k.strip()
                if nk:
                    val = str(v).strip()
                    out[nk] = val
                    self._cache[nk] = val
            logger.info("rescan_after_scroll(+%d lines): %d field(s) on screen",
                        line_advance, len(out))
            return out
        finally:
            try:
                win32gui.SetForegroundWindow(form_hwnd)
            except Exception:
                pass

    def lookup(self, field_label: str) -> Optional[str]:
        """
        Return the cached value for field_label.
        Uses fuzzy matching so "Date of Birth" matches "DOB", etc.
        Returns None if not found.
        """
        if not field_label:
            return None

        # 1. Exact match
        if field_label in self._cache:
            return self._cache[field_label]

        # 2. Case-insensitive exact
        lower = field_label.lower()
        for k, v in self._cache.items():
            if k.lower() == lower:
                return v

        # 3. Fuzzy match (handles abbreviations, spacing differences)
        keys  = list(self._cache.keys())
        close = get_close_matches(field_label, keys, n=1, cutoff=0.6)
        if close:
            logger.debug("VisualDataReader: fuzzy match %r → %r", field_label, close[0])
            return self._cache[close[0]]

        return None

    def get_all(self) -> dict[str, str]:
        return dict(self._cache)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _force_foreground(self, hwnd: int) -> None:
        """
        Bring hwnd to the foreground using the thread-attach trick.
        Direct SetForegroundWindow fails from background processes on Windows;
        attaching to the current foreground thread bypasses the restriction.
        """
        try:
            fg_hwnd  = win32gui.GetForegroundWindow()
            fg_tid   = win32process.GetWindowThreadProcessId(fg_hwnd)[0]
            cur_tid  = win32api.GetCurrentThreadId()

            attached = False
            if fg_tid and fg_tid != cur_tid:
                try:
                    win32process.AttachThreadInput(cur_tid, fg_tid, True)
                    attached = True
                except Exception:
                    pass

            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)

            if attached:
                try:
                    win32process.AttachThreadInput(cur_tid, fg_tid, False)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("VisualDataReader: _force_foreground failed — %s", exc)
            # Fall back: click the taskbar button via pyautogui as last resort
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            except Exception:
                pass

    def _extract_from_screenshot(self, screenshot) -> dict[str, str]:
        """Send screenshot to vision model and extract all key-value pairs."""
        import base64

        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        buf.seek(0)
        img_bytes = buf.read()

        prompt = (
            "You are reading a data source document (could be Notepad, PDF, Excel, "
            "a web page, or any other format). "
            "Extract EVERY field name and its corresponding value that you can clearly see. "
            "If you see a header like 'RECORD N OF M' (where N and M are integers), "
            "include a key '__record__' whose value is N (as a string). "
            "If you see a section header like '[ Driver 2 ]' or '[ Vehicle 1 ]', "
            "prefix subsequent field names from that section with the section label "
            "(e.g. 'Driver 2 First Name', 'Vehicle 1 VIN'). "
            "Return ONLY a valid JSON object where keys are field names and values are their values. "
            'Example: {"__record__": "1", "First Name": "John", "Driver 2 First Name": "Maria"} '
            "Do not include fields whose values are blank or unclear. "
            "Do not add any explanation — JSON only."
        )

        def _call_gemini(img_bytes_: bytes, prompt_: str) -> str:
            from google.genai import types as _gt
            import google.genai as _genai
            _gc = _genai.Client(api_key=self._gemini_key)
            _r  = _gc.models.generate_content(
                model="models/gemini-2.0-flash-lite",
                contents=[
                    _gt.Part.from_text(text=prompt_),
                    _gt.Part.from_bytes(data=img_bytes_, mime_type="image/png"),
                ],
            )
            return _r.text.strip()

        def _parse(raw: str) -> dict:
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip())

        try:
            if self._backend == "groq":
                b64 = base64.b64encode(img_bytes).decode()
                try:
                    response = self._client.chat.completions.create(
                        model="meta-llama/llama-4-scout-17b-16e-instruct",
                        messages=[{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                            ],
                        }],
                        max_tokens=1024,
                    )
                    text = response.choices[0].message.content.strip()
                except Exception as groq_exc:
                    exc_str = str(groq_exc)
                    if ("429" in exc_str or "rate_limit" in exc_str.lower()) and self._gemini_key:
                        logger.warning("VisualDataReader: Groq rate limited — falling back to Gemini")
                        try:
                            text = _call_gemini(img_bytes, prompt)
                        except Exception as gem_exc:
                            logger.warning("VisualDataReader: Gemini fallback failed — %s", gem_exc)
                            return {}
                    else:
                        logger.warning("VisualDataReader: Groq extraction failed — %s", groq_exc)
                        return {}
            else:
                text = _call_gemini(img_bytes, prompt)

            return _parse(text)

        except json.JSONDecodeError:
            logger.warning("VisualDataReader: vision model returned non-JSON — skipping frame")
            return {}
        except Exception as exc:
            logger.warning("VisualDataReader: extraction failed — %s", exc)
            return {}

    def scan_tab(self, tab_name: str, record_num: int,
                 full_text: str, source_window_title: str) -> dict[str, str]:
        """
        Live scan: scroll source window to the TAB N — TABNAME section for
        record_num, screenshot, ask the VLM what is on screen. Returns the
        full {field: value} dict for what the VLM saw — NOT filtered against
        any prior cache, so per-record values overwrite stale ones.

        Used per tab-switch as the authoritative "what the VLM sees" read.
        """
        import re
        import win32api
        import win32gui

        EM_GETFIRSTVISIBLELINE = 0x00CE
        EM_LINESCROLL          = 0x00B6

        hwnd = self._find_window(source_window_title)
        if not hwnd:
            logger.warning("scan_tab: window %r not found — skipping", source_window_title)
            return {}

        edit_hwnd = self._find_edit_hwnd(hwnd)

        # Find the line range for TAB N — TABNAME within the current record.
        # We need both the section start AND end (next TAB or next RECORD)
        # to cap the scan window.
        lines      = full_text.splitlines()
        tab_upper  = tab_name.upper()
        target_line = 0
        section_end_line = -1
        record_count = 0
        in_target_record = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            if re.search(r'RECORD\s+\d+\s+OF\s+\d+', stripped):
                record_count += 1
                if in_target_record and target_line:
                    section_end_line = i
                    break
                in_target_record = (record_count == record_num)
                continue
            if in_target_record and target_line == 0 and re.search(
                    rf'TAB\s+\d+\s+[—\-]\s+{re.escape(tab_upper)}', stripped):
                target_line = i
                continue
            # Once in target section, watch for next TAB header to mark end
            if in_target_record and target_line > 0 and section_end_line < 0:
                if re.search(r'TAB\s+\d+\s+[—\-]\s+\w+', stripped):
                    section_end_line = i
                    break

        if target_line == 0:
            logger.warning("scan_tab: section %r not found in record %d", tab_name, record_num)
            return {}
        if section_end_line < 0:
            section_end_line = min(target_line + 60, len(lines))   # safety cap

        # Bring Notepad forward + scroll edit control to tab section header
        form_hwnd = win32gui.GetForegroundWindow()
        try:
            self._force_foreground(hwnd)
            time.sleep(0.35)
            if edit_hwnd:
                first_visible = win32api.SendMessage(edit_hwnd, EM_GETFIRSTVISIBLELINE, 0, 0)
                delta = max(0, target_line - 2) - first_visible
                if delta != 0:
                    win32api.SendMessage(edit_hwnd, EM_LINESCROLL, 0, delta)
                    time.sleep(0.35)

            # ONE screenshot — what the VLM sees right now is what the agent
            # gets to type. If the agent later needs a field not visible on
            # this screen, the caller must scroll Notepad and call scan_tab
            # again. This is the strict human-like rule: type only what you
            # currently see.
            screenshot = pyautogui.screenshot()
            extracted  = self._extract_from_screenshot(screenshot)
            extracted.pop("__record__", None)

            extracted_all: dict[str, str] = {}
            for k, v in extracted.items():
                normalised = k.strip()
                if not normalised:
                    continue
                val_str = str(v).strip()
                extracted_all[normalised] = val_str
                self._cache[normalised]   = val_str

            logger.info("scan_tab[%s record=%d]: 1 screenshot — %d field(s) on screen",
                        tab_name, record_num, len(extracted_all))

            logger.info("scan_tab[%s record=%d]: complete — %d total field(s) live-extracted",
                        tab_name, record_num, len(extracted_all))
            return extracted_all
        finally:
            try:
                win32gui.SetForegroundWindow(form_hwnd)
            except Exception:
                pass

    def _find_edit_hwnd(self, np_hwnd: int) -> Optional[int]:
        """Find the text edit control inside a Notepad/text-editor window."""
        import win32gui

        _EDIT_CLASSES = {"Edit", "RichEditD2DPT", "RichEdit20W", "RICHEDIT50W"}
        edit_hwnd = None

        def _find_edit(hwnd, _):
            nonlocal edit_hwnd
            if edit_hwnd:
                return
            try:
                if win32gui.GetClassName(hwnd) in _EDIT_CLASSES:
                    edit_hwnd = hwnd
            except Exception:
                pass

        win32gui.EnumChildWindows(np_hwnd, _find_edit, None)

        # Win11 Notepad nests the editor deeper — walk grandchildren
        if not edit_hwnd:
            child = win32gui.GetWindow(np_hwnd, 5)   # GW_CHILD
            while child and not edit_hwnd:
                win32gui.EnumChildWindows(child, _find_edit, None)
                child = win32gui.GetWindow(child, 2)  # GW_HWNDNEXT

        return edit_hwnd

    def _find_window(self, title_fragment: str) -> Optional[int]:
        """Find a visible window whose title contains title_fragment."""
        result = None

        def _cb(hwnd, _):
            nonlocal result
            if result:
                return
            if not win32gui.IsWindowVisible(hwnd):
                return
            t = win32gui.GetWindowText(hwnd)
            if title_fragment.lower() in t.lower():
                result = hwnd

        win32gui.EnumWindows(_cb, None)
        return result
