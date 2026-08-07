"""
components/agent/agent.py
=========================
LLMAgent — goal-driven agentic loop with multi-provider LLM support.

Supported providers
-------------------
  anthropic  — Claude (claude-sonnet-4-6)         paid, best quality
  groq       — Llama 3.3 70B via Groq API         free tier, very fast
  gemini     — Google Gemini Flash                 free tier
  lmstudio   — Local LM Studio (OpenAI-compatible) completely free, offline
  none       — Transformer-only fallback           no LLM, no API needed

Architecture
------------
  User goal (natural language)
        ↓
  LLMAgent.run()
        ↓  ← UIAutomationObserver.snapshot()  (live screen every step)
  LLM provider  (anthropic | groq | gemini | lmstudio)
    • Understands the task goal
    • Reads current screen state as plain text
    • Decides: continue | done | error | wait
        ↓
  TransformerAgentNetwork.predict()
    • Predicts exact action: click(x,y) | keyboard | no_op
        ↓
  _TextResolver  (Options 1 + 2 + 3)
    • Finds text to type from background window elements
        ↓
  ActionExecutor
    • Fires real OS input via pyautogui + clipboard paste

Usage
-----
  agent = LLMAgent(
      goal="Fill the form using data from Notepad",
      provider="groq",
      api_key="gsk_...",
  )
  results = agent.run(max_steps=30)
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

# ── path setup ────────────────────────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_COMP_DIR  = os.path.dirname(_THIS_DIR)
_ROOT      = os.path.dirname(_COMP_DIR)
for _p in (_ROOT, _COMP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── provider SDK imports (all optional) ───────────────────────────────────────
try:
    import anthropic as _anthropic
    _ANTHROPIC_OK = True
except ImportError:
    _ANTHROPIC_OK = False

try:
    from groq import Groq as _Groq
    _GROQ_OK = True
except ImportError:
    _GROQ_OK = False

try:
    from google import genai as _genai
    _GEMINI_OK = True
except ImportError:
    _GEMINI_OK = False

try:
    from openai import OpenAI as _OpenAI   # used for LM Studio (OpenAI-compatible)
    _OPENAI_OK = True
except ImportError:
    _OPENAI_OK = False

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)

# ── default model IDs per provider ────────────────────────────────────────────
_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "groq":      "llama-3.3-70b-versatile",
    "gemini":    "gemini-2.0-flash",
    "lmstudio":  "local-model",   # LM Studio uses whatever model is loaded
}

# ── system prompt (shared across all providers) ───────────────────────────────
_SYSTEM_PROMPT = """\
You are Intern, a desktop automation agent. You observe any GUI and control the
mouse and keyboard to accomplish goals — forms, email, Excel, web, file management,
or anything else visible on screen.

You receive three sections each step:
  GOAL             — what the user wants accomplished.
  SCREEN           — every visible UI element (type, label, current value).
  DATA SOURCES     — text visible in background windows (Notepad, files, etc.)
                     Use these values when you need to type something.
  LAST ACTIONS     — your recent actions and whether they changed the screen.

Respond with ONE JSON action only — no other text:

{
  "action_type": "click" | "type" | "hotkey" | "scroll" | "done" | "wait",
  "target":      "<EXACT label or text from SCREEN — never paraphrase>",
  "text":        "<exact value from DATA SOURCES — never invent>",
  "keys":        ["tab"],
  "direction":   "down" | "up",
  "clicks":      3,
  "reason":      "<one sentence>"
}

Action rules:
- "click"  → any element: field, button, checkbox, dropdown, tab, menu item.
             target = EXACT string from SCREEN. Never guess or paraphrase.
- "type"   → types into the focused field. text = value from DATA SOURCES only.
- "hotkey" → keyboard shortcut. keys: ["tab"] advances focus, ["return"] confirms,
             ["ctrl+c"] copies, ["alt+f4"] closes, etc.
- "scroll" → scroll in any direction. target = element to scroll near.
- "done"   → goal is fully achieved. Only use when certain.
- "wait"   → UI is loading or animating. Use sparingly.

General rules:
1. CRITICAL: "text" must be copied VERBATIM from DATA SOURCES. If the exact value is not visible in DATA SOURCES, do NOT type — skip with hotkey ["tab"] instead.
2. Never complete, guess, reformat, or paraphrase values. Copy the exact string.
3. If a field already has the correct value → hotkey ["tab"] to move on.
4. Interact with whatever app is on screen — do not assume it is a form.
5. Output JSON only. No explanation outside the object.
6. ALWAYS act on the CURRENTLY FOCUSED FIELD first. Do not click other fields to skip ahead.
7. Fill fields in order — top to bottom, left to right. Never jump over a field.
8. UNKNOWN VALUE RULE: If you do not see the value for the focused field in DATA SOURCES, do NOT guess, invent, or reuse a value from another field. Output exactly: {"action_type": "hotkey", "keys": ["tab"], "reason": "value not found in data sources — skipping"}
9. UNCERTAINTY RULE: If you are unsure what action to take next, skip with tab rather than guess. A skipped field is recoverable. A wrong value is not.
10. All required data is already provided in DATA SOURCES. Do not click buttons or controls that would fetch, import, or load additional data — doing so will open dialogs that derail the task.
11. DIALOG ESCAPE: If an unexpected dialog or popup appears that you did not intend to open, press Escape immediately: {"action_type": "hotkey", "keys": ["escape"], "reason": "dismissing unexpected dialog"}
"""


# ══════════════════════════════════════════════════════════════════════════════
#  State / history → text helpers
# ══════════════════════════════════════════════════════════════════════════════

# Canonical record parser + default data source live in data_sources.notepad_source.
# This re-export keeps existing call sites working.
from data_sources.notepad_source import _parse_records, NotepadDataSource  # noqa: F401


def _state_to_text(state: Dict[str, Any], record_num: int = 1, visual_cache: Optional[Dict[str, str]] = None, filled_labels: Optional[set] = None) -> str:
    lines: List[str] = []
    lines.append(f"Active window: {state.get('application','?')} — {state.get('window_title','')!r}")

    for w in state.get("windows", []):
        lines.append(
            f"  [{w.get('role','?')}] {w.get('app','')} — "
            f"{w.get('title','')!r}  ({w.get('element_count',0)} elements)"
        )

    lines.append("")
    # ── CURRENT FOCUS banner ─────────────────────────────────────────────────
    focused_id = state.get("focused_element_id")
    all_elems  = state.get("elements", [])
    focused_el = next((e for e in all_elems if e.get("element_id") == focused_id), None)
    if focused_el:
        _fn  = (focused_el.get("label") or focused_el.get("text") or "?").strip()[:200]
        _fv  = (focused_el.get("value") or "").strip()[:200]
        _ft  = focused_el.get("type", "?")
        lines.append(f">>> CURRENTLY FOCUSED: [{_ft}] \"{_fn}\"" +
                     (f"  current value: {_fv!r}" if _fv else "  (empty)") + " <<<")
        lines.append("")

    active = [e for e in state.get("elements", []) if e.get("window_role") in ("active", None)]
    bg     = [e for e in state.get("elements", []) if e.get("window_role") == "background"]

    _INTERACTIVE = {
        "input", "button", "checkbox", "radio", "combobox",
        "listitem", "tabitem", "splitbutton", "link", "list",
        # UIA ControlTypeName variants (suffix "Control")
        "editcontrol", "buttoncontrol", "checkboxcontrol", "radiobuttoncontrol",
        "comboboxcontrol", "listitemcontrol", "tabitemcontrol", "listcontrol",
        "hyperlinkcontrol", "splitbuttoncontrol",
    }
    _SKIP_TYPES  = {
        "window", "titlebar", "pane", "toolbar", "statusbar",
        "menubar", "menu", "group", "separator", "scrollbar",
        "progressbar", "tooltip", "document",
        "windowcontrol", "titlebarcontrol", "panecontrol", "toolbarcontrol",
        "menubarcontrol", "menuitemcontrol", "scrollbarcontrol",
    }

    all_elems = state.get("elements", [])

    # Form-specific control types — only these identify a window as a form.
    # Excludes buttons/scrollbars that appear in ANY window (e.g. Notepad toolbar).
    _FORM_CONTROLS = {
        "input", "combobox", "checkbox", "radio",
        "editcontrol", "comboboxcontrol", "checkboxcontrol", "radiobuttoncontrol",
    }

    # Form elements = windows that contain actual form-fill inputs.
    # This works even when the terminal has OS focus (form is "background").
    form_windows = {
        e.get("window_title") for e in all_elems
        if e.get("type") in _FORM_CONTROLS
    }

    # Fallback: if no form-fill controls detected anywhere, treat the active
    # window as the form (handles wx apps where UIA maps controls to custom types)
    if not form_windows:
        active_title = state.get("window_title", "")
        if active_title:
            form_windows = {active_title}

    form_elems = [e for e in all_elems if e.get("window_title") in form_windows]

    # Data-source elements = windows with no interactive inputs (e.g. Notepad)
    data_elems = [e for e in all_elems if e.get("window_title") not in form_windows]

    labels      = [e for e in form_elems
                   if e.get("type") not in _INTERACTIVE
                   and e.get("type") not in _SKIP_TYPES
                   and (e.get("text") or "").strip()]
    interactive = [e for e in form_elems if e.get("type") in _INTERACTIVE]

    # Debug: log all unique types in the form so we can see what UIA reports
    all_types = sorted({e.get("type", "?") for e in form_elems})


    _filled = filled_labels or set()
    _filled_lower = {s.lower() for s in _filled}

    lines.append("SCREEN (use EXACT strings as 'target' when clicking):")
    for e in labels[:15]:
        focused = " [FOCUSED]" if e.get("focused") else ""
        txt = (e.get('text') or '').strip()
        if len(txt) > 200:
            continue
        lines.append(f"  \"{txt}\"{focused}")

    # Only show unfilled interactive elements (+ always show focused element)
    _focused_id_st = state.get("focused_element_id")
    _shown = 0
    lines.append(f"\nINTERACTIVE ELEMENTS (showing unfilled):")
    for e in interactive:
        if _shown >= 15:
            break
        val   = (e.get("value") or "").strip()
        text  = (e.get("text") or "").strip()
        if len(val) > 300 or len(text) > 300:
            continue
        label = text or val or "(empty)"
        # Skip already-filled non-focused elements to save tokens
        if (label.lower() in _filled_lower
                and e.get("element_id") != _focused_id_st
                and e.get("type") not in ("tabitem", "tabitemcontrol", "buttoncontrol")):
            continue
        focused = " [FOCUSED]" if e.get("focused") else ""
        lines.append(f"  [{e.get('type','?')}] \"{label}\"{focused}"
                     + (f"  current value: {val!r}" if val else ""))
        _shown += 1

    if visual_cache:
        # Only show unfilled fields — already-filled ones waste tokens
        # Always include the focused field's entry even if it's past the cap
        _focused_field_name = ""
        if focused_el:
            _focused_field_name = (focused_el.get("label") or focused_el.get("text") or "").strip().lower()
        lines.append(f"\nDATA SOURCES (Record {record_num}, unfilled fields):")
        _shown_ds = 0
        for field, value in visual_cache.items():
            _is_focused_field = field.lower() == _focused_field_name
            if _shown_ds >= 20 and not _is_focused_field:
                continue
            if field.lower() in _filled_lower and not _is_focused_field:
                continue
            lines.append(f"  {field} : {value}")
            _shown_ds += 1
    elif data_elems:
        # Collect all background text blobs
        bg_blobs = []
        for e in data_elems:
            val = (e.get("value") or "").strip()
            if val:
                bg_blobs.append(val)

        logger.debug("Background elements: %d total, %d with text (blob sizes: %s)",
                    len(data_elems), len(bg_blobs),
                    [len(b) for b in bg_blobs])

        if bg_blobs:
            # Try each blob for record structure; prefer the one that parses
            records  = {}
            raw_text = ""
            for blob in sorted(bg_blobs, key=len, reverse=True):
                r = _parse_records(blob)
                if r:
                    records  = r
                    raw_text = blob
                    break
            if not raw_text:
                raw_text = max(bg_blobs, key=len)
            logger.debug("_parse_records → %d record(s) found (blob size=%d)", len(records), len(raw_text))

            if records:
                rec = records.get(record_num, records.get(min(records), {}))
                lines.append(f"\nDATA SOURCES (Record {record_num}):")
                for field, value in rec.items():
                    lines.append(f"  {field} : {value}")
            else:
                # Plain text — dump up to 3 000 chars to keep prompt small
                lines.append(f"\nDATA SOURCES:")
                lines.append(f"  {raw_text[:3000]}")

    return "\n".join(lines)


def _is_llm_unavailable(llm_action: Dict[str, Any]) -> bool:
    """
    True iff this llm_action is _ask_llm()'s infra-failure sentinel (connection
    error / non-JSON response / timeout — see _ask_llm's except block), NOT a
    genuine "the value should be blank" decision from a reachable LLM.

    The two used to collapse to the same thing downstream (both end up as an
    empty prediction["text"]), which made a dead LLM connection silently look
    like every field was legitimately blank — the agent would Tab-skip an
    entire form instead of surfacing that the provider was unreachable.
    """
    return llm_action.get("action_type") == "wait" and llm_action.get("reason") == "llm unavailable"


# Matches the gate already established for the non-Option-B merge path
# (agent.py's _merge(), ~L4045) — same meaning there: below this, the
# transformer's own pointer isn't trustworthy enough to act on.
_CLICK_CONF_FLOOR = 0.30

# A wrong click on a tab-strip element is far more costly than a wrong click
# on a same-tab field: it skips an entire tab's worth of fields, not just one
# step. Found live 2026-08-07 (twice, same session): the model clicked away
# to the Vehicle tab with confidence 0.38-0.39 while Policyholder still had
# unfilled fields — comfortably above the general 0.30 floor, so nothing
# blocked it. Section changes get a stricter bar than in-tab navigation.
# Not tuned to this form's tabs specifically — applies to any tabitem/
# tabitemcontrol element on any form.
_TAB_CLICK_CONF_FLOOR = 0.50


def _is_tab_strip_click(pos: Optional[List[float]], elements: Optional[List[Dict[str, Any]]]) -> bool:
    """True if `pos` falls inside any tab-strip element's bbox."""
    if not pos or not elements:
        return False
    px, py = pos[0], pos[1]
    for e in elements:
        if (e.get("type") or "").lower() not in ("tabitem", "tabitemcontrol"):
            continue
        if e.get("window_role") == "background":
            continue
        b = e.get("bbox")
        if not b or len(b) != 4:
            continue
        if b[0] <= px <= b[2] and b[1] <= py <= b[3]:
            return True
    return False


def _round_click_pos(pos: List[float]) -> Tuple[float, float]:
    """10px-bucketed position key, matching the rounding _merge() already
    uses for its own no_change blacklist — small pointer jitter between two
    predictions of "the same" click shouldn't dodge the blacklist."""
    return (round(pos[0] / 10) * 10, round(pos[1] / 10) * 10)


def _gate_low_confidence_click(
    pos: Optional[List[float]],
    click_conf: float,
    elements: Optional[List[Dict[str, Any]]] = None,
    blacklist: Optional[Set[Tuple[float, float]]] = None,
) -> Optional[List[float]]:
    """
    Returns `pos` unchanged if it's structurally invalid anyway (nothing to
    gate) or `click_conf` clears the applicable floor and the position isn't
    blacklisted; returns None if it's a plausible click the model itself
    flagged as unreliable, or one already proven unproductive — the caller
    then falls back to the generic Tab-advance already used for a
    structurally invalid pointer, same as this function's own null case.

    Found 2026-08-07, live: a ptr_conf=0.20 guess landed on the tab strip
    and ended a tab pass 9 fields early (Policy: 4/13 filled) — the
    confidence was already being logged, never acted on. This does NOT hand
    WHERE to the LLM (forbidden in pure/Option-B mode, see _merge()) — it
    only declines to act on an unreliable guess. Tab is a universal
    navigation primitive, not task-specific, so this generalizes to any
    form the agent is pointed at.

    A click landing on a tab-strip element uses the stricter
    _TAB_CLICK_CONF_FLOOR instead — see that constant's comment. `elements`
    is optional (defaults to the general floor) so existing call sites that
    can't easily supply it keep working unchanged.

    `blacklist` catches a DIFFERENT failure mode confidence alone can't:
    found live 2026-08-07, a HIGH-confidence (0.91) click landed on the same
    empty, optional combobox 30+ times in a row — correctly recognized each
    time as "nothing to fill here," but the code path handling that (Escape
    + Tab + mark-attempted) `continue`d immediately, bypassing the
    repeat-action guard that would otherwise have broken the loop. A caller
    that tracks proven-unproductive positions (e.g. self._nochange_click_pos)
    can pass them here so a confidently-wrong pointer gets rejected too.
    """
    if not pos or not (pos[0] > 1 or pos[1] > 1):
        return pos
    if blacklist and _round_click_pos(pos) in blacklist:
        return None
    floor = _TAB_CLICK_CONF_FLOOR if _is_tab_strip_click(pos, elements) else _CLICK_CONF_FLOOR
    if click_conf < floor:
        return None
    return pos


_LISTITEM_TYPES = {"listitem", "listitemcontrol"}


def _open_dropdown_items(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Real, on-screen list items in `elements` — non-empty exactly when some
    combobox's dropdown is currently open.

    A combobox click TOGGLES, it doesn't just open — clicking one that's
    already open closes it instead. Found live 2026-08-07, the actual root
    cause of a loop that survived both an earlier type-name fix and a poll-
    timing widening: a PRECEDING step's click (nominally aimed at a
    different, adjacent field) landed on this combobox's screen position
    and popped it open by accident. The dedicated combobox-fill handler then
    blindly clicked "to open" it again, which closed it — so every
    subsequent poll correctly found 0 items, because there was genuinely
    nothing open to find. Callers should check this BEFORE clicking to open
    a combobox, and skip the click if it's already open.
    """
    return [e for e in elements
            if e.get("type") in _LISTITEM_TYPES
            and e.get("window_role") != "background"
            and e.get("bbox")]


def _tokenize_option(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _option_matches(value: str, option: str) -> bool:
    """
    True if `option` (a dropdown item's text) should count as matching the
    wanted `value`, beyond a bare identical string.

    Three tiers, each covering a real live case:
      1. Exact match (case/whitespace-insensitive).
      2. Prefix match, either direction — 'Full Coverage' vs 'Full Coverage
         (Comprehensive)'.
      3. Whole-word-token containment — 'Sedan' vs '4-Door Sedan'. Found
         live 2026-08-07: prefix-only matching couldn't catch this (neither
         string is a prefix of the other), so the agent looped forever
         opening the dropdown, never finding 'Sedan', closing it, and
         trying again — 25+ times in one run.

    Deliberately NOT a raw substring check — 'Active' must never match
    'Inactive' (an explicit constraint from when this matching was first
    written). Splitting into whole tokens on non-alphanumeric boundaries and
    requiring the value's token sequence to appear as a contiguous run
    within the option's tokens preserves that: 'inactive' tokenizes to a
    single token ['inactive'], so 'active' can never match it as a
    substring of that token.
    """
    v = value.strip().lower()
    o = option.strip().lower()
    if not v or not o:
        return False
    if v == o or o.startswith(v) or v.startswith(o):
        return True
    v_tokens = _tokenize_option(value)
    o_tokens = _tokenize_option(option)
    if not v_tokens:
        return False
    n = len(v_tokens)
    return any(o_tokens[i:i + n] == v_tokens for i in range(len(o_tokens) - n + 1))


_MAX_VERIFY_RETRIES = 2


def _find_element_by_id(elements: List[Dict[str, Any]], element_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Look up an element in a state's elements list by its element_id — used
    to find the same field's post-action value in a later observation."""
    if element_id is None:
        return None
    return next((e for e in elements if e.get("element_id") == element_id), None)


def _is_leave_blank_prediction(prediction: Dict[str, Any]) -> bool:
    """
    True only when `prediction` is still actually a type/keyboard action AND
    its resolved text means "leave this field empty" (blank / none / n-a /
    "leave blank ..."). Callers should Tab past + mark-attempted only when
    this returns True.

    Found 2026-08-07, live: `_merge()` can silently turn a "type" decision
    into a "click" (comboboxes need a click to open their dropdown before a
    value can be selected). A click prediction has no "text" key at all, so
    naively checking `prediction.get("text")` after that override always
    reads as empty and looks exactly like "leave blank" — even when the LLM
    correctly resolved a real value (e.g. 'Sport 2.0T'). That falsely
    Tab-skipped away before the click could even open the dropdown, and the
    transformer then clicked straight back onto the same combobox next step
    — a confirmed live infinite loop. Gating on action_type=="keyboard" first
    means a click override is never mistaken for a deliberate leave-blank.
    """
    if prediction.get("action_type") != "keyboard":
        return False
    txt  = (prediction.get("text") or "").strip()
    norm = txt.lower().strip().strip("()").strip()
    return (not txt) or norm in {"none", "n/a", "na"} or norm.startswith("leave blank")


def _verify_fill_matches(elements_after: List[Dict[str, Any]],
                          focused_id: Optional[str],
                          expected_text: str) -> bool:
    """
    True if the field that was focused before typing now holds exactly the
    text the agent intended to type. Pure comparison, no I/O — the caller
    (LLMAgent's main loop) owns the retry/re-observe orchestration, since
    that requires live executor/observer calls this function can't make.

    Found 2026-08-07, from a direct user report about the old (deleted)
    Intern iteration lacking any verify-at-fill step: StateValidator only
    checks whether SOMETHING changed after an action (focus moved, value
    differs from before) — never whether it changed to the RIGHT thing. A
    field can type successfully (validator says "ok") while still holding
    the wrong value, and nothing used to catch that.
    """
    elem = _find_element_by_id(elements_after, focused_id)
    actual_text = (elem.get("value") or "").strip() if elem else ""
    return actual_text == expected_text.strip()


def _history_to_text(history: List[Dict[str, Any]]) -> str:
    if not history:
        return "No actions taken yet."

    # Count consecutive no_change failures at the tail
    consecutive_failures = 0
    last_failed_target = ""
    for h in reversed(history):
        if h.get("validation") == "no_change":
            consecutive_failures += 1
            last_failed_target = h.get("target", "")
        else:
            break

    lines = []
    for i, h in enumerate(history[-3:], 1):
        at     = h.get("action_type", "?")
        txt    = h.get("typed_text", "")
        target = h.get("target", "")
        val    = h.get("validation", "")
        status = f"  [{val}]" if val else ""
        if at == "click":
            lines.append(f"  {i}. click {target!r}{status}")
        elif at == "keyboard":
            lines.append(f"  {i}. typed {txt!r}{status}" if txt else f"  {i}. keyboard{status}")
        else:
            lines.append(f"  {i}. {at}{status}")

    if consecutive_failures >= 2:
        lines.append(
            f"\n  WARNING: last {consecutive_failures} actions on {last_failed_target!r} "
            f"all failed with no_change. This target is not responding — try a different action or skip it."
        )
    return "\n".join(lines)


def _parse_llm_response(raw: str) -> Dict[str, Any]:
    """Strip markdown fences, thinking blocks, and parse JSON from any LLM response."""
    import re as _re
    raw = raw.strip()
    # Strip <think>...</think> blocks emitted by reasoning models (QwQ, DeepSeek-R1, etc.)
    raw = _re.sub(r"<think>.*?</think>", "", raw, flags=_re.DOTALL).strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    return json.loads(raw)


