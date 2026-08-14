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

import ctypes
import ctypes.wintypes as wintypes
import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, NamedTuple, Optional

logger = logging.getLogger(__name__)

# BM_CLICK -- simulates a click on a native win32 "Button"-class control
# (push button, checkbox, or radio button) by delivering the message
# straight to that window's own procedure. No coordinates, no cursor.
_BM_CLICK = 0x00F5

# WM_SETTEXT direct-fill fast path -- see _keyboard_direct's own docstring
# for the full story. WM_GETTEXT/WM_GETTEXTLENGTH read a value back the
# same way ui_observer.py's WM_GETTEXT fallback already does.
_WM_SETTEXT       = 0x000C
_WM_GETTEXT       = 0x000D
_WM_GETTEXTLENGTH = 0x000E

# CB_SETCURSEL direct-select fast path -- see _combobox_direct's own
# docstring. CB_GETCOUNT/CB_GETLBTEXT(LEN) enumerate a combobox's real
# options directly, no dropdown ever opened; CB_GETCURSEL reads back
# which index actually ended up selected, for verification.
_CB_GETCOUNT     = 0x0146
_CB_GETLBTEXT    = 0x0148
_CB_GETLBTEXTLEN = 0x0149
_CB_SETCURSEL    = 0x014E
_CB_GETCURSEL    = 0x0147

# Module-level handle, mirroring the _uia optional-dependency pattern above
# -- gives tests a single name to monkeypatch (mod._user32) instead of
# patching the live, C-backed, process-wide ctypes.windll singleton.
_user32 = ctypes.windll.user32

# Shared with ui_observer.py's own WM_GETTEXT read-side fallback, so the
# write side here and the read side there always agree on exactly which
# native control classes are safe to talk to directly via raw messages,
# rather than maintaining two independent copies of this set.
try:
    from observers.ui_observer.ui_observer import _EDIT_CLASSES
except ImportError:
    _EDIT_CLASSES = {"Edit", "RichEditD2DPT", "RichEdit20W", "RICHEDIT50W",
                      "RICHEDIT60W", "RichEdit"}

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
    # We guard invalid coords ourselves (skip (0,0)/corner). Disable the
    # built-in fail-safe so a single bad prediction doesn't abort the whole run.
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI_AVAILABLE = True
except ImportError:
    _PYAUTOGUI_AVAILABLE = False

# ── optional dependency: uiautomation (for cursor-free clicks + caret placement) ──
try:
    import uiautomation as _uia
    _UIA_AVAILABLE = True
except ImportError:
    _UIA_AVAILABLE = False

