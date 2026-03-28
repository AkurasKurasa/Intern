"""
agent/executor.py
=================
Low-level action execution — the agent's hands.

Classes
-------
  ExecutionResult  — immutable record of what was executed
  ActionExecutor   — converts a prediction dict into real OS input
  _TextResolver    — resolves what text to type from background elements
  _snap_to_element — snaps a predicted click to the nearest UI element
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, NamedTuple, Optional

logger = logging.getLogger(__name__)

# ── path setup ────────────────────────────────────────────────────────────────
_HERE     = os.path.dirname(os.path.abspath(__file__))   # agent/
_COMP_DIR = os.path.dirname(_HERE)                        # components/
_ROOT     = os.path.dirname(_COMP_DIR)                    # Intern/
for _p in (_ROOT, _COMP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── optional dependency: pyautogui ────────────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI_AVAILABLE = True
except ImportError:
    _PYAUTOGUI_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════════════════
#  ExecutionResult
# ══════════════════════════════════════════════════════════════════════════════

class ExecutionResult(NamedTuple):
    action_type : str
    position    : Optional[tuple]
    key_count   : int
    keystrokes  : List[str]
    timestamp   : str
    dry_run     : bool
    success     : bool
    error       : str

    def __str__(self) -> str:
        tag = "[DRY-RUN] " if self.dry_run else ""
        ok  = "[OK]" if self.success else "[FAIL]"
        if self.action_type == "click":
            detail = f"@ {self.position}"
        elif self.action_type == "keyboard":
            detail = f"keys={self.key_count}"
            if self.keystrokes:
                detail += f" ({', '.join(self.keystrokes[:6])}{'...' if len(self.keystrokes) > 6 else ''})"
        else:
            detail = ""
        return (
            f"{tag}{ok} ExecutionResult(type={self.action_type!r}, {detail}, ts={self.timestamp})"
            + (f" ERROR: {self.error}" if self.error else "")
        )


# ══════════════════════════════════════════════════════════════════════════════
#  ActionExecutor
# ══════════════════════════════════════════════════════════════════════════════

class ActionExecutor:
    """Converts a single prediction dict into a real (or dry-run) OS action."""

    def __init__(
        self,
        dry_run:          bool  = False,
        pre_click_delay:  float = 0.05,
        post_click_delay: float = 0.1,
        keyboard_delay:   float = 0.05,
    ):
        self.dry_run          = dry_run
        self.pre_click_delay  = pre_click_delay
        self.post_click_delay = post_click_delay
        self.keyboard_delay   = keyboard_delay

        if not dry_run and not _PYAUTOGUI_AVAILABLE:
            raise ImportError(
                "pyautogui is required for live execution.\n"
                "Install with:  pip install pyautogui\n"
                "Or use dry_run=True."
            )

    def execute(self, prediction: Dict[str, Any]) -> ExecutionResult:
        action_type = prediction.get("action_type", "no_op")
        ts          = datetime.now().isoformat()
        try:
            if action_type == "click":
                pos  = prediction.get("click_position", [0, 0])
                x, y = int(round(pos[0])), int(round(pos[1]))
                self._click(x, y)
                return ExecutionResult("click", (x, y), 0, [], ts, self.dry_run, True, "")

            elif action_type == "keyboard":
                key_count  = max(1, int(prediction.get("key_count", 1)))
                keystrokes = list(prediction.get("keystrokes", []))
                text       = prediction.get("text", "")
                issued     = self._keyboard(key_count, keystrokes, text)
                return ExecutionResult("keyboard", None, len(issued), issued, ts, self.dry_run, True, "")

            else:
                logger.info("NO_OP%s", "  [DRY-RUN]" if self.dry_run else "")
                return ExecutionResult("no_op", None, 0, [], ts, self.dry_run, True, "")

        except Exception as exc:
            logger.error("execute() raised: %s", exc)
            return ExecutionResult(action_type, None, 0, [], ts, self.dry_run, False, str(exc))

    def _click(self, x: int, y: int) -> None:
        logger.info("CLICK  @ (%d, %d)%s", x, y, "  [DRY-RUN]" if self.dry_run else "")
        if self.dry_run:
            return
        time.sleep(self.pre_click_delay)
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.click(x, y)
        time.sleep(self.post_click_delay)

    def _keyboard(self, key_count: int, keystrokes: List[str], text: str = "") -> List[str]:
        if text:
            logger.info("KEYBOARD  paste=%r%s", text[:60], "  [DRY-RUN]" if self.dry_run else "")
            if not self.dry_run:
                try:
                    import pyperclip
                    pyperclip.copy(text)
                    time.sleep(0.05)
                    pyautogui.hotkey("ctrl", "v")
                    time.sleep(self.post_click_delay)
                except Exception:
                    for ch in text:
                        pyautogui.typewrite(ch, interval=self.keyboard_delay)
            return list(text)

        elif keystrokes:
            issued = keystrokes[:key_count]
            logger.info("KEYBOARD  keys=%d %s%s", len(issued), issued, "  [DRY-RUN]" if self.dry_run else "")
            if not self.dry_run:
                for key in issued:
                    self._press_key(key)
                    time.sleep(self.keyboard_delay)
            return issued

        else:
            logger.warning("KEYBOARD  key_count=%d — no text resolved, skipped.", key_count)
            return []

    def _press_key(self, key: str) -> None:
        if key.startswith("Key."):
            pyautogui.press(key.split("Key.", 1)[1])
        elif len(key) == 1:
            pyautogui.typewrite(key, interval=0.0)
        else:
            pyautogui.press(key)


# ══════════════════════════════════════════════════════════════════════════════
#  _snap_to_element
# ══════════════════════════════════════════════════════════════════════════════

_CLICKABLE_TYPES = {
    "editcontrol", "comboboxcontrol", "checkboxcontrol",
    "buttoncontrol", "listitemcontrol", "tabitemcontrol",
}

def _snap_to_element(
    predicted_xy:  List[float],
    state:         Dict[str, Any],
    max_snap_dist: float = 120.0,
) -> Optional[List[float]]:
    """Snap a predicted click position to the nearest interactive UI element."""
    px, py    = float(predicted_xy[0]), float(predicted_xy[1])
    best_dist = float("inf")
    best_center = None

    for elem in state.get("elements", []):
        if elem.get("window_role") == "background":
            continue
        if (elem.get("type") or "").lower() not in _CLICKABLE_TYPES:
            continue
        bbox = elem.get("bbox", [])
        if len(bbox) < 4:
            continue
        x1, y1, x2, y2 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        dist = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best_center = [cx, cy]

    if best_center is not None and best_dist <= max_snap_dist:
        logger.info(
            "SnapClick: (%.0f,%.0f) → (%.0f,%.0f)  dist=%.0f px",
            px, py, best_center[0], best_center[1], best_dist,
        )
        return best_center
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  _TextResolver
# ══════════════════════════════════════════════════════════════════════════════

class _TextResolver:
    """Resolves what text to type from visible background window elements."""

    def __init__(self):
        self._used_texts: set = set()

    def resolve(self, state: Dict[str, Any], source_elem_idx: int = -1) -> str:
        elements = state.get("elements", [])
        bg_elems = [e for e in elements if e.get("window_role") == "background"]
        if not bg_elems:
            return ""

        # Option 1: transformer source pointer
        if 0 <= source_elem_idx < len(elements):
            pointed = elements[source_elem_idx]
            if pointed.get("window_role") == "background":
                raw  = (pointed.get("value") or pointed.get("text") or "").strip()
                text = self._clean_value(raw)
                if text and text not in self._used_texts and text.lower() not in {
                    "(none)", "none", "(leave blank)", "n/a", "(n/a)"
                }:
                    self._used_texts.add(text)
                    logger.info("TextResolver: source_ptr[%d] → %r", source_elem_idx, text)
                    return text

        # Option 2: field-name match
        focused_id = state.get("focused_element_id")
        focused    = next((e for e in elements if e.get("element_id") == focused_id), None)
        field_name = self._field_context(focused, elements) if focused else ""

        if field_name:
            value = self._match_value(field_name, bg_elems)
            if value and value not in self._used_texts:
                self._used_texts.add(value)
                logger.info("TextResolver: field=%r → %r", field_name, value)
                return value

        # No sequential fallback — without a field-name match we cannot safely
        # know which value belongs to which field, especially with multi-record
        # source documents.  Return empty and let the agent skip this step.
        logger.info("TextResolver: no field match for focused element — skipping.")
        return ""

    @staticmethod
    def _field_context(focused: Dict, elements: List[Dict]) -> str:
        fx1, fy1, fx2, fy2 = focused.get("bbox", [0, 0, 0, 0])
        label_types = {"label", "text", "headeritem", "header", "dataitem"}
        candidates  = [
            e for e in elements
            if e.get("window_role") in ("active", None)
            and e.get("type") in label_types
            and e.get("element_id") != focused.get("element_id")
        ]
        best, best_dist = None, float("inf")
        for lbl in candidates:
            lx1, ly1, lx2, ly2 = lbl.get("bbox", [0, 0, 0, 0])
            if lx2 <= fx2 and ly2 <= fy2:
                dist = ((lx2 - fx1) ** 2 + (ly1 - fy1) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best = lbl
        return (best.get("text") or "").strip() if best else ""

    @staticmethod
    def _clean_value(raw_val: str) -> str:
        """
        Strip noise from a matched value:
          - trailing [VERIFY ...] / [NOTE ...] bracket annotations
          - trailing ← arrow annotations
          - trailing parenthetical side-notes that follow a real value
            e.g. "63.18  (first month only)" → "63.18"
          - does NOT strip parens that ARE the value: "(none)", "(leave blank)"
        """
        # Remove trailing [...] blocks
        raw_val = re.sub(r"\s*\[.*", "", raw_val)
        # Remove trailing ← ... arrow annotations
        raw_val = re.sub(r"\s*←.*", "", raw_val)
        # Remove trailing (side note) only when preceded by a non-paren value
        raw_val = re.sub(r"\s+\((?!none|leave|n/a)[^)]+\)\s*$", "", raw_val, flags=re.IGNORECASE)
        return raw_val.strip()

    @classmethod
    def _match_value(cls, field_name: str, bg_elems: List[Dict]) -> str:
        fl = field_name.lower()

        def _search_line(line: str) -> str:
            line = line.strip()
            if not line:
                return ""
            for sep in (":", "—", "\t"):
                if sep in line:
                    parts = line.split(sep, 1)
                    if fl in parts[0].lower():
                        val = cls._clean_value(parts[1])
                        if not val:
                            return ""
                        # Reject placeholder/blank values — NOT "no", that's a real value
                        if val.lower().strip("()") in {
                            "none", "leave blank", "n/a",
                            "leave blank — liability only",
                            "leave blank — owned outright",
                        }:
                            return ""
                        return val
            return ""

        for elem in bg_elems:
            for raw in (elem.get("value") or "", elem.get("text") or ""):
                raw = raw.strip()
                if not raw:
                    continue
                for line in raw.splitlines():
                    result = _search_line(line)
                    if result:
                        return result
        return ""