# ══════════════════════════════════════════════════════════════════════════════
#  Element resolver — finds pixel coords for an LLM-named target
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_target(target: str, state: Dict[str, Any]) -> Optional[List[float]]:
    """
    Given a label like "First Name", find the matching interactive element in
    the active window and return its center [x, y].

    Matching strategy (in order):
      1. Exact text/label match (case-insensitive)
      2. Partial containment match
      3. Nearest label element + adjacent input element
    """
    if not target:
        return None

    tl = target.lower().strip()
    # Search ALL elements — the form may be a background window when the
    # terminal that launched the agent has OS focus, but pyautogui clicks
    # by absolute screen coordinates so it works regardless of focus.
    active_elems = state.get("elements", [])

    _INTERACTIVE = {
        "input", "button", "checkbox", "radio", "combobox",
        "listitem", "tabitem", "splitbutton", "link", "list",
        "editcontrol", "buttoncontrol", "checkboxcontrol", "radiobuttoncontrol",
        "comboboxcontrol", "listitemcontrol", "tabitemcontrol", "listcontrol",
        "hyperlinkcontrol", "splitbuttoncontrol",
    }
    # Tab navigation controls are deprioritized — they share names with form sections
    # (e.g. "Policy" tab header vs "Policy Number" field).  Only fall back to them
    # when nothing else matches, so LLM clicks like "click Policy" land on inputs first.
    _TAB_TYPES = {"tabitem", "tabitemcontrol"}
    _INTERACTIVE_NONTAB = _INTERACTIVE - _TAB_TYPES

    def _center(e: Dict) -> List[float]:
        b = e.get("bbox", [0, 0, 0, 0])
        return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]

    # 1. Exact match on non-tab interactive element text
    for e in active_elems:
        if e.get("type") in _INTERACTIVE_NONTAB:
            txt = (e.get("text") or e.get("label") or e.get("value") or "").lower()
            if txt == tl:
                return _center(e)

    # 2. Exact match on label element → return nearest non-tab interactive element
    for e in active_elems:
        txt = (e.get("text") or e.get("label") or "").lower()
        if txt == tl:
            cx, cy = _center(e)
            best, best_dist = None, float("inf")
            for other in active_elems:
                if other.get("type") not in _INTERACTIVE_NONTAB:
                    continue
                ox, oy = _center(other)
                dist = ((ox - cx) ** 2 + (oy - cy) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best = other
            if best and best_dist < 300:
                return _center(best)

    # 3. Partial match on non-tab interactive element text
    for e in active_elems:
        if e.get("type") in _INTERACTIVE_NONTAB:
            txt = (e.get("text") or e.get("label") or e.get("value") or "").lower()
            if tl in txt or txt in tl:
                return _center(e)

    # 4. Partial match on label → nearest non-tab interactive
    for e in active_elems:
        txt = (e.get("text") or e.get("label") or "").lower()
        if tl in txt or txt in tl:
            cx, cy = _center(e)
            best, best_dist = None, float("inf")
            for other in active_elems:
                if other.get("type") not in _INTERACTIVE_NONTAB:
                    continue
                ox, oy = _center(other)
                dist = ((ox - cx) ** 2 + (oy - cy) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best = other
            if best and best_dist < 300:
                return _center(best)

    # 5. Match by current element value (LLM sometimes uses the value as target)
    for e in active_elems:
        if e.get("type") in _INTERACTIVE_NONTAB:
            val = (e.get("value") or "").lower().strip()
            if val and val == tl:
                return _center(e)

    # 6. Last resort: repeat passes 1+3 including tab items (explicit tab switching)
    for e in active_elems:
        if e.get("type") in _TAB_TYPES:
            txt = (e.get("text") or e.get("label") or e.get("value") or "").lower()
            if txt == tl or tl in txt or txt in tl:
                return _center(e)

    return None


# ══════════════════════════════════════════════════════════════════════════════
#  LLMAgent
# ══════════════════════════════════════════════════════════════════════════════

class LLMAgent:
    """
    Goal-driven agentic loop with pluggable LLM providers.

    Parameters
    ----------
    goal        : Natural-language task description.
    provider    : "anthropic" | "groq" | "gemini" | "lmstudio" | "none"
    api_key     : API key for the chosen provider (not needed for lmstudio/none).
    model_id    : Override the default model for the provider.
    lmstudio_url: Base URL for LM Studio server (default: http://localhost:1234/v1).
    model_path  : Path to TransformerAgentNetwork checkpoint.
    dry_run     : Log actions but do not fire real OS input.
    max_steps   : Hard cap on loop iterations.
    step_delay  : Seconds between steps.
    llm_every   : Call LLM every N steps (higher = fewer API calls).
    device_str  : Torch device for the transformer.
    """

    def __init__(
        self,
        goal:          str,
        provider:      str            = "none",
        api_key:       Optional[str]  = None,
        model_id:      Optional[str]  = None,
        lmstudio_url:  str            = "http://localhost:1234/v1",
        model_path:    str            = "tasks/form_filling/model.pt",
        dry_run:       bool           = False,
        max_steps:     int            = 50,
        step_delay:    float          = 1.2,
        llm_every:     int            = 2,
        device_str:    str            = "auto",
        record_num:    int            = 1,
        use_ocr:       bool           = False,
        visual_cache:  Optional[Dict[str, str]] = None,  # Gemini-extracted {field: value}
        visual_reader: Optional[Any]  = None,   # VisualDataReader for per-tab scanning
        source_window:     str            = "",     # title fragment of source data window
        task_plugin:       Optional[Any]  = None,   # TaskPlugin for task-specific logic
        pure_transformer:  bool           = False,  # skip all hardcoded handlers; transformer+LLM only
        disable_auto_handlers: bool       = False,  # skip legacy heuristics but KEEP LLM+transformer merge
        start_tab_idx:     int            = 0,      # start agent at this tab index (drill testing)
        scope:             Optional[Any]  = None,   # ScopeConfig — app-specific tabs/sections/records
        observer:          Optional[Any]  = None,   # perception adapter (snapshot()→schema); default=UIA
        data_source:       Optional[Any]  = None,   # DataSource for field values; default=Notepad
        route_capsule:     bool           = True,   # False = use model_path as-is (skip capsule router)
        correction_watch_seconds: float   = 4.0,    # DAgger: how long to watch for a human correction after
                                                     # a failed step. 0 = disabled. Default preserves the
                                                     # original DAgger-collection behavior; verification/
                                                     # unattended runs should pass a much smaller value —
                                                     # this blocks in real time and no one's there to correct.
    ):
        # Per-application config (tabs, sections, record delimiter). Default =
        # fully generic: no tabs, no sections, no assumptions. Each scope passes
        # its own; the agent code stays application-blind.
        try:
            from agent.scope import ScopeConfig
        except ImportError:
            from components.agent.scope import ScopeConfig
        self._scope = scope if scope is not None else ScopeConfig()
        self.goal       = goal
        self.provider   = provider.lower().strip()
        # Capsule routing: if a registered capsule matches goal/window, use its model.
        # Skipped when route_capsule=False (caller passed an explicit model_path).
        if route_capsule:
            try:
                from agent.capsule import CapsuleRegistry
                _reg = CapsuleRegistry()
                _window = ""  # window title not known yet at init; re-route on first observe
                _routed = _reg.route(goal, _window, fallback=model_path)
                if _routed != model_path:
                    import logging as _lg
                    _lg.getLogger(__name__).info(
                        "Capsule router: matched '%s' → %s", goal[:60], _routed)
                model_path = _routed
            except Exception:
                pass
        self.model_path = model_path
        # Resolve relative model path against repo root so CWD doesn't matter
        if not os.path.isabs(self.model_path):
            _agent_dir = os.path.dirname(os.path.abspath(__file__))
            _repo_root = os.path.normpath(os.path.join(_agent_dir, "..", ".."))
            _abs_mp    = os.path.normpath(os.path.join(_repo_root, self.model_path))
            if os.path.exists(_abs_mp):
                self.model_path = _abs_mp
        # Fail fast — a missing capsule means every step silently runs as pure LLM with no spatial
        # grounding. Surface this at startup so it's never confused with a real run.
        if provider != "none" and not os.path.exists(self.model_path):
            raise FileNotFoundError(
                f"\n\n{'!'*60}\n"
                f"  CAPSULE NOT FOUND: {self.model_path}\n"
                f"  Train the model first:  python -m components.intelligence.model.transformer "
                f"--mode train --data_dir data/output/traces/live\n"
                f"{'!'*60}\n"
            )
        self.dry_run    = dry_run
        self.max_steps  = max_steps
        self.step_delay = step_delay
        self.llm_every  = max(1, llm_every)
        self.device_str = device_str

        _model = model_id or _DEFAULT_MODELS.get(self.provider, "")
        self._llm_client: Optional[Any] = None
        self._llm_model:  str           = _model

        self._init_provider(api_key or "", lmstudio_url)

        # ── Sub-components ────────────────────────────────────────────────────
        from agent.executor import ActionExecutor, _TextResolver, _snap_to_element
        self._snap = _snap_to_element
        try:
            from components.observers.ui_observer import UIAutomationObserver
        except ImportError:
            from observers.ui_observer import UIAutomationObserver

        try:
            from components.agent.state_validator import StateValidator
            from components.recorder.correction_handler import CorrectionHandler
            from components.agent import navigation_protocol as _navproto
        except ImportError:
            from agent.state_validator import StateValidator
            from recorder.correction_handler import CorrectionHandler
            from agent import navigation_protocol as _navproto
        self._navproto = _navproto

        self._executor          = ActionExecutor(dry_run=dry_run)
        self._text_resolver     = _TextResolver()
        # Perception is an injectable adapter (the seam). Any observer whose
        # snapshot() conforms to observers/schema.py plugs in here — UIA now,
        # Excel/web later — with zero agent changes. Default = UIA.
        # NOTE: the observer config MUST match the recorder's, or inference states
        # are out-of-distribution vs training and the transformer predicts garbage
        # (recorder uses background_apps={notepad,.txt} + default limits).
        if observer is not None:
            self._observer = observer
        else:
            try:
                self._observer  = UIAutomationObserver(
                    background_apps={"notepad", ".txt"},
                )
            except TypeError:
                self._observer  = UIAutomationObserver(
                    max_elements_per_window=300, max_total_elements=700,
                )
        self._schema_checked = False   # validate the adapter once, on first observe
        self._validator         = StateValidator()
        self._correction        = CorrectionHandler()
        self._correction_watch_seconds = correction_watch_seconds
        self._record_num: int               = record_num
        # 'attempted' state-feature (inference side): identities of fields acted on
        # this session, fed to the transformer so it stops re-targeting them (the
        # principled fix for empty-optional-field loops). Reset per record.
        self._attempted_keys: set            = set()
        self._attempted_record_num: int      = record_num
        # Form-window lock: captured on the first observe (the window the user
        # clicks at "GO"), re-asserted foreground every step so a stray click can
        # never drift focus into another window (PowerShell/Notepad) and cascade
        # wrong observations/clicks/scrolls. Also the live viewport for scroll.
        self._locked_hwnd: Optional[int]     = None
        self._locked_title: str              = ""
        self._visual_cache: Dict[str, str]  = visual_cache or {}   # Gemini pre-scan data
        self._visual_reader: Optional[Any]  = visual_reader        # VisualDataReader instance
        self._source_window: str            = source_window        # Notepad/source window title
        # Data source = where field VALUES come from. Injectable adapter (the seam):
        # NotepadDataSource now; Excel/web/email plug into the same slot. The agent
        # reads source-specific I/O (read_full_text/peek) only through self._source.
        self._source: Any = data_source if data_source is not None \
            else NotepadDataSource(source_window)
        self._history:        List[Dict[str, Any]] = []
        self._results:        List[Dict[str, Any]] = []
        self._heuristic_steps: int                 = 0
        self._checked_fields: set           = set()   # checkboxes already clicked this run
        self._filled_this_tab: set          = set()   # edit fields filled on current tab (prevents re-fill on cycling)
        self._nochange_click_pos: set       = set()   # (rx,ry) click positions that gave no_change this tab
        # (rx,ry) of the tab-strip button for whichever tab _try_advance_tab most
        # recently, deliberately left as "exhausted" — found live 2026-08-07: the
        # transformer's OWN pointer clicked straight back onto it the very next
        # step, 12-13 times in one run (Vehicle <-> Coverage), each cycle costing
        # ~4 steps of pure tug-of-war between the system's "move on" decision and
        # the model's "go back" decision. Bounded to at most one entry — cleared
        # and replaced (not accumulated) on every new advance — so this only ever
        # blocks an IMMEDIATE reversal of the last deliberate move, never
        # permanently walls off a tab from legitimate later revisits.
        self._advance_blacklist_pos: set    = set()
        self._current_tab_idx: int          = start_tab_idx  # tracks which tab we're on
        self._guidance: str = ""
        self._task_name: str = ""   # set via run(task_name=...)
        self._cached_record: Dict[str, str] = {}     # full parsed record from Notepad (bypasses 2000-char UIA cap)
        self._ocr_cache: Dict[str, Any] = {}        # instance-level OCR cache (clears per record)

        self._pure_transformer: bool = pure_transformer
        self._no_autohandlers:  bool = disable_auto_handlers

        # ── Task plugin (optional — encapsulates task-specific logic) ───────────
        self._task_plugin: Optional[Any] = task_plugin
        if task_plugin is not None:
            # Wire the executor and observe function into the plugin
            if hasattr(task_plugin, "_executor") and task_plugin._executor is None:
                task_plugin._executor = self._executor
            elif not hasattr(task_plugin, "_executor"):
                task_plugin._executor = self._executor
            task_plugin._observe_fn = self._observe
            # Sync record-level state to plugin if it wasn't set at construction time
            if hasattr(task_plugin, "_record_num") and task_plugin._record_num == 1 and record_num != 1:
                task_plugin._record_num = record_num
            # Sync visual state
            if hasattr(task_plugin, "_visual_cache") and not task_plugin._visual_cache and visual_cache:
                task_plugin._visual_cache = dict(visual_cache)
            if hasattr(task_plugin, "_visual_reader") and task_plugin._visual_reader is None and visual_reader:
                task_plugin._visual_reader = visual_reader
            if hasattr(task_plugin, "_source_window") and not task_plugin._source_window and source_window:
                task_plugin._source_window = source_window

        # EXPERIMENTAL — OCR overlay via VisionObserver
        # Disabled by default; enable with use_ocr=True in LLMAgent(...)
        self._use_ocr: bool = use_ocr
        self._vision_observer: Optional[Any] = None
        if use_ocr:
            try:
                try:
                    from components.observers.vlm.vision_observer.vision_observer import VisionObserver
                except ImportError:
                    from observers.vlm.vision_observer.vision_observer import VisionObserver
                self._vision_observer = VisionObserver()
                logger.info("OCR overlay enabled (VisionObserver loaded)")
            except Exception as exc:
                logger.warning("OCR overlay requested but VisionObserver unavailable: %s", exc)

        # ── Load persistent task spec → inject into system prompt ─────────────
        self._system_prompt: str = _SYSTEM_PROMPT
        _mp = self.model_path
        if not os.path.isabs(_mp):
            _mp = os.path.join(os.getcwd(), _mp)
        _spec_path = os.path.normpath(os.path.join(os.path.dirname(_mp), "ruleset.md"))
        if os.path.exists(_spec_path):
            try:
                _spec = open(_spec_path, encoding="utf-8").read().strip()
                if _spec:
                    self._system_prompt = (
                        _SYSTEM_PROMPT
                        + "\n\n## TASK SPECIFICATION (learned from demonstrations)\n"
                        + _spec
                    )
                    logger.info("LLMAgent: loaded task spec from %s", _spec_path)
            except Exception as _se:
                logger.warning("LLMAgent: failed to load task spec: %s", _se)

    # ── provider initialisation ───────────────────────────────────────────────

    def _init_provider(self, api_key: str, lmstudio_url: str) -> None:
        p = self.provider

        if p == "anthropic":
            if not _ANTHROPIC_OK:
                logger.warning("anthropic package not installed — falling back to transformer-only.")
                return
            key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                logger.warning("No Anthropic API key — falling back to transformer-only.")
                return
            self._llm_client = _anthropic.Anthropic(api_key=key)
            logger.info("LLMAgent: provider=anthropic  model=%s", self._llm_model)

        elif p == "groq":
            if not _GROQ_OK:
                logger.warning("groq package not installed — falling back to transformer-only.")
                return
            key = api_key or os.environ.get("GROQ_API_KEY", "")
            if not key:
                logger.warning("No Groq API key — falling back to transformer-only.")
                return
            self._llm_client = _Groq(api_key=key)
            logger.info("LLMAgent: provider=groq  model=%s", self._llm_model)

        elif p == "gemini":
            if not _GEMINI_OK:
                logger.warning("google-generativeai not installed — falling back to transformer-only.")
                return
            key = api_key or os.environ.get("GEMINI_API_KEY", "")
            if not key:
                logger.warning("No Gemini API key — falling back to transformer-only.")
                return
            self._llm_client = _genai.Client(api_key=key)
            logger.info("LLMAgent: provider=gemini  model=%s", self._llm_model)

        elif p == "lmstudio":
            if not _OPENAI_OK:
                logger.warning("openai package not installed — falling back to transformer-only.")
                return
            self._llm_client = _OpenAI(base_url=lmstudio_url, api_key="lm-studio")
            logger.info("LLMAgent: provider=lmstudio  url=%s  model=%s", lmstudio_url, self._llm_model)

        elif p == "none":
            logger.info("LLMAgent: provider=none — transformer-only mode.")

        else:
            logger.warning("Unknown provider %r — transformer-only.", p)

    # ── public API ────────────────────────────────────────────────────────────

    def run(
        self,
        max_steps:  Optional[int] = None,
        task_name:  str           = "",
    ) -> List[Dict[str, Any]]:
        n = max_steps if max_steps is not None else self.max_steps
        self._task_name = task_name
        logger.info(
            "LLMAgent.run() — goal=%r  provider=%s  max_steps=%d  dry_run=%s",
            self.goal, self.provider, n, self.dry_run,
        )

        # ── timing: run-level ────────────────────────────────────────────────
        import datetime as _dt
        _run_start_ts             = time.time()
        self._run_start_ts        = _run_start_ts
        self._run_start_iso       = _dt.datetime.now().isoformat()
        self._run_duration_sec    = 0.0
        self._time_to_first_action_sec: Optional[float] = None
        self._manual_interventions = 0

        # Catch crashes in background threads (OCR, recorder, etc.) so they're visible
        import threading as _threading, traceback as _tb_mod
        def _thread_exc_hook(args):
            print(f"\n=== BACKGROUND THREAD CRASH ({args.thread.name}) ===\n"
                  + "".join(_tb_mod.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
                  flush=True)
            logger.error("Background thread %s crashed:\n%s", args.thread.name,
                         "".join(_tb_mod.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
        _threading.excepthook = _thread_exc_hook

        _stuck_pos:   Optional[tuple] = None
        _stuck_count: int             = 0
        _STUCK_LIMIT: int             = 3
        _llm_click_pos:   Optional[tuple] = None
        _llm_click_count: int             = 0
        _LLM_CLICK_LIMIT: int             = 1   # force type after this many repeated clicks
        _no_change_streak: int            = 0
        _NO_CHANGE_LIMIT:  int            = 4   # advance tab after this many consecutive no_change
        _last_focused_id:  Optional[str]  = None  # track focus changes to reset streak
        _open_combobox_label: str         = ""  # label of combobox whose dropdown is currently open
        _tab_just_switched: bool          = True   # True on first step to focus first empty field
        _last_auto_step:   int            = -1  # step_idx of last step where an auto-handler fired
        _DROUGHT_LIMIT:    int            = 10  # scroll form after this many non-fill steps in a row
        _tab_scroll_count: int            = 0   # scrolls performed on current tab
        _MAX_TAB_SCROLLS:  int            = 6   # max drought-scrolls per tab before giving up
        _steps_on_tab:     int            = 0   # steps spent on current tab — forces advance if too many
        _TAB_STEP_LIMIT:   int            = 40  # force tab advance after this many steps on same tab
        _pane_escape_last_field: str      = ""  # last field pane-escape tried to click
        _pane_escape_streak:     int      = 0   # consecutive tries on the same field without escaping
        _confirmed_blank_fields: set      = set()  # fields where peek found no value → treat as blank
        _heuristic_steps:        int      = 0      # steps decided by auto-handlers (not LLM/transformer)
        _manual_interventions:   int      = 0      # DAgger corrections the human had to make — cognitive-load proxy
        _llm_unavailable_streak: int      = 0      # consecutive "llm unavailable" — infra failure, NOT "value is blank"
        _LLM_UNAVAILABLE_LIMIT:  int      = 3       # halt rather than silently Tab-skip the whole form

        _record_cache_loaded     = False
        _tc_advance_verified     = False   # True once the full top→bottom scan passes before advance/submit
        _prev_elem_count:    int = 0       # element count from previous step — spike = unexpected dialog

        # Repeat-action detector — fingerprint last N actions; identical streak → Tab out
        from collections import deque as _deque
        _action_history: _deque = _deque(maxlen=6)
        _REPEAT_LIMIT: int = 3

        for step_idx in range(n):
          try:
            _step_t0 = time.time()
            # 1. Observe — but first re-assert the locked form as foreground so a
            # stray click last step can't leave us observing/acting on a drifted
            # window. Lock is captured on the first observe (form is in front at GO).
            self._reassert_form_window()
            state      = self._observe()
            _t_observed = time.time()
            observe_time_sec = _t_observed - _step_t0
            if self._locked_hwnd is None:
                self._lock_form_window(state)
            llm_action: Dict[str, Any] = {}
            _steps_on_tab += 1
            _cur_elem_count = len(state.get("elements", []))
            logger.info("── Step %d/%d  (%d elements) ──", step_idx + 1, n, _cur_elem_count)

            # Dialog guard: large element-count spike → unexpected dialog opened → Escape
            if _prev_elem_count > 0 and _cur_elem_count - _prev_elem_count > 100:
                logger.warning("Element count spiked %d→%d — unexpected dialog detected, pressing Escape.",
                               _prev_elem_count, _cur_elem_count)
                self._executor.execute({"action_type": "hotkey", "keys": ["escape"]})
                time.sleep(self.step_delay)
                _prev_elem_count = 0
                continue
            _prev_elem_count = _cur_elem_count

            # Load record cache on first step
            if not _record_cache_loaded:
                self._refresh_record_cache(state)
                _record_cache_loaded = True

            # ── Task plugin delegation ────────────────────────────────────────
            # When a TaskPlugin is registered it takes over all task-specific
            # auto-handler blocks (tab switching, auto-fill, auto-skip, etc.).
            # The plugin returns (handled, should_continue):
            #   (True, True)  → plugin handled this step; loop continues
            #   (True, False) → plugin handled and signals task done; loop breaks
            #   (False, False) → not handled; fall through to transformer/LLM below
            if self._task_plugin is not None:
                _p_handled, _p_continue = self._task_plugin.handle_step(state, step_idx)
                if _p_handled:
                    # Sync no_change_streak from plugin back to local tracking
                    if hasattr(self._task_plugin, "_no_change_streak"):
                        _no_change_streak = self._task_plugin._no_change_streak
                    if not _p_continue:
                        break
                    continue
                # Not handled — fall through to transformer/LLM below

            # When a plugin is active OR pure-transformer mode, skip all legacy
            # form-specific auto-handlers and fall through to transformer/LLM only.
            _plugin_active = (self._task_plugin is not None) or self._pure_transformer or self._no_autohandlers

            # In pure_transformer mode: scroll to top + click first field on tab switch
            # (same as the non-plugin handler below, but without auto-fill logic)
            if self._pure_transformer and _tab_just_switched:
                _tab_just_switched  = False
                _steps_on_tab       = 0
                _tab_scroll_count   = 0
                _no_change_streak   = 0
                _last_auto_step     = step_idx
                if self._visual_reader:
                    self._scan_tab_visual(state)
                self._scroll_form_to_top(state)
                time.sleep(0.6)
                state = self._observe()
                # Click the topmost-left interactive field (by bbox Y then X).
                # Excludes tab-strip elements (tabitemcontrol) which sit at the top
                # of the window but are not form fields.
                _candidates = sorted(
                    [e for e in state.get("elements", [])
                     if e.get("type") in ("editcontrol", "comboboxcontrol", "checkboxcontrol")
                     and e.get("window_role") != "background"
                     and e.get("bbox") and e.get("enabled", True)],
                    key=lambda e: (e["bbox"][1], e["bbox"][0])
                )
                if _candidates:
                    _fe = _candidates[0]
                    _fx1, _fy1, _fx2, _fy2 = _fe["bbox"]
                    self._executor.execute({"action_type": "click",
                                            "click_position": [(_fx1+_fx2)/2, (_fy1+_fy2)/2]})
                    time.sleep(self.step_delay * 0.5)
                continue

            # 1a. After a tab switch: scroll to top, re-observe, then click first empty field
            if not _plugin_active and _tab_just_switched:
                _tab_just_switched    = False
                _steps_on_tab         = 0
                _tab_scroll_count     = 0
                _no_change_streak     = 0
                _last_auto_step       = step_idx  # treat tab switch as an auto step
                _pane_escape_last_field = ""
                _pane_escape_streak     = 0
                self._filled_this_tab.clear()     # new tab — reset filled-field tracking
                self._nochange_click_pos.clear()  # new tab — reset failed-click blacklist
                _confirmed_blank_fields.clear()   # new tab — reset peek-confirmed-blank set
                _tc_advance_verified = False      # new tab — reset full-scan gate
                # Visual scan: bring Notepad to foreground, scroll to this tab's section,
                # capture screenshots with Groq vision to read all field values for this tab.
                self._scan_tab_visual(state)
                self._scroll_form_to_top(state)
                time.sleep(0.6)
                state = self._observe()   # get updated positions after scroll
                # Click whichever field is topmost on-screen after scrolling up —
                # position-based, so it naturally lands on the first visible field.
                self._focus_first_empty_field(state)
                time.sleep(self.step_delay)
                continue   # re-observe so auto-handlers run first before LLM gets a chance

            # 1b. Stuck guard: if no_change repeats OR too many steps on same tab, advance.
            # DISABLED when disable_auto_handlers — we want to see the pure
            # transformer with no rescue (honest navigation test).
            _stuck = ((not self._no_autohandlers)
                      and (_no_change_streak >= _NO_CHANGE_LIMIT
                           or (not _plugin_active and _steps_on_tab >= _TAB_STEP_LIMIT)))
            if _stuck:
                if _steps_on_tab >= _TAB_STEP_LIMIT:
                    logger.info("Stuck guard: %d steps on tab — forcing advance.", _steps_on_tab)
                elif _no_change_streak >= _NO_CHANGE_LIMIT:
                    # Before escalating to a full tab-advance, Tab past the stuck field.
                    # This lets multi-section tabs (Driver 1/2/3) continue past a single
                    # problematic checkbox instead of jumping to the next tab entirely.
                    _foc_id = state.get("focused_element_id")
                    _foc_el = next((e for e in state.get("elements", [])
                                    if e.get("element_id") == _foc_id), None)
                    _foc_nm_bare = ((_foc_el.get("label") or _foc_el.get("text") or "?")
                                    if _foc_el else "?")
                    _foc_nm_sec  = self._detect_section(state, _foc_el) if _foc_el else ""
                    _foc_nm      = f"{_foc_nm_sec} {_foc_nm_bare}" if _foc_nm_sec else _foc_nm_bare
                    logger.info("Stuck guard: no_change x%d on %r — Tab past field.",
                                _no_change_streak, _foc_nm)
                    if _foc_nm_bare and _foc_nm_bare != "?":
                        self._filled_this_tab.add(_foc_nm)
                    self._executor.execute({"action_type": "keyboard",
                                            "key_count": 1, "keystrokes": ["tab"]})
                    _no_change_streak = 0
                    _last_auto_step   = step_idx
                    time.sleep(self.step_delay * 0.5)
                    continue
                if self._try_advance_tab(state):
                    _no_change_streak  = 0
                    _tab_just_switched = True
                    _tab_scroll_count  = 0
                    _last_auto_step    = step_idx
                    self._refresh_record_cache(state)
                    time.sleep(self.step_delay)
                    continue
                else:
                    logger.warning("Stuck guard: no next tab found — resetting streak.")
                    _no_change_streak = 0
                    _steps_on_tab     = 0

            # 1c. Universal tab-complete: when visible fields are all handled, do a
            # full top→bottom scan to confirm nothing is hiding above or below the
            # current viewport before advancing to the next tab or submitting.
            # A helper that checks pending edit/combobox fields in a given state.
            def _tc_key(e, _st=state):
                _fn  = (e.get("label") or e.get("text") or "").strip()
                _sec = self._detect_section(_st, e)
                return f"{_sec} {_fn}" if _sec else _fn
            def _tc_has_pending(_st):
                _fl = {s.lower() for s in self._filled_this_tab}
                _cb_lower = {s.lower() for s in _confirmed_blank_fields}
                for _e in _st.get("elements", []):
                    if (_e.get("window_role") == "background"
                            or _e.get("type") not in ("editcontrol", "comboboxcontrol")
                            or not _e.get("enabled", True)):
                        continue
                    # Inactive tab panels have negative screen coordinates in wxPython.
                    # Skip off-screen elements — they belong to other tabs, not this one.
                    if _e.get("bbox") and _e["bbox"][1] < 0:
                        continue
                    _fn = (_e.get("label") or _e.get("text") or "").strip()
                    _sec = self._detect_section(_st, _e)
                    _fk  = (f"{_sec} {_fn}" if _sec else _fn).lower()
                    if _fk in _fl or _fn.lower() in _fl:
                        continue
                    if _fk in _cb_lower or _fn.lower() in _cb_lower:
                        continue  # peeked and confirmed blank — skip
                    # Skip fields in a section that has no data at all (e.g. Driver 3 when
                    # record has only 2 drivers).  Without this check _lookup_field falls
                    # back to the bare key and returns the wrong value (e.g. Policyholder's
                    # First Name instead of Driver 3 First Name), making the section appear
                    # pending even though it doesn't exist.
                    if _sec and self._cached_record:
                        _sec_lower = _sec.lower()
                        _fn_key_tc = _sec_lower + " first name"
                        _fn_val_tc = next((rv for rk, rv in self._cached_record.items()
                                           if rk.lower() == _fn_key_tc), "")
                        _skip_pl_tc = {"(none)", "none", "(leave blank)", "n/a"}
                        if not (_fn_val_tc and _fn_val_tc.lower().strip("()") not in _skip_pl_tc):
                            continue  # section has no real person — not pending
                    _known = self._lookup_field(_fn, section=_sec)
                    _val   = (_e.get("value") or "").strip()
                    if _known:
                        _skip_vals = {"(none)", "none", "(leave blank)", "n/a"}
                        if _known.lower().strip("()") in _skip_vals:
                            continue  # field should be blank — not pending
                        # Pending only if empty OR value doesn't match expected
                        if not _val or _val.lower() != _known.lower():
                            return True
                        # Current value already matches expected — not pending
                        continue
                    # Empty editcontrol/combobox with unknown value — may still need filling via peek
                    if (not _val and _e.get("type") in ("editcontrol", "comboboxcontrol")):
                        return True
                # Also check unchecked checkboxes that should be checked (or are unknown)
                for _chk in _st.get("elements", []):
                    if (_chk.get("window_role") == "background"
                            or _chk.get("type") != "checkboxcontrol"
                            or not _chk.get("enabled", True)):
                        continue
                    if _chk.get("bbox") and _chk["bbox"][1] < 0:
                        continue
                    _nm = (_chk.get("label") or _chk.get("text") or "").strip()
                    if _nm in self._checked_fields:
                        continue
                    if _nm.lower() in _cb_lower:
                        continue  # confirmed blank after peek
                    _exp = self._lookup_field(_nm)
                    if _exp and _exp.lower().strip().startswith("yes"):
                        return True
                    # Unknown checkbox: no data → not blocking tab advance
                return False

            if not _plugin_active and self._filled_this_tab and not _tc_has_pending(state):
                # Visible fields look done — do a full top→bottom scan before acting
                if not _tc_advance_verified:
                    _tcav_state = state
                    _tcav_found = False
                    # 1. Scroll to top and check for missed fields above viewport
                    self._scroll_form_to_top(_tcav_state)
                    time.sleep(0.4)
                    _tcav_state = self._observe()
                    for _tcav_i in range(6):   # up to 6 scroll-down passes
                        if _tc_has_pending(_tcav_state):
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
                            state        = _tcav_state
                            _tcav_found  = True
                            _tc_advance_verified = False
                            self._focus_first_empty_field(_tcav_state)
                            time.sleep(self.step_delay)
                            break
                        self._scroll_form_down(_tcav_state)
                        time.sleep(0.25)
                        _tcav_state = self._observe()
                    if _tcav_found:
                        continue   # back to main loop to fill found fields
                    _tc_advance_verified = True   # full scan passed — safe to advance/submit

                # Full scan verified — advance tab or submit
                if self._try_advance_tab(state):
                    logger.info("Tab complete: all fields done — advancing to next tab.")
                    _no_change_streak    = 0
                    _tab_just_switched   = True
                    _tab_scroll_count    = 0
                    _last_auto_step      = step_idx
                    _tc_advance_verified = False
                    self._filled_this_tab.clear()
                    _confirmed_blank_fields.clear()
                    self._refresh_record_cache(state)
                    time.sleep(self.step_delay)
                    continue
                else:
                    # Last tab — click Submit via UIA (button may be scrolled off-screen)
                    _submitted = False
                    try:
                        import uiautomation as _uia_sub
                        for _btn_name in ("Submit  New", "Submit & New", "Submit"):
                            _btn = _uia_sub.ButtonControl(Name=_btn_name, searchDepth=8)
                            if _btn.Exists(maxSearchSeconds=0.5):
                                logger.info("Tab complete: last tab — UIA click Submit %r", _btn_name)
                                _btn.Click()
                                _submitted = True
                                break
                    except Exception as _sub_exc:
                        logger.warning("UIA Submit click failed: %s", _sub_exc)
                    if not _submitted:
                        # Fallback: coordinate click from element list
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
                        break

            # 1b. Pane-focus escape: if focus is on a section header / container (panecontrol),
            # Tab will do nothing. Jump directly to the first empty edit field instead.
            _focused_id  = state.get("focused_element_id")
            _focused_el  = next((e for e in state.get("elements", []) if e.get("element_id") == _focused_id), None)
            if not _plugin_active and _focused_el and _focused_el.get("type") == "panecontrol":
                _pane_label = (_focused_el.get("label") or _focused_el.get("text") or "?")[:40]
                _pane_y     = _focused_el["bbox"][1] if _focused_el.get("bbox") else 0
                # If this pane is a section_ with no data in the record (e.g. Driver 3 when
                # record has only 2 drivers), don't escape into it — advance tab instead.
                # Read the section directly from the pane's own label rather than calling
                # _detect_section (which finds panes ABOVE the element, not the element itself).
                import re as _pre
                _pane_raw = (_focused_el.get("label") or _focused_el.get("text") or "").lower()
                _pm = _pre.match(r"section_(driver|vehicle)_(\d+)$", _pane_raw)
                _pane_sec = f"{_pm.group(1).title()} {_pm.group(2)}" if _pm else ""
                if _pane_sec and self._cached_record:
                    _ps_lower = _pane_sec.lower()
                    # A section exists only if its First Name field has a real value.
                    # Boolean fields like "SR-22 Required: No" are present for every
                    # section and must not be used as presence indicators.
                    _fn_key   = _ps_lower + " first name"
                    _fn_val   = next((rv for rk, rv in self._cached_record.items()
                                      if rk.lower() == _fn_key), "")
                    _skip_pl  = {"(none)", "none", "(leave blank)", "n/a"}
                    _sec_has_real = bool(_fn_val and _fn_val.lower().strip("()") not in _skip_pl)
                    if not _sec_has_real:
                        logger.info("Pane-escape: section %r has no real data — advancing tab.", _pane_sec)
                        if self._try_advance_tab(state):
                            _no_change_streak  = 0
                            _tab_just_switched = True
                            _tab_scroll_count  = 0
                            _last_auto_step    = step_idx
                            self._filled_this_tab.clear()
                            _confirmed_blank_fields.clear()
                            self._refresh_record_cache(state)
                            time.sleep(self.step_delay)
                        _heuristic_steps += 1; continue
                logger.info("Focus on pane %r — clicking first empty field to escape.", _pane_label)
                # Peek at which field we'd click before actually clicking it.
                # If we've clicked the same field 3+ times with no escape (loop!), scroll down first.
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
                _pane_next_el = _pane_candidates[0] if _pane_candidates else None
                _pane_next_name = (_pane_next_el.get("label") or _pane_next_el.get("text") or "").strip() if _pane_next_el else ""
                if _pane_next_name and _pane_next_name == _pane_escape_last_field:
                    _pane_escape_streak += 1
                else:
                    _pane_escape_streak = 1
                    _pane_escape_last_field = _pane_next_name
                if _pane_escape_streak >= 3:
                    logger.warning("Pane-escape: stuck on %r for %d tries — scrolling down to reveal it.",
                                   _pane_next_name, _pane_escape_streak)
                    self._scroll_form_down(state)
                    time.sleep(self.step_delay * 0.5)
                    _pane_escape_streak = 0  # reset after scroll so we try click once fresh
                    _heuristic_steps += 1; continue
                # Press Tab instead of UIA SetFocus — the form's own tab order
                # reliably lands on the first field inside the section pane.
                # UIA SetFocus can bounce back to the previous section when the
                # target field hasn't been scrolled into the form's active region.
                self._executor.execute({"action_type": "keyboard",
                                        "key_count": 1, "keystrokes": ["tab"]})
                time.sleep(self.step_delay * 0.5)
                _heuristic_steps += 1; continue
                # No unhandled field found in this pane — fall through so the
                # universal tab-complete check (above) or LLM handles next steps.

            # 1c. Button-focus escape: if focus landed on a button but there are still
            # pending fields, seek the first unfilled field directly instead of waiting
            # for the LLM to figure out what to do (avoids 10-step wait-loop).
            if (not _plugin_active and _focused_el
                    and _focused_el.get("type") in {
                        "button", "buttoncontrol", "splitbutton", "splitbuttoncontrol",
                        "hyperlinkcontrol", "link",
                        "tabitem", "tabitemcontrol"}
                    and _tc_has_pending(state)):
                _btn_lbl = (_focused_el.get("label") or _focused_el.get("text") or "").strip()
                logger.info("Button-focus escape: focus on non-input %r but fields still pending "
                            "— seeking first unfilled field.", _btn_lbl)
                if self._focus_first_empty_field(state):
                    _no_change_streak = 0
                    time.sleep(self.step_delay * 0.5)
                    _heuristic_steps += 1; continue
                # No empty edit/combobox visible — try unchecked checkboxes directly
                _chk_pending = [
                    e for e in state.get("elements", [])
                    if e.get("type") == "checkboxcontrol"
                    and e.get("window_role") != "background"
                    and e.get("enabled", True)
                    and (e.get("label") or e.get("text") or "").strip() not in self._checked_fields
                ]
                _chk_acted = False
                for _chk_e in _chk_pending:
                    _chk_nm = (_chk_e.get("label") or _chk_e.get("text") or "").strip()
                    _exp_v  = self._lookup_field(_chk_nm)
                    if _exp_v and _exp_v.lower().strip().startswith("yes"):
                        _bbox = _chk_e.get("bbox")
                        if _bbox:
                            try:
                                import win32gui as _wgb; import win32api as _wab
                                _cx2 = (_bbox[0] + _bbox[2]) / 2
                                _cy2 = (_bbox[1] + _bbox[3]) / 2
                                _hw2 = _wgb.WindowFromPoint((int(_cx2), int(_cy2)))
                                if _hw2:
                                    _wab.SendMessage(_hw2, 0x00F1, 1, 0)
                                    logger.info("Button-escape: checked checkbox %r via BM_SETCHECK.", _chk_nm)
                                    self._checked_fields.add(_chk_nm)
                                    self._filled_this_tab.add(_chk_nm)
                                    _chk_acted = True
                            except Exception as _bce:
                                logger.warning("Button-escape checkbox BM_SETCHECK failed: %s", _bce)
                if _chk_acted:
                    time.sleep(self.step_delay * 0.5)
                    _heuristic_steps += 1; continue
                # Scroll down to reveal hidden fields
                if self._scroll_form_down(state):
                    _tab_scroll_count += 1
                    _last_auto_step    = step_idx
                    time.sleep(self.step_delay * 0.5)
                    _heuristic_steps += 1; continue
                # All seeks exhausted — force Tab past the button, never give it to LLM
                logger.info("Button-escape: all seeks failed — forcing Tab past %r.", _btn_lbl)
                self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
                time.sleep(self.step_delay * 0.5)
                _heuristic_steps += 1; continue

            # 1z. Already-handled guard: focus bounced back to a field we already filled.
            #     Tab past without calling LLM — prevents LLM from overwriting correct values.
            if not _plugin_active:
                _fid_ah = state.get("focused_element_id")
                _fe_ah  = next((e for e in state.get("elements", [])
                                if e.get("element_id") == _fid_ah), None)
                if _fe_ah and _fe_ah.get("type") in ("editcontrol", "comboboxcontrol"):
                    _fn_ah  = (_fe_ah.get("label") or _fe_ah.get("text") or "").strip()
                    _sec_ah = self._detect_section(state, _fe_ah)
                    _fk_ah  = f"{_sec_ah} {_fn_ah}" if _sec_ah else _fn_ah
                    if _fk_ah in self._filled_this_tab:
                        _foc_val_ah = (_fe_ah.get("value") or "").strip()
                        _exp_ah     = self._lookup_field(_fn_ah, section=_sec_ah)
                        if not _exp_ah or _foc_val_ah.lower() == _exp_ah.lower():
                            logger.info("Already-handled guard: %r already filled (%r) — Tab.",
                                        _fk_ah, _foc_val_ah[:30])
                            self._executor.execute({"action_type": "keyboard",
                                                    "key_count": 1, "keystrokes": ["tab"]})
                            _heuristic_steps += 1; continue

            # 2. Auto-skip: if focused field already has the correct value, Tab past it
            if not _plugin_active and self._llm_client and self._auto_skip(state):
                logger.info("Auto-skip: focused field already has correct value — Tab.")
                self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
                _no_change_streak = 0
                _steps_on_tab     = 0
                # NOTE: _last_auto_step is intentionally NOT reset here.
                # Resetting on auto-skip would mask LLM back-cycling (clicking already-filled
                # fields), preventing the drought guard from firing and scrolling down.
                time.sleep(self.step_delay)
                _heuristic_steps += 1; continue

            # 2a. Auto-fill: if focused field is empty (or has wrong leftover value), type correct value
            auto_text = (self._auto_fill(state) if not _plugin_active else None)
            # 2a-peek: focused empty editcontrol with no cached value — peek Notepad to populate cache.
            # Gated off when disable_auto_handlers — these are task guards (Notepad
            # peek + dup-label fill) that fill fields without the transformer.
            if not auto_text and not self._no_autohandlers:
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
                            and _fn_lower_p not in {s.lower() for s in _confirmed_blank_fields}
                            and _fn_lower_p not in {s.lower() for s in self._filled_this_tab}):
                        logger.info("Auto-fill: '%s' not in cache — peeking Notepad.", _fn_p)
                        self._peek_notepad(state, _fn_p)
                        auto_text = self._auto_fill(state)
                        if not auto_text:
                            # Peek exhausted — no value found; treat as blank and tab past
                            logger.info("Auto-fill: '%s' not found after peek — treating as blank, Tab.",
                                        _fn_p)
                            _confirmed_blank_fields.add(_fn_lower_p)
                            self._filled_this_tab.add(_fk_p)
                            self._executor.execute({"action_type": "keyboard",
                                                    "key_count": 1, "keystrokes": ["tab"]})
                            _no_change_streak = 0
                            _last_auto_step   = step_idx
                            time.sleep(self.step_delay)
                            _heuristic_steps += 1; continue
            # 2a-dup: duplicate UIA label — field is empty but label already in _filled_this_tab.
            # Peek Notepad for the next uncached field after that label and fill it.
            if not auto_text and not self._no_autohandlers:
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
                            and _fn_dup_low not in {s.lower() for s in _confirmed_blank_fields}):
                        logger.info("Dup-label: '%s' already filled this tab — peeking next field.", _fn_dup)
                        _next = self._peek_next_field_after(state, _fn_dup)
                        if _next:
                            _nk, _nv = _next
                            self._cached_record[_nk] = _nv
                            self._visual_cache[_nk]  = _nv
                            logger.info("Dup-label fill: actual field=%r  value=%r  (UIA label=%r)",
                                        _nk, _nv, _fn_dup)
                            self._ensure_form_foreground(state)
                            self._executor.execute({"action_type": "keyboard",
                                                    "key_count": 1, "keystrokes": ["ctrl+a"]})
                            self._executor.execute({
                                "action_type": "keyboard",
                                "key_count": len(_nv),
                                "keystrokes": list(_nv),
                                "text": _nv,
                            })
                            self._executor.execute({"action_type": "keyboard",
                                                    "key_count": 1, "keystrokes": ["tab"]})
                            self._filled_this_tab.add(_nk)
                            _no_change_streak = 0
                            _steps_on_tab     = 0
                            _last_auto_step   = step_idx
                            time.sleep(self.step_delay)
                            _heuristic_steps += 1; continue
                        else:
                            logger.info("Dup-label: no next field found — treating as blank, Tab.")
                            _confirmed_blank_fields.add(_fn_dup_low + "#dup")
                            self._executor.execute({"action_type": "keyboard",
                                                    "key_count": 1, "keystrokes": ["tab"]})
                            _no_change_streak = 0
                            _last_auto_step   = step_idx
                            time.sleep(self.step_delay)
                            _heuristic_steps += 1; continue

            if auto_text:
                field_name_log, text_val, needs_clear = auto_text
                self._peek_notepad(state, field_name_log)
                self._ensure_form_foreground(state)
                if needs_clear:
                    logger.info("Auto-fill (overwrite): '%s' → %r", field_name_log, text_val[:40])
                    self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["ctrl+a"]})
                else:
                    logger.info("Auto-fill: '%s' → %r", field_name_log, text_val[:40])
                self._executor.execute({
                    "action_type": "keyboard",
                    "key_count": len(text_val),
                    "keystrokes": list(text_val),
                    "text": text_val,
                })
                self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
                logger.info("Auto-Tab after auto-fill.")
                _no_change_streak = 0
                _steps_on_tab     = 0
                _last_auto_step   = step_idx
                self._filled_this_tab.add(field_name_log)
                time.sleep(self.step_delay)
                _heuristic_steps += 1; continue

            # 2a2. Auto-check: if focused checkbox should be checked per background data, click it
            auto_chk = (self._auto_check(state) if not _plugin_active else None)
            if auto_chk is not None:
                field_name_log, should_check = auto_chk
                _did_new_check = False
                if should_check:
                    if field_name_log in self._checked_fields:
                        # Already checked this run — don't toggle it off
                        logger.info("Auto-check: '%s' already checked this run — Tab.", field_name_log)
                    else:
                        elements   = state.get("elements", [])
                        focused_id = state.get("focused_element_id")
                        focused_el = next((e for e in elements if e.get("element_id") == focused_id), None)
                        if focused_el and focused_el.get("bbox"):
                            x1, y1, x2, y2 = focused_el["bbox"]
                            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                            # Check if already checked via Win32 BM_GETCHECK
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
                                logger.info("Auto-check: '%s' → clicking @ (%.0f, %.0f)", field_name_log, cx, cy)
                                self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
                                self._checked_fields.add(field_name_log)
                                _did_new_check = True
                        else:
                            logger.warning("Auto-check: '%s' — no bbox, pressing space", field_name_log)
                            self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["space"]})
                            self._checked_fields.add(field_name_log)
                            _did_new_check = True
                else:
                    logger.info("Auto-check: '%s' should remain unchecked — Tab.", field_name_log)
                self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
                _no_change_streak = 0
                if _did_new_check:
                    # Only reset drought/tab counters when a new checkbox was actually clicked.
                    # Revisits (already checked, should-remain-unchecked) let the guards accumulate
                    # so the drought and stuck guards eventually advance the tab.
                    _steps_on_tab   = 0
                    _last_auto_step = step_idx
                time.sleep(self.step_delay)
                _heuristic_steps += 1; continue

            # 2a3. Peek for unknown focused checkbox (not in cache → peek, then re-check)
            _fid_chk = state.get("focused_element_id")
            _fe_chk  = next((e for e in state.get("elements", [])
                             if e.get("element_id") == _fid_chk), None)
            if (not _plugin_active and _fe_chk and _fe_chk.get("type") == "checkboxcontrol"):
                _fn_chk      = (_fe_chk.get("label") or _fe_chk.get("text") or "").strip()
                _fn_chk_low  = _fn_chk.lower()
                if (_fn_chk
                        and not self._lookup_field(_fn_chk)
                        and _fn_chk_low not in {s.lower() for s in _confirmed_blank_fields}
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
                                self._executor.execute({"action_type": "click", "click_position": [cx2, cy2]})
                            else:
                                self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["space"]})
                            self._checked_fields.add(field_name_log2)
                        else:
                            logger.info("Auto-check (post-peek): '%s' should remain unchecked — Tab.", _fn_chk)
                        self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
                        _no_change_streak = 0
                        _last_auto_step   = step_idx
                        time.sleep(self.step_delay)
                        _heuristic_steps += 1; continue
                    else:
                        # Peek found nothing — mark confirmed blank, tab past
                        logger.info("Auto-check: '%s' not found after peek — treating as blank, Tab.", _fn_chk)
                        _confirmed_blank_fields.add(_fn_chk_low)
                        self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
                        _no_change_streak = 0
                        _last_auto_step   = step_idx
                        time.sleep(self.step_delay)
                        _heuristic_steps += 1; continue

            # 2b. Auto-fix combobox: if focused combobox has WRONG value, select correct option
            fix = (self._combobox_needs_fix(state) if not _plugin_active else None)
            if fix:
                field_name, current_val, expected_val = fix
                elements   = state.get("elements", [])
                # Check if dropdown is already open (listitems visible). Standard
                # UIA "ListItem" maps to "listitem" (no "control" suffix) in
                # ui_observer.py's _CTRL_TYPE_MAP -- "listitemcontrol" alone
                # misses every real dropdown using the standard, mapped type.
                listitems = [e for e in elements
                             if e.get("type") in ("listitem", "listitemcontrol")
                             and e.get("window_role") != "background"]
                if listitems:
                    # Find the matching listitem and click it. _option_matches
                    # handles 'Sedan' matching an option like '4-Door Sedan' —
                    # a bare exact-only check here would miss it the same way
                    # the other two combobox handlers did before being fixed.
                    target = next(
                        (e for e in listitems
                         if _option_matches(expected_val, e.get("text") or e.get("label") or "")),
                        None,
                    )
                    if target and target.get("bbox"):
                        x1, y1, x2, y2 = target["bbox"]
                        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                        logger.info("Combobox-fix: clicking listitem %r @ (%.0f,%.0f)",
                                    expected_val, cx, cy)
                        self._executor.execute({"action_type": "click",
                                                "click_position": [cx, cy]})
                        _steps_on_tab   = 0
                        _last_auto_step = step_idx
                        time.sleep(self.step_delay)
                        _heuristic_steps += 1; continue
                    else:
                        logger.warning("Combobox-fix: listitem %r not found in open list", expected_val)
                else:
                    # Dropdown is closed — open it
                    focused_id = state.get("focused_element_id")
                    focused    = next((e for e in elements if e.get("element_id") == focused_id), None)
                    if focused and focused.get("bbox"):
                        x1, y1, x2, y2 = focused["bbox"]
                        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                        self._peek_notepad(state, field_name)
                        logger.info("Combobox-fix: '%s' = %r → need %r — opening dropdown",
                                    field_name, current_val, expected_val)
                        self._executor.execute({"action_type": "click",
                                                "click_position": [cx, cy]})
                        _steps_on_tab   = 0
                        _last_auto_step = step_idx
                        time.sleep(self.step_delay)
                        _heuristic_steps += 1; continue
                        
            _drought = step_idx - _last_auto_step
            if not _plugin_active and _drought >= _DROUGHT_LIMIT:
                if _tab_scroll_count < _MAX_TAB_SCROLLS:
                    logger.info("Drought guard: %d steps without auto-handler — scrolling form (scroll %d/%d).",
                                _drought, _tab_scroll_count + 1, _MAX_TAB_SCROLLS)
                    if self._scroll_form_down(state):
                        _tab_scroll_count += 1
                        _last_auto_step    = step_idx   # reset drought so it doesn't fire next step
                        time.sleep(self.step_delay * 0.75)
                        state = self._observe()          # re-observe so we see newly revealed fields
                        self._focus_first_empty_field(state, after_scroll=True)  # skip already-filled
                        time.sleep(0.3)
                        _heuristic_steps += 1; continue   # re-observe with scrolled form + focused field
                else:
                    # All scroll attempts exhausted — all visible fields are done on this tab.
                    # Proactively advance to the next tab instead of waiting for the stuck guard.
                    logger.info("Drought guard: scrolls exhausted (%d/%d) — advancing tab.",
                                _tab_scroll_count, _MAX_TAB_SCROLLS)
                    if self._try_advance_tab(state):
                        _no_change_streak  = 0
                        _tab_just_switched = True
                        _tab_scroll_count  = 0
                        _last_auto_step    = step_idx
                        self._refresh_record_cache(state)
                        time.sleep(self.step_delay)
                        continue

            # ── Navigation Protocol (runs in EVERY mode) ───────────────────────────
            # System-level responsibility, not a cloned behavior: keep an actionable,
            # empty target visible so the transformer only ever has to decide WHERE
            # among what's on screen, never learn WHEN/HOW FAR to scroll from
            # demonstrations that essentially never scrolled (0/11,062 pre-campaign
            # steps). decide() is a pure function of the state snapshot + how many
            # consecutive scrolls have already failed to reveal anything new on this
            # tab — see components/agent/navigation_protocol.py for the full design
            # rationale and unit tests.
            _nav_vb = self._form_viewport_bottom(state) - 8   # small safety margin, matches prior behavior
            _nav = self._navproto.decide(
                state, _nav_vb, _tab_scroll_count, max_dead_scrolls=2,
                attempted_keys=self._attempted_keys, attempt_key_fn=self._attempt_key,
            )
            if _nav.action == self._navproto.NavAction.WAIT:
                _tab_scroll_count = 0                # actionable field visible → reset cap
            elif _nav.action == self._navproto.NavAction.SCROLL:
                # VERIFIED HOW: scroll ONCE, then check the visible-field signature
                # actually CHANGED. Changed → new content revealed, let the transformer
                # act on it. Unchanged → the view didn't move = we're at the bottom
                # (don't blind-spin) — the NEXT decide() call will see dead_scroll_count
                # cross the threshold and return ADVANCE_TAB. NO SetFocus here — it
                # yanks the view back and fights the scroll (the old 6x spin bug).
                _sig_before = self._navproto.visible_field_signature(state, _nav_vb)
                _scrolled   = self._scroll_form_down(state)
                time.sleep(self.step_delay * 0.6)
                _state_after = self._observe()
                _vb_after    = self._form_viewport_bottom(_state_after)
                if _scrolled and self._navproto.visible_field_signature(_state_after, _vb_after) != _sig_before:
                    logger.info("Navigation Protocol: scroll moved the view — new fields revealed.")
                    state             = _state_after
                    _tab_scroll_count = 0
                    _last_auto_step   = step_idx
                    _heuristic_steps += 1
                    continue
                _tab_scroll_count += 1
                logger.info("Navigation Protocol: view unchanged after scroll (%d) — at bottom of tab.",
                            _tab_scroll_count)
            elif _nav.action == self._navproto.NavAction.ADVANCE_TAB:
                logger.info("Navigation Protocol: %s", _nav.reason)
                if self._try_advance_tab(state):
                    _tab_just_switched = True
                    _tab_scroll_count  = 0
                    _last_auto_step    = step_idx
                    self._refresh_record_cache(self._observe())
                    time.sleep(self.step_delay)
                    continue

            # 2. Transformer always runs — learned behavioral engine
            llm_action = None

            t_pred = self._predict(state)
            t_type = t_pred.get("action_type", "no_op")
            t_conf = t_pred.get("confidence", max(t_pred.get("_scores", {}).values(), default=0.0))
            logger.info("[TRANSFORMER] action=%-8s  conf=%.2f", t_type, t_conf)

            # Routing: the transformer's ACTION-TYPE head collapses (always one
            # class), so we never let it act alone on action choice. The LLM
            # always decides WHAT (action + value); the transformer supplies
            # WHERE (click position) via _merge. Transformer's strength is
            # positioning (click_acc ~0.76), not action selection.
            _HIGH_CONF   = 1.01   # effectively: never skip the LLM
            _MED_CONF    = 0.50

            _decision_maker = "transformer"

            # ── OPTION 2: transformer-navigation + LLM value-oracle ──────────
            # Transformer's PROVEN strength is WHICH field (pointer, click_acc
            # ~0.76); its action-type head is unreliable, so we don't use it.
            # Rule:
            #   - If a fillable field is focused & empty → LLM supplies its value
            #     (one call, the "understanding"), then type it.
            #   - Otherwise → click the transformer's pointer target (navigate to
            #     the next field). The transformer DRIVES navigation.
            if self._no_autohandlers:
                _fid2 = state.get("focused_element_id")
                _fe2  = next((e for e in state.get("elements", [])
                              if e.get("element_id") == _fid2), None)

                # ── OPTION B (DEVELOPERS.md → Decisions): the FOCUSED widget's TYPE
                # decides fill-vs-navigate — NOT the transformer's action-type head
                # (which whipsaws click↔keyboard across retrains). A fillable+empty
                # focused field → FILL it (LLM value); otherwise the transformer's
                # POINTER navigates (click), and when its pointer lands on a tab /
                # button that IS the tab-switch / submit. Navigation — which element,
                # what order, WHEN to switch tabs — stays 100% transformer; only this
                # universal "is the focused thing a fillable empty field?" check
                # replaces the unstable head. Widget mechanics are universal GUI
                # semantics, not form-specific rules.
                _fe2_ty  = (_fe2.get("type") or "").lower() if _fe2 else ""
                _fe2_val = (_fe2.get("value") or "").strip() if _fe2 else ""
                _t_is_type = (_fe2_ty in ("editcontrol", "input", "comboboxcontrol",
                                          "checkboxcontrol", "checkbox")
                              and not _fe2_val)

                if _t_is_type and self._llm_client:
                    # transformer chose to FILL → LLM supplies the value (the WHAT).
                    # LLM owns value-filling per the architecture.
                    llm_action = self._ask_llm(state)

                    if _is_llm_unavailable(llm_action):
                        _llm_unavailable_streak += 1
                        logger.warning(
                            "LLM unavailable (%d/%d) — NOT treating as leave-blank; retrying, not skipping.",
                            _llm_unavailable_streak, _LLM_UNAVAILABLE_LIMIT,
                        )
                        if _llm_unavailable_streak >= _LLM_UNAVAILABLE_LIMIT:
                            logger.error(
                                "LLM unavailable %d times in a row — halting rather than blank-filling "
                                "the rest of the form. Check the provider (e.g. LM Studio's local server).",
                                _llm_unavailable_streak,
                            )
                            break
                        time.sleep(self.step_delay)
                        continue
                    _llm_unavailable_streak = 0

                    if llm_action.get("action_type") == "done":
                        logger.info("LLM: task complete."); break
                    prediction = self._merge(t_pred, t_conf, llm_action, state)
                    _decision_maker = "llm"
                    _flabel = ((_fe2.get("label") or _fe2.get("text") or "?")[:30]
                               if _fe2 else "(no focus)")
                    logger.info("[OPT2] TRANSFORMER chose TYPE → LLM value for '%s' → %r",
                                _flabel, prediction.get("text", "")[:40])

                    # Toggle-avoidance: _merge() can override a "type" decision into
                    # a "click" (comboboxes need a click to open before a value can
                    # be selected). If a dropdown is ALREADY open, executing that
                    # click via the generic path below would CLOSE it instead of
                    # opening it — a click toggles, it doesn't just open (see
                    # _open_dropdown_items' docstring). This is the THIRD place this
                    # exact bug class showed up, found live 2026-08-07: 'Education
                    # Level' and 'Number of Doors' each burned 8-11 steps toggling
                    # open/closed before accidentally landing on the right value
                    # through a different, already-fixed handler. The two dedicated
                    # combobox handlers already guard against this; this is the one
                    # place _merge()'s own override reaches the generic execution
                    # path, bypassing both.
                    if prediction.get("action_type") == "click":
                        _already_open_items = _open_dropdown_items(state.get("elements", []))
                        if _already_open_items:
                            _wanted = (llm_action.get("text") or "").strip()
                            _oh = next((e for e in _already_open_items
                                        if _wanted and _option_matches(_wanted, e.get("text") or e.get("label") or "")),
                                       None)
                            if _oh and _oh.get("bbox"):
                                _ohb = _oh["bbox"]
                                logger.info("[OPT2] Dropdown for %r already open — selecting %r directly "
                                            "instead of re-clicking (would toggle it closed).", _flabel, _wanted)
                                self._executor.execute({"action_type": "click",
                                                        "click_position": [(_ohb[0]+_ohb[2])/2, (_ohb[1]+_ohb[3])/2]})
                                time.sleep(0.25)
                                self._executor.execute({"action_type": "keyboard",
                                                        "key_count": 1, "keystrokes": ["tab"]})
                            else:
                                logger.info("[OPT2] Dropdown for %r already open, no match for %r — "
                                            "Escape instead of re-clicking (would toggle it closed).",
                                            _flabel, _wanted)
                                self._executor.execute({"action_type": "keyboard",
                                                        "key_count": 1, "keystrokes": ["escape"]})
                            time.sleep(self.step_delay * 0.4)
                            continue

                    # Leave-blank guard: if the resolved value means "leave empty"
                    # (record said '(leave blank)' / none / n-a, possibly with a note),
                    # DON'T type the placeholder literally — Tab past + mark attempted.
                    # See _is_leave_blank_prediction for why this must gate on
                    # action_type=="keyboard" first (a live infinite-loop fix).
                    if _is_leave_blank_prediction(prediction):
                        logger.info("[OPT2] %r → leave-blank/empty — Tab past (skip).", _flabel)
                        if _fe2 is not None:
                            self._mark_attempted(_fe2, elements=state.get("elements", []))
                        self._executor.execute({"action_type": "keyboard",
                                                "key_count": 1, "keystrokes": ["tab"]})
                        time.sleep(self.step_delay * 0.4)
                        continue
                else:
                    # Transformer pointer navigates to the next field
                    _decision_maker = "transformer"
                    _pos2 = t_pred.get("click_position")
                    # Low-confidence click gate. Found 2026-08-07, live: a
                    # ptr_conf=0.20 guess landed on the tab strip and ended a
                    # tab pass 9 fields early (Policy: 4/13 filled). The
                    # confidence was already being logged, never acted on.
                    # This does NOT hand WHERE to the LLM (that stays
                    # forbidden in pure/Option-B mode, see _merge()) — it
                    # only declines to act on a guess the model itself
                    # flagged as unreliable, falling back to the SAME
                    # generic Tab-advance already used below for a
                    # structurally invalid pointer. Tab is a universal
                    # navigation primitive, not task-specific, so this holds
                    # for any form this agent is pointed at, not just this one.
                    # Threshold matches the gate already established for the
                    # non-Option-B merge path (agent.py's _merge(), ~L4045).
                    _click_conf2 = t_pred.get("_click_conf", 0.0)
                    _elements2   = state.get("elements", [])
                    # Union with _advance_blacklist_pos: don't let the transformer's
                    # own pointer immediately undo a tab the system just deliberately
                    # left as exhausted (found live 2026-08-07 — see that attribute's
                    # comment for the full story, a 12-13x repeating tug-of-war).
                    _gated_pos2  = _gate_low_confidence_click(
                        _pos2, _click_conf2, elements=_elements2,
                        blacklist=self._nochange_click_pos | self._advance_blacklist_pos)
                    if _gated_pos2 is None and _pos2:
                        _floor2 = (_TAB_CLICK_CONF_FLOOR if _is_tab_strip_click(_pos2, _elements2)
                                   else _CLICK_CONF_FLOOR)
                        logger.info("[OPT2] pointer low-confidence (%.2f < %.2f) — Tab fallback instead of acting on it",
                                    _click_conf2, _floor2)
                    _pos2 = _gated_pos2
                    if _pos2 and (_pos2[0] > 1 or _pos2[1] > 1):
                        _snap2 = self._snap(_pos2, state) or _pos2
                        # COMBOBOX-AS-FILL: demos action comboboxes as CLICKS, so the
                        # model clicks them; but a plain click only toggles the
                        # dropdown → open/close oscillation. A click on an EMPTY
                        # combobox = intent to SET its value → route to FILL (focus it,
                        # then the LLM value + open/select handler below).
                        _cbox = None
                        if not self._pure_transformer and self._llm_client:
                            _cbox = next(
                                (e for e in state.get("elements", [])
                                 if e.get("type") == "comboboxcontrol"
                                 and not (e.get("value") or "").strip()
                                 and e.get("bbox")
                                 and e["bbox"][0] - 2 <= _snap2[0] <= e["bbox"][2] + 2
                                 and e["bbox"][1] - 2 <= _snap2[1] <= e["bbox"][3] + 2),
                                None)
                        if _cbox is not None:
                            _cb_label = (_cbox.get("label") or _cbox.get("text") or "").strip()
                            logger.info("[OPT2] CLICK on empty combobox %r → treat as FILL", _cb_label[:30])
                            _cb_sec = self._detect_section(state, _cbox)
                            _cb_val = self._lookup_field(_cb_label, section=_cb_sec)
                            # ONE click opens the dropdown; select inline + continue.
                            # (Don't route to the type-handler — its own open-click would
                            #  TOGGLE the dropdown shut → no options → Escape loop.)
                            #
                            # Skip the click entirely if a dropdown is ALREADY open —
                            # see _open_dropdown_items' docstring for why (a click
                            # toggles, it doesn't just open).
                            if not _open_dropdown_items(state.get("elements", [])):
                                self._executor.execute({"action_type": "click", "click_position": _snap2})
                            if not _cb_val:
                                # Optional field with no record value (e.g. Suffix).
                                # Escape + Tab past it, and MARK it attempted so the
                                # transformer stops re-targeting it (the real fix is the
                                # 'attempted' state-feature; this just records the hit).
                                logger.info("[OPT2] combobox %r — no value, Tab", _cb_label)
                                self._executor.execute({"action_type": "keyboard",
                                                        "key_count": 1, "keystrokes": ["escape"]})
                                self._mark_attempted(_cbox, elements=state.get("elements", []))
                                self._executor.execute({"action_type": "keyboard",
                                                        "key_count": 1, "keystrokes": ["tab"]})
                                # Fingerprint this exact outcome (position, not just
                                # label — same defense the general repeat-guard below
                                # uses) so a stuck loop is detected even though this
                                # branch `continue`s past that guard's own check.
                                # Found live 2026-08-07: attempted-marking alone didn't
                                # stop the transformer's own high-confidence (0.91)
                                # pointer from clicking straight back onto this same
                                # empty combobox 30+ times — Escape+Tab correctly
                                # recognized "nothing to fill" every time but never
                                # actually broke the cycle. Once this exact position
                                # repeats _REPEAT_LIMIT times, blacklist it so the
                                # confidence gate rejects it outright next time,
                                # regardless of how confident the model is.
                                _cb_fp = ("combobox_empty", _round_click_pos(_snap2))
                                _action_history.append(_cb_fp)
                                if list(_action_history)[-_REPEAT_LIMIT:] == [_cb_fp] * _REPEAT_LIMIT:
                                    logger.warning(
                                        "Repeat-action guard: %r stuck empty %dx in a row — blacklisting @ (%.0f,%.0f).",
                                        _cb_label, _REPEAT_LIMIT, _snap2[0], _snap2[1])
                                    self._nochange_click_pos.add(_round_click_pos(_snap2))
                                    _action_history.clear()
                                time.sleep(self.step_delay * 0.5)
                                continue
                            # Standard UIA "ListItem" maps to "listitem" (no "control"
                            # suffix) in ui_observer.py's _CTRL_TYPE_MAP.
                            # Poll count/interval: found live 2026-08-07 — 'Sedan' (a
                            # real, correct option) still came up with 0 list items
                            # found after the fix above, in the SAME run where other
                            # dropdowns (Make, Policy Type, ...) succeeded moments
                            # apart. Not a matching bug — the popup just hadn't
                            # finished rendering within the old 4x0.35s=1.4s window
                            # every time. Widened to give slow renders more room
                            # without meaningfully slowing the common fast case
                            # (the loop still exits the instant items appear).
                            _POLL_TRIES, _POLL_INTERVAL = 8, 0.4
                            _items = []
                            for _try in range(_POLL_TRIES):
                                time.sleep(_POLL_INTERVAL)
                                _cs = self._observe()
                                _items = [e for e in _cs.get("elements", [])
                                          if e.get("type") in ("listitem", "listitemcontrol")
                                          and e.get("window_role") != "background" and e.get("bbox")]
                                if _items:
                                    break
                            if not _items:
                                logger.warning(
                                    "Combobox: dropdown for %r still empty after %d tries (%.1fs) — giving up.",
                                    _cb_label, _POLL_TRIES, _POLL_TRIES * _POLL_INTERVAL)
                            _o = lambda e: (e.get("text") or e.get("label") or "").strip()
                            _hit = next((e for e in _items if _option_matches(_cb_val, _o(e))), None)
                            if _hit:
                                _b = _hit["bbox"]
                                self._executor.execute({"action_type": "click",
                                                        "click_position": [(_b[0]+_b[2])/2, (_b[1]+_b[3])/2]})
                                logger.info("Combobox(click-fill): %r → selected %r", _cb_label, _cb_val)
                                time.sleep(0.25)
                                self._executor.execute({"action_type": "keyboard",
                                                        "key_count": 1, "keystrokes": ["tab"]})
                                self._filled_this_tab.add(_cb_label)
                            else:
                                if _items:
                                    logger.warning("Combobox(click-fill): %r not in options %s",
                                                   _cb_val, [_o(e) for e in _items][:12])
                                else:
                                    logger.warning("Combobox(click-fill): dropdown for %r did not render", _cb_label)
                                self._executor.execute({"action_type": "keyboard",
                                                        "key_count": 1, "keystrokes": ["escape"]})
                            time.sleep(self.step_delay * 0.5)
                            continue
                        else:
                            # VISITED-ADVANCE crutch (gated off in pure/no-autohandler mode)
                            if not self._no_autohandlers:
                                _vis = getattr(self, "_visited_pos", None)
                                if _vis is None:
                                    _vis = self._visited_pos = set()
                                _vk = (round(_snap2[0] / 15) * 15, round(_snap2[1] / 15) * 15)
                                if _vk in _vis:
                                    _cands = sorted(
                                        [e for e in state.get("elements", [])
                                         if e.get("window_role") != "background"
                                         and e.get("type") in ("editcontrol", "input",
                                                               "comboboxcontrol", "combobox")
                                         and e.get("bbox")],
                                        key=lambda e: (e["bbox"][1], e["bbox"][0]))
                                    for _e in _cands:
                                        _b = _e["bbox"]
                                        _cx, _cy = (_b[0] + _b[2]) / 2, (_b[1] + _b[3]) / 2
                                        _ck = (round(_cx / 15) * 15, round(_cy / 15) * 15)
                                        if _ck not in _vis:
                                            _snap2 = [_cx, _cy]; _vk = _ck
                                            logger.info("[VISITED-ADVANCE] pointer repeated — next unvisited field @ (%.0f,%.0f)", _cx, _cy)
                                            break
                                _vis.add(_vk)
                            if not self._point_in_form(_snap2, state):
                                # Hallucinated target outside the form (e.g. (114,72)
                                # into another window, or below the viewport). Don't
                                # drift — Tab instead (stays in-form; wx auto-scrolls
                                # the focused field into view).
                                logger.warning("[GUARD] target (%.0f,%.0f) OUTSIDE form window — Tab instead of drifting.",
                                               _snap2[0], _snap2[1])
                                self._executor.execute({"action_type": "keyboard",
                                                        "key_count": 1, "keystrokes": ["tab"]})
                                time.sleep(self.step_delay * 0.5)
                                continue
                            prediction = {"action_type": "click", "click_position": _snap2}
                            logger.info("[OPT2] TRANSFORMER navigates → click @ (%.0f,%.0f)  ptr_conf=%.2f",
                                        _snap2[0], _snap2[1], t_pred.get("_click_conf", 0.0))
                    else:
                        prediction = {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
                        logger.info("[OPT2] pointer invalid — Tab fallback")

            elif self._llm_client and t_conf < _HIGH_CONF:
                llm_action = self._ask_llm(state)
                action_type = llm_action.get("action_type", "wait")
                reason = llm_action.get("reason", "")
                logger.info("[LLM:%s] action=%-8s", self.provider, action_type)

                if action_type == "done":
                    logger.info("LLM: task complete.")
                    break
                if action_type == "wait":
                    time.sleep(2.0)
                    continue

                # ── Merge: LLM decides what, transformer decides where ──────
                prediction = self._merge(t_pred, t_conf, llm_action, state)
                _decision_maker = "llm"
                print(f"\n  [LLM TOOK OVER]  reason: {reason}\n", flush=True)

                # Stuck-click guard
                if prediction.get("action_type") == "click":
                    pos = tuple(int(v) for v in prediction.get("click_position", [0, 0]))
                    if pos == _llm_click_pos:
                        _llm_click_count += 1
                    else:
                        _llm_click_pos, _llm_click_count = pos, 1
                    if _llm_click_count > _LLM_CLICK_LIMIT:
                        text = self._value_for_focused(state)
                        if text:
                            logger.warning("LLM stuck clicking — forcing type %r", text[:40])
                            prediction = {"action_type": "keyboard", "key_count": len(text),
                                          "keystrokes": list(text), "text": text}
                            _llm_click_pos, _llm_click_count = None, 0
                else:
                    _llm_click_pos, _llm_click_count = None, 0

            # 2c. Transformer acts alone — high confidence OR no LLM provider
            else:
                if self._llm_client:
                    logger.info("[TRANSFORMER] high-conf (%.2f) — acting alone, LLM skipped", t_conf)
                else:
                    logger.info("[TRANSFORMER] no LLM provider — acting alone")
                prediction = t_pred

                # Anti-scroll-loop: if scroll keeps producing no_change, the model
                # is stuck over-predicting scroll (overfit on small data). Swap to
                # its next-best non-scroll action so it progresses.
                if (prediction.get("action_type") == "scroll"
                        and getattr(self, "_scroll_nochange", 0) >= 2):
                    _alt = sorted(
                        ((p, a) for a, p in t_pred.get("_scores", {}).items() if a != "scroll"),
                        reverse=True,
                    )
                    if _alt:
                        prediction = dict(prediction)
                        prediction["action_type"] = _alt[0][1]
                        logger.warning("Scroll-loop (%dx no_change) — switching to next-best: %s (%.2f)",
                                       self._scroll_nochange, _alt[0][1], _alt[0][0])
                    self._scroll_nochange = 0

                if prediction.get("action_type") == "click":
                    snapped = self._snap(prediction.get("click_position", [0, 0]), state)
                    if snapped:
                        prediction = dict(prediction)
                        prediction["click_position"] = snapped

                    snap_tuple = tuple(int(v) for v in (snapped or prediction.get("click_position", [0, 0])))
                    if snap_tuple == _stuck_pos:
                        _stuck_count += 1
                    else:
                        _stuck_pos, _stuck_count = snap_tuple, 1

                    # VISITED-ADVANCE protocol (transformer-only runs): after a
                    # field is clicked, assume it's handled. If the (collapsed)
                    # pointer targets an already-visited field, advance to the
                    # next UNVISITED fillable field by position order — lets the
                    # run cover the form instead of looping on one spot.
                    # Gated OFF in pure mode — the model's is_visited feature
                    # handles progression; this crutch fights it.
                    if not self._llm_client and not self._no_autohandlers:
                        _vis = getattr(self, "_visited_pos", None)
                        if _vis is None:
                            _vis = self._visited_pos = set()
                        _key = (round(snap_tuple[0] / 15) * 15, round(snap_tuple[1] / 15) * 15)
                        if _key in _vis:
                            _cands = sorted(
                                [e for e in state.get("elements", [])
                                 if e.get("window_role") != "background"
                                 and e.get("type") in ("editcontrol", "input",
                                                       "comboboxcontrol", "combobox")
                                 and e.get("bbox")],
                                key=lambda e: (e["bbox"][1], e["bbox"][0]))
                            for _e in _cands:
                                _b = _e["bbox"]
                                _cx, _cy = (_b[0] + _b[2]) / 2, (_b[1] + _b[3]) / 2
                                _ck = (round(_cx / 15) * 15, round(_cy / 15) * 15)
                                if _ck not in _vis:
                                    prediction = {"action_type": "click",
                                                  "click_position": [_cx, _cy]}
                                    snap_tuple = (int(_cx), int(_cy))
                                    _key = _ck
                                    logger.info("[VISITED-ADVANCE] pointer repeated — next unvisited field @ (%.0f,%.0f)", _cx, _cy)
                                    break
                        _vis.add(_key)
                        _stuck_count = 0   # visited-advance handles progression

                    if _stuck_count >= _STUCK_LIMIT:
                        logger.warning("Loop detected @ %s %dx — forcing keyboard.", snap_tuple, _stuck_count)
                        prediction   = {"action_type": "keyboard", "key_count": 1, "keystrokes": []}
                        _stuck_count = 0
                else:
                    _stuck_pos, _stuck_count = None, 0

                # TextResolver turns the transformer's source_elem_idx (which
                # Notepad element to read) into actual typed text. Required in
                # pure mode too — without it the model can navigate but never
                # types Notepad values (the "doesn't know how to get from the
                # Notepad" symptom).
                if prediction.get("action_type") == "keyboard":
                    src_idx = prediction.get("source_elem_idx", -1)
                    text = self._text_resolver.resolve(state, source_elem_idx=src_idx)
                    if text:
                        prediction = dict(prediction)
                        prediction["text"] = text

            # 3. Execute — guard against typing into non-edit elements or clicking submit early
            _fid = state.get("focused_element_id")
            _fel = next((e for e in state.get("elements", []) if e.get("element_id") == _fid), None)
            _flabel     = ((_fel.get("label") or _fel.get("text") or "?").strip() if _fel else "unknown")
            _flabel_sec = self._detect_section(state, _fel) if _fel else ""
            _flabel_full = f"{_flabel_sec} {_flabel}" if _flabel_sec else _flabel

            if (not self._pure_transformer
                    and prediction.get("action_type") == "keyboard" and prediction.get("text")):
                logger.info("Type target: focused=[%s] %r", _fel.get("type","?") if _fel else "?", _flabel_full)
                # If focused field already has the correct value → Tab instead of re-typing
                if _fel and _fel.get("type") in ("editcontrol", "input"):
                    _cur_val = (_fel.get("value") or "").strip()
                    _new_val = prediction["text"].strip()
                    if _cur_val and _cur_val == _new_val:
                        # Field already holds the right value — don't re-type. Tab to
                        # release focus and let the TRANSFORMER decide the next move.
                        # (No hardcoded "go to empty field" / "click Submit" crutch —
                        # the end-game is the model's job; gaps there are a data
                        # problem to fix with demos, not agent navigation.)
                        logger.info("Re-type guard: %r already has %r — Tab instead.", _flabel_full, _cur_val[:40])
                        self._filled_this_tab.add(_flabel_full)
                        _no_change_streak += 1
                        prediction = {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
                # If the focused element is NOT an editable text field, typing will do nothing
                # (or corrupt a pane/tab/button). Replace with Tab to advance focus instead.
                if _fel and _fel.get("type") not in ("editcontrol", "input", "comboboxcontrol"):
                    if _fel.get("type") == "checkboxcontrol":
                        # LLM typed a truthy value (e.g. "YES (check)") into a checkbox — check it
                        _chk_text = prediction.get("text", "").lower().strip()
                        _should_chk = _chk_text not in ("", "no", "false", "0", "unchecked")
                        if _should_chk and _flabel not in self._checked_fields:
                            _chk_bbox = _fel.get("bbox")
                            if _chk_bbox:
                                try:
                                    import win32gui as _wgc; import win32api as _wac
                                    _cx = (_chk_bbox[0] + _chk_bbox[2]) / 2
                                    _cy = (_chk_bbox[1] + _chk_bbox[3]) / 2
                                    _hw = _wgc.WindowFromPoint((int(_cx), int(_cy)))
                                    if _hw:
                                        _wac.SendMessage(_hw, 0x00F1, 1, 0)  # BM_SETCHECK, BST_CHECKED
                                        logger.info("Checkbox %r checked via BM_SETCHECK (type intercept).", _flabel_full)
                                        self._checked_fields.add(_flabel_full)
                                        self._filled_this_tab.add(_flabel_full)
                                except Exception as _cbe:
                                    logger.warning("Checkbox BM_SETCHECK failed: %s", _cbe)
                    else:
                        logger.warning("LLM type into non-edit [%s] %r — pressing Tab instead.",
                                       _fel.get("type","?"), _flabel[:40])
                    prediction = {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}

            if prediction.get("action_type") == "click":
                _cp = prediction.get("click_position", [0, 0])
                # Guard: block LLM from re-clicking already-checked checkboxes (would uncheck them)
                _chk_at_cp = next(
                    (e for e in state.get("elements", [])
                     if e.get("type") in ("checkboxcontrol", "checkbox")
                     and e.get("window_role") != "background"
                     and e.get("bbox")
                     and e["bbox"][0] <= _cp[0] <= e["bbox"][2]
                     and e["bbox"][1] <= _cp[1] <= e["bbox"][3]),
                    None
                )
                if _chk_at_cp:
                    _chk_label_bare = (_chk_at_cp.get("label") or _chk_at_cp.get("text") or "").strip()
                    _chk_sec   = self._detect_section(state, _chk_at_cp)
                    _chk_label = f"{_chk_sec} {_chk_label_bare}" if _chk_sec else _chk_label_bare
                    _chk_cx = (_chk_at_cp["bbox"][0] + _chk_at_cp["bbox"][2]) / 2
                    _chk_cy = (_chk_at_cp["bbox"][1] + _chk_at_cp["bbox"][3]) / 2
                    # Use Win32 BM_SETCHECK — pyautogui clicks don't toggle wx checkboxes
                    try:
                        import win32gui as _wg2; import win32api as _wa2
                        BM_GETCHECK = 0x00F0; BM_SETCHECK = 0x00F1; BST_CHECKED = 1
                        _hwnd2 = _wg2.WindowFromPoint((int(_chk_cx), int(_chk_cy)))
                        if _hwnd2:
                            _already = (_wa2.SendMessage(_hwnd2, BM_GETCHECK, 0, 0) == BST_CHECKED)
                            if _chk_label in self._checked_fields or _already:
                                logger.warning("Checkbox %r already checked — Tab instead.", _chk_label)
                                prediction = {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
                            else:
                                _wa2.SendMessage(_hwnd2, BM_SETCHECK, BST_CHECKED, 0)
                                logger.info("Checkbox %r checked via Win32 BM_SETCHECK.", _chk_label)
                                self._checked_fields.add(_chk_label)
                                self._filled_this_tab.add(_chk_label)
                                _no_change_streak = 0
                                _last_auto_step   = step_idx
                                prediction = {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
                    except Exception as _ce:
                        logger.warning("BM_SETCHECK failed for %r: %s", _chk_label, _ce)

                _SUBMIT_KW = {"submit", "save", "ok", "accept", "done", "finish", "new"}
                # NOTE: do NOT filter by window_role here — Submit/Submit & New buttons live
                # on the main form window which is tagged "background" by the state scanner,
                # and we must block clicks on them regardless of their window role.
                _btn_at_cp = next(
                    (e for e in state.get("elements", [])
                     if e.get("type") in ("buttoncontrol", "button")
                     and e.get("bbox")
                     and any(kw in (e.get("text") or e.get("label") or "").lower()
                             for kw in _SUBMIT_KW)
                     and e["bbox"][0] <= _cp[0] <= e["bbox"][2]
                     and e["bbox"][1] <= _cp[1] <= e["bbox"][3]),
                    None
                )
                if _btn_at_cp and not self._no_autohandlers:
                    _btn_name = (_btn_at_cp.get("text") or _btn_at_cp.get("label") or "button")
                    logger.warning("Blocking premature click on button %r — advancing tab instead.", _btn_name)
                    if self._try_advance_tab(state):
                        _no_change_streak  = 0
                        _tab_just_switched = True
                        _tab_scroll_count  = 0
                        _last_auto_step    = step_idx
                        self._refresh_record_cache(state)
                        time.sleep(self.step_delay)
                        continue
                    prediction = {"action_type": "no_op"}

            # Intercept LLM clicks that target a tab element — resolved coords can be wrong.
            # Route through _try_advance_tab which uses the correct indexed bbox instead.
            if prediction.get("action_type") == "click":
                _cp = prediction.get("click_position", [0, 0])
                _all_tabs = [e for e in state.get("elements", [])
                             if e.get("type") in ("tabitem", "tabitemcontrol")
                             and e.get("window_role") != "background"
                             and e.get("bbox")]
                _tab_hit = next(
                    (e for e in _all_tabs
                     if e["bbox"][0] <= _cp[0] <= e["bbox"][2]
                     and e["bbox"][1] <= _cp[1] <= e["bbox"][3]),
                    None,
                )
                if _tab_hit:
                    _hit_name = (_tab_hit.get("text") or _tab_hit.get("label") or "?").strip()
                    # Go to the tab the model ACTUALLY clicked — NOT current+1.
                    # (Routing every tab-click through advance-to-next made repeated
                    # clicks on one tab race through ALL tabs, filling none.)
                    _sorted_tabs = sorted(_all_tabs, key=lambda e: e["bbox"][0])
                    _hit_idx = _sorted_tabs.index(_tab_hit)
                    if _hit_idx != self._current_tab_idx:
                        x1, y1, x2, y2 = _tab_hit["bbox"]
                        logger.info("Tab-click → navigating to %r (idx %d).", _hit_name, _hit_idx)
                        self._executor.execute({"action_type": "click",
                                                "click_position": [(x1 + x2) / 2, (y1 + y2) / 2]})
                        self._current_tab_idx = _hit_idx
                        _no_change_streak  = 0
                        _tab_just_switched = True
                        _tab_scroll_count  = 0
                        _last_auto_step    = step_idx
                        self._filled_this_tab.clear()
                        _confirmed_blank_fields.clear()
                        self._refresh_record_cache(state)
                        time.sleep(self.step_delay)
                        continue
                    # Model re-clicked the tab it's ALREADY on → don't race. Fill the
                    # next unfilled field instead; only if none left, advance to next tab.
                    if self._focus_first_empty_field(state):
                        logger.info("Tab %r already active — focusing next unfilled field instead.", _hit_name)
                        time.sleep(0.3)
                        _heuristic_steps += 1
                        continue
                    if self._try_advance_tab(state):
                        _no_change_streak  = 0
                        _tab_just_switched = True
                        _tab_scroll_count  = 0
                        _last_auto_step    = step_idx
                        self._filled_this_tab.clear()
                        _confirmed_blank_fields.clear()
                        self._refresh_record_cache(state)
                        time.sleep(self.step_delay)
                        continue
                    prediction = {"action_type": "no_op"}

            # Combobox: type-into-combobox → open dropdown + click matching listitem
            if (not self._pure_transformer
                    and prediction.get("action_type") == "keyboard"
                    and prediction.get("text")
                    and _fel and _fel.get("type") == "comboboxcontrol"
                    and _fel.get("bbox")):
                _combo_value = prediction["text"]
                _bx1, _by1, _bx2, _by2 = _fel["bbox"]
                _ccx, _ccy = (_bx1 + _bx2) / 2, (_by1 + _by2) / 2
                # Check whether the dropdown is ALREADY open before clicking to
                # "open" it — see _open_dropdown_items' docstring for why (a
                # click toggles, it doesn't just open); this was the actual root
                # cause of a loop that survived both the earlier type-name fix
                # and the poll-timing widening.
                _already_open = _open_dropdown_items(state.get("elements", []))
                if _already_open:
                    logger.info("Combobox: dropdown for %r already open (%d items) — skipping the open-click.",
                                _flabel, len(_already_open))
                else:
                    logger.info("Combobox: clicking to open dropdown for %r → %r", _flabel, _combo_value)
                    self._executor.execute({"action_type": "click", "click_position": [_ccx, _ccy]})
                # The dropdown can take a moment to render. Poll a few times before
                # giving up — otherwise we Escape on an empty observe and waste a
                # whole open→escape→reopen cycle (the #1 combobox time-sink).
                # ui_observer.py's _CTRL_TYPE_MAP maps the standard UIA "ListItem"
                # control type to "listitem" (no "control" suffix) -- only raw,
                # unmapped ControlTypeName strings fall through as
                # "listitemcontrol". Checking for "listitemcontrol" alone missed
                # every real dropdown whose items report the standard, mapped
                # type -- found live 2026-08-07: a run looped forever opening
                # "Body Type"'s dropdown, finding 0 list items every time despite
                # 8 new elements genuinely appearing, and Escaping, over and over.
                #
                # That fix alone wasn't enough, though: re-tested live and 'Sedan'
                # (a genuinely correct, exact-match option) STILL came up with 0
                # items in the SAME run where other dropdowns succeeded moments
                # apart. Turned out to be neither a matching nor a timing bug —
                # the REAL cause was the toggle-click issue guarded against just
                # above (_already_open): once that's fixed, an open→escape→
                # reopen cycle should be rare, but the poll stays widened as a
                # second line of defense against genuinely slow renders.
                _POLL_TRIES, _POLL_INTERVAL = 8, 0.4
                _listitems = list(_already_open)
                for _try in range(_POLL_TRIES):
                    if _listitems:
                        break
                    time.sleep(_POLL_INTERVAL)
                    _combo_state = self._observe()
                    _listitems = [e for e in _combo_state.get("elements", [])
                                  if e.get("type") in _LISTITEM_TYPES
                                  and e.get("window_role") != "background"
                                  and e.get("bbox")]
                    if _listitems:
                        break
                if not _listitems:
                    logger.warning("Combobox: dropdown for %r still empty after %d tries (%.1fs) — giving up.",
                                    _combo_value, _POLL_TRIES, _POLL_TRIES * _POLL_INTERVAL)
                def _opt(e): return (e.get("text") or e.get("label") or "").strip()
                # _option_matches: exact, then prefix-fuzzy ('Full Coverage' vs
                # 'Full Coverage (Comprehensive)'), then whole-word-token
                # containment (e.g. a value matching only part of a longer option)
                # — without ever letting
                # 'Active' match 'Inactive'. See its docstring for the live bug
                # (25+-iteration loop) that motivated the third tier.
                _match = next((e for e in _listitems if _option_matches(_combo_value, _opt(e))), None)
                if not _match and _listitems:
                    logger.warning("Combobox: %r not in options %s",
                                   _combo_value, [_opt(e) for e in _listitems][:12])
                if _match:
                    _lx1, _ly1, _lx2, _ly2 = _match["bbox"]
                    self._executor.execute({"action_type": "click",
                                            "click_position": [(_lx1+_lx2)/2, (_ly1+_ly2)/2]})
                    logger.info("Combobox: selected %r", _combo_value)
                    _no_change_streak = 0
                    _last_auto_step   = step_idx
                    # Tab past to advance focus — without this, LLM re-opens the same combobox
                    time.sleep(0.25)
                    self._executor.execute({"action_type": "keyboard", "key_count": 1,
                                            "keystrokes": ["tab"]})
                    # Mark in action history so repeat-guard catches any re-attempt
                    _action_history.append(("combobox_done", _flabel_full))
                    self._filled_this_tab.add(_flabel_full)
                else:
                    logger.warning("Combobox: %r not in dropdown — pressing Escape", _combo_value)
                    self._executor.execute({"action_type": "keyboard", "key_count": 1,
                                            "keystrokes": ["escape"]})
                    _action_history.append(("combobox_fail", _flabel))
                time.sleep(self.step_delay * 0.5)
                continue

            # Repeat-action guard: fingerprint this prediction; if last N identical → Tab
            if self._pure_transformer:
                _action_history.clear()  # don't accumulate in pure-transformer mode
            _atype = prediction.get("action_type", "no_op")
            if _atype == "keyboard":
                _fp = ("keyboard", (prediction.get("text") or "".join(prediction.get("keystrokes", [])))[:40])
            elif _atype == "click":
                _cp2 = prediction.get("click_position", [0, 0])
                _fp  = ("click", round(_cp2[0] / 20) * 20, round(_cp2[1] / 20) * 20)
            else:
                _fp = (_atype,)
            _action_history.append(_fp)
            if (len(_action_history) >= _REPEAT_LIMIT
                    and len(set(_action_history)) == 1):
                logger.warning("Repeat-action guard: same action %dx in a row — forcing Tab.", _REPEAT_LIMIT)
                _action_history.clear()
                _no_change_streak = 0
                self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
                time.sleep(self.step_delay * 0.5)
                continue

            if prediction.get("action_type") == "keyboard":
                self._ensure_form_foreground(state)
                # Clear existing field value before typing to prevent append corruption
                # (e.g. "Maria" + "James" → "MariaJames").  Skip for nav-only actions.
                _pred_text = prediction.get("text", "")
                _pred_keys = prediction.get("keystrokes", [])
                _nav_keys  = {"tab", "return", "enter", "escape", "Key.tab",
                               "Key.return", "Key.enter", "Key.escape"}
                _is_nav_only = not _pred_text and all(k in _nav_keys for k in _pred_keys)
                if not _is_nav_only and _focused_el:
                    _foc_val = (_focused_el.get("value") or "").strip()
                    if _foc_val:
                        logger.info("Pre-type clear: field has %r — ctrl+a.", _foc_val[:40])
                        self._executor.execute({"action_type": "keyboard",
                                                "key_count": 1, "keystrokes": ["ctrl+a"]})
            # Foreground guard: before a keystroke, re-assert the window we OBSERVED.
            # If a stray click knocked the form out of front, keystrokes would leak
            # into whatever stole focus (e.g. the terminal → "Windows PowerShell").
            # Universal (re-focus the observed active window), not form-specific.
            if prediction.get("action_type") == "keyboard":
                self._ensure_foreground(state)
            _t_decided = time.time()
            decide_time_sec = _t_decided - _t_observed
            result = self._executor.execute(prediction)
            _t_executed = time.time()
            execute_time_sec = _t_executed - _t_decided
            logger.info("%s", result)

            # Detect tab click: if the executed click landed on a tab element, mark a tab switch
            # so the next step scrolls to top and focuses the first empty field.
            if prediction.get("action_type") == "click":
                _cp = prediction.get("click_position", [0, 0])
                _all_tabs = [e for e in state.get("elements", [])
                             if e.get("type") in ("tabitem", "tabitemcontrol")
                             and e.get("window_role") != "background"
                             and e.get("bbox")]
                for _ti_idx, _ti_el in enumerate(_all_tabs):
                    _tx1, _ty1, _tx2, _ty2 = _ti_el["bbox"]
                    if _tx1 <= _cp[0] <= _tx2 and _ty1 <= _cp[1] <= _ty2:
                        self._current_tab_idx = _ti_idx
                        _tab_just_switched    = True
                        _tab_scroll_count     = 0
                        _no_change_streak     = 0
                        _steps_on_tab         = 0
                        logger.info("Tab click detected: switched to tab idx=%d  '%s'",
                                    _ti_idx,
                                    _ti_el.get("text") or _ti_el.get("label") or "?")
                        # Notify plugin so it also resets its tab-switch state
                        if self._task_plugin is not None and hasattr(self._task_plugin, "notify_tab_click"):
                            self._task_plugin.notify_tab_click(_ti_idx, state)
                        break

            # Auto-Tab DISABLED — it was a navigation crutch (heuristic advancing
            # fields instead of the transformer clicking the next field). With it
            # off, the transformer MUST navigate (click next field) itself, so we
            # measure honestly whether it learned navigation.
            if (False and self._llm_client
                    and prediction.get("action_type") == "keyboard"
                    and prediction.get("text")):
                tab_pred = {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
                self._executor.execute(tab_pred)
                logger.info("Auto-Tab after type.")

            # 4. Validate
            state_after = self._observe()

            # ── Verify-at-fill: catch a WRONG value the moment it's typed, not later ──
            # StateValidator (right below) only checks whether SOMETHING changed —
            # focus moved, value differs from before — never whether it changed to
            # the RIGHT thing. A field can type successfully (validator says "ok")
            # while still holding the wrong value, and nothing used to catch that.
            # Found 2026-08-07 from a direct user report about the old (deleted)
            # Intern iteration lacking exactly this: "we don't want to keep coming
            # back to something... a constant checkback always consumes too much
            # time." Fix is inline and bounded, not a separate re-check pass —
            # verify against the value the agent itself just decided to type
            # (prediction["text"]), retry a couple of times if it doesn't match,
            # then move on regardless so a stubborn field can't stall the run.
            if prediction.get("action_type") == "keyboard" and prediction.get("text"):
                _expected_text = prediction["text"].strip()
                for _verify_attempt in range(_MAX_VERIFY_RETRIES + 1):
                    # Use THIS snapshot's own reported focused_element_id, not the
                    # pre-typing one from `state`. element_id is assigned purely by
                    # scan position ("elem_{offset+count}") — self-consistent WITHIN
                    # one observation, but not stable ACROSS separate observations
                    # (any element gaining/losing text between scans shifts every
                    # id after it). Found 2026-08-07, live: the very first field
                    # ('Policy Number') triggered 2 wasted retries because the
                    # pre-typing id no longer pointed at the same field in the
                    # post-typing snapshot, reading a false empty mismatch even
                    # though the type had actually already succeeded.
                    _foc_id_vf_now = state_after.get("focused_element_id")
                    if _verify_fill_matches(state_after.get("elements", []), _foc_id_vf_now, _expected_text):
                        if _verify_attempt > 0:
                            logger.info("Verify-at-fill: corrected after retry %d.", _verify_attempt)
                        break
                    _actual_elem = _find_element_by_id(state_after.get("elements", []), _foc_id_vf_now)
                    _actual_text = (_actual_elem.get("value") or "").strip() if _actual_elem else ""
                    if _verify_attempt >= _MAX_VERIFY_RETRIES:
                        logger.warning(
                            "Verify-at-fill: still mismatched after %d retries — moving on "
                            "(expected %r, got %r).",
                            _MAX_VERIFY_RETRIES, _expected_text[:40], _actual_text[:40])
                        break
                    logger.warning("Verify-at-fill: expected %r, got %r — retrying (%d/%d).",
                                    _expected_text[:40], _actual_text[:40],
                                    _verify_attempt + 1, _MAX_VERIFY_RETRIES)
                    self._executor.execute({"action_type": "keyboard",
                                            "key_count": 1, "keystrokes": ["ctrl+a"]})
                    self._executor.execute({"action_type": "keyboard", "text": _expected_text})
                    time.sleep(self.step_delay * 0.3)
                    state_after = self._observe()

            validation  = self._validator.validate(state, state_after, prediction)
            logger.info("Validator → %s: %s", validation.status, validation.reason)

            # Record the field acted on this step (attempted feature) — mirrors the
            # train-time derivation (every click/type target becomes 'attempted').
            self._record_attempt(state, prediction)

            if validation.status == "done":
                logger.info("StateValidator: task appears complete.")
                break

            _cur_focused_id = state.get("focused_element_id")
            # Track scroll-specific no-change so we can break scroll loops
            if validation.status == "no_change" and prediction.get("action_type") == "scroll":
                self._scroll_nochange = getattr(self, "_scroll_nochange", 0) + 1
            else:
                self._scroll_nochange = 0
            if validation.status == "no_change":
                if _cur_focused_id and _cur_focused_id != _last_focused_id:
                    _no_change_streak = 0
                else:
                    _no_change_streak += 1
                # Blacklist click position so transformer won't override LLM type there again
                if prediction.get("action_type") == "click":
                    _fcp = prediction.get("click_position", [])
                    if len(_fcp) >= 2:
                        self._nochange_click_pos.add((round(_fcp[0] / 10) * 10, round(_fcp[1] / 10) * 10))
            else:
                _no_change_streak = 0
            _last_focused_id = _cur_focused_id

            # Combobox open/close tracking — mark field filled when dropdown closes
            _n_before = len(state.get("elements", []))
            _n_after  = len(state_after.get("elements", []))
            _delta    = _n_after - _n_before
            if _delta > 2:
                # Dropdown opened — record which combobox is open
                _foc_id_cb = state.get("focused_element_id")
                _foc_cb = next((e for e in state.get("elements", [])
                                if e.get("element_id") == _foc_id_cb), None)
                if _foc_cb and _foc_cb.get("type") in ("comboboxcontrol", "combobox"):
                    _ocb_bare = (_foc_cb.get("label") or _foc_cb.get("text") or "").strip()
                    _ocb_sec  = self._detect_section(state, _foc_cb)
                    _open_combobox_label = f"{_ocb_sec} {_ocb_bare}" if _ocb_sec else _ocb_bare
            elif _delta < -2 and _open_combobox_label and validation.status == "ok":
                # Dropdown closed after a successful action — mark combobox as filled
                logger.info("Combobox-close tracking: marking %r as filled.", _open_combobox_label)
                self._filled_this_tab.add(_open_combobox_label)
                _open_combobox_label = ""

            # Notify plugin of validation result so it can update its own streak tracking
            if self._task_plugin is not None:
                self._task_plugin.notify_validation(validation.status, _cur_focused_id)
                # Sync plugin streak back to local so stuck guard in run() stays accurate
                if hasattr(self._task_plugin, "_no_change_streak"):
                    _no_change_streak = self._task_plugin._no_change_streak

            if (validation.status in ("no_change", "unexpected", "error") and self._task_name
                    and self._correction_watch_seconds > 0):
                logger.info("Validation failed (%s) — watching for user correction (%.1fs) …",
                            validation.status, self._correction_watch_seconds)
                steps = self._correction.watch(self._observer, seconds=self._correction_watch_seconds)
                if steps:
                    saved = self._correction.save(self._task_name, steps)
                    if saved:
                        logger.info("Correction saved → %s", saved)
                        _manual_interventions += 1

            # 7. Record
            pos = prediction.get("click_position", [0.0, 0.0])
            res = state.get("screen_resolution", [1920, 1080])
            W, H = float(res[0]) or 1920, float(res[1]) or 1080
            self._history.append({
                "state":       state,
                "action_type": prediction.get("action_type", "no_op"),
                "click_xy":    [pos[0] / W, pos[1] / H] if pos else [0.0, 0.0],
                "key_count":   prediction.get("key_count", 0),
                "typed_text":  prediction.get("text", ""),
                "target":      llm_action.get("target", "") if self._llm_client and llm_action else "",
                "validation":  validation.status,
            })
            _step_time_sec = time.time() - _step_t0
            if (self._time_to_first_action_sec is None
                    and prediction.get("action_type") not in (None, "no_op", "noop", "wait")):
                self._time_to_first_action_sec = time.time() - _run_start_ts
            self._results.append({
                "step":              step_idx + 1,
                "state":             state,
                "action":            prediction,
                "result":            str(result),
                "validation":        validation.status,
                "guidance":          self._guidance,
                "elements":          len(state.get("elements", [])),
                "decision_by":       _decision_maker,
                "t_conf":            t_conf,
                "timestamp":         _dt.datetime.now().isoformat(),
                "observe_time_sec":  round(observe_time_sec, 4),
                "decide_time_sec":   round(decide_time_sec, 4),
                "execute_time_sec":  round(execute_time_sec, 4),
                "step_time_sec":     round(_step_time_sec, 4),
            })

            if not result.success:
                # Skip-and-continue: one failed action shouldn't kill the whole
                # run. Count consecutive failures and only abort if stuck.
                logger.warning("Execution failed — skipping: %s", result.error)
                _exec_fail_streak = getattr(self, "_exec_fail_streak", 0) + 1
                self._exec_fail_streak = _exec_fail_streak
                if _exec_fail_streak >= 8:
                    logger.error("8 consecutive execution failures — halting.")
                    break
                time.sleep(self.step_delay)
                continue
            self._exec_fail_streak = 0

            time.sleep(self.step_delay)

          except Exception as _step_exc:
            import traceback as _tb
            _tb_str = _tb.format_exc()
            logger.error("STEP %d CRASHED:\n%s", step_idx + 1, _tb_str)
            print(f"\n=== STEP {step_idx+1} CRASHED ===\n{_tb_str}", flush=True)
            break

        self._heuristic_steps       = _heuristic_steps
        self._manual_interventions  = _manual_interventions
        self._run_duration_sec = time.time() - _run_start_ts
        logger.info(
            "LLMAgent finished — %d step(s)  (%d heuristic)  duration=%.1fs  time_to_first_action=%s",
            len(self._results), _heuristic_steps, self._run_duration_sec,
            f"{self._time_to_first_action_sec:.1f}s" if self._time_to_first_action_sec is not None else "n/a",
        )
        self._export_run_traces(self._results, task_name)
        return list(self._results)

    @property
    def results(self) -> List[Dict[str, Any]]:
        return list(self._results)

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    def _export_run_traces(self, results: List[Dict[str, Any]], task_name: str) -> None:
        """
        Convert completed run results to trace JSONs and write to disk.

        Only exports steps where the agent's action had a real effect (validation != no_change
        and action_type != no_op) — bad steps teach bad behavior.

        Output: data/output/traces/agent_runs/session_{timestamp}/live_step_{idx:04d}.json
        Compatible with TrajectoryDataset — ContinualLearner picks these up automatically.
        """
        good = [
            r for r in results
            if r.get("validation") != "no_change"
            and r.get("action", {}).get("action_type", "no_op") not in ("no_op", "noop", "wait", None)
        ]
        if len(good) < 3:
            logger.info("Trace export: only %d usable steps — skipping (run too short/failed).", len(good))
            return

        import datetime as _dt, json as _json
        session_ts  = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        session_tag = task_name or "run"
        out_dir     = os.path.join(_ROOT, "data", "output", "traces", "agent_runs",
                                   f"session_{session_ts}_{session_tag}")
        os.makedirs(out_dir, exist_ok=True)

        written = 0
        for i, r in enumerate(good):
            state  = r.get("state", {})
            action = r.get("action", {})
            a_type = action.get("action_type", "no_op")

            res = state.get("screen_resolution", [1920, 1080])
            W   = float(res[0]) or 1920.0

            if a_type == "click":
                pos = action.get("click_position") or [0.0, 0.0]
                mouse_actions = [{"position": [float(pos[0]), float(pos[1])],
                                  "type": "click",
                                  "timestamp": r.get("state", {}).get("timestamp", "")}]
                kb_actions = []
            elif a_type == "keyboard":
                mouse_actions = []
                text = action.get("text", "") or ""
                key_count = action.get("key_count", 0)
                if text:
                    strokes = [{"pasted_text": text, "key": ""}]
                else:
                    strokes = [{"key": "tab", "pasted_text": ""}] * max(1, key_count)
                kb_actions = [{"strokes": strokes}]
            else:
                continue  # skip anything else (scroll, done markers, etc.)

            trace = {
                "trace_id":  f"live_step_{i:04d}",
                "timestamp": session_ts,
                "duration":  1.5,
                "type":      session_tag,
                "state":     state,
                "mouse":     {"actions": mouse_actions},
                "keyboard":  {"actions": kb_actions},
                "diff":      {},
                "action":    a_type,
            }
            out_path = os.path.join(out_dir, f"live_step_{i:04d}.json")
            try:
                with open(out_path, "w", encoding="utf-8") as _f:
                    _json.dump(trace, _f, ensure_ascii=False)
                written += 1
            except Exception as _e:
                logger.debug("Trace export: failed writing step %d: %s", i, _e)

        logger.info("Trace export: %d step(s) → %s", written, out_dir)

    # ── Auto-skip helper ─────────────────────────────────────────────────────

    def _auto_skip(self, state: Dict[str, Any]) -> bool:
        """
        Return True if the currently focused field already contains the value
        that BACKGROUND DATA says it should have — no LLM call needed.
        Only applies to non-empty fields (text inputs and dropdowns).
        """
        elements   = state.get("elements", [])
        focused_id = state.get("focused_element_id")
        if not focused_id:
            return False
        focused = next((e for e in elements if e.get("element_id") == focused_id), None)
        if not focused:
            return False
        # Skip buttons — they are not fillable fields
        if focused.get("type") in ("buttoncontrol",):
            return False
        current = (focused.get("value") or "").strip()

        field_name = (focused.get("label") or focused.get("text") or "").strip()
        if not field_name:
            return False

        # Use cached record (full Notepad text); fall back to bg_blobs
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

        # Detect Driver/Vehicle section context for section-prefixed lookup.
        section  = self._detect_section(state, focused)
        filled_key = f"{section} {field_name}" if section else field_name
        # Duplicate-label guard: if we already processed a field with this label this tab,
        # don't auto-skip the second occurrence — it's a different field (e.g. Prior Policy No.
        # reported as 'Prior Insurer' in UIA). Let the dup-label handler deal with it.
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
            return False   # field not in record — let LLM decide

        # (leave blank) / (none) in data → leave field as-is regardless of current value
        _leave_blank_raws = {"(none)", "none", "(leave blank)", "n/a",
                             "leave blank — liability only", "leave blank — owned outright"}
        if expected.lower().strip("()") in {s.strip("()") for s in _leave_blank_raws}:
            logger.info("Auto-skip: '%s' = (leave blank) — Tab.", filled_key)
            self._filled_this_tab.add(filled_key)
            return True

        if not current:
            return False   # empty field that needs filling — let auto-fill handle it

        match = current.lower() == expected.lower()
        if match:
            logger.info("Auto-skip: '%s' already = %r", filled_key, current)
            self._filled_this_tab.add(filled_key)
        return match

    def _read_notepad_full_text(self, state: Dict[str, Any]) -> str:
        """
        Read the full source text via the injected data source. Source-specific
        I/O (Win32 WM_GETTEXT for Notepad, COM for Excel, …) lives in the adapter,
        not here — this is just the agent's call into the seam.
        """
        try:
            return self._source.read_full_text(state) or ""
        except Exception:
            return ""

    def _refresh_record_cache(self, state: Dict[str, Any]) -> None:
        """
        Populate self._cached_record with {field: value} for the current record.
        Priority:
          1. Visual cache (Gemini pre-scan) — used when available; no Win32 needed.
          2. Win32 Notepad text read → parse records.
          3. UIA background element blobs / OCR fallback.
        """
        import re

        # When visual_reader is active, use ONLY what the VLM has seen on screen.
        # Win32 reads Notepad memory directly (agent "knows" without looking) — skip it.
        if self._visual_reader:
            self._cached_record = dict(self._visual_cache)
            sample = list(self._cached_record.items())[:5]
            logger.info("Record cache: VLM-only mode — %d field(s) from visual cache  sample=%r",
                        len(self._cached_record), sample)
            return

        full_text = self._read_notepad_full_text(state)

        # Fallback: reconstruct text from background elements (UIA or OCR)
        if not full_text:
            elements = state.get("elements", [])
            bg_elems = [e for e in elements if e.get("window_role") == "background"]

            # Try UIA blobs first (they carry full multi-line text in a single value)
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
                full_text = blobs[0]   # longest UIA blob even if unstructured
                logger.info("Record cache: Win32 failed — using largest UIA blob (%d chars)", len(full_text))

            # If UIA blobs were empty, try OCR elements: group by y-coord to rebuild lines
            if not full_text:
                ocr_elems = [e for e in bg_elems
                             if e.get("source") == "ocr" and (e.get("text") or "")]
                if ocr_elems:
                    ocr_elems.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]) if e.get("bbox") else (0, 0))
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
                    logger.info("Record cache: Win32 failed — rebuilt %d lines from OCR boxes", len(lines))

        if not full_text:
            logger.warning("Record cache: no Notepad text available — cache unchanged (%d fields)", len(self._cached_record))
            return

        records = _parse_records(full_text)
        if records:
            rec = records.get(self._record_num, records.get(min(records), {}))
            self._cached_record = rec
            # New record = new session → clear attempted history so fields on the
            # fresh record aren't pre-marked from the previous one.
            if self._record_num != self._attempted_record_num:
                self._attempted_keys.clear()
                self._attempted_record_num = self._record_num
            sample = list(rec.items())[:5]
            logger.info("Record cache refreshed: %d fields for record %d  sample=%r",
                        len(rec), self._record_num, sample)
        else:
            # Plain key:value text — parse directly
            data: Dict[str, str] = {}
            for line in full_text.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = re.sub(r"[\[\]]", "", key).strip()
                    val = re.sub(r"\s*\[.*", "", val)
                    val = re.sub(r"\s*←.*", "", val).strip()
                    if key and key not in data and val and val.lower() not in {"(none)", "none", "(leave blank)"}:
                        data[key] = val
            self._cached_record = data
            sample = list(data.items())[:5]
            logger.info("Record cache refreshed (plain text): %d fields  sample=%r", len(data), sample)

        # ── Overlay visual cache on top: Groq/Gemini values win on conflicts ──
        if self._visual_cache:
            before = len(self._cached_record)
            self._cached_record.update(self._visual_cache)
            logger.info("Record cache: merged %d visual cache field(s) on top of %d Win32 fields",
                        len(self._visual_cache), before)

    def _scan_tab_visual(self, state: Dict[str, Any]) -> None:
        """
        When a form tab switches, scroll the source Notepad to the matching
        TAB N — TABNAME section for the current record, then use the visual
        reader (Groq/Gemini) to capture all field values by scrolling downward.
        New fields are merged into self._visual_cache and self._cached_record.
        """
        if not self._visual_reader or not self._source_window:
            return

        # Resolve current tab name from _current_tab_idx + state tab elements
        _KNOWN_TABS = self._scope.tab_names   # scope-provided; empty → no tab filter
        elements = state.get("elements", [])
        tabs = [
            e for e in elements
            if e.get("type") in ("tabitem", "tabitemcontrol")
            and e.get("window_role") != "background"
            and e.get("bbox")
            and (not _KNOWN_TABS
                 or (e.get("text") or e.get("label") or "").strip().lower() in _KNOWN_TABS)
        ]
        if not tabs or self._current_tab_idx >= len(tabs):
            return
        tab_name = (tabs[self._current_tab_idx].get("text")
                    or tabs[self._current_tab_idx].get("label") or "").strip()
        if not tab_name:
            return

        # Get full Notepad text for line-number lookup
        full_text = self._read_notepad_full_text(state) or ""
        if not full_text:
            return

        logger.info("Visual tab scan: scanning Notepad for tab=%r record=%d",
                    tab_name, self._record_num)
        new_fields = self._visual_reader.scan_tab(
            tab_name, self._record_num, full_text, self._source_window
        )

        if new_fields:
            self._visual_cache.update(new_fields)
            self._cached_record.update(new_fields)
            logger.info("Visual tab scan: %d new field(s) added to cache (total visual=%d)",
                        len(new_fields), len(self._visual_cache))

    def _peek_next_field_after(self, state: Dict[str, Any], after_label: str) -> Optional[tuple]:
        """
        Read Notepad text and return (key, value) of the first field that appears
        AFTER after_label in the document and has NOT yet been cached in _cached_record.
        Used to resolve duplicate-UIA-label fields (e.g. 'Prior Policy No.' reported as
        'Prior Insurer' by UIA).  Returns None if not found.
        """
        full_text = self._read_notepad_full_text(state)
        if not full_text:
            return None
        lines = full_text.splitlines()
        fl = after_label.lower()
        found_target = False
        for line in lines:
            stripped = line.strip()
            if not found_target:
                if fl in stripped.lower() and ":" in stripped:
                    found_target = True
                continue
            if ":" in stripped:
                colon_idx = stripped.index(":")
                k = stripped[:colon_idx].strip()
                v = stripped[colon_idx + 1:].strip()
                if k and v and k not in self._cached_record:
                    logger.info("_peek_next_field_after: '%s' → next uncached field='%s' value=%r",
                                after_label, k, v)
                    return (k, v)
        return None

    def _peek_notepad(self, state: Dict[str, Any], field_name: str) -> None:
        """
        Scroll Notepad to the line containing field_name, then hover the
        mouse over it — gives the visual impression of reading.
        Uses win32 EM_LINESCROLL so focus never leaves the form.
        Works with both classic Notepad and Windows 11 Notepad (WinUI3).
        When VLM (visual_reader) is active:
          - Field already in visual cache → skip (VLM already read it).
          - Field NOT in visual cache → Win32 fallback: scroll, extract value, cache it, log.
        When no VLM: original scroll-and-hover UX behaviour.
        """
        _is_vlm_mode = bool(self._visual_reader)
        if field_name in self._cached_record:
            return   # already have a value — no need to peek
        try:
            import pyautogui
            import win32gui
            import win32api

            EM_GETFIRSTVISIBLELINE = 0x00CE
            EM_LINESCROLL          = 0x00B6

            # Find the raw text and background window title from state.
            # Prefer text-file windows (.txt / Notepad) over terminal windows
            # (PowerShell/CMD accumulate huge log buffers that confuse detection).
            # Separate two concerns:
            #   bg_window_title — used ONLY to find the Notepad hwnd; prefer .txt windows
            #   raw_text        — used ONLY for line-number lookup; use largest blob from
            #                     any background window (Win11 Notepad exposes only 1 char
            #                     via UIA, but PowerShell accumulates full file content)
            _TERMINAL_HINTS = {"powershell", "terminal", "command prompt", "cmd.exe"}
            elements = state.get("elements", [])
            bg_window_title = ""   # hwnd-lookup title (.txt preferred)
            raw_text        = ""   # largest blob for line-number lookup
            for e in elements:
                if e.get("window_role") != "background":
                    continue
                win_title = (e.get("window_title") or "").strip()
                blob      = (e.get("value") or "").strip()
                # Track the .txt / Notepad window for hwnd regardless of blob size
                if not bg_window_title:
                    is_textfile = ".txt" in win_title or "notepad" in win_title.lower()
                    if is_textfile:
                        bg_window_title = win_title
                # Track largest blob from any background window for line lookup
                if blob and len(blob) > len(raw_text):
                    raw_text = blob

            # Prefer full Win32 text (bypasses the 2000-char UIA cap) for accurate line lookup.
            # Fall back to raw_text from bg_blobs if Win32 read fails.
            full_np_text = self._read_notepad_full_text(state)
            if full_np_text:
                raw_text = full_np_text

            logger.info("_peek_notepad: field=%r  bg_title=%r  raw_text_len=%d",
                        field_name, bg_window_title, len(raw_text))
            if not raw_text:
                logger.info("_peek_notepad: no background text found — skipping scroll")
                return

            lines = raw_text.splitlines()
            from data_sources.notepad_source import _find_field_line
            hit = _find_field_line(lines, field_name)
            target_line = hit[0] if hit else 0
            fl = field_name.lower()

            # ── Find the Notepad window ───────────────────────────────────────
            np_hwnd = None

            # Strategy 1: use exact title from UIA background element (most reliable)
            if bg_window_title:
                np_hwnd = win32gui.FindWindow(None, bg_window_title)
                if not np_hwnd:
                    # Title might include app name suffix, e.g. "file.txt - Notepad"
                    np_hwnd = win32gui.FindWindow(None, bg_window_title + " - Notepad")

            # Strategy 2: enumerate top-level windows by class / title heuristic
            if not np_hwnd:
                def _find_np(hwnd, _):
                    nonlocal np_hwnd
                    if np_hwnd:
                        return
                    if not win32gui.IsWindowVisible(hwnd):
                        return
                    title = win32gui.GetWindowText(hwnd)
                    cls   = win32gui.GetClassName(hwnd)
                    if cls == "Notepad":
                        np_hwnd = hwnd
                    elif ".txt" in title or "notepad" in title.lower():
                        np_hwnd = hwnd
                win32gui.EnumWindows(_find_np, None)

            logger.info("_peek_notepad: np_hwnd=%s  title=%r",
                        np_hwnd, win32gui.GetWindowText(np_hwnd) if np_hwnd else None)
            if not np_hwnd:
                return

            # ── Find the edit control (classic or Win11) ─────────────────────
            _EDIT_CLASSES = {"Edit", "RichEditD2DPT", "RichEdit20W", "RICHEDIT50W"}
            edit_hwnd = None

            def _find_edit(hwnd, _):
                nonlocal edit_hwnd
                if edit_hwnd:
                    return
                try:
                    cls = win32gui.GetClassName(hwnd)
                    if cls in _EDIT_CLASSES:
                        edit_hwnd = hwnd
                except Exception:
                    pass

            # Enumerate direct children first
            win32gui.EnumChildWindows(np_hwnd, _find_edit, None)

            # Win11 Notepad nests the editor deeper — walk one level of grandchildren
            if not edit_hwnd:
                child = win32gui.GetWindow(np_hwnd, 5)   # GW_CHILD
                while child and not edit_hwnd:
                    win32gui.EnumChildWindows(child, _find_edit, None)
                    child = win32gui.GetWindow(child, 2)  # GW_HWNDNEXT

            logger.info("_peek_notepad: edit_hwnd=%s  target_line=%d", edit_hwnd, target_line)
            if not edit_hwnd:
                return

            # ── Scroll to target line ─────────────────────────────────────────
            first_visible = win32api.SendMessage(edit_hwnd, EM_GETFIRSTVISIBLELINE, 0, 0)
            delta = target_line - first_visible - 3   # show 3 lines above target
            logger.info("_peek_notepad: first_visible=%d  delta=%d", first_visible, delta)
            if delta != 0:
                win32api.SendMessage(edit_hwnd, EM_LINESCROLL, 0, delta)

            # ── Default: parse value directly from Win32 text (no API call) ──
            # Exact key match preferred; whole-word fallback inside _find_field_line.
            if hit and hit[1]:
                _val = hit[1]
                self._visual_cache[field_name]  = _val
                self._cached_record[field_name] = _val
                logger.info("_peek_notepad: Win32 text found field=%r  value=%r",
                            field_name, _val)
                return

            if _is_vlm_mode and self._visual_reader:
                # ── VLM fallback: scroll Notepad until Groq sees the field ──────
                form_hwnd = win32gui.GetForegroundWindow()   # save form focus
                try:
                    self._visual_reader._force_foreground(np_hwnd)
                    time.sleep(0.4)
                    # pyautogui.scroll targets cursor window — move mouse over Notepad
                    try:
                        rect = win32gui.GetWindowRect(np_hwnd)
                        pyautogui.moveTo((rect[0]+rect[2])//2, (rect[1]+rect[3])//2,
                                         duration=0.1)
                    except Exception:
                        pass

                    found_val   = None
                    seen_hashes : set = set()
                    fl_lower    = field_name.lower()

                    for attempt in range(10):   # scroll up to 10 screens to find field
                        screenshot = pyautogui.screenshot()
                        img_hash   = hash(screenshot.tobytes())

                        if img_hash not in seen_hashes:
                            seen_hashes.add(img_hash)
                            new_fields = self._visual_reader._extract_from_screenshot(screenshot)
                            for k, v in new_fields.items():
                                norm = k.strip()
                                if norm and norm not in self._visual_cache:
                                    self._visual_cache[norm]  = str(v).strip()
                                    self._cached_record[norm] = str(v).strip()
                                if norm.lower() == fl_lower:
                                    found_val = str(v).strip()

                        if found_val:
                            logger.info("_peek_notepad: VLM fallback found field=%r  value=%r"
                                        "  (attempt %d)", field_name, found_val, attempt + 1)
                            break

                        # Field not seen yet — scroll down and try again
                        logger.info("_peek_notepad: VLM fallback scrolling down for field=%r"
                                    "  (attempt %d)", field_name, attempt + 1)
                        pyautogui.scroll(-10)
                        time.sleep(0.35)

                    if not found_val:
                        logger.warning("_peek_notepad: VLM fallback exhausted — "
                                       "field=%r not found on screen", field_name)
                finally:
                    try:
                        win32gui.SetForegroundWindow(form_hwnd)
                    except Exception:
                        pass
            else:
                # ── Non-VLM mode: hover mouse over Notepad (purely visual UX) ──
                rect = win32gui.GetWindowRect(np_hwnd)
                cx   = (rect[0] + rect[2]) / 2
                cy   = (rect[1] + rect[3]) / 2
                orig = pyautogui.position()
                pyautogui.moveTo(cx, cy, duration=0.25)
                time.sleep(0.4)
                pyautogui.moveTo(orig.x, orig.y, duration=0.2)

        except Exception:
            pass   # never block the agent over a cosmetic action

    def _lock_form_window(self, state: Dict[str, Any]) -> None:
        """Capture the form's window handle once (the window in front at 'GO'),
        so we can re-assert it every step and never drift into another window."""
        if self._locked_hwnd is not None:
            return
        try:
            import win32gui
            title = (state.get("window_title") or "").strip()
            hwnd = win32gui.FindWindow(None, title) if title else win32gui.GetForegroundWindow()
            if hwnd:
                self._locked_hwnd  = hwnd
                self._locked_title = title
                logger.info("Form window LOCKED: %r (hwnd=%s)", title[:40], hwnd)
        except Exception as exc:
            logger.debug("Form-lock failed: %s", exc)

    def _reassert_form_window(self) -> None:
        """Bring the locked form back to foreground if focus drifted away. Runs at
        the top of every step → the model physically cannot act on another window."""
        if not self._locked_hwnd:
            return
        try:
            import win32gui
            fg = win32gui.GetForegroundWindow()
            if fg == self._locked_hwnd:
                return
            if not win32gui.IsWindow(self._locked_hwnd):
                return

            # A MODAL dialog owned by the SAME process as the locked form (e.g. a
            # wx.MessageBox popped by clicking Submit) blocks SetForegroundWindow
            # from ever winning the form back — Windows won't foreground an owner
            # window while its modal child is open, so re-asserting alone loops
            # forever. Detect this generically (same PID, different hwnd — no
            # hardcoded dialog titles/text) and dismiss it with Escape first.
            try:
                import win32process
                _, form_pid = win32process.GetWindowThreadProcessId(self._locked_hwnd)
                _, fg_pid   = win32process.GetWindowThreadProcessId(fg)
                if fg and fg_pid == form_pid:
                    logger.warning(
                        "Foreign window from the form's own process is blocking it "
                        "(hwnd=%s) — likely a modal dialog. Dismissing with Escape.", fg)
                    import pyautogui
                    pyautogui.press("escape")
                    time.sleep(0.15)
            except Exception as _dlg_exc:
                logger.debug("Modal-dialog dismiss check failed: %s", _dlg_exc)

            # Alt keypress satisfies the SetForegroundWindow focus-steal restriction.
            try:
                import win32com.client
                win32com.client.Dispatch("WScript.Shell").SendKeys('%')
            except Exception:
                pass
            win32gui.SetForegroundWindow(self._locked_hwnd)
            time.sleep(0.05)
            logger.info("Re-asserted form foreground (focus had drifted off the form).")
        except Exception as exc:
            logger.debug("Re-assert form failed: %s", exc)

    def _form_rect(self, state: Dict[str, Any]):
        """Live (l, t, r, b) of the locked form window."""
        try:
            import win32gui
            hwnd = self._locked_hwnd or win32gui.GetForegroundWindow()
            return win32gui.GetWindowRect(hwnd)
        except Exception:
            sw, sh = state.get("screen_resolution", [1920, 1200])
            return (0, 0, sw, sh)

    def _point_in_form(self, pos, state: Dict[str, Any], margin: int = 6) -> bool:
        """True if a click target falls inside the locked form window (so the
        transformer can't drift a click into another window)."""
        if not pos or len(pos) < 2:
            return False
        l, t, r, b = self._form_rect(state)
        return (l - margin) <= pos[0] <= (r + margin) and (t - margin) <= pos[1] <= (b + margin)

    def _form_viewport_bottom(self, state: Dict[str, Any]) -> float:
        """Live bottom edge of the form's scroll viewport (locked window rect,
        clamped to the screen). Used to tell on-screen fields from scrolled-off
        ones — the window size varies per run, so this must be read live."""
        sh = state.get("screen_resolution", [1920, 1200])[1]
        try:
            import win32gui
            hwnd = self._locked_hwnd or win32gui.GetForegroundWindow()
            _, _, _, wb = win32gui.GetWindowRect(hwnd)
            return min(wb, sh)
        except Exception:
            return sh

    def _ensure_foreground(self, state: Dict[str, Any]) -> None:
        """Re-assert the observed active window as foreground before typing, so
        keystrokes can't leak into a window that stole focus (e.g. the terminal).
        Best-effort + universal — re-focuses whatever window we just observed."""
        try:
            import win32gui
            title = (state.get("window_title") or "").strip()
            if not title:
                return
            fg = win32gui.GetForegroundWindow()
            if (win32gui.GetWindowText(fg) or "").strip() == title:
                return                      # already in front — nothing to do
            hwnd = win32gui.FindWindow(None, title)
            if hwnd:
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.05)
                logger.info("Foreground guard: re-focused %r before typing", title[:40])
        except Exception:
            pass

    def _detect_section(self, state: Dict[str, Any], focused_el: Dict) -> str:
        """
        Return the repeated-section name (e.g. 'Driver 2') the focused element
        belongs to, or '' if none / the scope has no sections.

        Generic mechanism: the section is the lowest section-pane whose top edge
        is at/above the focused field's vertical centre. The pane prefix, the
        name pattern, and the display format all come from the injected
        ScopeConfig — so a non-sectioned app (default scope, pattern=None) gets
        '' for free, and no app-specific names live here.
        """
        import re as _re
        _pat = getattr(self._scope, "section_pattern", None)
        if not _pat:                         # scope has no sections → no-op
            return ""
        if not focused_el or not focused_el.get("bbox"):
            return ""
        _prefix = getattr(self._scope, "section_prefix", "section_")
        fx1, fy1, fx2, fy2 = focused_el["bbox"]
        fy_center = (fy1 + fy2) / 2

        section_panes = sorted(
            [e for e in state.get("elements", [])
             if e.get("type") == "panecontrol"
             and e.get("window_role") != "background"
             and e.get("bbox")
             and (e.get("label") or e.get("text") or "").startswith(_prefix)],
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
        m = _re.match(_pat, current_label.lower())
        if m:
            return self._scope.section_format(*m.groups())
        return ""

    def _lookup_field(self, field_name: str, section: str = "") -> str:
        """
        Look up a field value from the cached record.
        If `section` is provided (e.g. 'Driver 2'), try the section-prefixed key
        first (e.g. 'Driver 2 First Name') then fall back to the bare key.
        Returns empty string if not found or if value is a placeholder.
        """
        if not self._cached_record:
            return ""
        rec = self._cached_record

        import re as _re

        def _is_blank(v: str) -> bool:
            """A record value that means 'leave the field empty'. Robust to parens
            and trailing notes: '(leave blank)', 'leave blank — liability only',
            '(none)', 'none', 'n/a' all count as blank → field is skipped."""
            if not v:
                return True
            n = v.lower().strip().strip("()").strip()
            return (n in {"none", "n/a", "na", ""}
                    or n.startswith("leave blank")
                    or n.startswith("none "))

        def _get(key: str) -> str:
            kl = key.lower()
            v = rec.get(key) or next((rv for rk, rv in rec.items() if rk.lower() == kl), "")
            return "" if _is_blank(v) else v

        def _get_fuzzy(key: str) -> str:
            """Strip punctuation/spaces and match any record key that contains all words."""
            kl_words = set(_re.sub(r"[^a-z0-9 ]", " ", key.lower()).split())
            if not kl_words:
                return ""
            for rk, rv in rec.items():
                rk_words = set(_re.sub(r"[^a-z0-9 ]", " ", rk.lower()).split())
                if kl_words <= rk_words or rk_words <= kl_words:
                    if not _is_blank(rv):
                        return rv
            return ""

        if section:
            val = _get(f"{section} {field_name}")
            if val:
                return val
        val = _get(field_name)
        if not val:
            val = _get_fuzzy(field_name)
        return val

    def _auto_fill(self, state: Dict[str, Any]) -> Optional[tuple]:
        """
        If the focused element is an empty text field that has a known value in
        the record cache (or BACKGROUND DATA as fallback), return (field_name, value).
        Only fires for editcontrol — not comboboxes/checkboxes.
        """
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

        # Detect which Driver/Vehicle section this field is in (e.g. "Driver 2")
        # so we look up "Driver 2 First Name" instead of bare "First Name".
        section = self._detect_section(state, focused)
        # Use section-prefixed key in _filled_this_tab so "Driver 2 First Name"
        # and "Driver 3 First Name" are tracked independently.
        filled_key = f"{section} {field_name}" if section else field_name

        # Skip re-filling a field we already filled this tab (prevents cycling loops
        # where the form clears numeric/spin fields on re-focus).
        if filled_key in self._filled_this_tab:
            return None

        if current:
            # Field has a value — check if it's wrong (leftover from a previous run).
            # If wrong, return it flagged for overwrite (Ctrl+A before typing).
            expected_check = self._lookup_field(field_name, section=section)
            _skip_check = {"(none)", "none", "(leave blank)", "n/a", "yes (check)",
                           "leave blank — liability only", "leave blank — owned outright"}
            if (expected_check
                    and expected_check.lower().strip("()") not in _skip_check
                    and current.lower() != expected_check.lower()):
                logger.info("Auto-fill: '%s' has wrong value %r — will overwrite with %r",
                            filled_key, current, expected_check)
                return (filled_key, expected_check, True)   # True = needs Ctrl+A clear first
            return None   # correct value or no expected — let auto_skip handle it

        # ── Primary: use the cached full record (bypasses 2000-char UIA cap) ──
        expected = self._lookup_field(field_name, section=section)

        # ── Fallback: parse bg_blobs from UIA state ───────────────────────────
        if not expected:
            bg_elems = [e for e in elements if e.get("window_role") == "background"]
            bg_blobs = [(e.get("value") or "").strip() for e in bg_elems if e.get("value")]
            sec_key  = f"{section} {field_name}" if section else ""
            for blob in sorted(bg_blobs, key=len, reverse=True):
                r = _parse_records(blob)
                if r:
                    rec = r.get(self._record_num, r.get(min(r), {}))
                    fl  = field_name.lower()
                    # Try section-prefixed key first, then bare key
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

        # Return filled_key (section-prefixed if in a driver/vehicle section) as the
        # first element so the caller uses it for _filled_this_tab tracking, keeping
        # "Driver 2 First Name" and "Driver 3 First Name" independent.
        return (filled_key, expected, False)   # False = field is empty, no clear needed

    def _combobox_needs_fix(self, state: Dict[str, Any]) -> Optional[tuple]:
        """
        If the focused element is a combobox whose current value does NOT match
        BACKGROUND DATA, return (field_name, current_value, expected_value).
        Otherwise return None.
        """
        elements   = state.get("elements", [])
        focused_id = state.get("focused_element_id")
        focused    = next((e for e in elements if e.get("element_id") == focused_id), None)
        if not focused:
            return None
        if focused.get("type") != "comboboxcontrol":
            return None
        current = (focused.get("value") or "").strip()
        # NOTE: do NOT short-circuit on empty current — comboboxes now have a blank
        # first option, so current="" is the default state and may need to be fixed.

        field_name = (focused.get("label") or focused.get("text") or "").strip()
        if not field_name:
            return None
        section  = self._detect_section(state, focused)

        # Check raw record value so we can detect "(leave blank)" sentinels that
        # _lookup_field strips out.
        _leave_blank_raws = {"(none)", "none", "(leave blank)", "n/a",
                             "leave blank — liability only", "leave blank — owned outright"}
        if self._cached_record:
            _rec = self._cached_record
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
                    # Non-empty combobox should be cleared — select the blank option
                    return (field_name, current, "")
                return None   # already blank

        expected = self._lookup_field(field_name, section=section)
        if not expected or current.lower() == expected.lower():
            return None
        return (field_name, current, expected)

    def _auto_check(self, state: Dict[str, Any]) -> Optional[tuple]:
        """
        If the focused element is a checkbox, look it up in BACKGROUND DATA.
        Returns (field_name, should_check: bool) if the field is found, else None.
        When should_check is True the caller clicks the checkbox; when False it tabs past.
        """
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
            return None   # not in background data — let LLM decide

        # "YES (check)", "yes", "true", "checked" → check it
        ev = expected.lower().strip()
        should_check = ev.startswith("yes") or ev in {"check", "true", "1", "checked"}
        return (field_name, should_check)

    def _scroll_form_down(self, state: Dict[str, Any]) -> bool:
        """
        Scroll the active form window down to reveal fields hidden below the visible area.
        Uses pyautogui.scroll over the center of the active window elements.
        Returns True if scroll was attempted.
        """
        try:
            import pyautogui
            elements = state.get("elements", [])
            # Exclude comboboxcontrol — scroll wheel over a combobox changes its value.
            _SAFE_TYPES = {
                "editcontrol", "checkboxcontrol",
                "radiobuttoncontrol", "tabitemcontrol", "buttoncontrol",
            }
            active = [e for e in elements
                      if e.get("type") in _SAFE_TYPES
                      and e.get("window_role") != "background"
                      and e.get("bbox")]
            # Fallback: any non-background, non-combobox element with bbox
            if not active:
                active = [e for e in elements
                          if e.get("type") != "comboboxcontrol"
                          and e.get("window_role") != "background" and e.get("bbox")]
            if not active:
                return False
            # Use centroid of active form elements, capped to the middle of the
            # screen so repeated scrolls don't push the target off the bottom edge.
            xs = [(e["bbox"][0] + e["bbox"][2]) / 2 for e in active]
            ys = [(e["bbox"][1] + e["bbox"][3]) / 2 for e in active]
            cx = sum(xs) / len(xs)
            cy = min(sum(ys) / len(ys), state.get("screen_resolution", [1920, 1080])[1] * 0.55)
            # Final safety: if the computed point is inside a combobox, shift left
            _cb_list = [e for e in elements
                        if e.get("type") == "comboboxcontrol" and e.get("bbox")]
            for _cb in _cb_list:
                bx1, by1, bx2, by2 = _cb["bbox"]
                if bx1 <= cx <= bx2 and by1 <= cy <= by2:
                    cx = max(bx1 - 40, 10)
                    logger.debug("Scroll-form: shifted cx=%.0f to avoid combobox", cx)
                    break
            orig = pyautogui.position()
            pyautogui.moveTo(cx, cy, duration=0.15)
            pyautogui.scroll(-5)   # 5 scroll units down
            pyautogui.moveTo(orig.x, orig.y, duration=0.1)
            logger.info("Scroll-form: scrolled down at form center (%.0f, %.0f)", cx, cy)
            return True
        except Exception as exc:
            logger.warning("Scroll-form: failed — %s", exc)
            return False

    # ── EXPERIMENTAL: OCR background-window reader ───────────────────────────
    # Uses Tesseract to screenshot the background window (Notepad, PDF, etc.)
    # and convert detected text boxes into trace-format elements tagged
    # source="ocr", window_role="background".  Only fires when UIA returns
    # thin background data (≤1 char).  Result is cached per window title so
    # Tesseract only runs once per unique background document, not every step.

    def _ocr_background_window(self, state: Dict[str, Any]) -> List[Dict]:
        """
        Screenshot the background window and run Tesseract OCR on it.
        Returns a list of trace-compatible elements (or [] on any error).
        Results are cached by window title so OCR only runs once per document.
        """
        try:
            import pytesseract
            from PIL import ImageGrab
            import win32gui
        except ImportError:
            return []

        # Find the background window hwnd and title
        bg_hwnd  = None
        bg_title = ""
        elements = state.get("elements", [])
        for e in elements:
            if e.get("window_role") != "background":
                continue
            win_title = (e.get("window_title") or "").strip()
            if not win_title:
                continue
            hwnd = win32gui.FindWindow(None, win_title)
            if hwnd:
                bg_hwnd  = hwnd
                bg_title = win_title
                break

        if not bg_hwnd or not bg_title:
            return []

        # Return cached result if we've already OCR'd this window
        if bg_title in self._ocr_cache:
            logger.debug("OCR cache hit for %r (%d elements)", bg_title,
                         len(self._ocr_cache[bg_title]))
            return self._ocr_cache[bg_title]

        logger.info("OCR: scanning background window %r …", bg_title)

        try:
            # Screenshot only the background window region
            rect = win32gui.GetWindowRect(bg_hwnd)   # (left, top, right, bottom)
            img  = ImageGrab.grab(bbox=rect)
            win_x, win_y = rect[0], rect[1]

            # Run Tesseract — page segmentation mode 11 (sparse text) works
            # well for mixed label+value layouts like insurance forms / PDFs.
            data = pytesseract.image_to_data(
                img,
                output_type=pytesseract.Output.DICT,
                config="--psm 11",
            )

            ocr_elems = []
            n_boxes = len(data["text"])
            for i in range(n_boxes):
                conf = int(data["conf"][i])
                text = (data["text"][i] or "").strip()
                if conf < 30 or not text:
                    continue

                # Convert from image-relative coords to screen coords
                x1 = win_x + data["left"][i]
                y1 = win_y + data["top"][i]
                x2 = x1    + data["width"][i]
                y2 = y1    + data["height"][i]

                # Infer element type from text shape
                if text.endswith(":"):
                    elem_type = "label"
                elif len(text) > 3 and text[0].isdigit():
                    elem_type = "input"
                else:
                    elem_type = "label"

                ocr_elems.append({
                    "element_id":   f"ocr_{i}",
                    "type":         elem_type,
                    "control_type": "OCR",
                    "bbox":         [x1, y1, x2, y2],
                    "text":         text,
                    "value":        text,
                    "label":        text,
                    "enabled":      True,
                    "visible":      True,
                    "focused":      False,
                    "confidence":   conf / 100.0,
                    "source":       "ocr",
                    "window_role":  "background",
                    "window_title": bg_title,
                    "app":          "",
                    "metadata":     {"ocr_conf": conf},
                })

            logger.info("OCR: found %d text boxes in %r", len(ocr_elems), bg_title)
            self._ocr_cache[bg_title] = ocr_elems
            return ocr_elems

        except Exception as exc:
            logger.warning("OCR scan failed: %s", exc)
            return []

    def _ensure_form_foreground(self, state: Dict[str, Any]) -> None:
        """
        Bring the form window to the foreground before sending keyboard events.
        pyautogui sends keystrokes to the OS foreground window — if Notepad or
        the terminal grabbed focus, keystrokes land there instead of the form.
        Uses the hwnd of the first active (non-background) element in state.
        """
        try:
            import win32gui
            elements = state.get("elements", [])
            active_el = next((e for e in elements
                              if e.get("window_role") != "background"
                              and e.get("bbox")), None)
            if not active_el:
                return
            bbox = active_el["bbox"]
            cx, cy = int((bbox[0] + bbox[2]) / 2), int((bbox[1] + bbox[3]) / 2)
            hwnd = win32gui.WindowFromPoint((cx, cy))
            if not hwnd:
                return
            # Walk up to the top-level window
            parent = win32gui.GetParent(hwnd)
            while parent:
                hwnd   = parent
                parent = win32gui.GetParent(hwnd)
            fg = win32gui.GetForegroundWindow()
            if fg == hwnd:
                return  # already foreground — nothing to do
            win32gui.SetForegroundWindow(hwnd)
            import time as _t; _t.sleep(0.05)
        except Exception:
            pass

    def _scroll_form_to_top(self, state: Dict[str, Any]) -> None:
        """
        Scroll the active form window back to the top so that _focus_first_empty_field
        always starts from the first visible field, not mid-page.
        Uses Ctrl+Home then multiple large scroll-up passes to guarantee reaching the top.
        """
        try:
            import pyautogui
            elements = state.get("elements", [])
            # Exclude comboboxcontrol — scroll wheel over a combobox changes its value.
            _SAFE_TYPES = {
                "editcontrol", "checkboxcontrol",
                "radiobuttoncontrol", "tabitemcontrol", "buttoncontrol",
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
            # Final safety: if the computed point is inside a combobox, shift left
            _cb_list2 = [e for e in elements
                         if e.get("type") == "comboboxcontrol" and e.get("bbox")]
            for _cb2 in _cb_list2:
                bx1, by1, bx2, by2 = _cb2["bbox"]
                if bx1 <= cx <= bx2 and by1 <= cy <= by2:
                    cx = max(bx1 - 40, 10)
                    break
            orig = pyautogui.position()
            pyautogui.moveTo(cx, cy, duration=0.15)
            # Click the form body so it receives keyboard events, then Ctrl+Home
            # to jump unconditionally to scroll position (0, 0).  Follow with
            # several large upward scrolls as insurance for panels that don't
            # honour Ctrl+Home.
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

    def _uia_focus_first_field(self) -> bool:
        """
        Walk the UIA tree of the foreground window and SetFocus on the first
        EditControl or ComboBox not yet handled this tab pass.
        Restricts the walk to the currently active tab pane so that fields on
        inactive tabs (whose deeply-nested controls can still have positive
        BoundingRectangles in UIA even after wx hides the panel) are never
        mistakenly focused.
        Returns True if a field was found and focused.
        """
        try:
            import win32gui as _w32g
            import uiautomation as _uia
            fg = _w32g.GetForegroundWindow()
            if not fg:
                return False
            root = _uia.ControlFromHandle(fg)
            _filled_lower = {s.lower() for s in self._filled_this_tab}
            _TARGET_NAMES = {"Edit", "ComboBox"}

            # ── Detect active tab pane ────────────────────────────────────────
            # Bug found 2026-08-07, live: iterating _TAB_PANE_NAMES in list order
            # and taking the FIRST one that "exists with a positive-coord child"
            # is not the same as finding the ACTUALLY active one — verified live
            # this caused the agent to lock onto an earlier tab's pane and skip
            # straight past Policyholder (Policy -> Vehicle -> Coverage, only
            # 3/13 Policy fields touched) because that earlier pane satisfied the
            # loop's check first. self._current_tab_idx is already reliably
            # maintained elsewhere (it correctly detects "already on this tab"),
            # so use it directly instead of guessing from coordinates.
            _TAB_PANE_NAMES = self._scope.tab_pane_names   # scope-provided; [] → none
            _search_root = root   # fallback: search entire tree
            if 0 <= self._current_tab_idx < len(_TAB_PANE_NAMES):
                _pname = _TAB_PANE_NAMES[self._current_tab_idx]
                # Bug found 2026-08-07, live, right after the fix above landed:
                # this lookup ran ~3s after the tab-switch click and still came
                # up empty for a genuinely-active, 31-field tab (Policyholder) —
                # wx hadn't finished registering the new page's controls into
                # the UIA tree yet. A single maxSearchSeconds=0.05 check doesn't
                # give it room to settle. Retry briefly before accepting "not
                # found" and falling back to the coordinate-guess scan, which
                # is exactly the path that then wrongly skips the whole tab.
                _pane = None
                for _attempt in range(4):   # ~0.05 + 0.2*3 = up to ~0.65s total
                    _pane = root.PaneControl(searchDepth=6, Name=_pname)
                    if _pane.Exists(maxSearchSeconds=0.05):
                        break
                    time.sleep(0.2)
                if _pane is not None and _pane.Exists(maxSearchSeconds=0.05):
                    _search_root = _pane
                    logger.info("_uia_focus_first_field: active pane = %r (tab idx %d)",
                                _pname, self._current_tab_idx)
                else:
                    logger.warning("_uia_focus_first_field: pane %r (tab idx %d) not found after retries — "
                                    "falling back to coordinate-guess scan.",
                                    _pname, self._current_tab_idx)
                    # Diagnostics — the retry fix didn't hold up on a live re-test
                    # (still "not found" ~4s after the click, ruling out a simple
                    # timing gap). Dump exactly what's really in the tree instead
                    # of guessing at another fix blind: which window this actually
                    # queried, and what pane names genuinely exist at this moment.
                    try:
                        _fg_title = _w32g.GetWindowText(fg)
                        logger.warning("_uia_focus_first_field DIAG: foreground hwnd=%s title=%r", fg, _fg_title)
                        _found_panes = []
                        def _dump(ctrl, depth=0):
                            if depth > 5:
                                return
                            try:
                                if ctrl.ControlTypeName == "PaneControl":
                                    _found_panes.append((ctrl.Name, depth))
                            except Exception:
                                pass
                            for c in ctrl.GetChildren():
                                _dump(c, depth + 1)
                        _dump(root)
                        logger.warning("_uia_focus_first_field DIAG: panes actually in tree = %s", _found_panes[:20])
                    except Exception as _diag_exc:
                        logger.warning("_uia_focus_first_field DIAG: dump failed — %s", _diag_exc)
            if _search_root is root:
                # Fallback only: current-tab-idx lookup unavailable/failed. Same
                # coordinate-based guess as before — kept for robustness, not trusted.
                for _pname in _TAB_PANE_NAMES:
                    _pane = root.PaneControl(searchDepth=6, Name=_pname)
                    if not _pane.Exists(maxSearchSeconds=0.05):
                        continue
                    for _ch in _pane.GetChildren():
                        try:
                            _r = _ch.BoundingRectangle
                            if _r.left >= 0 and _r.top >= 0 and _r.width > 0:
                                _search_root = _pane   # restrict walk to this pane
                                break
                        except Exception:
                            pass
                    if _search_root is not root:
                        logger.info("_uia_focus_first_field: active pane = %r (coordinate guess)", _pname)
                        break

            # ── Walk the active pane (or full tree as fallback) ───────────────
            # Collect ALL candidates, then sort by screen position (top → left)
            # so we always focus the visually-topmost field regardless of
            # the UIA child enumeration order (which differs from creation order
            # on wxPython ScrolledPanel controls).
            _candidates: list = []

            def _walk(ctrl, depth=0):
                if depth > 12:
                    return
                try:
                    ctn = ctrl.ControlTypeName
                except Exception:
                    ctn = ""
                if ctn in _TARGET_NAMES:
                    name = (ctrl.Name or "").strip()
                    if name and name.lower() not in _filled_lower:
                        try:
                            rect = ctrl.BoundingRectangle
                            # Only include controls that are actually on-screen
                            # (positive coords, non-zero size).  This filters out
                            # controls on inactive tab panels that wx parks off-screen.
                            if (rect.left >= 0 and rect.top >= 0
                                    and rect.width > 0 and rect.height > 0):
                                _candidates.append((rect.top, rect.left, ctrl))
                        except Exception:
                            pass
                for child in ctrl.GetChildren():
                    _walk(child, depth + 1)

            _walk(_search_root)
            if not _candidates:
                return False
            _candidates.sort(key=lambda t: (t[0], t[1]))
            target = _candidates[0][2]
            target.SetFocus()
            logger.info("Tab-advance focus [pane-scoped]: UIA SetFocus on %r (first unhandled)", (target.Name or "").strip())
            return True
        except Exception as exc:
            logger.warning("_uia_focus_first_field: failed — %s", exc)
            return False

    def _focus_first_empty_field(
        self,
        state:        Dict[str, Any],
        after_scroll: bool = False,
        min_y:        float = 0,
    ) -> bool:
        """
        Click the first editcontrol on the current tab that has not yet been
        handled this tab pass.  Includes both empty fields (need filling) and
        non-empty fields with wrong values (need overwriting) — the auto-fill
        overwrite / auto-skip logic handles each field correctly once focused.
        Returns True if a field was found and clicked, False otherwise.

        after_scroll=True : historical flag (kept for callers); filtering is now
                            always done via _filled_this_tab.
        min_y             : only consider fields whose top-edge y >= min_y (used
                            by pane-escape to avoid jumping back above the pane).

        Bug found 2026-08-07 via a live stuck-loop (repeatedly re-focusing a
        field from an INACTIVE tab, forever): the candidate scan below trusts
        `state["elements"]` bboxes to tell active-tab fields from inactive-tab
        ones, which does not hold — verified live that a hidden tab's field
        can report the exact same positive on-screen bbox as when visible.
        What IS reliable, also verified live: the inactive tab's own PANE
        genuinely stops existing in the UIA tree (Exists()==False), while the
        active tab's pane exists with real children. `_uia_focus_first_field`
        already implements exactly that pane-scoped search correctly.

        First fix attempt tried it, then fell back to the scan below on
        failure — still broken, caught live: when the active pane is
        genuinely out of unhandled fields, `_uia_focus_first_field` correctly
        returns False, but the "fallback" then went and found a WRONG-tab
        field via the very scan that can't tell tabs apart, masking the one
        signal (`False` = nothing left here) that callers already use
        correctly to advance to the next tab (`_try_advance_tab`). So for the
        common case (min_y<=0, true for every current caller), the pane-scoped
        result is now authoritative — no unsafe fallback. The state-dict scan
        only still runs for the min_y>0 pane-escape case, which
        `_uia_focus_first_field` doesn't support and no caller currently uses.
        """
        if min_y <= 0:
            return self._uia_focus_first_field()
        elements = state.get("elements", [])
        # Compute actual tab-strip bottom so clicks never land on the tab bar.
        _tab_bottoms = [
            e["bbox"][3] for e in elements
            if e.get("type") in ("tabitem", "tabitemcontrol")
            and e.get("window_role") != "background"
            and e.get("bbox")
        ]
        _tab_floor = (max(_tab_bottoms) + 5) if _tab_bottoms else 110
        # For tab-switch (min_y=0) use no y-floor: fields above the viewport have negative
        # screen-y in a scrolled panel but UIA SetFocus reaches them regardless of position.
        # For pane-escape (min_y=_pane_y>0) keep the floor so we don't jump above the pane.
        _min_y = min_y if min_y > 0 else float("-inf")
        # Candidates: any enabled editcontrol or combobox not yet handled this tab pass.
        # _filled_this_tab stores section-prefixed keys (e.g. "Driver 2 First Name") for
        # driver/vehicle fields and bare names for top-level fields.
        _filled_lower = {s.lower() for s in self._filled_this_tab}
        fillable = []
        for e in elements:
            if (e.get("window_role") == "background"
                    or e.get("type") not in ("editcontrol", "comboboxcontrol")
                    or not e.get("bbox")
                    or not e.get("enabled", True)
                    or e["bbox"][1] < _min_y):
                continue
            fn   = (e.get("label") or e.get("text") or "").strip()
            sec  = self._detect_section(state, e)
            fkey = f"{sec} {fn}".lower() if sec else fn.lower()
            if fkey in _filled_lower or fn.lower() in _filled_lower:
                continue   # already handled this tab pass
            fillable.append(e)
        if not fillable:
            return False
        # Sort top-to-bottom so we get the topmost unfilled field
        fillable.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
        e = fillable[0]
        x1, y1, x2, y2 = e["bbox"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        field_label = (e.get("label") or e.get("text") or "?").strip()
        # Try UIA SetFocus first — avoids coordinate mis-clicks near the tab strip.
        # Falls back to coordinate click if UIA search fails or times out.
        if field_label and field_label != "?":
            try:
                import win32gui as _w32g
                import uiautomation as _uia_mod
                _fg = _w32g.GetForegroundWindow()
                _root = _uia_mod.ControlFromHandle(_fg)
                # Try EditControl first, then ComboBox (for combobox fields)
                _ctrl = _root.EditControl(searchDepth=10, Name=field_label)
                if not _ctrl.Exists(maxSearchSeconds=0.2):
                    _ctrl = _root.ComboBoxControl(searchDepth=10, Name=field_label)
                if _ctrl.Exists(maxSearchSeconds=0.2):
                    _ctrl.SetFocus()
                    logger.info("Tab-advance focus [state-scan FALLBACK]: UIA SetFocus on %r (first unhandled)", field_label)
                    return True
            except Exception:
                pass  # fall through to coordinate click
        # Fallback: coordinate click, clipped to be within the field and below the tab strip
        cy = max(cy, _tab_floor)
        cy = min(cy, y2 - 2)
        logger.info("Tab-advance focus: clicking first unhandled field %r @ (%.0f, %.0f)", field_label, cx, cy)
        self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
        return True

    def _try_advance_tab(self, state: Dict[str, Any]) -> bool:
        """
        When stuck (too many no_change steps), click the next form tab.
        Returns True if a tab was found and clicked.
        """
        elements = state.get("elements", [])
        # Accept both UIA type names: "tabitem" (standard) and "tabitemcontrol" (wxPython)
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
            # Last resort: re-observe after scrolling form back to top so tab bar is visible
            logger.warning("_try_advance_tab: no tabs found — scroll to top and re-observe.")
            self._scroll_form_to_top(state)
            import time; time.sleep(0.5)
            state2 = self._observe()
            tabs = [
                e for e in state2.get("elements", [])
                if e.get("type") in ("tabitem", "tabitemcontrol")
                and e.get("window_role") != "background"
                and e.get("bbox")
            ]
            logger.info("_try_advance_tab: after re-observe found %d tab(s)", len(tabs))
            if not tabs:
                return False

        # Detect the active tab by finding which tab's elements have positive
        # screen coordinates.  wx moves inactive tab panels to negative coords,
        # so any tab whose children have bbox[1] >= 0 is the active one.
        # No hardcoded tab names — works for any tabbed form.
        _active_idx = self._current_tab_idx  # fallback
        _all_elems  = state.get("elements", [])
        for _tii, _tab_el in enumerate(tabs):
            _tbbox = _tab_el.get("bbox")
            if not _tbbox:
                continue
            _tab_cx = (_tbbox[0] + _tbbox[2]) / 2
            _tab_cy = (_tbbox[1] + _tbbox[3]) / 2
            # Check if any interactive element is spatially "below" this tab header
            # and has a positive y — meaning it belongs to an on-screen panel.
            _panel_elems = [
                e for e in _all_elems
                if e.get("window_role") != "background"
                and e.get("bbox")
                and e["bbox"][1] >= 0
                and e["bbox"][1] > _tab_cy   # below the tab strip
                and e.get("type") in (
                    "editcontrol", "comboboxcontrol", "checkboxcontrol",
                    "buttoncontrol", "button", "panecontrol",
                )
            ]
            if _panel_elems:
                _active_idx = _tii
                break
        # Sanity check: pane detection must stay within one step of the tracked index.
        # Jumping ahead OR falling behind the tracker is BoundingRectangle noise from
        # inactive panels that briefly have positive coordinates.  Trust the tracker.
        if _active_idx != self._current_tab_idx:
            logger.warning(
                "_try_advance_tab: pane detection drifted (detected=%d, tracked=%d) — using tracker.",
                _active_idx, self._current_tab_idx)
            _active_idx = self._current_tab_idx

        logger.info("_try_advance_tab: active tab detected as idx=%d (fallback=%d)",
                    _active_idx, self._current_tab_idx)

        next_idx = _active_idx + 1
        if next_idx >= len(tabs):
            logger.info("Stuck guard: already on last tab (%d tabs total) — signalling done.",
                        len(tabs))
            return False

        next_tab = tabs[next_idx]
        x1, y1, x2, y2 = next_tab["bbox"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        tab_name = (next_tab.get("text") or next_tab.get("label") or "?").strip()
        logger.info("Stuck guard: advancing to tab %r @ (%.0f, %.0f)", tab_name, cx, cy)
        # Blacklist the tab we're LEAVING (not the one we're going to) — see
        # _advance_blacklist_pos's own comment for why. Bounded to one entry:
        # this REPLACES whatever was blacklisted before, it doesn't accumulate.
        _left_tab = tabs[_active_idx]
        if _left_tab.get("bbox"):
            _lx1, _ly1, _lx2, _ly2 = _left_tab["bbox"]
            self._advance_blacklist_pos = {_round_click_pos([(_lx1 + _lx2) / 2, (_ly1 + _ly2) / 2])}
        self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
        self._current_tab_idx = next_idx
        return True

    def _merge(
        self,
        t_pred:     Dict[str, Any],
        t_conf:     float,
        llm_action: Dict[str, Any],
        state:      Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Combine transformer + LLM outputs into one prediction.

        Rule:
          - LLM decides the action type (it has the reasoning).
          - For CLICK: if transformer also says click with good confidence,
            use its learned coordinates instead of label-resolution — this
            reflects where the user actually clicks, not just element centres.
          - For KEYBOARD/TYPE: LLM supplies the text value; transformer's
            source_elem_idx acts as a fallback if LLM text is empty.
          - For HOTKEY / SCROLL: LLM wins entirely.
        """
        l_type = llm_action.get("action_type", "wait")

        # If transformer is confident it should click but LLM says type,
        # trust the transformer — it learned from real demos and knows which
        # elements are clickable vs typeable (e.g. comboboxes need click).
        # Exception: if transformer's click lands on a tab element, do NOT override —
        # the LLM is still trying to fill fields on the current tab.
        _TRANSFORMER_TYPE_OVERRIDE_THRESHOLD = 0.92
        if (l_type == "type"
                and t_pred.get("action_type") == "click"
                and t_conf >= _TRANSFORMER_TYPE_OVERRIDE_THRESHOLD
                and t_pred.get("click_position")):
            pos = t_pred["click_position"]
            _tab_elems = [e for e in state.get("elements", [])
                          if e.get("type") in ("tabitem", "tabitemcontrol")
                          and e.get("window_role") != "background"
                          and e.get("bbox")]
            _hits_tab = any(
                e["bbox"][0] <= pos[0] <= e["bbox"][2] and e["bbox"][1] <= pos[1] <= e["bbox"][3]
                for e in _tab_elems
            )
            _pos_key = (round(pos[0] / 10) * 10, round(pos[1] / 10) * 10)
            _pos_blacklisted = _pos_key in self._nochange_click_pos
            if _hits_tab:
                logger.info("[MERGE] TRANSFORMER wants tab advance but LLM says type — deferring to LLM")
            elif _pos_blacklisted:
                logger.info("[MERGE] TRANSFORMER click @ (%.0f,%.0f) blacklisted (prev no_change) — LLM types",
                            pos[0], pos[1])
            else:
                snapped = self._snap(pos, state)
                coords  = snapped or pos
                logger.info("[MERGE] TRANSFORMER overrides LLM type→click  conf=%.2f  @ (%.0f,%.0f)",
                            t_conf, coords[0], coords[1])
                return {"action_type": "click", "click_position": coords}

        # Hotkey / scroll — pure LLM reasoning, transformer can't help
        if l_type in ("hotkey", "scroll"):
            return self._llm_action_to_prediction(llm_action, state)

        # Type — LLM provides the value; transformer source_elem_idx as backup
        if l_type == "type":
            text = llm_action.get("text", "")
            if not text:
                src_idx = t_pred.get("source_elem_idx", -1)
                text = self._text_resolver.resolve(state, source_elem_idx=src_idx)
                if text:
                    logger.info("[MERGE] type: LLM had no text — TRANSFORMER source resolved %r", text[:40])
            if not text:
                return {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
            logger.info("[MERGE] type: value=%r", text[:40])
            return {"action_type": "keyboard", "key_count": len(text),
                    "keystrokes": list(text), "text": text}

        # Click — prefer LLM's named target (resolved to element bbox center),
        # fall back to transformer coords only when no resolvable label is available.
        if l_type == "click":
            # LLM decided it's a click (WHEN to move). Transformer decides WHICH
            # field (WHERE) — its navigation pointer is always exposed now and is
            # its real strength (click_acc ~0.76). Prefer it; fall back to LLM's
            # named target only if the transformer pointer is invalid.
            _click_conf = t_pred.get("_click_conf", 0.0)
            _t_pos      = t_pred.get("click_position")
            # In pure mode the transformer ALWAYS owns WHERE (LLM only supplies
            # values). Drop the confidence gate so the LLM can't hijack the click
            # target when the pointer is merely low-confidence.
            _conf_gate = 0.0 if self._no_autohandlers else 0.30
            if _t_pos and (_t_pos[0] > 1 or _t_pos[1] > 1) and _click_conf >= _conf_gate:
                snapped = self._snap(_t_pos, state)
                coords  = snapped or _t_pos
                logger.info("[MERGE] TRANSFORMER navigates — click @ (%.0f,%.0f)  ptr_conf=%.2f",
                            coords[0], coords[1], _click_conf)
                return {"action_type": "click", "click_position": coords}

            target = llm_action.get("target", "")
            if target:
                coords = _resolve_target(target, state)
                if coords is not None:
                    logger.info("[MERGE] LLM target (transformer ptr weak) — click=%r @ (%.0f,%.0f)",
                                target, coords[0], coords[1])
                    return {"action_type": "click", "click_position": coords}
            logger.info("[MERGE] LLM wins (no transformer click) — fallback to LLM position")
            return self._llm_action_to_prediction(llm_action, state)

        # Fallback
        logger.info("[MERGE] LLM wins (hotkey/scroll/fallback)")
        return self._llm_action_to_prediction(llm_action, state)

    def _value_for_focused(self, state: Dict[str, Any]) -> str:
        """Return the background-record value for the currently focused field, or ''."""
        elements   = state.get("elements", [])
        focused_id = state.get("focused_element_id")
        focused    = next((e for e in elements if e.get("element_id") == focused_id), None)
        if not focused:
            return ""
        field_name = (focused.get("label") or focused.get("text") or "").strip()
        if not field_name:
            return ""
        bg_elems = [e for e in elements if e.get("window_role") == "background"]
        bg_blobs = [(e.get("value") or "").strip() for e in bg_elems]
        bg_blobs = [b for b in bg_blobs if b]
        if not bg_blobs:
            return ""
        records = {}
        for blob in sorted(bg_blobs, key=len, reverse=True):
            r = _parse_records(blob)
            if r:
                records = r
                break
        if not records:
            return ""
        rec = records.get(self._record_num, records.get(min(records), {}))
        fl = field_name.lower()
        for k, v in rec.items():
            if k.lower() == fl:
                skip = {"(none)", "none", "(leave blank)", "n/a", "no", "yes (check)"}
                return "" if v.lower().strip("()") in skip else v
        return ""

    # ── LLM dispatch ─────────────────────────────────────────────────────────

    def _ask_llm(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # Prefer _cached_record (Win32 Notepad read, fully parsed) over visual_cache
        # (which is empty when VLM pre-scan is disabled). This prevents the LLM from
        # reading a truncated UIA text blob and hallucinating field values.
        _vc_for_llm = self._cached_record if self._cached_record else (self._visual_cache or None)
        screen_text = _state_to_text(
            state,
            record_num=self._record_num,
            visual_cache=_vc_for_llm,
            filled_labels=self._filled_this_tab,
        )
        logger.debug("LLM screen context:\n%s", screen_text)

        # Build focused-field banner — put it FIRST so the LLM sees it immediately
        focused_banner = ""
        focused_id = state.get("focused_element_id")
        if focused_id:
            focused_el = next(
                (e for e in state.get("elements", []) if e.get("element_id") == focused_id), None
            )
            if focused_el:
                _fn  = (focused_el.get("label") or focused_el.get("text") or "?").strip()[:200]
                _fv  = (focused_el.get("value") or "").strip()[:200]
                _ft  = focused_el.get("type", "?")
                _fsec = self._detect_section(state, focused_el)
                # Look up the expected value — section-qualified so Driver 3 First Name
                # returns "Tyler" not the policyholder's "James".
                _expected = self._lookup_field(_fn, section=_fsec) if _fn != "?" else ""
                # Cache miss — force a live re-read of Notepad then retry
                if not _expected and _fn != "?":
                    self._refresh_record_cache(state)
                    _expected = self._lookup_field(_fn, section=_fsec)
                # Still nothing — direct peek for this specific field
                if not _expected and _fn != "?":
                    self._peek_notepad(state, _fn)
                    _expected = self._lookup_field(_fn, section=_fsec)
                logger.info("LLM focused-field lookup: field=%r  expected=%r  cache_size=%d",
                            _fn, _expected[:40] if _expected else "", len(self._cached_record))

                # Fast path found 2026-08-07: the record lookup above already
                # HAS the answer — every logged run tonight showed 100% value
                # accuracy, meaning this direct/constant lookup was already
                # correct every single time. The code used to hand that exact
                # value to the LLM as a hint ("use EXACTLY this string... do
                # NOT invent") and then pay a full ~5s network round-trip just
                # to have it echoed back. Skip the call entirely when the
                # lookup is confident; only fall through to the LLM for
                # fields the record genuinely doesn't answer (blank/derived/
                # ambiguous ones — the case it's actually needed for).
                if _expected:
                    logger.info("LLM call skipped — direct lookup already answered %r -> %r",
                                _fn, _expected[:40])
                    return {"action_type": "type", "text": _expected, "_fast_path": "lookup"}

                _expect_hint = (
                    f"\n  → EXPECTED VALUE FROM DATA SOURCES: {_expected!r}"
                    f"\n  → Use EXACTLY this string as 'text'. Do NOT modify or invent."
                    if _expected else ""
                )
                focused_banner = (
                    f"⚠ CURRENTLY FOCUSED FIELD: [{_ft}] \"{_fn}\""
                    + (f" — current value: {_fv!r}" if _fv else " — EMPTY")
                    + _expect_hint
                    + "\nYou MUST act on THIS field only. Do NOT click other fields.\n\n"
                )

        # Build a compact already-filled summary so the LLM doesn't revisit fields
        filled_fields = []
        for e in state.get("elements", []):
            if e.get("window_role") == "background":
                continue
            val = (e.get("value") or "").strip()
            lbl = (e.get("label") or e.get("text") or "").strip()
            if val and lbl and len(lbl) <= 200 and len(val) <= 200:
                filled_fields.append(f"  {lbl} = {val!r}")
        filled_summary = ""
        if filled_fields:
            filled_summary = "\nAlready filled (DO NOT retype these):\n" + "\n".join(filled_fields[:20])

        user_msg = (
            f"{focused_banner}"
            f"Task goal: {self.goal}\n\n"
            f"Current screen:\n{screen_text}"
            f"{filled_summary}\n\n"
            f"Recent actions:\n{_history_to_text(self._history)}"
        )

        logger.info("LLM prompt size: user_msg=%d chars  screen_text=%d chars",
                    len(user_msg), len(screen_text))

        # Hard safety cap — LMStudio default context is 4096 tokens (~16k chars)
        _MAX_USER_CHARS = 12000
        if len(user_msg) > _MAX_USER_CHARS:
            logger.warning("user_msg too large (%d chars) — truncating screen_text to fit",
                           len(user_msg))
            overhead   = len(user_msg) - len(screen_text)
            keep_chars = max(2000, _MAX_USER_CHARS - overhead)
            screen_text = screen_text[:keep_chars]
            user_msg = (
                f"{focused_banner}"
                f"Task goal: {self.goal}\n\n"
                f"Current screen:\n{screen_text}"
                f"{filled_summary}\n\n"
                f"Recent actions:\n{_history_to_text(self._history)}"
            )
            logger.info("After truncation: user_msg=%d chars", len(user_msg))

        try:
            if self.provider == "anthropic":
                return self._call_anthropic(user_msg)
            elif self.provider == "groq":
                return self._call_openai_compat(user_msg)
            elif self.provider == "gemini":
                return self._call_gemini(user_msg)
            elif self.provider == "lmstudio":
                return self._call_openai_compat(user_msg)
        except json.JSONDecodeError as e:
            logger.warning("LLM non-JSON response: %s", e)
        except Exception as exc:
            logger.warning("LLM call failed: %s", exc)
        return {"action_type": "wait", "reason": "llm unavailable"}

    def _llm_action_to_prediction(
        self, llm_action: Dict[str, Any], state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Convert an LLM action dict into the executor's prediction format."""
        action_type = llm_action.get("action_type", "wait")

        if action_type == "click":
            target = llm_action.get("target", "")
            coords = _resolve_target(target, state)
            if coords is None:
                logger.warning("LLM target %r not found in element tree — skipping.", target)
                return {"action_type": "no_op"}
            logger.info("Resolved %r → (%.0f, %.0f)", target, coords[0], coords[1])
            return {"action_type": "click", "click_position": coords}

        elif action_type == "type":
            text = llm_action.get("text", "")
            if not text:
                logger.warning("LLM 'type' action has no text — pressing Tab to skip field.")
                return {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
            return {"action_type": "keyboard", "key_count": len(text),
                    "keystrokes": list(text), "text": text}

        elif action_type == "tab":
            # LLM is signalling "advance to next field/tab" — press Tab key
            return {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}

        elif action_type == "hotkey":
            keys = llm_action.get("keys", [])
            if not keys:
                return {"action_type": "no_op"}
            return {"action_type": "keyboard", "key_count": len(keys),
                    "keystrokes": keys}

        elif action_type == "scroll":
            target    = llm_action.get("target", "")
            direction = llm_action.get("direction", "down")
            clicks    = int(llm_action.get("clicks", 3))
            # Resolve target to coordinates — scroll at that position
            coords = _resolve_target(target, state) if target else None
            if coords is None:
                # Fall back to centre of screen if no target
                res    = state.get("screen_resolution", [1920, 1080])
                coords = [res[0] / 2, res[1] / 2]
            return {"action_type": "scroll", "click_position": coords,
                    "direction": direction, "clicks": clicks}

        return {"action_type": "no_op"}

    def _call_anthropic(self, user_msg: str) -> Dict[str, Any]:
        resp = self._llm_client.messages.create(
            model=self._llm_model,
            max_tokens=256,
            system=self._system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        return _parse_llm_response(resp.content[0].text)

    def _call_openai_compat(self, user_msg: str) -> Dict[str, Any]:
        """Handles both Groq and LM Studio — both use the OpenAI client format."""
        # A random per-call [sid:...] tag used to be appended here specifically
        # to defeat LM Studio's KV-cache reuse ("breaks server-side KV-cache
        # accumulation"). Traced 2026-08-07: that line was bundled into an
        # unrelated commit (OCR-cache instance scoping) with no diagnosed bug,
        # no test, no explanation — not a proven fix. Meanwhile it forced the
        # full ~750-token system prompt to be reprocessed from scratch on
        # every call, measured as the dominant per-step latency cost (~5s/call
        # regardless of max_tokens). Removed so the identical system-prompt
        # prefix can actually be cached across calls within a run. Each call
        # still sends a complete, self-contained message list (no threaded
        # conversation history), so there's nothing else here that could
        # legitimately bleed between calls even if the cache activates.
        system_msg = (
            self._system_prompt
            + "\n\nBefore choosing an action, reason briefly (1-2 short sentences) inside"
            + " <think>...</think> tags. Then output ONLY a JSON object on the last line."
        )
        # max_tokens found 2026-08-07: this task is a short field-value decision,
        # not open-ended generation — 2048 was a generous, unused ceiling that
        # only mattered if the model rambled. Paired with the tightened "1-2
        # short sentences" instruction above so the cap doesn't truncate a
        # verbose <think> block before the JSON line ever gets emitted.
        resp = self._llm_client.chat.completions.create(
            model=self._llm_model,
            max_tokens=512,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
        )
        return _parse_llm_response(resp.choices[0].message.content)

    def _call_gemini(self, user_msg: str) -> Dict[str, Any]:
        resp = self._llm_client.models.generate_content(
            model=self._llm_model,
            contents=user_msg,
        )
        return _parse_llm_response(resp.text)

    # ── observer / transformer helpers ───────────────────────────────────────

    def _observe(self) -> Dict[str, Any]:
        try:
            state = self._observer.snapshot()
            # Validate the perception adapter ONCE against the schema contract.
            # Fail LOUD (log) instead of the agent silently seeing a blank screen
            # because an adapter speaks a different dialect (e.g. Excel type=cell).
            if not self._schema_checked:
                self._schema_checked = True
                try:
                    from components.observers.schema import validate_state
                except ImportError:
                    from observers.schema import validate_state
                for _issue in validate_state(state):
                    _lvl = logger.error if _issue.startswith("ERROR") else logger.warning
                    _lvl("Perception schema: %s", _issue)
            if state and state.get("elements") is not None:
                # EXPERIMENTAL: OCR background-window overlay.
                # Only fires when use_ocr=True AND UIA background data is thin
                # (≤1 char total), meaning the background app (Notepad, PDF viewer,
                # etc.) isn't exposing its content via UIA.  Tesseract screenshots
                # the background window, detects text boxes, and injects them as
                # background elements so the LLM can read label→value pairs.
                if self._use_ocr:
                    bg_text_total = sum(
                        len((e.get("value") or "").strip())
                        for e in state.get("elements", [])
                        if e.get("window_role") == "background"
                    )
                    if bg_text_total <= 1:
                        ocr_elems = self._ocr_background_window(state)
                        if ocr_elems:
                            state["elements"] = state["elements"] + ocr_elems
                            logger.info("OCR overlay: injected %d element(s) from background window",
                                        len(ocr_elems))
                return state
        except Exception as exc:
            logger.warning("Observer error: %s", exc)
        return {"elements": [], "screen_resolution": [1920, 1080]}

    @staticmethod
    def _slim_for_model(state: Dict[str, Any]) -> Dict[str, Any]:
        """Match the recorder's slim EXACTLY so the transformer sees the same
        element shape it trained on (else inputs are out-of-distribution)."""
        _KEEP = ("element_id", "type", "label", "text", "value", "bbox",
                 "window_role", "window_title", "app", "focused", "confidence",
                 "attempted")
        out = dict(state)
        slim = []
        for e in state.get("elements", []):
            ne = {k: e[k] for k in _KEEP if k in e}
            lb = ne.get("label")
            if lb and len(lb) > 200:
                ne["label"] = lb[:200]
            for fld in ("value", "text"):
                v = ne.get(fld)
                if v and len(v) > 6000:
                    ne[fld] = v[:6000]
            slim.append(ne)
        out["elements"] = slim
        return out

    def _attempt_key(self, elem: Dict[str, Any], elements: Optional[list] = None):
        """Match transformer._attempt_key exactly (label-primary, scroll-stable,
        disambiguated by rank among same-labeled elements when `elements` — the
        full elements list from elem's own state — is given). Repeated-section
        forms (Driver 1/2/3, Vehicle 1/2, ...) give every instance of a field
        the same label; without disambiguation, filling Driver 1's First Name
        silently marked Driver 2's identically-labeled, still-empty First Name
        as already-attempted too. See transformer._attempt_key for the full
        rationale — keep both copies in sync."""
        lbl = (elem.get("label") or elem.get("text") or "").strip().lower()
        if not lbl:
            b = elem.get("bbox") or [0, 0, 0, 0]
            return ("@", round((b[0] + b[2]) / 2 / 20) * 20, round((b[1] + b[3]) / 2 / 20) * 20)
        if elements:
            same_label_ids = [id(e) for e in elements
                               if (e.get("label") or e.get("text") or "").strip().lower() == lbl]
            if len(same_label_ids) > 1:
                try:
                    return (lbl, same_label_ids.index(id(elem)))
                except ValueError:
                    pass
        return lbl

    def _mark_attempted(self, elem: Dict[str, Any], elements: Optional[list] = None) -> None:
        """Record that a field has been acted on this session (attempted feature)."""
        if isinstance(elem, dict):
            self._attempted_keys.add(self._attempt_key(elem, elements=elements))

    def _elem_at(self, state: Dict[str, Any], pos) -> Optional[Dict[str, Any]]:
        """Element whose bbox contains pos (nearest center on ties)."""
        if not pos or len(pos) < 2:
            return None
        px, py = pos[0], pos[1]
        best, best_d = None, 1e18
        for e in state.get("elements", []):
            b = e.get("bbox")
            if not b or len(b) != 4:
                continue
            if b[0] - 2 <= px <= b[2] + 2 and b[1] - 2 <= py <= b[3] + 2:
                d = ((b[0] + b[2]) / 2 - px) ** 2 + ((b[1] + b[3]) / 2 - py) ** 2
                if d < best_d:
                    best, best_d = e, d
        return best

    def _record_attempt(self, state: Dict[str, Any], prediction: Dict[str, Any]) -> None:
        """Mark the element this step acted on — keyboard→focused, click→element
        under the cursor — so the transformer sees it as attempted next frame."""
        at = prediction.get("action_type")
        elements = state.get("elements", [])
        elem = None
        if at == "keyboard":
            fid = state.get("focused_element_id")
            elem = next((e for e in elements if e.get("element_id") == fid), None)
        elif at == "click":
            elem = self._elem_at(state, prediction.get("click_position") or [])
        if elem is not None:
            self._mark_attempted(elem, elements=elements)

    def _stamp_attempted_live(self, state: Dict[str, Any]) -> None:
        """Stamp attempted=1 on observed elements acted on earlier this session."""
        if not self._attempted_keys:
            return
        elements = state.get("elements", [])
        for e in elements:
            if self._attempt_key(e, elements=elements) in self._attempted_keys:
                e["attempted"] = 1.0

    def _predict(self, state: Dict[str, Any]) -> Dict[str, Any]:
        try:
            try:
                from components.intelligence.model.transformer import predict
            except ImportError:
                from intelligence.model.transformer import predict
            self._stamp_attempted_live(state)
            return predict(
                state=self._slim_for_model(state),
                history=self._history[-3:],
                model_path=self.model_path,
                device_str=self.device_str,
            )
        except Exception as exc:
            logger.error("Transformer predict failed: %s", exc)
            raise