# Found 2026-08-09, live, direct user report ("It closed the fucking
# Terminal holy shit") -- the process died silently mid-run, no traceback,
# no "interrupted by user" line, consistent with the TERMINAL window
# itself receiving an OS-level close signal. The log offered no direct
# proof of which specific keystroke was involved (it cuts off before
# whatever was attempted next), but a real, independently-serious gap was
# confirmed by reading this file: _press_key's hotkey branch
# (`pyautogui.hotkey(*key.split("+"))`) executes ANY "modifier+key" string
# completely unfiltered -- and this module's own docstring even lists
# `["alt+f4"] closes` as a recognized example. Every keystroke list this
# codebase's OWN code currently hardcodes is small and known-safe (tab,
# escape, space, ctrl+a) -- but nothing stops a future code path, an LLM
# response, or a bad transformer decode from producing something like
# "alt+f4" and having it executed verbatim, with real, severe consequences
# (closing whatever window happens to have focus at that instant -- which,
# per the user's own report, was the terminal running this very process,
# not the target form). A destructive-CLICK guard already exists for
# mouse actions (_find_destructive_button_at, agent.py) on exactly this
# reasoning; nothing equivalent existed for the keyboard. Allowlist,
# not a blocklist -- refusing by default is far more robust than trying to
# name every dangerous combo (the same lesson _find_destructive_button_at's
# own history already taught this project once).
_SAFE_HOTKEY_COMBOS = {"ctrl+a", "ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z", "shift+tab"}


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
        ghost_cursor:      bool  = False,
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

        # Opt-in, default OFF -- specifically so constructing an
        # ActionExecutor(dry_run=False) in a test (many already do exactly
        # that, with pyautogui mocked out) never spawns a real overlay
        # window. Only agent.py's live-run construction turns this on.
        self.ghost = None
        if ghost_cursor and not dry_run:
            try:
                try:
                    from components.agent.ghost_overlay import GhostOverlay
                except ImportError:
                    from agent.ghost_overlay import GhostOverlay
                self.ghost = GhostOverlay()
                self.ghost.start()
            except Exception as exc:
                logger.warning("GhostOverlay failed to start; continuing without it: %s", exc)
                self.ghost = None

    def execute(self, prediction: Dict[str, Any]) -> ExecutionResult:
        action_type = prediction.get("action_type", "no_op")
        ts          = datetime.now().isoformat()
        try:
            if action_type == "click":
                pos  = prediction.get("click_position", [0, 0])
                x, y = int(round(pos[0])), int(round(pos[1]))
                # Skip invalid coords — the transformer pointed at a padding/empty
                # element (zero bbox → (0,0)). Don't move the mouse to the corner.
                if x <= 1 and y <= 1:
                    logger.warning("CLICK skipped — invalid (0,0) target (bad element prediction)")
                    return ExecutionResult("click", None, 0, [], ts, self.dry_run, False, "invalid_coords")
                self._click(x, y)
                return ExecutionResult("click", (x, y), 0, [], ts, self.dry_run, True, "")

            elif action_type == "keyboard":
                key_count        = max(1, int(prediction.get("key_count", 1)))
                keystrokes       = list(prediction.get("keystrokes", []))
                text             = prediction.get("text", "")
                direct_fill_hwnd = prediction.get("direct_fill_hwnd")
                issued           = self._keyboard(key_count, keystrokes, text, direct_fill_hwnd)
                return ExecutionResult("keyboard", None, len(issued), issued, ts, self.dry_run, True, "")

            elif action_type == "combobox_select":
                # A genuinely new action type (unlike the keyboard/direct-fill
                # case) -- today's click-based combobox filling never sets
                # action_type == "keyboard" at all (it's two separate "click"
                # actions in agent.py), so there's no existing gate to widen
                # or risk silently missing.
                text = prediction.get("text", "")
                hwnd = prediction.get("combobox_hwnd")
                if self.dry_run:
                    logger.info("COMBOBOX  direct select would target hwnd=%s: %r  [DRY-RUN]",
                                hwnd, text[:60])
                    return ExecutionResult("combobox_select", None, 0, [], ts, self.dry_run, True, "")
                ok = bool(hwnd) and self._combobox_direct(hwnd, text)
                return ExecutionResult("combobox_select", None, 0, [], ts, self.dry_run, ok,
                                       "" if ok else "no_match_or_wrong_class")

            elif action_type == "scroll":
                pos       = prediction.get("click_position", [0, 0])
                x, y      = int(round(pos[0])), int(round(pos[1]))
                direction = prediction.get("direction", "down")
                clicks    = int(prediction.get("clicks", 3))
                self._scroll(x, y, direction, clicks)
                return ExecutionResult("scroll", (x, y), 0, [], ts, self.dry_run, True, "")

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
        if self.ghost:
            try:
                self.ghost.show_cursor(x, y)
            except Exception:
                pass
        time.sleep(self.pre_click_delay)
        if not self._try_uia_invoke(x, y) and not self._try_semantic_click(x, y):
            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.click(x, y)
        time.sleep(self.post_click_delay)

    def _try_uia_invoke(self, x: int, y: int) -> bool:
        """Attempt a real UIA action (Invoke/Toggle/Select) at (x, y)
        instead of a simulated mouse click -- the real OS cursor never
        moves for this path. Falls back to a coordinate click (handled by
        the caller) for anything that doesn't support one of these
        patterns -- not every control does, e.g. many comboboxes and
        custom/legacy controls, so this is a best-effort upgrade, not a
        guarantee. Uses the same generic ctrl.GetPattern(PatternId) shape
        ui_observer.py already relies on for reading ValuePattern/
        TogglePattern, rather than assuming a specific typed Control
        subclass's named getters exist on whatever ControlFromPoint hands
        back.

        KNOWN LIMITATION, found live 2026-08-10, not fixed here: while the
        ghost overlay (self.ghost) is running, ControlFromPoint(x, y) at a
        point the overlay currently covers resolves to the overlay's own
        window instead of the real target underneath -- confirmed directly
        that neither ShowWindow(SW_HIDE) nor SetWindowPos (moving the
        window off-screen) makes any difference to this, even fully
        synchronous, even after a full second's wait; only actually
        stopping (destroying) the overlay's window resolves it. That's far
        too expensive to do around every single click in a run -- unlike
        agent.py's _select_combobox_value_via_keyboard() (fixed this same
        day by stopping/restarting the overlay ONCE for the whole
        keyboard-fallback call, not per read), this method is called on
        EVERY click throughout an entire run, so the same fix here would
        add a real stop/restart delay per click. Left as a known,
        documented gap: this optimization silently falls back to the
        pyautogui path below whenever the overlay is active, which still
        works correctly, just without the UIA-invoke speedup."""
        if not _UIA_AVAILABLE:
            return False
        try:
            ctrl = _uia.ControlFromPoint(x, y)
            if ctrl is None:
                return False
            for pattern_id, method in (
                (_uia.PatternId.InvokePattern, "Invoke"),
                (_uia.PatternId.TogglePattern, "Toggle"),
                (_uia.PatternId.SelectionItemPattern, "Select"),
            ):
                pattern = ctrl.GetPattern(pattern_id)
                if pattern is not None:
                    getattr(pattern, method)()
                    logger.info("CLICK  @ (%d, %d) via UIA %s (real cursor untouched)", x, y, method)
                    return True
        except Exception as exc:
            logger.debug("UIA invoke at (%d, %d) failed, falling back to a real click: %s", x, y, exc)
        return False

    def _try_semantic_click(self, x: int, y: int, user32=None) -> bool:
        """Second-tier fallback, between _try_uia_invoke() and the raw
        pyautogui click below: sends a semantic Win32 control message
        (BM_CLICK) to the native window under (x, y) instead of a
        coordinate-precise simulated mouse click. Per explicit direction
        2026-08-11 ("use semantic, not precise") -- WM_LBUTTONDOWN/UP
        posted at an exact pixel was tested and rejected first: it never
        moved a text caret to the clicked position on a real EDIT
        control (EM_GETSEL stayed 0,0 no matter what), because pixel-
        accurate hit-testing inside a control's own client area isn't
        something a coordinate-blind message can do. BM_CLICK sidesteps
        that entirely by not needing a position at all -- it tells the
        control itself "you were clicked" and lets the control's own
        window procedure decide what that means. Real OS cursor is never
        touched by this path -- SendMessageW hands the message straight
        to the target window's own procedure; nothing in that call ever
        reaches SetCursorPos/mouse_event/SendInput. (GetCursorPos()
        before/after isn't itself proof of that, since a live human's
        hand on the mouse will move it anyway during the round trip --
        the real guarantee is architectural: BM_CLICK's documented
        delivery path has no step that touches the system cursor.)

        Only handles the win32 "Button" window class (push buttons,
        checkboxes, and radio buttons all share this class under
        wxWidgets on Windows) -- confirmed live 2026-08-11 against a
        real BS_AUTOCHECKBOX that SendMessageW(hwnd, BM_CLICK, 0, 0)
        correctly toggles BM_GETCHECK's state. (An earlier run of that
        same test appeared to fail -- turned out to be a bug in the test
        script itself, using plain BS_CHECKBOX instead of
        BS_AUTOCHECKBOX; plain BS_CHECKBOX requires the app to manage
        its own check state and was never going to toggle from BM_CLICK
        alone, regardless of how correct the message delivery was.) No
        explicit SetFocus() call is needed first -- BM_CLICK drives
        DefWindowProc's click handling directly, which doesn't depend on
        which window currently has OS keyboard focus.

        Everything else (Edit, ComboBox, custom/owner-drawn controls)
        falls through to the pyautogui fallback -- there's no semantic
        message for "place the caret at this pixel" or "open this
        specific dropdown item," so a coordinate is genuinely required
        there. Comboboxes in particular have a separate, deeper, already-
        documented issue (some never render their dropdown at all,
        _select_combobox_value_via_keyboard's whole reason for existing)
        that a different click mechanism wouldn't change.

        `user32` is injectable for tests (defaulting to the module-level
        `_user32`, itself monkeypatchable), same reasoning as
        ghost_overlay._make_click_through: ctypes.windll.user32 is a
        live, C-backed, process-wide object, unsafe to patch in place.
        """
        if user32 is None:
            user32 = _user32
        try:
            user32.WindowFromPoint.restype = wintypes.HWND
            hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
            if not hwnd:
                return False
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value != "Button":
                return False
            user32.SendMessageW(hwnd, _BM_CLICK, 0, 0)
            logger.info("CLICK  @ (%d, %d) via semantic BM_CLICK (real cursor untouched)", x, y)
            return True
        except Exception as exc:
            logger.debug("Semantic click at (%d, %d) failed, falling back to a real click: %s", x, y, exc)
            return False

    def _keyboard_direct(self, hwnd: int, text: str, user32=None) -> bool:
        """Fast path for filling a plain text field: set its value with one
        raw WM_SETTEXT message, no click, no simulated keystrokes, no
        select-all+paste. Falls back (returns False) on anything this
        can't confidently handle -- the caller (_keyboard) then runs the
        existing paste mechanism exactly as it always has.

        Live-tested 2026-08-14 against the real practice form (read-only/
        reversible tests, not assumed): WM_SETTEXT sent via
        ctypes.windll.user32.SendMessageW reliably sets an EditControl's
        value in one call, confirmed correct two independent ways -- a raw
        ctypes WM_GETTEXTLENGTH/WM_GETTEXT readback, and UIA's own
        ValuePattern readback -- that agree with each other. Two real
        traps were found and ruled out along the way, not assumed away:
        UIA's own ValuePattern.SetValue() fails outright (COM error) on
        this form's controls, so it isn't used here; and win32gui's own
        GetWindowText() wrapper was independently found to be buggy for
        reading this exact control back (disagreed with two OTHER correct
        readbacks), which is why verification here uses raw ctypes
        SendMessageW, not that wrapper. Also live-tested against a
        ComboBoxControl and found to be a silent no-op (value unchanged,
        no error) -- comboboxes are excluded via the _EDIT_CLASSES class
        check below and keep their existing click-based handling entirely.

        Same injectable-`user32` shape as _try_semantic_click, for the
        same reason: ctypes.windll.user32 is a live, C-backed, process-
        wide object, unsafe to patch in place in a test.
        """
        if user32 is None:
            user32 = _user32
        try:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value not in _EDIT_CLASSES:
                return False
            user32.SendMessageW(hwnd, _WM_SETTEXT, 0, text)
            length = user32.SendMessageW(hwnd, _WM_GETTEXTLENGTH, 0, 0)
            readback = ctypes.create_unicode_buffer(length + 1)
            user32.SendMessageW(hwnd, _WM_GETTEXT, length + 1, readback)
            if readback.value != text:
                logger.debug("Direct fill readback mismatch (%r != %r) -- falling back to paste.",
                             readback.value[:40], text[:40])
                return False
            logger.info("KEYBOARD  direct fill via WM_SETTEXT (no click, no paste): %r", text[:60])
            return True
        except Exception as exc:
            logger.debug("Direct fill via WM_SETTEXT failed, falling back to paste: %s", exc)
            return False

    def _combobox_direct(self, hwnd: int, text: str, user32=None) -> bool:
        """Fast path for selecting a combobox option: one CB_SETCURSEL
        message, no click, no opening the dropdown at all. Falls back
        (returns False) on anything this can't confidently handle -- the
        caller then runs the existing click-based open+select mechanism
        exactly as it always has.

        Live-tested 2026-08-14 against the real practice form (read-only/
        reversible test): the combobox's real options were read directly
        via CB_GETCOUNT/CB_GETLBTEXT (no dropdown ever opened), then
        CB_SETCURSEL was sent for one of them -- confirmed correct via
        both CB_GETCURSEL's own readback and UIA's ValuePattern, which
        agreed with each other. This is the direct-selection counterpart
        to _keyboard_direct's WM_SETTEXT (text fields) and the existing
        BM_SETCHECK mechanism (checkboxes) -- the third and last of the
        three control types this form uses.

        Same injectable-`user32` shape as _try_semantic_click/
        _keyboard_direct, for the same reason: ctypes.windll.user32 is a
        live, C-backed, process-wide object, unsafe to patch in place in
        a test.
        """
        if user32 is None:
            user32 = _user32
        try:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value != "ComboBox":
                return False
            count = user32.SendMessageW(hwnd, _CB_GETCOUNT, 0, 0)
            target_idx = None
            wanted = text.strip().lower()
            for i in range(count):
                length = user32.SendMessageW(hwnd, _CB_GETLBTEXTLEN, i, 0)
                opt_buf = ctypes.create_unicode_buffer(length + 1)
                user32.SendMessageW(hwnd, _CB_GETLBTEXT, i, opt_buf)
                if opt_buf.value.strip().lower() == wanted:
                    target_idx = i
                    break
            if target_idx is None:
                logger.debug("Direct combobox select: %r not found among %d real option(s) "
                             "-- falling back to click-based open+select.", text[:40], count)
                return False
            user32.SendMessageW(hwnd, _CB_SETCURSEL, target_idx, 0)
            actual_idx = user32.SendMessageW(hwnd, _CB_GETCURSEL, 0, 0)
            if actual_idx != target_idx:
                logger.debug("Direct combobox select readback mismatch (index %s != %s) "
                             "-- falling back to click-based open+select.", actual_idx, target_idx)
                return False
            logger.info("COMBOBOX  direct select via CB_SETCURSEL (no click, no dropdown): %r", text[:60])
            return True
        except Exception as exc:
            logger.debug("Direct combobox select via CB_SETCURSEL failed, falling back: %s", exc)
            return False

    def _show_ghost_caret(self) -> None:
        if not self.ghost or not _UIA_AVAILABLE:
            return
        try:
            fc = _uia.GetFocusedControl()
            rect = fc.BoundingRectangle if fc else None
            if rect and rect.right > rect.left and rect.bottom > rect.top:
                self.ghost.show_caret(rect.left + 4, rect.top + 3, max(10, (rect.bottom - rect.top) - 6))
        except Exception as exc:
            logger.debug("GhostOverlay: couldn't place caret at focused control: %s", exc)

    def _keyboard(self, key_count: int, keystrokes: List[str], text: str = "",
                  direct_fill_hwnd: Optional[int] = None) -> List[str]:
        if not self.dry_run:
            self._show_ghost_caret()
        if text:
            if self.dry_run:
                if direct_fill_hwnd:
                    logger.info("KEYBOARD  direct fill (WM_SETTEXT) would target hwnd=%s: %r  [DRY-RUN]",
                                direct_fill_hwnd, text[:60])
                else:
                    logger.info("KEYBOARD  paste=%r  [DRY-RUN]", text[:60])
                return list(text)

            if direct_fill_hwnd and self._keyboard_direct(direct_fill_hwnd, text):
                return list(text)

            logger.info("KEYBOARD  paste=%r", text[:60])
            try:
                import pyperclip
                pyperclip.copy(text)
                time.sleep(0.15)
                # Select-all before paste so typing is IDEMPOTENT: if a step is
                # retried (e.g. validator false no_change), the paste OVERWRITES
                # the field instead of appending (was producing '9'+'9'='99').
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.05)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(self.post_click_delay)
            except Exception:
                pyautogui.hotkey("ctrl", "a")
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

    def _scroll(self, x: int, y: int, direction: str, clicks: int) -> None:
        amount = -clicks if direction == "down" else clicks
        # Guard against (0,0)/corner coords — moving there trips pyautogui's
        # fail-safe and aborts the run. Scroll at the current cursor position
        # (or screen center) instead of an invalid predicted point.
        if x <= 1 and y <= 1:
            try:
                x, y = pyautogui.position()
            except Exception:
                w, h = pyautogui.size()
                x, y = w // 2, h // 2
        logger.info("SCROLL  @ (%d, %d)  dir=%s  clicks=%d%s",
                    x, y, direction, clicks, "  [DRY-RUN]" if self.dry_run else "")
        if self.dry_run:
            return
        try:
            pyautogui.scroll(amount, x=x, y=y)
        except pyautogui.FailSafeException:
            pyautogui.scroll(amount)  # scroll at current pos, no move
        time.sleep(self.post_click_delay)

    def _press_key(self, key: str) -> None:
        if key.startswith("Key."):
            pyautogui.press(key.split("Key.", 1)[1])
        elif "+" in key:
            # Allowlist, not a blocklist -- see _SAFE_HOTKEY_COMBOS' own
            # comment for why. Anything not explicitly known-safe is
            # refused outright rather than executed on trust.
            if key.lower() not in _SAFE_HOTKEY_COMBOS:
                logger.warning(
                    "KEYBOARD  hotkey %r refused — not in the known-safe allowlist %s.",
                    key, sorted(_SAFE_HOTKEY_COMBOS))
                return
            pyautogui.hotkey(*key.split("+"))
        elif len(key) == 1:
            pyautogui.typewrite(key, interval=0.0)
        else:
            pyautogui.press(key)


# ══════════════════════════════════════════════════════════════════════════════
#  _snap_to_element
# ══════════════════════════════════════════════════════════════════════════════

_CLICKABLE_TYPES = {
    "editcontrol", "comboboxcontrol", "checkboxcontrol",
    "buttoncontrol", "listitemcontrol",
    # tabitemcontrol excluded — tabs clicked via LLM label resolution only
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
        # Skip if the pointed element is a large text blob (e.g. Notepad text area) —
        # in that case field-name matching (Option 2) is more accurate.
        _BLOB_THRESHOLD = 200
        if 0 <= source_elem_idx < len(elements):
            pointed = elements[source_elem_idx]
            if pointed.get("window_role") == "background":
                raw = (pointed.get("value") or pointed.get("text") or "").strip()
                if len(raw) < _BLOB_THRESHOLD:
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
        # Fallback: use the focused element's own label/text if nearby-label search failed
        if not field_name and focused:
            field_name = (focused.get("label") or focused.get("text") or "").strip()

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
