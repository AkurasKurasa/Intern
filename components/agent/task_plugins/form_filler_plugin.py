"""
components/agent/task_plugins/form_filler_plugin.py
=====================================================
FormFillerPlugin — encapsulates all form-filling-specific automation logic.

Extracted from LLMAgent.run() so the core agent loop is task-agnostic.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_plugin import TaskPlugin
from ..field_planner import (
    DivergenceStatus, PlannedField, Resolution,
    check_divergence, is_fast_replayable, plan_visible_fields,
)

logger = logging.getLogger(__name__)


# ── Shared record parser ─────────────────────────────────────────────────────
# Canonical parser lives in data_sources.notepad_source. Re-export to keep
# existing call sites in this module working.
from data_sources.notepad_source import _parse_records  # noqa: F401


class FormFillerPlugin(TaskPlugin):
    """
    Handles all form-filling automation: auto-fill, auto-skip, tab navigation,
    Notepad data reading, combobox correction, checkbox handling, and more.

    Parameters
    ----------
    executor     : ActionExecutor instance from agent.
    data_source  : DataSource (typically NotepadDataSource).
    visual_reader: VisualDataReader for VLM-based tab scanning (optional).
    record_num   : Which record number to fill (1-based).
    step_delay   : Seconds between steps (mirrors agent step_delay).
    observe_fn   : Callable returning a fresh state snapshot (set by agent).
    """

    def __init__(
        self,
        executor,                        # ActionExecutor
        data_source,                     # DataSource
        visual_reader=None,              # VisualDataReader (optional)
        record_num:  int   = 1,
        step_delay:  float = 1.2,
        observe_fn=None,                 # () -> state dict; set by LLMAgent after init
        form_title_fragment: str = "",   # fragment of form window title for focus check
        plan_replay: bool = False,       # opt-in: batch-plan a tab, replay it mechanically
        focus_fn=None,                   # (label, expected_bbox) -> bool; set by LLMAgent after init
        settle_wait_fn=None,             # (max_wait) -> None; set by LLMAgent after init
        viewport_bottom_fn=None,         # (state) -> float; set by LLMAgent after init
    ) -> None:
        self._executor            = executor
        self._data_source         = data_source
        self._visual_reader       = visual_reader
        self._record_num          = record_num
        self.step_delay           = step_delay
        self._observe_fn          = observe_fn
        self._form_title_fragment = form_title_fragment.lower() if form_title_fragment else ""

        # ── Plan-then-replay mode (off by default) ──────────────────────────
        # See components/agent/field_planner.py for the "why". _focus_fn and
        # _settle_wait_fn are wired onto this instance by LLMAgent.__init__
        # (the same hasattr-gated pattern already used for _executor etc.);
        # when left unwired (e.g. this plugin is used standalone/in tests),
        # _focus_by_label and a plain time.sleep are used instead.
        self._plan_replay:    bool                = plan_replay
        self._focus_fn                            = focus_fn
        self._settle_wait_fn                      = settle_wait_fn
        self._viewport_bottom_fn                  = viewport_bottom_fn
        self._field_plan:     List[PlannedField]  = []
        self._plan_idx:       int                 = 0

        # ── Mirrors of agent instance state that was previously on LLMAgent ──
        self._filled_this_tab:    Set[str] = set()
        self._confirmed_blank_fields: Set[str] = set()
        self._checked_fields:     Set[str] = set()
        self._current_tab_idx:    int      = 0
        self._tc_advance_verified: bool    = False

        # Visual / data caches (mirrors _visual_cache / _cached_record on agent)
        self._visual_cache:   Dict[str, str] = {}
        self._cached_record:  Dict[str, str] = {}
        self._source_window:  str            = (
            getattr(data_source, "_title_fragment", "") if data_source else ""
        )
        # Re-scan-on-miss state: when an _auto_fill/lookup misses, the plugin
        # asks the VLM to scroll Notepad and look again, capped per tab to
        # avoid burning rate limits on hopeless misses.
        self._rescan_attempts_this_tab: int = 0
        self._RESCAN_MAX_PER_TAB:       int = 4

        # ── Per-run loop state ────────────────────────────────────────────────
        self._tab_just_switched: bool  = True   # True on first step
        self._last_auto_step:    int   = -1
        self._tab_scroll_count:  int   = 0
        self._steps_on_tab:      int   = 0
        self._no_change_streak:  int   = 0
        self._last_focused_id:   Optional[str] = None
        self._pane_escape_last_field: str = ""
        self._pane_escape_streak:     int = 0
        self._record_cache_loaded: bool   = False
        # fill-retry tracking: counts paste attempts per field name.
        # After _FILL_RETRY_LIMIT failures the field is added to
        # _confirmed_blank_fields so the agent moves on rather than looping.
        self._fill_retries:   Dict[str, int] = {}
        self._FILL_RETRY_LIMIT: int          = 3

        # Constants (could be overridden if needed)
        self._NO_CHANGE_LIMIT:   int   = 4
        self._TAB_STEP_LIMIT:    int   = 120
        self._DROUGHT_LIMIT:     int   = 10
        self._MAX_TAB_SCROLLS:   int   = 6
        self._TAB_TIMEOUT_SECS:  float = 180.0  # wall-clock cap, only fires with no progress
        self._tab_start_time:    float = time.time()

    # ── Public API ────────────────────────────────────────────────────────────

    def handle_step(self, state: Dict[str, Any], step_idx: int) -> Tuple[bool, bool]:
        """
        Run all form-filling auto-handler blocks for one agent step.

        Returns (handled, should_continue):
          (True, True)   -> plugin handled this step; loop should continue
          (True, False)  -> plugin handled and signals task complete (submit done)
          (False, False) -> not handled; agent falls through to transformer/LLM
        """
        self._steps_on_tab += 1

        # ── 0. Focus check — bail if a different window grabbed foreground ───
        if self._form_title_fragment:
            try:
                import win32gui as _wg_fc
                _fg_title = _wg_fc.GetWindowText(_wg_fc.GetForegroundWindow()).lower()
                if self._form_title_fragment not in _fg_title:
                    logger.warning(
                        "Focus check: foreground is %r, not the form — pausing 1 s.",
                        _fg_title[:60],
                    )
                    time.sleep(1.0)
                    return (True, True)
            except Exception:
                pass

        # Load record cache on first step
        if not self._record_cache_loaded:
            self._refresh_record_cache(state)
            self._record_cache_loaded = True

        # ── 1a. Tab-just-switched: scroll to top, focus first empty field ────
        if self._tab_just_switched:
            self._tab_just_switched           = False
            self._steps_on_tab                = 0
            self._tab_scroll_count            = 0
            self._no_change_streak            = 0
            self._last_auto_step              = step_idx
            self._tab_start_time              = time.time()
            self._pane_escape_last_field      = ""
            self._pane_escape_streak          = 0
            self._filled_this_tab.clear()
            self._confirmed_blank_fields.clear()
            self._tc_advance_verified         = False
            self._rescan_attempts_this_tab    = 0
            self._fill_retries.clear()
            self._scan_tab_visual(state)
            # Re-observe before scrolling — old state has previous tab's element
            # positions; scroll target (cx,cy) would be wrong without refresh.
            if self._observe_fn:
                state = self._observe_fn()
            self._scroll_form_to_top(state)
            time.sleep(0.6)
            if self._observe_fn:
                state = self._observe_fn()
            self._focus_first_empty_field(state)
            time.sleep(self.step_delay)
            return (True, True)

        # ── 1b. Stuck guard ──────────────────────────────────────────────────
        _tab_elapsed   = time.time() - self._tab_start_time
        _drought_steps = step_idx - self._last_auto_step
        # Wall-clock timeout only fires when ALSO no productive action for
        # _DROUGHT_LIMIT steps — prevents firing while fields are actively filling.
        _timed_out = (_tab_elapsed >= self._TAB_TIMEOUT_SECS
                      and _drought_steps >= self._DROUGHT_LIMIT)
        _stuck = (self._no_change_streak >= self._NO_CHANGE_LIMIT
                  or self._steps_on_tab   >= self._TAB_STEP_LIMIT
                  or _timed_out)
        if _stuck:
            if _timed_out:
                logger.warning("Stuck guard: tab timed out (%.0f s, no progress for %d steps) — forcing advance.",
                               _tab_elapsed, _drought_steps)
            elif self._steps_on_tab >= self._TAB_STEP_LIMIT:
                logger.info("Stuck guard: %d steps on tab — forcing advance.", self._steps_on_tab)
            elif self._no_change_streak >= self._NO_CHANGE_LIMIT:
                _foc_id = state.get("focused_element_id")
                _foc_el = next((e for e in state.get("elements", [])
                                if e.get("element_id") == _foc_id), None)
                _foc_nm = ((_foc_el.get("label") or _foc_el.get("text") or "?")
                           if _foc_el else "?")
                _foc_sec = self._detect_section(state, _foc_el) if _foc_el else ""
                _foc_key = f"{_foc_sec} {_foc_nm}" if _foc_sec else _foc_nm
                logger.info("Stuck guard: no_change x%d on %r — Tab past field.",
                            self._no_change_streak, _foc_nm)
                # Mark field as handled so _tc_has_pending doesn't re-flag it
                self._confirmed_blank_fields.add(_foc_nm.lower())
                self._filled_this_tab.add(_foc_key)
                self._executor.execute({"action_type": "keyboard",
                                        "key_count": 1, "keystrokes": ["tab"]})
                self._no_change_streak = 0
                self._last_auto_step   = step_idx
                time.sleep(self.step_delay * 0.5)
                return (True, True)
            if self._try_advance_tab(state):
                self._no_change_streak  = 0
                self._tab_just_switched = True
                self._tab_scroll_count  = 0
                self._last_auto_step    = step_idx
                self._refresh_record_cache(state)
                time.sleep(self.step_delay)
                return (True, True)
            else:
                logger.warning("Stuck guard: no next tab found — resetting streak.")
                self._no_change_streak = 0
                self._steps_on_tab     = 0

        # ── 1c. Universal tab-complete check ─────────────────────────────────
        if self._filled_this_tab and not self._tc_has_pending(state):
            if not self._tc_advance_verified:
                _tcav_state = state
                _tcav_found = False
                self._scroll_form_to_top(_tcav_state)
                time.sleep(0.4)
                if self._observe_fn:
                    _tcav_state = self._observe_fn()
                _tcav_clean_streak = 0
                for _tcav_i in range(self._MAX_TAB_SCROLLS):
                    if self._tc_has_pending(_tcav_state):
                        _pend_names = [
                            (e.get("label") or e.get("text") or "?")
                            for e in _tcav_state.get("elements", [])
                            if e.get("type") in ("editcontrol", "comboboxcontrol")
                            and e.get("window_role") != "background"
                            and e.get("enabled", True)
                            and self._lookup_field(
                                (e.get("label") or e.get("text") or "").strip(),
                                section=self._detect_section(_tcav_state, e))
                            and (e.get("label") or e.get("text") or "").strip().lower()
                                not in {s.lower() for s in self._filled_this_tab}
                        ][:4]
                        logger.info("Tab-complete scan (pass %d): pending fields found: %s",
                                    _tcav_i, _pend_names)
                        _tcav_found = True
                        _tcav_clean_streak = 0
                        self._tc_advance_verified = False
                        self._focus_first_empty_field(_tcav_state)
                        time.sleep(self.step_delay)
                        break
                    _tcav_clean_streak += 1
                    if _tcav_clean_streak >= 2:
                        # Two consecutive scroll positions all clear — bottom reached
                        break
                    self._scroll_form_down(_tcav_state)
                    time.sleep(0.25)
                    if self._observe_fn:
                        _tcav_state = self._observe_fn()
                if _tcav_found:
                    return (True, True)
                self._tc_advance_verified = True

            # Full scan verified — advance tab or submit
            if self._try_advance_tab(state):
                logger.info("Tab complete: all fields done — advancing to next tab.")
                self._no_change_streak    = 0
                self._tab_just_switched   = True
                self._tab_scroll_count    = 0
                self._last_auto_step      = step_idx
                self._tc_advance_verified = False
                self._filled_this_tab.clear()
                self._confirmed_blank_fields.clear()
                self._refresh_record_cache(state)
                time.sleep(self.step_delay)
                return (True, True)
            else:
                # Last tab — click Submit via UIA
                _submitted = False
                try:
                    import uiautomation as _uia_sub
                    for _btn_name in ("Submit & New", "Submit"):
                        _btn = _uia_sub.ButtonControl(Name=_btn_name, searchDepth=8)
                        if _btn.Exists(maxSearchSeconds=0.5):
                            logger.info("Tab complete: last tab — UIA click Submit %r", _btn_name)
                            _btn.Click()
                            _submitted = True
                            break
                except Exception as _sub_exc:
                    logger.warning("UIA Submit click failed: %s", _sub_exc)
                if not _submitted:
                    _submit_el = next(
                        (e for e in state.get("elements", [])
                         if e.get("type") == "buttoncontrol"
                         and "submit" in (e.get("label") or e.get("text") or "").lower()
                         and e.get("window_role") != "background"
                         and e.get("bbox")),
                        None
                    )
                    if _submit_el:
                        x1, y1, x2, y2 = _submit_el["bbox"]
                        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                        logger.info("Tab complete: last tab — coord click Submit @ (%.0f, %.0f)", cx, cy)
                        self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
                        _submitted = True
                if _submitted:
                    return (True, False)  # signal loop break

        # ── 1b-pane. Pane-focus escape ───────────────────────────────────────
        _focused_id  = state.get("focused_element_id")
        _focused_el  = next((e for e in state.get("elements", [])
                             if e.get("element_id") == _focused_id), None)
        if _focused_el and _focused_el.get("type") == "panecontrol":
            _pane_label = (_focused_el.get("label") or _focused_el.get("text") or "?")[:40]
            _pane_y     = _focused_el["bbox"][1] if _focused_el.get("bbox") else 0
            logger.info("Focus on pane %r — clicking first empty field to escape.", _pane_label)
            _pane_filled_lower = {s.lower() for s in self._filled_this_tab}

            def _pane_not_handled(_pe):
                _fn  = (_pe.get("label") or _pe.get("text") or "").strip()
                _sec = self._detect_section(state, _pe)
                _fk  = f"{_sec} {_fn}".lower() if _sec else _fn.lower()
                return _fk not in _pane_filled_lower and _fn.lower() not in _pane_filled_lower

            _pane_candidates = sorted(
                [_pe for _pe in state.get("elements", [])
                 if (_pe.get("window_role") != "background"
                     and _pe.get("type") == "editcontrol"
                     and _pe.get("bbox") and _pe.get("enabled", True)
                     and _pe["bbox"][1] >= max(100, _pane_y)
                     and _pane_not_handled(_pe))],
                key=lambda e: (e["bbox"][1], e["bbox"][0])
            )
            _pane_next_el   = _pane_candidates[0] if _pane_candidates else None
            _pane_next_name = (
                (_pane_next_el.get("label") or _pane_next_el.get("text") or "").strip()
                if _pane_next_el else ""
            )
            # Increment streak whenever stuck on same (or no) candidate.
            if _pane_next_name and _pane_next_name != self._pane_escape_last_field:
                self._pane_escape_streak     = 1
                self._pane_escape_last_field = _pane_next_name
            else:
                # Empty candidates OR same field repeated — still stuck.
                self._pane_escape_streak += 1

            # After enough failed attempts, try advancing tab.
            if (self._pane_escape_streak >= 3
                    or self._tab_scroll_count >= self._MAX_TAB_SCROLLS):
                logger.warning(
                    "Pane-escape: stuck for %d tries (tab_scroll=%d) — "
                    "attempting tab advance.",
                    self._pane_escape_streak, self._tab_scroll_count,
                )
                self._pane_escape_streak = 0
                if self._try_advance_tab(state):
                    self._no_change_streak  = 0
                    self._tab_just_switched = True
                    self._tab_scroll_count  = 0
                    self._last_auto_step    = step_idx
                    self._refresh_record_cache(state)
                    time.sleep(self.step_delay)
                    return (True, True)
                # Last tab — fall through to submit logic via tc_advance_verified
                logger.info("Pane-escape: no next tab — letting submit path handle it.")
                self._tc_advance_verified = False
                return (True, True)

            if self._focus_first_empty_field(state, min_y=_pane_y):
                time.sleep(self.step_delay * 0.5)
                return (True, True)
            # No fillable field visible — scroll down, track scroll count.
            logger.info("Pane-escape: no reachable field — scrolling down.")
            self._scroll_form_down(state)
            self._tab_scroll_count += 1
            time.sleep(self.step_delay * 0.5)
            return (True, True)

        # ── 1c-btn. Button-focus escape ──────────────────────────────────────
        if (_focused_el and _focused_el.get("type") == "buttoncontrol"
                and self._tc_has_pending(state)):
            _btn_lbl = (_focused_el.get("label") or _focused_el.get("text") or "").strip()
            logger.info("Button-focus escape: focus on button %r but fields still pending "
                        "— seeking first unfilled field.", _btn_lbl)
            if self._focus_first_empty_field(state):
                self._no_change_streak = 0
                time.sleep(self.step_delay * 0.5)
                return (True, True)
            if self._tab_scroll_count < self._MAX_TAB_SCROLLS:
                _foc_id_pe = state.get("focused_element_id")
                _foc_el_pe = next((e for e in state.get("elements", [])
                                   if e.get("element_id") == _foc_id_pe), None)
                _pe_scroll_y = float(_foc_el_pe["bbox"][1]) if (
                    _foc_el_pe and _foc_el_pe.get("bbox")) else 0.0
                if self._scroll_form_down(state):
                    self._tab_scroll_count += 1
                    self._last_auto_step    = step_idx
                    time.sleep(self.step_delay * 0.5)
                    if self._observe_fn:
                        state = self._observe_fn()
                    focused = (self._focus_first_empty_field(state, after_scroll=True,
                                                             min_y=_pe_scroll_y)
                               or self._focus_first_empty_field(state))
                    if focused:
                        self._no_change_streak = 0
                    return (True, True)
            # Scrolls exhausted — no reachable pending field; advance tab
            logger.info("Button-focus escape: scrolls exhausted — advancing tab.")
            if self._try_advance_tab(state):
                self._tab_just_switched = True
                self._tab_scroll_count  = 0
                self._last_auto_step    = step_idx
                self._filled_this_tab.clear()
                self._confirmed_blank_fields.clear()
                time.sleep(self.step_delay)
                return (True, True)

        # ── 1d. Plan-replay (opt-in, off by default) ─────────────────────────
        # Consume one entry from a pre-built field plan mechanically, instead
        # of falling through to the full auto-skip/auto-fill/auto-check
        # cascade below. See components/agent/field_planner.py for the "why".
        # With self._plan_replay False (the default) this block's guard is
        # always false and every branch below is reached exactly as before --
        # this mode is purely additive.
        if self._plan_replay:
            if self._plan_idx >= len(self._field_plan):
                # First call after a tab switch (plan starts empty) or the
                # previous plan just got fully consumed -- (re)build it. Also
                # catches fields that only appear after another field's value
                # changes, without needing a dedicated detector.
                _vb = self._viewport_bottom_fn(state) if self._viewport_bottom_fn else float("inf")
                self._field_plan = plan_visible_fields(
                    state, _vb, lookup_fn=self._lookup_field, peek_fn=self._peek_notepad,
                    section_fn=self._detect_section,
                )
                self._plan_idx = 0

        if self._plan_replay and self._plan_idx < len(self._field_plan):
            planned = self._field_plan[self._plan_idx]
            status  = check_divergence(planned, state)

            if status == DivergenceStatus.DIVERGED:
                # Not what we planned for anymore (vanished, or already holds
                # something unexpected) -- consume the stale entry and let the
                # existing cascade below handle whatever's actually focused.
                self._plan_idx += 1
            elif status == DivergenceStatus.SATISFIED:
                self._plan_idx += 1
                self._executor.execute({"action_type": "keyboard",
                                        "key_count": 1, "keystrokes": ["tab"]})
                self._no_change_streak = 0
                time.sleep(self.step_delay * 0.25)
                return (True, True)
            elif planned.resolution == Resolution.NEEDS_LLM or not is_fast_replayable(planned):
                # Genuinely ambiguous, or a combobox/checkbox (still on the
                # existing reactive branches in v1) -- best-effort focus so
                # the reactive path below at least starts from the right
                # target, then let it decide. _plan_idx untouched: re-checked
                # next call, only advances once this field stops being pending.
                (self._focus_fn or self._focus_by_label)(planned.label, planned.bbox)
                return (False, False)
            else:
                # LOOKUP_HIT / LOOKUP_BLANK editcontrol -- mechanical fill,
                # no re-derivation of the cascade below at all.
                focused = (self._focus_fn or self._focus_by_label)(planned.label, planned.bbox)
                if focused:
                    if planned.resolution == Resolution.LOOKUP_BLANK:
                        self._executor.execute({"action_type": "keyboard",
                                                "key_count": 1, "keystrokes": ["tab"]})
                    else:
                        _ok = self._paste_and_verify(planned.label, planned.expected_value,
                                                     planned.needs_clear)
                        self._executor.execute({"action_type": "keyboard",
                                                "key_count": 1, "keystrokes": ["tab"]})
                        if _ok:
                            self._filled_this_tab.add(planned.label)
                            self._fill_retries.pop(planned.label, None)
                    self._plan_idx += 1
                    self._no_change_streak = 0
                    self._steps_on_tab     = 0
                    self._last_auto_step   = step_idx
                    (self._settle_wait_fn or (lambda w: time.sleep(w)))(self.step_delay)
                    return (True, True)
                # Focus failed entirely (element genuinely not reachable via
                # UIA by name right now) -- treat as diverged, don't guess at
                # a stale coordinate. Let the cascade below have this step.
                self._plan_idx += 1

        # ── 2. Auto-skip ─────────────────────────────────────────────────────
        if self._auto_skip(state):
            logger.info("Auto-skip: focused field already has correct value — Tab.")
            self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
            self._no_change_streak = 0
            self._steps_on_tab     = 0
            time.sleep(self.step_delay)
            return (True, True)

        # ── 2a. Auto-fill ────────────────────────────────────────────────────
        auto_text = self._auto_fill(state)

        # 2a-peek: empty field with no cached value → peek Notepad
        if not auto_text:
            _fid_p = state.get("focused_element_id")
            _fe_p  = next((e for e in state.get("elements", [])
                           if e.get("element_id") == _fid_p), None)
            if (_fe_p and _fe_p.get("type") in ("editcontrol", "comboboxcontrol")
                    and not (_fe_p.get("value") or "").strip()):
                _fn_p      = (_fe_p.get("label") or _fe_p.get("text") or "").strip()
                _sec_p     = self._detect_section(state, _fe_p)
                _fk_p      = f"{_sec_p} {_fn_p}" if _sec_p else _fn_p
                _fn_lower_p = _fn_p.lower()
                if (_fn_p
                        and not self._lookup_field(_fn_p, section=_sec_p)
                        and _fn_lower_p not in {s.lower() for s in self._confirmed_blank_fields}
                        and _fn_lower_p not in {s.lower() for s in self._filled_this_tab}):
                    logger.info("Auto-fill: '%s' not in cache — peeking Notepad.", _fn_p)
                    self._peek_notepad(state, _fn_p)
                    auto_text = self._auto_fill(state)
                    if not auto_text:
                        if _fe_p.get("type") == "comboboxcontrol":
                            # Value now in cache — fall through to combobox handler below
                            logger.info("Auto-fill: '%s' is combobox — deferring to combobox handler.", _fn_p)
                        else:
                            logger.info("Auto-fill: '%s' not found after peek — treating as blank, Tab.",
                                        _fn_p)
                            self._confirmed_blank_fields.add(_fn_lower_p)
                            self._filled_this_tab.add(_fk_p)
                            self._executor.execute({"action_type": "keyboard",
                                                    "key_count": 1, "keystrokes": ["tab"]})
                            self._no_change_streak = 0
                            self._last_auto_step   = step_idx
                            time.sleep(self.step_delay)
                            return (True, True)

        # 2a-dup: duplicate UIA label — field empty but label already in _filled_this_tab
        if not auto_text:
            _fid_dup = state.get("focused_element_id")
            _fe_dup  = next((e for e in state.get("elements", [])
                             if e.get("element_id") == _fid_dup), None)
            if (_fe_dup and _fe_dup.get("type") in ("editcontrol", "comboboxcontrol")
                    and not (_fe_dup.get("value") or "").strip()):
                _fn_dup     = (_fe_dup.get("label") or _fe_dup.get("text") or "").strip()
                _fn_dup_low = _fn_dup.lower()
                _sec_dup    = self._detect_section(state, _fe_dup)
                _fk_dup     = f"{_sec_dup} {_fn_dup}" if _sec_dup else _fn_dup
                if (_fn_dup
                        and _fk_dup in self._filled_this_tab
                        and _fn_dup_low not in {s.lower() for s in self._confirmed_blank_fields}):
                    logger.info("Dup-label: '%s' already filled this tab — peeking next field.", _fn_dup)
                    _next = self._peek_next_field_after(state, _fn_dup)
                    if _next:
                        _nk, _nv = _next
                        self._cached_record[_nk] = _nv
                        self._visual_cache[_nk]  = _nv
                        logger.info("Dup-label fill: actual field=%r  value=%r  (UIA label=%r)",
                                    _nk, _nv, _fn_dup)
                        _dup_ok = self._paste_and_verify(_nk, _nv, needs_clear=True)
                        self._executor.execute({"action_type": "keyboard",
                                                "key_count": 1, "keystrokes": ["tab"]})
                        if _dup_ok:
                            self._filled_this_tab.add(_nk)
                            self._fill_retries.pop(_nk, None)
                        else:
                            if self._fill_retries.get(_nk, 0) >= self._FILL_RETRY_LIMIT:
                                self._confirmed_blank_fields.add(_nk.lower())
                                self._filled_this_tab.add(_nk)
                        self._no_change_streak = 0
                        self._steps_on_tab     = 0
                        self._last_auto_step   = step_idx
                        time.sleep(self.step_delay)
                        return (True, True)
                    else:
                        logger.info("Dup-label: no next field found — treating as blank, Tab.")
                        self._confirmed_blank_fields.add(_fn_dup_low + "#dup")
                        self._executor.execute({"action_type": "keyboard",
                                                "key_count": 1, "keystrokes": ["tab"]})
                        self._no_change_streak = 0
                        self._last_auto_step   = step_idx
                        time.sleep(self.step_delay)
                        return (True, True)

        if auto_text:
            field_name_log, text_val, needs_clear = auto_text
            self._peek_notepad(state, field_name_log)
            logger.info("Auto-fill%s: '%s' → %r",
                        " (overwrite)" if needs_clear else "",
                        field_name_log, text_val[:40])
            _fill_ok = self._paste_and_verify(field_name_log, text_val, needs_clear)
            self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
            logger.info("Auto-Tab after auto-fill (verified=%s).", _fill_ok)
            self._no_change_streak = 0
            self._steps_on_tab     = 0
            self._last_auto_step   = step_idx
            if _fill_ok:
                self._filled_this_tab.add(field_name_log)
                self._fill_retries.pop(field_name_log, None)
            else:
                # Value never landed — don't mark filled so tc_has_pending
                # keeps the field in scope for retry.  But if retry limit hit,
                # give up and treat as blank so the agent can move forward.
                if self._fill_retries.get(field_name_log, 0) >= self._FILL_RETRY_LIMIT:
                    self._confirmed_blank_fields.add(field_name_log.lower())
                    self._filled_this_tab.add(field_name_log)
                    logger.warning("Auto-fill: '%s' surrendered after %d attempts.",
                                   field_name_log, self._FILL_RETRY_LIMIT)
            time.sleep(self.step_delay)
            return (True, True)

        # ── 2a2. Auto-check: focused checkbox should be checked ──────────────
        auto_chk = self._auto_check(state)
        if auto_chk is not None:
            field_name_log, should_check = auto_chk
            _did_new_check = False
            if should_check:
                if field_name_log in self._checked_fields:
                    logger.info("Auto-check: '%s' already checked this run — Tab.", field_name_log)
                else:
                    elements   = state.get("elements", [])
                    focused_id = state.get("focused_element_id")
                    focused_el = next((e for e in elements if e.get("element_id") == focused_id), None)
                    if focused_el and focused_el.get("bbox"):
                        x1, y1, x2, y2 = focused_el["bbox"]
                        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                        already_checked = False
                        try:
                            import win32gui as _wg
                            import win32api as _wa
                            BM_GETCHECK = 0x00F0
                            _hwnd = _wg.WindowFromPoint((int(cx), int(cy)))
                            if _hwnd:
                                already_checked = (_wa.SendMessage(_hwnd, BM_GETCHECK, 0, 0) == 1)
                        except Exception:
                            pass
                        if already_checked:
                            logger.info("Auto-check: '%s' already checked — Tab.", field_name_log)
                            self._checked_fields.add(field_name_log)
                        else:
                            self._peek_notepad(state, field_name_log)
                            logger.info("Auto-check: '%s' → clicking @ (%.0f, %.0f)",
                                        field_name_log, cx, cy)
                            self._executor.execute({"action_type": "click",
                                                    "click_position": [cx, cy]})
                            self._checked_fields.add(field_name_log)
                            _did_new_check = True
                    else:
                        logger.warning("Auto-check: '%s' — no bbox, pressing space", field_name_log)
                        self._executor.execute({"action_type": "keyboard",
                                                "key_count": 1, "keystrokes": ["space"]})
                        self._checked_fields.add(field_name_log)
                        _did_new_check = True
            else:
                logger.info("Auto-check: '%s' should remain unchecked — Tab.", field_name_log)
            self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
            self._no_change_streak = 0
            if _did_new_check:
                self._steps_on_tab   = 0
                self._last_auto_step = step_idx
            time.sleep(self.step_delay)
            return (True, True)

        # ── 2a3. Peek for unknown focused checkbox ───────────────────────────
        _fid_chk = state.get("focused_element_id")
        _fe_chk  = next((e for e in state.get("elements", [])
                         if e.get("element_id") == _fid_chk), None)
        if (_fe_chk and _fe_chk.get("type") == "checkboxcontrol"):
            _fn_chk     = (_fe_chk.get("label") or _fe_chk.get("text") or "").strip()
            _fn_chk_low = _fn_chk.lower()
            if (_fn_chk
                    and not self._lookup_field(_fn_chk)
                    and _fn_chk_low not in {s.lower() for s in self._confirmed_blank_fields}
                    and _fn_chk_low not in {s.lower() for s in self._checked_fields}):
                logger.info("Auto-check: '%s' not in cache — peeking Notepad.", _fn_chk)
                self._peek_notepad(state, _fn_chk)
                auto_chk2 = self._auto_check(state)
                if auto_chk2 is not None:
                    field_name_log2, should_check2 = auto_chk2
                    if should_check2 and field_name_log2 not in self._checked_fields:
                        if _fe_chk.get("bbox"):
                            x1, y1, x2, y2 = _fe_chk["bbox"]
                            cx2, cy2 = (x1 + x2) / 2, (y1 + y2) / 2
                            logger.info("Auto-check (post-peek): '%s' → clicking", field_name_log2)
                            self._executor.execute({"action_type": "click",
                                                    "click_position": [cx2, cy2]})
                        else:
                            self._executor.execute({"action_type": "keyboard",
                                                    "key_count": 1, "keystrokes": ["space"]})
                        self._checked_fields.add(field_name_log2)
                    else:
                        logger.info("Auto-check (post-peek): '%s' should remain unchecked — Tab.",
                                    _fn_chk)
                    self._executor.execute({"action_type": "keyboard",
                                            "key_count": 1, "keystrokes": ["tab"]})
                    self._no_change_streak = 0
                    self._last_auto_step   = step_idx
                    time.sleep(self.step_delay)
                    return (True, True)
                else:
                    logger.info("Auto-check: '%s' not found after peek — treating as blank, Tab.",
                                _fn_chk)
                    self._confirmed_blank_fields.add(_fn_chk_low)
                    self._executor.execute({"action_type": "keyboard",
                                            "key_count": 1, "keystrokes": ["tab"]})
                    self._no_change_streak = 0
                    self._last_auto_step   = step_idx
                    time.sleep(self.step_delay)
                    return (True, True)

        # ── 2b. Combobox fix ─────────────────────────────────────────────────
        fix = self._combobox_needs_fix(state)
        if fix:
            field_name, current_val, expected_val = fix
            elements  = state.get("elements", [])
            listitems = [e for e in elements
                         if e.get("type") == "listitemcontrol"
                         and e.get("window_role") != "background"]
            if listitems:
                target = next(
                    (e for e in listitems
                     if (e.get("text") or e.get("label") or "").strip().lower()
                        == expected_val.lower()),
                    None,
                )
                if target and target.get("bbox"):
                    x1, y1, x2, y2 = target["bbox"]
                    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                    logger.info("Combobox-fix: clicking listitem %r @ (%.0f,%.0f)",
                                expected_val, cx, cy)
                    self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
                    self._steps_on_tab   = 0
                    self._last_auto_step = step_idx
                    time.sleep(self.step_delay)
                    return (True, True)
                else:
                    logger.warning("Combobox-fix: listitem %r not found in open list", expected_val)
            else:
                focused_id = state.get("focused_element_id")
                focused    = next((e for e in elements if e.get("element_id") == focused_id), None)
                if focused and focused.get("bbox"):
                    x1, y1, x2, y2 = focused["bbox"]
                    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                    self._peek_notepad(state, field_name)
                    logger.info("Combobox-fix: '%s' = %r → need %r — opening dropdown",
                                field_name, current_val, expected_val)
                    self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
                    self._steps_on_tab   = 0
                    self._last_auto_step = step_idx
                    time.sleep(self.step_delay)
                    return (True, True)

        # ── Drought guard ────────────────────────────────────────────────────
        _drought = step_idx - self._last_auto_step
        if _drought >= self._DROUGHT_LIMIT:
            if self._tab_scroll_count < self._MAX_TAB_SCROLLS:
                logger.info("Drought guard: %d steps without auto-handler — scrolling (scroll %d/%d).",
                            _drought, self._tab_scroll_count + 1, self._MAX_TAB_SCROLLS)
                # Record y of current focused field — after scroll it moves up,
                # so min_y filters it out and targets only newly revealed fields below.
                _pre_scroll_y = 0.0
                _foc_id_pre = state.get("focused_element_id")
                _foc_el_pre = next((e for e in state.get("elements", [])
                                    if e.get("element_id") == _foc_id_pre), None)
                if _foc_el_pre and _foc_el_pre.get("bbox"):
                    _pre_scroll_y = float(_foc_el_pre["bbox"][1])
                if self._scroll_form_down(state):
                    self._tab_scroll_count += 1
                    self._last_auto_step    = step_idx
                    time.sleep(self.step_delay * 0.75)
                    if self._observe_fn:
                        state = self._observe_fn()
                    focused = (self._focus_first_empty_field(state, after_scroll=True,
                                                             min_y=_pre_scroll_y)
                               or self._focus_first_empty_field(state))
                    if focused:
                        self._no_change_streak = 0
                    time.sleep(0.3)
                    return (True, True)
            else:
                logger.info("Drought guard: scrolls exhausted (%d/%d) — advancing tab.",
                            self._tab_scroll_count, self._MAX_TAB_SCROLLS)
                if self._try_advance_tab(state):
                    self._no_change_streak  = 0
                    self._tab_just_switched = True
                    self._tab_scroll_count  = 0
                    self._last_auto_step    = step_idx
                    self._refresh_record_cache(state)
                    time.sleep(self.step_delay)
                    return (True, True)

        # Nothing handled — fall through to transformer/LLM
        return (False, False)

    def notify_tab_click(self, tab_idx: int, state: Dict[str, Any]) -> None:
        """
        Called by the agent after detecting a tab-element click in the executed action.
        Sets tab-switch state so next handle_step() will scroll/focus.
        """
        self._current_tab_idx   = tab_idx
        self._tab_just_switched = True
        self._tab_scroll_count  = 0
        self._no_change_streak  = 0
        self._steps_on_tab      = 0
        self._filled_this_tab.clear()
        self._confirmed_blank_fields.clear()
        # A plan built for the previous tab must never be replayed against
        # this one -- discard it, plan_replay's tab-just-switched branch
        # will rebuild it against the new tab's own elements.
        self._field_plan = []
        self._plan_idx   = 0

    def notify_validation(self, validation_status: str, focused_id: Optional[str]) -> None:
        """
        Called by the agent after state validation so the plugin can update
        no_change_streak and last_focused_id tracking.
        """
        if validation_status == "no_change":
            if focused_id and focused_id != self._last_focused_id:
                self._no_change_streak = 0
            else:
                self._no_change_streak += 1
        else:
            self._no_change_streak = 0
        self._last_focused_id = focused_id

    # ── Helpers (extracted from LLMAgent) ────────────────────────────────────

    # ── Post-fill verification helpers ───────────────────────────────────────

    def _get_focused_value(self) -> str:
        """Return current value of the focused edit control via UIA, '' on failure."""
        try:
            import uiautomation as _uia
            ctrl = _uia.GetFocusedControl()
            if ctrl is None:
                return ""
            try:
                vp = ctrl.GetValuePattern()
                if vp:
                    return (vp.Value or "").strip()
            except Exception:
                pass
            return (ctrl.Name or "").strip()
        except Exception:
            return ""

    def _paste_and_verify(
        self,
        field_key: str,
        text_val: str,
        needs_clear: bool,
    ) -> bool:
        """
        Paste text_val into the currently focused field, verify it landed,
        retry once on failure.  Returns True if the value was confirmed.

        Tracks retries per field in self._fill_retries.  After
        _FILL_RETRY_LIMIT failed attempts the field is surrendered (caller
        should add it to _confirmed_blank_fields and move on).
        """
        def _do_paste(clear: bool) -> None:
            # Always ctrl+a: UIA may report empty while the field has a hidden
            # value (wxPython/UIA discrepancy), so unconditional select-all
            # prevents content from being appended instead of replaced.
            self._executor.execute({"action_type": "keyboard",
                                    "key_count": 1, "keystrokes": ["ctrl+a"]})
            time.sleep(0.05)
            self._executor.execute({
                "action_type": "keyboard",
                "key_count":   len(text_val),
                "keystrokes":  list(text_val),
                "text":        text_val,
            })

        _do_paste(needs_clear)
        time.sleep(0.15)   # give wxPython time to process paste + validators

        actual = self._get_focused_value()
        if actual.lower() == text_val.lower():
            return True

        # First verify failed — log and retry once
        self._fill_retries[field_key] = self._fill_retries.get(field_key, 0) + 1
        logger.warning(
            "Auto-fill verify (%d/%d): '%s' got %r, expected %r — retrying.",
            self._fill_retries[field_key], self._FILL_RETRY_LIMIT,
            field_key, actual[:60], text_val[:60],
        )

        if self._fill_retries.get(field_key, 0) >= self._FILL_RETRY_LIMIT:
            logger.warning("Auto-fill: '%s' failed %d times — surrendering field.",
                           field_key, self._FILL_RETRY_LIMIT)
            return False

        _do_paste(clear=True)     # always clear before retry
        time.sleep(0.25)
        actual = self._get_focused_value()
        if actual.lower() == text_val.lower():
            return True

        self._fill_retries[field_key] = self._fill_retries.get(field_key, 0) + 1
        logger.warning("Auto-fill: '%s' still wrong after retry — got %r.", field_key, actual[:60])
        return False

    def _tc_has_pending(self, state: Dict[str, Any]) -> bool:
        """Return True if any visible edit/combobox/checkbox still needs attention."""
        _fl      = {s.lower() for s in self._filled_this_tab}
        _cb_lower = {s.lower() for s in self._confirmed_blank_fields}
        for _e in state.get("elements", []):
            if (_e.get("window_role") == "background"
                    or _e.get("type") not in ("editcontrol", "comboboxcontrol")
                    or not _e.get("enabled", True)):
                continue
            _fn  = (_e.get("label") or _e.get("text") or "").strip()
            _sec = self._detect_section(state, _e)
            _fk  = (f"{_sec} {_fn}" if _sec else _fn).lower()
            if _fk in _cb_lower or _fn.lower() in _cb_lower:
                continue
            _known = self._lookup_field(_fn, section=_sec)
            _val   = (_e.get("value") or "").strip()
            _skip_vals = {"(none)", "none", "(leave blank)", "n/a"}
            if _fk in _fl or _fn.lower() in _fl:
                # Re-verify value wasn't rejected by wxPython validation after tab-away
                if (_known
                        and _known.lower().strip("()") not in _skip_vals
                        and (not _val or _val.lower() != _known.lower())):
                    # Remove from filled set so auto-fill retries it
                    for _item in list(self._filled_this_tab):
                        if _item.lower() in (_fk, _fn.lower()):
                            self._filled_this_tab.discard(_item)
                    logger.info("Post-fill verify: '%s' value rejected (got %r, expected %r) — retrying.",
                                _fn, _val[:40], _known[:40])
                    return True
                continue
            if _known:
                if _known.lower().strip("()") in _skip_vals:
                    continue
                if not _val or _val.lower() != _known.lower():
                    return True
                continue
            # No known expected value — field is optional or cache miss.
            # Don't block tab advance for fields we can't determine status of.
        for _chk in state.get("elements", []):
            if (_chk.get("window_role") == "background"
                    or _chk.get("type") != "checkboxcontrol"
                    or not _chk.get("enabled", True)):
                continue
            _nm = (_chk.get("label") or _chk.get("text") or "").strip()
            if _nm in self._checked_fields:
                continue
            if _nm.lower() in _cb_lower:
                continue
            _exp = self._lookup_field(_nm)
            if _exp:
                if _exp.lower().strip().startswith("yes"):
                    return True
            else:
                return True
        return False

    def _detect_section(self, state: Dict[str, Any], focused_el: Dict) -> str:
        """Return Driver/Vehicle section name for a focused element, or ''."""
        if not focused_el or not focused_el.get("bbox"):
            return ""
        fx1, fy1, fx2, fy2 = focused_el["bbox"]
        fy_center = (fy1 + fy2) / 2

        section_panes = sorted(
            [e for e in state.get("elements", [])
             if e.get("type") == "panecontrol"
             and e.get("window_role") != "background"
             and e.get("bbox")
             and (e.get("label") or e.get("text") or "").startswith("section_")],
            key=lambda e: e["bbox"][1]
        )

        current_label = ""
        for pane in section_panes:
            if pane["bbox"][1] <= fy_center:
                current_label = pane.get("label") or pane.get("text") or ""
            else:
                break

        if not current_label:
            return ""
        m = re.match(r"section_(driver|vehicle)_(\d+)$", current_label.lower())
        if m:
            return f"{m.group(1).title()} {m.group(2)}"
        return ""

    def _lookup_field(self, field_name: str, section: str = "") -> str:
        """
        Look up a field value from the cached record.

        On a cache miss, when VLM is enabled and the per-tab rescan budget
        isn't exhausted, ask the VLM to scroll Notepad +12 lines and look
        again. Newly-seen fields merge into the cache so subsequent lookups
        in the same screen hit immediately.
        """
        _skip_vals = {"(none)", "none", "(leave blank)", "n/a", "yes (check)",
                      "leave blank — liability only", "leave blank — owned outright"}

        def _hit() -> str:
            rec = self._cached_record
            if not rec:
                return ""

            def _get(key: str) -> str:
                kl = key.lower()
                v = rec.get(key) or next(
                    (rv for rk, rv in rec.items() if rk.lower() == kl), "")
                if not v or v.lower().strip("()") in _skip_vals:
                    return ""
                return v

            if section:
                v = _get(f"{section} {field_name}")
                if v:
                    return v
            return _get(field_name)

        v = _hit()
        if v:
            return v

        # ── VLM rescan-on-miss ───────────────────────────────────────────
        if (self._visual_reader and self._source_window
                and self._rescan_attempts_this_tab < self._RESCAN_MAX_PER_TAB):
            for _ in range(self._RESCAN_MAX_PER_TAB - self._rescan_attempts_this_tab):
                self._rescan_attempts_this_tab += 1
                logger.info("Lookup miss for %r (sec=%r) — VLM rescan attempt %d/%d",
                            field_name, section, self._rescan_attempts_this_tab,
                            self._RESCAN_MAX_PER_TAB)
                new_fields = self._visual_reader.rescan_after_scroll(
                    self._source_window, line_advance=12
                )
                if not new_fields:
                    break
                for _k, _v in new_fields.items():
                    if _v and not self._cached_record.get(_k):
                        self._cached_record[_k] = _v
                        self._visual_cache[_k] = _v
                        if self._data_source and hasattr(self._data_source, "_cache"):
                            self._data_source._cache[_k] = _v
                v = _hit()
                if v:
                    logger.info("Lookup recovered %r → %r via VLM rescan", field_name, v)
                    return v
        return ""

    def _auto_skip(self, state: Dict[str, Any]) -> bool:
        """Return True if the focused field already has the correct value."""
        elements   = state.get("elements", [])
        focused_id = state.get("focused_element_id")
        if not focused_id:
            return False
        focused = next((e for e in elements if e.get("element_id") == focused_id), None)
        if not focused:
            return False
        if focused.get("type") in ("buttoncontrol",):
            return False
        current = (focused.get("value") or "").strip()

        field_name = (focused.get("label") or focused.get("text") or "").strip()
        if not field_name:
            return False

        rec: Dict[str, str] = {}
        if self._cached_record:
            rec = self._cached_record
        else:
            bg_elems = [e for e in elements if e.get("window_role") == "background"]
            bg_blobs = [(e.get("value") or "").strip() for e in bg_elems if e.get("value")]
            for blob in sorted(bg_blobs, key=len, reverse=True):
                r = _parse_records(blob)
                if r:
                    rec = r.get(self._record_num, r.get(min(r), {}))
                    break

        if not rec:
            return False

        section    = self._detect_section(state, focused)
        filled_key = f"{section} {field_name}" if section else field_name
        if filled_key in self._filled_this_tab:
            return False
        fl = field_name.lower()
        expected = None
        if section:
            sec_key = f"{section} {field_name}"
            expected = rec.get(sec_key) or next(
                (v for k, v in rec.items() if k.lower() == sec_key.lower()), None)
        if expected is None:
            expected = rec.get(field_name) or next(
                (v for k, v in rec.items() if k.lower() == fl), None)
        if expected is None:
            return False

        _leave_blank_raws = {"(none)", "none", "(leave blank)", "n/a",
                             "leave blank — liability only", "leave blank — owned outright"}
        if expected.lower().strip("()") in {s.strip("()") for s in _leave_blank_raws}:
            logger.info("Auto-skip: '%s' = (leave blank) — Tab.", filled_key)
            self._filled_this_tab.add(filled_key)
            return True

        if not current:
            return False

        match = current.lower() == expected.lower()
        if match:
            logger.info("Auto-skip: '%s' already = %r", filled_key, current)
            self._filled_this_tab.add(filled_key)
        return match

    def _auto_fill(self, state: Dict[str, Any]) -> Optional[tuple]:
        """Return (field_name, value, needs_clear) if focused empty field has a known value."""
        elements   = state.get("elements", [])
        focused_id = state.get("focused_element_id")
        if not focused_id:
            return None
        focused = next((e for e in elements if e.get("element_id") == focused_id), None)
        if not focused:
            return None
        if focused.get("type") not in ("editcontrol",):
            return None
        current = (focused.get("value") or "").strip()

        field_name = (focused.get("label") or focused.get("text") or "").strip()
        if not field_name:
            return None

        section    = self._detect_section(state, focused)
        filled_key = f"{section} {field_name}" if section else field_name

        if filled_key in self._filled_this_tab:
            return None

        if current:
            expected_check = self._lookup_field(field_name, section=section)
            _skip_check = {"(none)", "none", "(leave blank)", "n/a", "yes (check)",
                           "leave blank — liability only", "leave blank — owned outright"}
            if (expected_check
                    and expected_check.lower().strip("()") not in _skip_check
                    and current.lower() != expected_check.lower()):
                logger.info("Auto-fill: '%s' has wrong value %r — will overwrite with %r",
                            filled_key, current, expected_check)
                return (filled_key, expected_check, True)
            return None

        expected = self._lookup_field(field_name, section=section)

        if not expected:
            bg_elems = [e for e in elements if e.get("window_role") == "background"]
            bg_blobs = [(e.get("value") or "").strip() for e in bg_elems if e.get("value")]
            sec_key  = f"{section} {field_name}" if section else ""
            for blob in sorted(bg_blobs, key=len, reverse=True):
                r = _parse_records(blob)
                if r:
                    rec = r.get(self._record_num, r.get(min(r), {}))
                    fl  = field_name.lower()
                    if sec_key:
                        expected = rec.get(sec_key) or next(
                            (v for k, v in rec.items() if k.lower() == sec_key.lower()), "")
                    if not expected:
                        expected = rec.get(field_name) or next(
                            (v for k, v in rec.items() if k.lower() == fl), "")
                    if expected:
                        break

        if not expected:
            return None

        _skip_vals = {"(none)", "none", "(leave blank)", "n/a", "yes (check)",
                      "leave blank — liability only", "leave blank — owned outright"}
        if expected.lower().strip("()") in _skip_vals:
            return None

        return (filled_key, expected, False)

    def _combobox_needs_fix(self, state: Dict[str, Any]) -> Optional[tuple]:
        """If focused combobox has wrong value return (field_name, current, expected)."""
        elements   = state.get("elements", [])
        focused_id = state.get("focused_element_id")
        focused    = next((e for e in elements if e.get("element_id") == focused_id), None)
        if not focused:
            return None
        if focused.get("type") != "comboboxcontrol":
            return None
        current = (focused.get("value") or "").strip()

        field_name = (focused.get("label") or focused.get("text") or "").strip()
        if not field_name:
            return None
        section = self._detect_section(state, focused)

        _leave_blank_raws = {"(none)", "none", "(leave blank)", "n/a",
                             "leave blank — liability only", "leave blank — owned outright"}
        if self._cached_record:
            _rec     = self._cached_record
            _sec_key = f"{section} {field_name}" if section else ""
            _raw = ""
            if _sec_key:
                _raw = _rec.get(_sec_key) or next(
                    (v for k, v in _rec.items() if k.lower() == _sec_key.lower()), "")
            if not _raw:
                _raw = _rec.get(field_name) or next(
                    (v for k, v in _rec.items() if k.lower() == field_name.lower()), "")
            if _raw and _raw.lower().strip("()") in {s.strip("()") for s in _leave_blank_raws}:
                if current:
                    return (field_name, current, "")
                return None

        expected = self._lookup_field(field_name, section=section)
        if not expected or current.lower() == expected.lower():
            return None
        return (field_name, current, expected)

    def _auto_check(self, state: Dict[str, Any]) -> Optional[tuple]:
        """Return (field_name, should_check) for focused checkbox, or None."""
        elements   = state.get("elements", [])
        focused_id = state.get("focused_element_id")
        if not focused_id:
            return None
        focused = next((e for e in elements if e.get("element_id") == focused_id), None)
        if not focused:
            return None
        if focused.get("type") != "checkboxcontrol":
            return None

        field_name = (focused.get("label") or focused.get("text") or "").strip()
        if not field_name:
            return None

        expected = self._lookup_field(field_name)
        if not expected:
            return None

        ev = expected.lower().strip()
        should_check = ev.startswith("yes") or ev in {"check", "true", "1", "checked"}
        return (field_name, should_check)

    def _scroll_form_down(self, state: Dict[str, Any]) -> bool:
        """Scroll the active form window down. Returns True if attempted."""
        try:
            import pyautogui
            elements = state.get("elements", [])
            # Exclude tabitemcontrol — tab bar items span the full form width
            # and pull the scroll target far off the scrollable content area.
            _SAFE_TYPES = {
                "editcontrol", "checkboxcontrol",
                "radiobuttoncontrol", "buttoncontrol",
            }
            active = [e for e in elements
                      if e.get("type") in _SAFE_TYPES
                      and e.get("window_role") != "background"
                      and e.get("bbox")]
            if not active:
                active = [e for e in elements
                          if e.get("type") not in (
                              "comboboxcontrol", "tabitemcontrol", "panecontrol")
                          and e.get("window_role") != "background" and e.get("bbox")]
            if not active:
                return False
            xs = [(e["bbox"][0] + e["bbox"][2]) / 2 for e in active]
            ys = [(e["bbox"][1] + e["bbox"][3]) / 2 for e in active]
            cx = sum(xs) / len(xs)
            cy = min(sum(ys) / len(ys), state.get("screen_resolution", [1920, 1080])[1] * 0.55)
            _cb_list = [e for e in elements
                        if e.get("type") == "comboboxcontrol" and e.get("bbox")]
            for _cb in _cb_list:
                bx1, by1, bx2, by2 = _cb["bbox"]
                if bx1 <= cx <= bx2 and by1 <= cy <= by2:
                    cx = max(bx1 - 40, 10)
                    break
            orig = pyautogui.position()
            # Dismiss any open dropdown before scrolling — scroll would otherwise
            # navigate the dropdown list instead of the form.
            pyautogui.press("escape")
            time.sleep(0.05)
            pyautogui.moveTo(cx, cy, duration=0.15)
            pyautogui.scroll(-5)
            pyautogui.moveTo(orig.x, orig.y, duration=0.1)
            logger.info("Scroll-form: scrolled down at (%.0f, %.0f)", cx, cy)
            return True
        except Exception as exc:
            logger.warning("Scroll-form: failed — %s", exc)
            return False

    def _scroll_form_to_top(self, state: Dict[str, Any]) -> None:
        """Scroll the active form window back to top."""
        try:
            import pyautogui
            elements = state.get("elements", [])
            _SAFE_TYPES = {
                "editcontrol", "checkboxcontrol",
                "radiobuttoncontrol", "buttoncontrol",
            }
            active = [e for e in elements
                      if e.get("type") in _SAFE_TYPES
                      and e.get("window_role") != "background"
                      and e.get("bbox")]
            if not active:
                active = [e for e in elements
                          if e.get("type") != "comboboxcontrol"
                          and e.get("window_role") != "background" and e.get("bbox")]
            if not active:
                return
            xs = [(e["bbox"][0] + e["bbox"][2]) / 2 for e in active]
            ys = [(e["bbox"][1] + e["bbox"][3]) / 2 for e in active]
            cx = sum(xs) / len(xs)
            cy = sum(ys) / len(ys)
            _cb_list2 = [e for e in elements
                         if e.get("type") == "comboboxcontrol" and e.get("bbox")]
            for _cb2 in _cb_list2:
                bx1, by1, bx2, by2 = _cb2["bbox"]
                if bx1 <= cx <= bx2 and by1 <= cy <= by2:
                    cx = max(bx1 - 40, 10)
                    break
            orig = pyautogui.position()
            pyautogui.moveTo(cx, cy, duration=0.15)
            pyautogui.click(cx, cy)
            time.sleep(0.05)
            pyautogui.hotkey("ctrl", "Home")
            time.sleep(0.15)
            for _ in range(5):
                pyautogui.scroll(200)
                time.sleep(0.05)
            pyautogui.moveTo(orig.x, orig.y, duration=0.1)
            logger.info("Scroll-form-top: scrolled to top at (%.0f, %.0f)", cx, cy)
        except Exception as exc:
            logger.warning("Scroll-form-top: failed — %s", exc)

    def _focus_by_label(self, label: str, expected_bbox: Optional[List[float]] = None) -> bool:
        """
        SetFocus a control by its UIA Name, trying EditControl, then
        ComboBoxControl, then CheckBoxControl. Returns False (never raises)
        on any failure. This is the local fallback used only when
        LLMAgent hasn't wired a real _focus_fn (_focus_element_via_uia,
        which additionally disambiguates same-labeled elements by
        expected_bbox) onto this plugin -- e.g. when FormFillerPlugin is
        used standalone or in tests. `expected_bbox` is accepted for
        signature symmetry with that real method but unused here.
        """
        if not label or label == "?":
            return False
        try:
            import win32gui as _w32g
            import uiautomation as _uia_mod
            _fg = _w32g.GetForegroundWindow()
            _root = _uia_mod.ControlFromHandle(_fg)
            _ctrl = _root.EditControl(searchDepth=10, Name=label)
            if not _ctrl.Exists(maxSearchSeconds=0.2):
                _ctrl = _root.ComboBoxControl(searchDepth=10, Name=label)
            if not _ctrl.Exists(maxSearchSeconds=0.2):
                _ctrl = _root.CheckBoxControl(searchDepth=10, Name=label)
            if _ctrl.Exists(maxSearchSeconds=0.2):
                _ctrl.SetFocus()
                logger.info("_focus_by_label: UIA SetFocus on %r", label)
                return True
        except Exception:
            pass
        return False

    def _focus_first_empty_field(
        self,
        state:        Dict[str, Any],
        after_scroll: bool  = False,
        min_y:        float = 0,
    ) -> bool:
        """Click/focus the first unfilled editcontrol, combobox, or pending checkbox."""
        elements = state.get("elements", [])
        _tab_bottoms = [
            e["bbox"][3] for e in elements
            if e.get("type") in ("tabitem", "tabitemcontrol")
            and e.get("window_role") != "background"
            and e.get("bbox")
        ]
        _tab_floor = (max(_tab_bottoms) + 5) if _tab_bottoms else 110
        _min_y = min_y if min_y > 0 else float("-inf")
        _filled_lower  = {s.lower() for s in self._filled_this_tab}
        _cb_lower      = {s.lower() for s in self._confirmed_blank_fields}
        fillable = []
        for e in elements:
            if (e.get("window_role") == "background"
                    or not e.get("bbox")
                    or not e.get("enabled", True)
                    or e["bbox"][1] < _min_y):
                continue
            etype = e.get("type", "")
            fn    = (e.get("label") or e.get("text") or "").strip()
            sec   = self._detect_section(state, e)
            fkey  = f"{sec} {fn}".lower() if sec else fn.lower()
            if etype in ("editcontrol", "comboboxcontrol"):
                if fkey in _filled_lower or fn.lower() in _filled_lower:
                    continue
                fillable.append(e)
            elif etype == "checkboxcontrol":
                # Only include checkboxes that are pending (known-yes or unknown)
                if fn.lower() in _cb_lower or fn in self._checked_fields:
                    continue
                _exp = self._lookup_field(fn)
                if _exp:
                    if _exp.lower().strip().startswith("yes"):
                        fillable.append(e)
                else:
                    # Unknown — include so peek gets a chance
                    fillable.append(e)
        if not fillable:
            return False
        fillable.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
        e = fillable[0]
        x1, y1, x2, y2 = e["bbox"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        field_label = (e.get("label") or e.get("text") or "?").strip()
        # Try UIA SetFocus first
        if field_label and field_label != "?" and self._focus_by_label(field_label):
            return True
        cy = max(cy, _tab_floor)
        cy = min(cy, y2 - 2)
        logger.info("Tab-advance focus: clicking first unhandled field %r @ (%.0f, %.0f)",
                    field_label, cx, cy)
        self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
        return True

    def _try_advance_tab(self, state: Dict[str, Any]) -> bool:
        """Click the next form tab. Returns True if a tab was found and clicked."""
        elements = state.get("elements", [])
        tabs = [
            e for e in elements
            if e.get("type") in ("tabitem", "tabitemcontrol")
            and e.get("window_role") != "background"
            and e.get("bbox")
        ]
        logger.info("_try_advance_tab: found %d tab(s): %s",
                    len(tabs),
                    [((e.get("text") or e.get("label") or "?"), e.get("type")) for e in tabs])
        if not tabs:
            logger.warning("_try_advance_tab: no tabs found — scroll to top and re-observe.")
            self._scroll_form_to_top(state)
            time.sleep(0.5)
            if self._observe_fn:
                state2 = self._observe_fn()
                tabs = [
                    e for e in state2.get("elements", [])
                    if e.get("type") in ("tabitem", "tabitemcontrol")
                    and e.get("window_role") != "background"
                    and e.get("bbox")
                ]
                logger.info("_try_advance_tab: after re-observe found %d tab(s)", len(tabs))
            if not tabs:
                return False

        _TAB_PANE_MAP = {
            "Policy":       "tab_policy",
            "Policyholder": "tab_policyholder",
            "Vehicle":      "tab_vehicle",
            "Coverage":     "tab_coverage",
            "Drivers":      "tab_drivers",
            "History":      "tab_history",
            "Claims":       "tab_claims",
            "Payment":      "tab_payment",
        }
        _active_idx = self._current_tab_idx
        try:
            import win32gui as _w32g2
            import uiautomation as _uia2
            _fg2   = _w32g2.GetForegroundWindow()
            _root2 = _uia2.ControlFromHandle(_fg2)
            for _tii, _tab_el in enumerate(tabs):
                _tname = (_tab_el.get("text") or _tab_el.get("label") or "").strip()
                _pname = _TAB_PANE_MAP.get(_tname)
                if not _pname:
                    continue
                _pane = _root2.PaneControl(searchDepth=6, Name=_pname)
                if not _pane.Exists(maxSearchSeconds=0.05):
                    continue
                _found = False
                for _ch in _pane.GetChildren():
                    try:
                        _r = _ch.BoundingRectangle
                        if _r.left >= 0 and _r.top >= 0 and _r.width > 0:
                            _active_idx = _tii
                            _found = True
                            break
                    except Exception:
                        pass
                if _found:
                    break
        except Exception:
            pass

        if _active_idx > self._current_tab_idx + 1:
            logger.warning(
                "_try_advance_tab: pane detection jumped tabs (idx=%d > tracked+1=%d) — fallback.",
                _active_idx, self._current_tab_idx + 1)
            _active_idx = self._current_tab_idx

        logger.info("_try_advance_tab: active tab idx=%d (fallback=%d)",
                    _active_idx, self._current_tab_idx)

        next_idx = _active_idx + 1
        if next_idx >= len(tabs):
            logger.info("Stuck guard: already on last tab (%d tabs) — signalling done.", len(tabs))
            return False

        next_tab = tabs[next_idx]
        x1, y1, x2, y2 = next_tab["bbox"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        tab_name = (next_tab.get("text") or next_tab.get("label") or "?").strip()
        logger.info("Stuck guard: advancing to tab %r @ (%.0f, %.0f)", tab_name, cx, cy)
        self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
        self._current_tab_idx = next_idx
        return True

    def _scan_tab_visual(self, state: Dict[str, Any]) -> None:
        """VLM tab scan: read field values for the current tab from Notepad."""
        if not self._visual_reader or not self._source_window:
            return

        _KNOWN_TABS = {"policy", "policyholder", "vehicle", "coverage",
                       "drivers", "history", "claims", "payment"}
        elements = state.get("elements", [])
        tabs = [
            e for e in elements
            if e.get("type") in ("tabitem", "tabitemcontrol")
            and e.get("window_role") != "background"
            and e.get("bbox")
            and (e.get("text") or e.get("label") or "").strip().lower() in _KNOWN_TABS
        ]
        if not tabs or self._current_tab_idx >= len(tabs):
            return
        tab_name = (tabs[self._current_tab_idx].get("text")
                    or tabs[self._current_tab_idx].get("label") or "").strip()
        if not tab_name:
            return

        full_text = ""
        if self._data_source:
            full_text = self._data_source.read_full_text(state) if hasattr(
                self._data_source, "read_full_text") else ""
        if not full_text:
            return

        logger.info("Visual tab scan: tab=%r record=%d (live VLM read)",
                    tab_name, self._record_num)
        live_fields = self._visual_reader.scan_tab(
            tab_name, self._record_num, full_text, self._source_window
        )
        if live_fields:
            # Live VLM read is authoritative — overwrite stale cache values
            # so cross-record contamination cannot leak into form fills.
            self._cached_record.update(live_fields)
            self._visual_cache.update(live_fields)
            # Sync data_source cache so any downstream lookup() agrees
            if self._data_source and hasattr(self._data_source, "_cache"):
                self._data_source._cache.update(live_fields)
            logger.info("Visual tab scan: %d field(s) refreshed live (cached_record=%d)",
                        len(live_fields), len(self._cached_record))
        else:
            logger.warning("Visual tab scan: VLM saw nothing for tab=%r record=%d",
                           tab_name, self._record_num)

    def _peek_notepad(self, state: Dict[str, Any], field_name: str) -> None:
        """Scroll Notepad to the field and extract its value into caches."""
        if self._cached_record.get(field_name):
            return
        if self._data_source and hasattr(self._data_source, "peek"):
            val = self._data_source.peek(
                field_name, state,
                visual_reader=self._visual_reader,
                visual_cache=self._visual_cache,
                step_delay=self.step_delay,
            )
            if val:
                self._cached_record[field_name] = val

    def _peek_next_field_after(self, state: Dict[str, Any], after_label: str) -> Optional[tuple]:
        """Return (key, value) of first uncached field after after_label in Notepad."""
        if self._data_source and hasattr(self._data_source, "peek_next_after"):
            return self._data_source.peek_next_after(after_label, state)
        return None

    def _refresh_record_cache(self, state: Dict[str, Any]) -> None:
        """
        Populate _cached_record for the current record number.

        Priority:
          1. VLM visual_cache (now record-specific — run_agent passes only
             this record's bucket from VisualDataReader._record_cache).
             VLM is the agent's "eyes" per the human-like-control rule —
             it sees what is on screen and is the authoritative source.
          2. Win32 / UIA text — fallback when VLM is disabled or didn't
             extract this record (e.g. unscrolled, header missed).
        """
        import re as _re

        # ── 1. VLM cache as primary ──────────────────────────────────────
        if self._visual_reader and self._visual_cache:
            self._cached_record = dict(self._visual_cache)
            logger.info("Record cache: VLM primary — %d field(s) for record %d",
                        len(self._cached_record), self._record_num)
            # Sync data_source cache for any downstream lookup
            if self._data_source and hasattr(self._data_source, "_cache"):
                self._data_source._cache = dict(self._cached_record)
            # Optional: try to fill missing keys from Win32 text as safety net
            full_text_safety = ""
            if self._data_source and hasattr(self._data_source, "read_full_text"):
                full_text_safety = self._data_source.read_full_text(state)
            if full_text_safety:
                recs = _parse_records(full_text_safety)
                if recs:
                    win32_rec = recs.get(self._record_num, {})
                    before        = len(self._cached_record)
                    corrected     = 0
                    for _wk, _wv in win32_rec.items():
                        if _wv:
                            # Win32 reads Notepad text directly — deterministic.
                            # Override VLM even when VLM has a value: VLM can
                            # hallucinate or read form defaults instead of source.
                            old = self._cached_record.get(_wk, "")
                            if old != _wv:
                                corrected += 1
                            self._cached_record[_wk] = _wv
                        else:
                            # Win32 found the key but value is empty — only add
                            # if VLM also has nothing (don't clobber VLM value
                            # with empty string).
                            self._cached_record.setdefault(_wk, _wv)
                    added = len(self._cached_record) - before
                    if corrected:
                        logger.info("Record cache: Win32 corrected %d VLM value(s) "
                                    "(VLM hallucinated or read form defaults)", corrected)
                    if added:
                        logger.info("Record cache: Win32 filled %d gap(s) VLM missed", added)
                    if self._data_source and hasattr(self._data_source, "_cache"):
                        self._data_source._cache = dict(self._cached_record)
            return

        # ── 2. Win32 / UIA fallback path (no VLM or VLM had nothing) ─────
        full_text = ""
        if self._data_source and hasattr(self._data_source, "read_full_text"):
            full_text = self._data_source.read_full_text(state)

        if not full_text:
            # Fallback: UIA background blobs
            elements = state.get("elements", [])
            bg_elems = [e for e in elements if e.get("window_role") == "background"]
            blobs = sorted(
                [(e.get("value") or "").strip() for e in bg_elems],
                key=len, reverse=True
            )
            for blob in blobs:
                if blob and _parse_records(blob):
                    full_text = blob
                    logger.info("Record cache: Win32 failed — using UIA blob (%d chars)", len(blob))
                    break
            if not full_text and blobs and blobs[0]:
                full_text = blobs[0]

            if not full_text:
                ocr_elems = [e for e in bg_elems
                             if e.get("source") == "ocr" and (e.get("text") or "")]
                if ocr_elems:
                    ocr_elems.sort(
                        key=lambda e: (e["bbox"][1], e["bbox"][0]) if e.get("bbox") else (0, 0))
                    lines, cur_line, cur_y = [], [], None
                    for e in ocr_elems:
                        y = e["bbox"][1] if e.get("bbox") else 0
                        if cur_y is None or abs(y - cur_y) <= 10:
                            cur_line.append(e.get("text", ""))
                        else:
                            if cur_line:
                                lines.append(" ".join(cur_line))
                            cur_line = [e.get("text", "")]
                        cur_y = y
                    if cur_line:
                        lines.append(" ".join(cur_line))
                    full_text = "\n".join(lines)

        if not full_text:
            # No record-aware text source — fall back to flat VLM cache.
            # Best-effort only; flat cache cannot disambiguate cross-record fields.
            if self._visual_cache and not self._cached_record:
                self._cached_record = dict(self._visual_cache)
                logger.warning("Record cache: no Win32/UIA text — using flat VLM cache (%d fields, "
                               "WARNING: not record-aware, may carry record 1's values for record %d)",
                               len(self._cached_record), self._record_num)
            else:
                logger.warning("Record cache: no text available — unchanged (%d fields)",
                               len(self._cached_record))
            return

        records = _parse_records(full_text)
        if records:
            rec = records.get(self._record_num, records.get(min(records), {}))
            self._cached_record = rec
            logger.info("Record cache refreshed: %d fields for record %d",
                        len(rec), self._record_num)
        else:
            data: Dict[str, str] = {}
            for line in full_text.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = _re.sub(r"[\[\]]", "", key).strip()
                    val = _re.sub(r"\s*\[.*", "", val)
                    val = _re.sub(r"\s*←.*", "", val).strip()
                    if key and key not in data and val and val.lower() not in {
                            "(none)", "none", "(leave blank)"}:
                        data[key] = val
            self._cached_record = data
            logger.info("Record cache refreshed (plain text): %d fields", len(data))

        if self._visual_cache:
            before = len(self._cached_record)
            # Fill ONLY missing keys — Win32 record-aware data must win over
            # the flat VLM cache (which contains record 1's values for keys
            # that repeat across records).
            for _vk, _vv in self._visual_cache.items():
                self._cached_record.setdefault(_vk, _vv)
            added = len(self._cached_record) - before
            logger.info("Record cache: filled %d missing field(s) from VLM cache (Win32 had %d)",
                        added, before)

        # Also update the data_source cache to stay in sync
        if self._data_source and hasattr(self._data_source, "_cache"):
            self._data_source._cache = dict(self._cached_record)
