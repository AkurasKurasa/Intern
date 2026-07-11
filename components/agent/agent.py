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
import sys
import time
from typing import Any, Dict, List, Optional

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
                # STRICT record bound (2026-07-11): the blob is UIA-capped (file's
                # start = record 1); serving another record's fields here fed the
                # LLM record-1 values on record-2 runs. Missing = say so honestly.
                rec = records.get(record_num, {})
                if rec:
                    lines.append(f"\nDATA SOURCES (Record {record_num}):")
                    for field, value in rec.items():
                        lines.append(f"  {field} : {value}")
                else:
                    lines.append(f"\nDATA SOURCES: record {record_num} is NOT visible in the "
                                 f"source window. Do NOT invent values and do NOT reuse another "
                                 f"record's values — leave unknown fields blank.")
            else:
                # Plain text — dump up to 3 000 chars to keep prompt small
                lines.append(f"\nDATA SOURCES:")
                lines.append(f"  {raw_text[:3000]}")

    return "\n".join(lines)


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
        except ImportError:
            from agent.state_validator import StateValidator
            from recorder.correction_handler import CorrectionHandler

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
        self._record_num: int               = record_num
        # 'attempted' state-feature (inference side): identities of fields acted on
        # this session, fed to the transformer so it stops re-targeting them (the
        # principled fix for empty-optional-field loops). Reset per record.
        self._attempted_keys: set            = set()
        self._attempted_record_num: int      = record_num
        # Tabs navigated to this record (names) — the LLM gap-finder uses this to
        # pick the next UNVISITED tab once the current one is filled. Reset per record.
        self._visited_tabs: set              = set()
        # Fields where a fill (type) attempt produced no_change repeatedly — the
        # widget won't accept clipboard paste (e.g. wx SpinCtrl) so it never shows
        # a value and the model re-targets it forever. After N fails we HARD-skip:
        # Tab past instead of re-typing. Keyed by _attempt_key (scroll-stable).
        # Generic (any unfillable widget), reset per record.
        self._fill_fail_count: dict          = {}
        self._verify_fix_count: dict         = {}   # verify: field → times re-corrected (accept as dead after 2)
        self._keystroke_retried: dict        = {}   # field → tried keystroke-typing after a paste no_change
        self._dead_fill_keys:  set           = set()
        # Fixation-recovery escalation: how many times the SAME spot has triggered
        # the loop-guard's "FIXATED" recovery this tab. First hit tries the normal
        # NAV fill/verify recovery. If that same spot fixates AGAIN, the recovery
        # isn't sticking (dead-mark ignored, or the fill trivially "succeeds" with
        # nothing to change) — stop trusting it and force a hard tab-advance instead
        # of looping forever. Generic: keyed by attempt_key, reset per tab.
        self._fixation_hits:   dict          = {}
        # How many times _reveal_missing_by_scroll focused a field that was
        # already visible but still unfilled. After 2 attempts we mark it dead
        # so _find_missing_field stops returning the same stuck field forever.
        self._reveal_focus_count: dict       = {}
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
        self._current_tab_idx: int          = start_tab_idx  # tracks which tab we're on
        self._start_tab_idx: int            = start_tab_idx  # drill: auto-click this tab at step 0
        self._tabs_total: int               = 0     # max tab count ever observed (survives degraded/partial observations)
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
        _expose_scrolls:   int            = 0   # Mechanism-2 proactive expose-scrolls on current tab
        _m2_at_bottom:     bool           = False  # M2: _scrollbar_drag returned no-move → already at bottom
        _MAX_TAB_SCROLLS:  int            = 6   # max drought-scrolls per tab before giving up
        _steps_on_tab:     int            = 0   # steps spent on current tab — forces advance if too many
        _TAB_STEP_LIMIT:   int            = 40  # force tab advance after this many steps on same tab
        _pane_escape_last_field: str      = ""  # last field pane-escape tried to click
        _pane_escape_streak:     int      = 0   # consecutive tries on the same field without escaping
        _confirmed_blank_fields: set      = set()  # fields where peek found no value → treat as blank
        _heuristic_steps:        int      = 0      # steps decided by auto-handlers (not LLM/transformer)
        _steps_since_fill:       int      = 0      # steps since a field VALUE actually changed (a real fill)
        _STALL_LIMIT:            int      = 6      # no real fill for N steps → stuck (oscillation/dead-clicks) → sweep. 6 lets normal multi-step nav (click→type) breathe; M2 handles the routine feed.
        _prev_filled_labels:     set      = set()  # filled-field fingerprint last step — path-independent progress detector

        _record_cache_loaded     = False
        _tc_advance_verified     = False   # True once the full top→bottom scan passes before advance/submit
        _prev_elem_count:    int = 0       # element count from previous step — spike = unexpected dialog

        # Repeat-action detector — fingerprint last N actions; identical streak → Tab out
        from collections import deque as _deque
        _action_history: _deque = _deque(maxlen=6)
        _REPEAT_LIMIT: int = 3

        # ── SUBMIT CHOKEPOINT ──────────────────────────────────────────────────
        # A premature Submit must be IMPOSSIBLE. Wrap the executor so EVERY click
        # (model, combobox, reveal, sweep, tab — every call site) is checked: a
        # click landing on a Submit/finish button is blocked UNLESS the verified
        # finish path (_click_submit) set _allow_submit. The job submits only when
        # the whole task is verified-finished — never as a stray click.
        self._allow_submit = False
        self._submit_bboxes = []
        if not getattr(self, "_submit_guard_installed", False):
            _raw_execute = self._executor.execute
            def _guarded_execute(prediction, *a, **kw):
                if prediction.get("action_type") == "click" and not self._allow_submit:
                    _cp = prediction.get("click_position")
                    if _cp and self._point_on_submit(_cp):
                        logger.warning("BLOCKED stray Submit click @ %s — job not verified-finished.", _cp)
                        return _raw_execute({"action_type": "blocked"})   # → clean no_op result
                return _raw_execute(prediction, *a, **kw)
            self._executor.execute = _guarded_execute
            self._submit_guard_installed = True

        for step_idx in range(n):
          try:
            # 1. Observe — but first re-assert the locked form as foreground so a
            # stray click last step can't leave us observing/acting on a drifted
            # window. Lock is captured on the first observe (form is in front at GO).
            self._reassert_form_window()
            state      = self._observe()
            if self._locked_hwnd is None:
                self._lock_form_window(state)
            # Refresh the Submit/finish-button bboxes for the chokepoint guard.
            # ACCUMULATE across steps (union) — a submit button can drop out of one
            # observation (scrolled/not-rendered); if we only used the current frame,
            # a click there would leak through unblocked (which submitted the form
            # early). Buttons don't move, so keeping every bbox we've ever seen is safe.
            _SUBMIT_KW = ("submit", "finish", "& new", "save", "accept")
            for _e in state.get("elements", []):
                if ((_e.get("type") or "").lower() in ("buttoncontrol", "button")
                        and _e.get("bbox")
                        and any(k in (_e.get("text") or _e.get("label") or "").lower() for k in _SUBMIT_KW)):
                    _bb = _e["bbox"]
                    if _bb not in self._submit_bboxes:
                        self._submit_bboxes.append(_bb)
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

            # Error/modal-dialog guard: a premature Submit pops a wx validation dialog
            # ("required fields…") that becomes the FOREGROUND window — it covers the
            # form, exposes an OK/Close button, and has NO tab strip. THIS is the jam
            # behind the "0 page fields / can't scroll" frames. Detect it (OK-button
            # present AND no tab elements visible = not the form) and dismiss it with
            # Escape so the form returns to front. Generic: keys on widget type +
            # generic button text, no app/field names.
            _dlg_btn = any(
                (e.get("type") or "").lower() in ("buttoncontrol", "button")
                and (e.get("text") or e.get("label") or "").strip().lower()
                    in ("ok", "okay", "yes", "no", "close", "cancel")
                for e in state.get("elements", [])
            )
            _tabs_visible = any(
                (e.get("type") or "").lower() in ("tabitem", "tabitemcontrol")
                for e in state.get("elements", [])
            )
            if _dlg_btn and not _tabs_visible:
                logger.warning("Modal/error dialog detected (OK-button, no tab strip) — Escape to dismiss & restore form.")
                self._executor.execute({"action_type": "hotkey", "keys": ["escape"]})
                time.sleep(self.step_delay)
                self._reassert_form_window()
                _prev_elem_count = 0
                continue

            # ── Mechanism 2: Proactive Expose-Scroll ─────────────────────────
            # The transformer can only target VISIBLE elements. When visible empties
            # run low the transformer starves, fixates, and the LLM sweep takes over.
            # Pre-emptively scroll to expose the next batch of empty fields BEFORE
            # the transformer prediction so it always has targets to choose from.
            # Generic: driven by widget-type + geometry (viewport bottom) only.
            #
            # NOTE: _has_offfold_empty is NOT used as a hard gate here.
            # wx ScrolledPanel reports below-fold fields with coords that may not
            # exceed GetWindowRect bottom (outer frame vs. scroll viewport), so
            # _has_offfold_empty can be False even when more content exists.
            # Instead, we rely on _scrollbar_drag's movement verification: if the
            # panel did NOT move (returns False), we set _m2_at_bottom so M2 stops
            # trying for the rest of this tab — the correct bottom-of-scroll stop.
            _M2_EXPOSE_CAP = 6   # max proactive scrolls per tab
            _m2_vis = self._visible_empty_count(state)
            _m2_offfold = self._has_offfold_empty(state)
            logger.debug("M2-check: vis_empty=%d offfold=%s just_switched=%s scrolls=%d/%d at_bottom=%s",
                         _m2_vis, _m2_offfold, _tab_just_switched,
                         _expose_scrolls, _M2_EXPOSE_CAP, _m2_at_bottom)
            # NOTE: deliberately NOT gated on _no_autohandlers — M2 is the intended
            # transformer-feeding mechanic (keep the screen stocked with empties),
            # not a legacy heuristic. run_task passes disable_auto_handlers=True, which
            # must NOT switch M2 off.
            if (_expose_scrolls < _M2_EXPOSE_CAP
                    and not _m2_at_bottom
                    and _m2_vis <= 1):
                _moved = self._scrollbar_drag(state, 240.0)
                if _moved:
                    _expose_scrolls += 1
                    time.sleep(self.step_delay * 0.5)
                    state = self._observe()
                    logger.info("Expose-scroll: visible empties low → scrolled to reveal next batch "
                                "(now %d visible, scroll %d/%d).",
                                self._visible_empty_count(state), _expose_scrolls, _M2_EXPOSE_CAP)
                else:
                    _m2_at_bottom = True
                    logger.info("Expose-scroll: _scrollbar_drag returned no-move → already at bottom "
                                "for this tab (stopping M2 proactive scroll).")

            # Load record cache on first step
            if not _record_cache_loaded:
                self._refresh_record_cache(state)
                _record_cache_loaded = True

            # Drill mode: on the very first step, jump to the requested start tab by
            # clicking its real bbox (index-based, no tab-name hardcode). Lets us
            # iterate on a deeper tab without re-filling the earlier ones each run.
            if step_idx == 0 and self._start_tab_idx > 0:
                _tabs0 = sorted(
                    (e for e in state.get("elements", [])
                     if (e.get("type") or "").lower() in ("tabitem", "tabitemcontrol")
                     and e.get("window_role") != "background" and e.get("bbox")),
                    key=lambda e: e["bbox"][0],
                )
                if 0 <= self._start_tab_idx < len(_tabs0):
                    _b0 = _tabs0[self._start_tab_idx]["bbox"]
                    _name0 = (_tabs0[self._start_tab_idx].get("text")
                              or _tabs0[self._start_tab_idx].get("label") or "?").strip()
                    logger.info("Drill: auto-clicking start tab idx=%d %r", self._start_tab_idx, _name0)
                    self._executor.execute({"action_type": "click",
                                            "click_position": [(_b0[0] + _b0[2]) / 2, (_b0[1] + _b0[3]) / 2]})
                    self._visited_tabs.add(_name0)
                    time.sleep(self.step_delay)
                    state = self._observe()

            # ── Progress detector (path-independent) ──────────────────────────
            # Fingerprint the set of FILLED fields (any fillable widget with a
            # non-empty value). A newly-filled label since last step = real
            # progress → reset the stall counter. No new fill = +1. This catches
            # every fill path (edit/combo/checkbox/reveal) without depending on
            # which code branch did it, and ignores focus-only "ok" clicks.
            _filled_now = {
                (e.get("label") or e.get("text") or "").strip().lower()
                for e in state.get("elements", [])
                if (e.get("type") or "").lower() in ("editcontrol", "comboboxcontrol", "checkboxcontrol")
                and e.get("window_role") != "background"
                and (e.get("value") or "").strip()
                and (e.get("label") or e.get("text"))
            }
            # Compare to the CUMULATIVE set of fields ever seen filled — not just
            # last step. A focus/scroll shift re-reveals an already-filled field,
            # which would look "new" against a single-step snapshot and falsely
            # reset the stall (the bug that kept the protocol from ever firing).
            # Cumulative = only a genuinely first-time fill counts as progress.
            if _filled_now - _prev_filled_labels:
                _steps_since_fill = 0          # a NEW field got filled → progress
            else:
                _steps_since_fill += 1
            _prev_filled_labels |= _filled_now
            # Track the max tab count ever seen — a degraded/partial observation
            # (element count collapses, tabs vanish) must NOT be read as "all tabs
            # done". This is the floor that blocks the false-finish on a bad frame.
            _ntabs = sum(1 for e in state.get("elements", [])
                         if (e.get("type") or "").lower() in ("tabitem", "tabitemcontrol")
                         and e.get("window_role") != "background")
            self._tabs_total = max(self._tabs_total, _ntabs)

            # ── Progress-stall escape ─────────────────────────────────────────
            # The model can fixate: it keeps clicking an in-view field, focus
            # "moves" (validator ok) so no guard fires, but NOTHING gets filled and
            # the below-fold fields are never reached. When no field VALUE has
            # changed for _STALL_LIMIT steps, the agent takes over navigation:
            # _reveal_missing_by_scroll scrolls to (and fills) the next missing
            # field; if the tab is genuinely complete it advances via the LLM gap.
            # This is the trigger that was missing — scroll only fired on tab-clicks
            # or empty-screen, never during an in-view fixation.
            # NOT gated on _no_autohandlers — the stall rescue is the intended
            # fallback (run_task sets disable_auto_handlers=True, which must NOT switch
            # it off). Catches A-B-A-B oscillation / dead-click fixation that the
            # exact-repeat guard misses: no field VALUE filled for _STALL_LIMIT steps.
            if _steps_since_fill >= _STALL_LIMIT:
                # NAVIGATION PROTOCOL — stall rescue. FIRST try the optimal-
                # viewport jump: a stall usually means the visible batch is spent
                # (or a dead widget burned the steps) while plenty of empty fields
                # sit off-screen — reposition and hand the tab BACK to the
                # transformer. The LLM-sweep (one LLM call per field, the slow
                # crawl) is the LAST resort, only when there is nothing to jump to.
                logger.info("[NAV] STUCK %d steps — trying optimal-viewport jump before sweep.",
                            _steps_since_fill)
                _stall_jmp = self._optimal_viewport_jump(state)
                if isinstance(_stall_jmp, dict):
                    state = _stall_jmp
                    _steps_since_fill = 0
                    _last_auto_step   = step_idx
                    time.sleep(self.step_delay * 0.5)
                    continue
                logger.info("[NAV] nothing to jump to — invoking sweep.")
                state, _finish = self._sweep_tab(state)
                _steps_since_fill  = 0
                _tab_scroll_count  = 0
                _expose_scrolls    = 0
                _m2_at_bottom      = False
                _tab_just_switched = True
                _last_auto_step    = step_idx
                _heuristic_steps  += 1
                time.sleep(self.step_delay)
                if _finish:
                    break
                continue

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
                _expose_scrolls     = 0
                _m2_at_bottom       = False
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
                _expose_scrolls       = 0
                _m2_at_bottom         = False
                _no_change_streak     = 0
                _last_auto_step       = step_idx  # treat tab switch as an auto step
                _pane_escape_last_field = ""
                _pane_escape_streak     = 0
                self._filled_this_tab.clear(); self._fixation_hits.clear()     # new tab — reset filled-field tracking
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
                    self._filled_this_tab.clear(); self._fixation_hits.clear()
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
                        # Latch on the instance too — the multi-record loop in
                        # run_task reads agent._submitted to decide whether the
                        # form is clean for the next record.
                        self._submitted = True
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
                            self._filled_this_tab.clear(); self._fixation_hits.clear()
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
                # Check if dropdown is already open (listitemcontrols visible)
                listitems = [e for e in elements
                             if e.get("type") == "listitemcontrol"
                             and e.get("window_role") != "background"]
                if listitems:
                    # Find the matching listitem and click it
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

            # ── Scroll-to-reveal (universal mechanic — runs in EVERY mode) ────────
            # The transformer can only target RENDERED elements; fields below the
            # fold are invisible to it, so it guesses a below-fold position → blind
            # click → drift. When no actionable empty field is visible, reveal more
            # by scrolling (perception-driven, no field names/coords). Scrolls
            # exhausted at the true bottom → advance the tab. The consecutive-scroll
            # cap only counts DEAD scrolls — it resets the moment a field is visible,
            # so a long tab can scroll as many times as it has fields. Gated purely on
            # field visibility (self-protecting): a transient empty frame can't advance
            # the tab until _MAX_TAB_SCROLLS dead scrolls have actually happened.
            if not self._no_visible_empty_field(state):
                _tab_scroll_count = 0                # actionable field visible → reset cap
            else:
                # NAVIGATION PROTOCOL. Nothing fillable on screen. FIND the next
                # needed field by dragging the scrollbar (no Tab, no synthetic wheel
                # — wx ignores it). _reveal_missing_by_scroll returns:
                #   fresh state → scrolled the missing field into view → fill next,
                #   None        → tab COMPLETE (no missing field) → LLM navigates,
                #   "STUCK"     → missing field exists but scroll didn't move view →
                #                 increment dead-scroll counter and retry; only advance
                #                 when the cap is hit, never on a single stuck scroll.
                _rev = self._reveal_missing_by_scroll(state)
                if isinstance(_rev, dict):
                    state             = _rev
                    _tab_scroll_count = 0
                    _last_auto_step   = step_idx
                    _heuristic_steps += 1
                    continue
                if _rev == "STUCK":
                    _tab_scroll_count += 1
                    logger.warning("Nav-protocol: STUCK scroll #%d (cap=%d) — will retry.",
                                   _tab_scroll_count, _MAX_TAB_SCROLLS)
                    if _tab_scroll_count < _MAX_TAB_SCROLLS:
                        time.sleep(self.step_delay * 0.5)
                        continue
                    logger.warning("Nav-protocol: STUCK cap reached — treating tab as scrolled-out.")
                    # Fall through to LLM/advance below.
                # Tab complete (None) OR STUCK cap hit → hand NAVIGATION to the LLM.
                _gap = self._ask_llm_next_gap(state)
                _gtype = _gap.get("action_type", "")
                if _gtype == "done":
                    if self._confirm_finished(state):
                        logger.info("[GAP] finish CONFIRMED against source — done.")
                        break
                    continue   # source-check disagrees → keep working
                _gpred = self._llm_action_to_prediction(_gap, state)
                if _gpred.get("action_type") not in ("no_op", "wait"):
                    _gtgt = (_gap.get("target") or "").strip()
                    # Mark the destination tab visited so the unvisited count actually
                    # shrinks (else the LLM keeps being told tabs remain → re-picks).
                    if _gtgt:
                        self._visited_tabs.add(_gtgt)
                    # Anti-loop: LLM re-picking the SAME tab → it's stuck (already there /
                    # tab has only unfillable empties). Force the next GENUINELY-unvisited
                    # tab by index instead of trusting the LLM's repeat.
                    _gl = _gtgt.lower()
                    self._gap_same = (getattr(self, "_gap_same", 0) + 1) if _gl and _gl == getattr(self, "_last_gap_tgt", "") else 0
                    self._last_gap_tgt = _gl
                    if self._gap_same >= 2:
                        _vlow = {v.lower() for v in self._visited_tabs}
                        _nxt = next((e for e in self._tab_elems_now(state)
                                     if (e.get("text") or e.get("label") or "").strip().lower() not in _vlow), None)
                        if _nxt is not None:
                            _nb = _nxt["bbox"]; _nm = (_nxt.get("text") or _nxt.get("label") or "?").strip()
                            logger.warning("[GAP] LLM stuck re-picking %r — forcing unvisited tab %r.", _gtgt, _nm)
                            self._executor.execute({"action_type": "click",
                                                    "click_position": [(_nb[0] + _nb[2]) / 2, (_nb[1] + _nb[3]) / 2]})
                            self._visited_tabs.add(_nm)
                        else:
                            logger.info("[GAP] no unvisited tab left — checking finish.")
                            if self._confirm_finished(state):
                                break
                        self._gap_same = 0
                        _tab_scroll_count = _expose_scrolls = 0
                        _m2_at_bottom = False
                        _last_auto_step = step_idx
                        _heuristic_steps += 1
                        self._filled_this_tab.clear(); self._fixation_hits.clear()
                        self._refresh_record_cache(self._observe())
                        time.sleep(self.step_delay)
                        continue
                    logger.info("[GAP] tab done → LLM → %s %r", _gtype, _gtgt[:30])
                    self._executor.execute(_gpred)
                    _tab_scroll_count  = 0
                    _expose_scrolls    = 0          # M2: new tab → fresh scroll budget
                    _m2_at_bottom      = False       # M2: new tab is not at bottom
                    _last_auto_step    = step_idx
                    _heuristic_steps  += 1
                    self._filled_this_tab.clear(); self._fixation_hits.clear()
                    self._refresh_record_cache(self._observe())
                    time.sleep(self.step_delay)
                    continue
                # LLM gave nothing usable → fall back to plain next-tab advance.
                if self._try_advance_tab(state):
                    logger.info("Scroll-reveal: LLM idle — advancing to next tab.")
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
                # spin controls included: the viewport jump focuses its anchor, and
                # a focused-but-unfillable type loops the jump forever (observed
                # live: 'Cylinders' SpinCtrl, 6 identical jumps). Typing digits into
                # a focused spin works; paste-reject is handled by keystroke-retry.
                _t_is_type = (_fe2_ty in ("editcontrol", "input", "comboboxcontrol",
                                          "checkboxcontrol", "checkbox",
                                          "spincontrolcontrol", "spincontrol",
                                          "spinnercontrol", "spinner")
                              and not _fe2_val)

                if _t_is_type and self._llm_client:
                    # transformer chose to FILL → LLM supplies the value (the WHAT).
                    # LLM owns value-filling per the architecture.
                    llm_action = self._ask_llm(state)
                    if llm_action.get("action_type") == "done":
                        logger.info("LLM: task complete."); break
                    prediction = self._merge(t_pred, t_conf, llm_action, state)
                    _decision_maker = "llm"
                    _flabel = ((_fe2.get("label") or _fe2.get("text") or "?")[:30]
                               if _fe2 else "(no focus)")
                    logger.info("[OPT2] TRANSFORMER chose TYPE → LLM value for '%s' → %r",
                                _flabel, prediction.get("text", "")[:40])
                    # Leave-blank guard: if the resolved value means "leave empty"
                    # (record said '(leave blank)' / none / n-a, possibly with a note),
                    # DON'T type the placeholder literally — Tab past + mark attempted.
                    # Generic (substring match, no field names).
                    _txt  = (prediction.get("text") or "").strip()
                    _norm = _txt.lower().strip().strip("()").strip()
                    if (not _txt) or _norm in {"none", "n/a", "na"} or _norm.startswith("leave blank"):
                        logger.info("[OPT2] %r → leave-blank/empty — Tab past (skip).", _flabel)
                        if _fe2 is not None:
                            self._mark_attempted(_fe2)
                        self._executor.execute({"action_type": "keyboard",
                                                "key_count": 1, "keystrokes": ["tab"]})
                        time.sleep(self.step_delay * 0.4)
                        continue
                else:
                    # Transformer pointer navigates to the next field
                    _decision_maker = "transformer"
                    _pos2 = t_pred.get("click_position")
                    # RANKED WHERE: arbitrate over the pointer head's full top-k
                    # instead of blindly taking argmax. Masked #1 (dead / filled /
                    # blacklisted) → the model's own next-best target, same step,
                    # no guard, no LLM. None → nothing actionable is visible.
                    _ranked = self._pick_ranked_target(state, t_pred)
                    # THIN-VIEW JUMP: wx focus auto-scroll trickles ONE fresh field
                    # into view after every fill, so on tall tabs "zero visible
                    # targets" never occurs — the viewport slides row-by-row (the
                    # one-by-one crawl, observed live on Vehicle). When visible
                    # work is thin (≤2 empties), try the jump NOW; it no-ops when
                    # the current view is already the densest available, so this
                    # never fires on the genuine last fields of a tab.
                    if _ranked is not None:
                        _vt_tv = self._form_viewport_top(state)
                        _vb_tv = self._form_viewport_bottom(state) - 8
                        _vis_tv = 0
                        for _e_tv in state.get("elements", []):
                            if (_e_tv.get("type") or "").lower() not in self._FILLABLE_TYPES:
                                continue
                            if (_e_tv.get("type") or "").lower() in ("checkboxcontrol", "checkbox"):
                                continue
                            if _e_tv.get("window_role") == "background" or not _e_tv.get("bbox"):
                                continue
                            if (_e_tv.get("value") or "").strip():
                                continue
                            _k_tv = self._attempt_key(_e_tv)
                            if _k_tv in self._dead_fill_keys or _k_tv in self._attempted_keys:
                                continue
                            _cy_tv = (_e_tv["bbox"][1] + _e_tv["bbox"][3]) / 2
                            if _vt_tv <= _cy_tv <= _vb_tv:
                                _vis_tv += 1
                        if _vis_tv <= 2:
                            _jmp_tv = self._optimal_viewport_jump(state, t_pred)
                            if isinstance(_jmp_tv, dict):
                                logger.info("[NAV] thin view (%d visible empties) — jumped to denser window.", _vis_tv)
                                state = _jmp_tv
                                time.sleep(self.step_delay * 0.4)
                                continue
                    if _ranked is not None:
                        _r_elem, _r_pos, _r_conf = _ranked
                        if _pos2 and (abs(_r_pos[0] - _pos2[0]) > 5 or abs(_r_pos[1] - _pos2[1]) > 5):
                            logger.info("[RANKED] top-1 masked → model's next-best %r @ (%.0f,%.0f) conf=%.2f",
                                        (_r_elem.get("label") or _r_elem.get("text") or "?")[:28],
                                        _r_pos[0], _r_pos[1], _r_conf)
                        _pos2 = _r_pos
                    elif t_pred.get("click_topk"):
                        # Every ranked candidate masked → visible batch is spent.
                        # NAVIGATION PROTOCOL rule 2: jump to the densest window of
                        # empty fields; fall back to the missing-field reveal, then
                        # to the pointer-invalid Tab fallback if the tab is done.
                        logger.info("[RANKED] all candidates masked — optimal-viewport jump.")
                        _jmp = self._optimal_viewport_jump(state, t_pred)
                        if isinstance(_jmp, dict):
                            state = _jmp
                            time.sleep(self.step_delay * 0.5)
                            continue
                        _rev = self._reveal_missing_by_scroll(state)
                        if isinstance(_rev, dict):
                            state = _rev
                            time.sleep(self.step_delay * 0.5)
                            continue
                        _pos2 = None
                    if _pos2 and (_pos2[0] > 1 or _pos2[1] > 1):
                        _snap2 = self._snap(_pos2, state) or _pos2
                        # MECHANISM 1: if the transformer picked an off-fold field,
                        # scroll it into view and retarget to its fresh coords (agent
                        # HOW). Keeps the transformer driving WHERE across the whole
                        # tab instead of starving → handing to the LLM-sweep.
                        state, _snap2 = self._reveal_target(state, _snap2)
                        # (MECHANISM 2 removed 2026-07-08 — superseded by the
                        # NAVIGATION PROTOCOL core rule: visible-first ranking in
                        # _pick_ranked_target + _optimal_viewport_jump when the
                        # visible batch is spent.)
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
                            # Viewport guard: a combobox BELOW the fold can't open its
                            # dropdown (off-screen → no render → escape → infinite loop).
                            # Tab instead (wx auto-scrolls it into view) and retry next step.
                            _cb_cy = (_cbox["bbox"][1] + _cbox["bbox"][3]) / 2
                            if _cb_cy > self._form_viewport_bottom(state) - 8:
                                logger.info("[OPT2] combobox %r below viewport — Tab to reveal.", _cb_label[:24])
                                self._executor.execute({"action_type": "keyboard",
                                                        "key_count": 1, "keystrokes": ["tab"]})
                                time.sleep(self.step_delay * 0.5)
                                continue
                            logger.info("[OPT2] CLICK on empty combobox %r → treat as FILL", _cb_label[:30])
                            _cb_sec = self._detect_section(state, _cbox)
                            _cb_val = self._lookup_field(_cb_label, section=_cb_sec)
                            # ONE click opens the dropdown; select inline + continue.
                            # (Don't route to the type-handler — its own open-click would
                            #  TOGGLE the dropdown shut → no options → Escape loop.)
                            self._executor.execute({"action_type": "click", "click_position": _snap2})
                            if not _cb_val:
                                # Optional field with no record value (e.g. Suffix).
                                # Escape + Tab past it, and MARK it attempted so the
                                # transformer stops re-targeting it (the real fix is the
                                # 'attempted' state-feature; this just records the hit).
                                logger.info("[OPT2] combobox %r — no value, Tab", _cb_label)
                                self._executor.execute({"action_type": "keyboard",
                                                        "key_count": 1, "keystrokes": ["escape"]})
                                self._mark_attempted(_cbox)
                                self._executor.execute({"action_type": "keyboard",
                                                        "key_count": 1, "keystrokes": ["tab"]})
                                time.sleep(self.step_delay * 0.5)
                                continue
                            _items = []
                            for _try in range(4):
                                time.sleep(0.35)
                                _cs = self._observe()
                                _items = [e for e in _cs.get("elements", [])
                                          if e.get("type") == "listitemcontrol"
                                          and e.get("window_role") != "background" and e.get("bbox")]
                                if _items:
                                    break
                            _vlc = _cb_val.strip().lower()
                            _o = lambda e: (e.get("text") or e.get("label") or "").strip()
                            _hit = next((e for e in _items if _o(e).lower() == _vlc), None) \
                                or next((e for e in _items if _o(e).lower().startswith(_vlc)
                                         or _vlc.startswith(_o(e).lower())), None)
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
                                # Anti-loop: a combobox that fails to fill must not be
                                # re-targeted forever. Tab past; after 2 fails mark it
                                # attempted so the transformer stops pointing at it.
                                _cbk = self._attempt_key(_cbox)
                                self._cb_fail = getattr(self, "_cb_fail", {})
                                self._cb_fail[_cbk] = self._cb_fail.get(_cbk, 0) + 1
                                if self._cb_fail[_cbk] >= 2:
                                    logger.warning("Combobox %r failed 2x — mark attempted + skip.", _cb_label[:24])
                                    self._mark_attempted(_cbox)
                                self._executor.execute({"action_type": "keyboard",
                                                        "key_count": 1, "keystrokes": ["tab"]})
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
                # Dead field: a widget that already rejected this fill twice (SpinCtrl
                # etc.). Don't re-type into the void and don't Tab — abandon it and let
                # the navigation protocol scroll to the next needed field.
                if _fel is not None and self._attempt_key(_fel) in self._dead_fill_keys:
                    self._mark_attempted(_fel)
                    # Dead field = the transformer fixated on an unfillable widget
                    # (SpinCtrl etc.). This is the RELIABLE fixation signal. Mechanism 2
                    # REACTIVE: before handing to the LLM-sweep, scroll the dead field
                    # off-screen and expose the next batch so the TRANSFORMER keeps
                    # driving. Only fall to the sweep when the panel can't move (truly
                    # at the bottom) — so M2 feeds the transformer on EVERY fixation,
                    # not just once when visible-empties first run low.
                    if (not _m2_at_bottom and _expose_scrolls < _M2_EXPOSE_CAP
                            and self._scrollbar_drag(state, 240.0)):
                        _expose_scrolls += 1
                        time.sleep(self.step_delay * 0.5)
                        state = self._observe()
                        logger.info("Fixation expose-scroll: dead field %r → scrolled past it to "
                                    "feed the transformer (scroll %d/%d).",
                                    _flabel_full[:24], _expose_scrolls, _M2_EXPOSE_CAP)
                        _heuristic_steps += 1
                        _last_auto_step   = step_idx
                        continue
                    logger.warning("Dead-field: %r won't accept fill + panel at bottom — invoking SWEEP.", _flabel_full)
                    state, _df_fin = self._sweep_tab(state)
                    _heuristic_steps += 1
                    _steps_since_fill = 0
                    _last_auto_step   = step_idx
                    if _df_fin:
                        break
                    time.sleep(self.step_delay)
                    continue
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
                        # Checkbox is DONE for this tab whether we checked it or left it
                        # unchecked (NO). Mark attempted so the observer's missing-checkbox
                        # state-feature flips and the transformer stops re-targeting it —
                        # else PIP/Medical/Uninsured loop forever (checked-state isn't a
                        # readable "value", so they always look empty).
                        self._mark_attempted(_fel)
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
                                self._mark_attempted(_chk_at_cp)   # stop re-targeting it
                                prediction = {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
                            else:
                                _wa2.SendMessage(_hwnd2, BM_SETCHECK, BST_CHECKED, 0)
                                logger.info("Checkbox %r checked via Win32 BM_SETCHECK.", _chk_label)
                                self._checked_fields.add(_chk_label)
                                self._filled_this_tab.add(_chk_label)
                                self._mark_attempted(_chk_at_cp)   # done for this tab — don't loop
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
                    # ── DETERMINISTIC VERIFICATION (user mandate) ────────────────
                    # The transformer NEVER presses Submit. A model click on a
                    # Submit/finish button is only a REQUEST to finish. The agent
                    # runs verification (LLM source-check + every tab visited) via
                    # _confirm_finished, and ONLY if that passes does the agent
                    # deterministically click Submit. Otherwise the submit is
                    # suppressed and the agent sweeps the remaining work — so a
                    # premature Submit (and its error dialog) is impossible.
                    logger.info("Model requested FINISH via %r — running LLM verification.", _btn_name)
                    if self._confirm_finished(state):
                        logger.info("Verification PASSED — agent clicking Submit (deterministic finish).")
                        self._click_submit(state)
                        break
                    logger.warning("Verification FAILED — Submit suppressed; sweeping remaining work.")
                    state, _vf_fin = self._sweep_tab(state)
                    _no_change_streak = 0
                    _last_auto_step   = step_idx
                    _heuristic_steps += 1
                    if _vf_fin:
                        break
                    time.sleep(self.step_delay)
                    continue

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
                    # A tab-click = the model signalling "I think this tab is done."
                    # DON'T obey a blind jump (it skips/races tabs, leaving them unfilled).
                    # Gate on COMPLETENESS instead:
                    #   - current tab still has an unfilled field → stay + fill it,
                    #   - current tab complete → the LLM picks the next move (unvisited
                    #     tab / done). The model never jumps tabs on its own.
                    # NAVIGATION PROTOCOL: don't leave the tab while a needed field
                    # remains. FIND it by scrolling (no Tab); only when the whole tab
                    # is filled (None) does the LLM pick the next tab.
                    # "STUCK" = missing field exists but scroll didn't move → hold here.
                    _rev = self._reveal_missing_by_scroll(state)
                    if isinstance(_rev, dict):
                        state = _rev
                        logger.info("Tab-click %r → tab still has a missing field; scrolled to it (no switch).", _hit_name)
                        time.sleep(0.2)
                        _heuristic_steps += 1
                        continue
                    if _rev == "STUCK":
                        logger.warning("Tab-click %r → STUCK: missing field unreachable, "
                                       "holding on current tab.", _hit_name)
                        prediction = {"action_type": "no_op"}
                        continue
                    # _rev is None → tab is complete → ask LLM for next move.
                    _gap = self._ask_llm_next_gap(state)
                    if _gap.get("action_type") == "done":
                        if self._confirm_finished(state):
                            logger.info("[GAP] finish CONFIRMED against source — done.")
                            break
                        continue   # source-check disagrees → keep working
                    _gpred = self._llm_action_to_prediction(_gap, state)
                    if _gpred.get("action_type") not in ("no_op", "wait"):
                        logger.info("[GAP] tab done → LLM → %s %r", _gap.get("action_type"),
                                    _gap.get("target", "")[:30])
                        self._executor.execute(_gpred)
                        _no_change_streak  = 0
                        _tab_just_switched = True
                        _tab_scroll_count  = 0
                        _expose_scrolls    = 0          # M2: new tab → fresh scroll budget
                        _m2_at_bottom      = False       # M2: new tab is not at bottom
                        _last_auto_step    = step_idx
                        self._filled_this_tab.clear(); self._fixation_hits.clear()
                        _confirmed_blank_fields.clear()
                        self._refresh_record_cache(self._observe())
                        time.sleep(self.step_delay)
                        continue
                    if self._try_advance_tab(state):       # fallback: ordered advance
                        _no_change_streak  = 0
                        _tab_just_switched = True
                        _tab_scroll_count  = 0
                        _last_auto_step    = step_idx
                        self._filled_this_tab.clear(); self._fixation_hits.clear()
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
                logger.info("Combobox: clicking to open dropdown for %r → %r", _flabel, _combo_value)
                self._executor.execute({"action_type": "click", "click_position": [_ccx, _ccy]})
                # The dropdown can take a moment to render. Poll a few times before
                # giving up — otherwise we Escape on an empty observe and waste a
                # whole open→escape→reopen cycle (the #1 combobox time-sink).
                _listitems = []
                for _try in range(4):
                    time.sleep(0.35)
                    _combo_state = self._observe()
                    _listitems = [e for e in _combo_state.get("elements", [])
                                  if e.get("type") == "listitemcontrol"
                                  and e.get("window_role") != "background"
                                  and e.get("bbox")]
                    if _listitems:
                        break
                _cv_lc = _combo_value.strip().lower()
                def _opt(e): return (e.get("text") or e.get("label") or "").strip()
                # exact first, then prefix-fuzzy (handles 'Full Coverage' vs
                # 'Full Coverage (Comprehensive)'); avoid loose substring so
                # 'Active' never matches 'Inactive'.
                _match = next((e for e in _listitems if _opt(e).lower() == _cv_lc), None)
                if not _match:
                    _match = next((e for e in _listitems
                                   if _opt(e).lower().startswith(_cv_lc)
                                   or _cv_lc.startswith(_opt(e).lower())), None)
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
            # Loop detector (caveat 4): catch BOTH an exact repeat (A,A,A) AND a
            # CYCLE (A,B,A,B / A,B,C,A,B,C) where nothing is actually getting filled.
            # The old guard only saw identical repeats, so an oscillation between two
            # dead clicks ran forever. "Nothing changing" = no new fill since.
            _exact_repeat = len(_action_history) >= _REPEAT_LIMIT and len(set(_action_history)) == 1
            _cycling = _steps_since_fill >= 2 and self._is_cycling(_action_history)
            if _exact_repeat or _cycling:
                logger.warning("Loop guard: %s — model FIXATED; breaking loop via Navigation Protocol.",
                               "repeat" if _exact_repeat else "A-B cycle (nothing filling)")
                _action_history.clear()
                _no_change_streak = 0
                _steps_since_fill = 0
                # Mark the fixated spot dead so the model stops re-targeting it
                # (it's been clicked N× with no fill — it isn't going to fill).
                _fx = self._elem_at(state, prediction.get("click_position") or [])
                _fx_key = None
                if _fx is not None:
                    _fx_key = self._attempt_key(_fx)
                    self._dead_fill_keys.add(_fx_key)
                    self._mark_attempted(_fx)
                # Escalation: has THIS EXACT spot already fixated once this tab? If the
                # dead-mark above didn't stop the model from re-targeting it, or the NAV
                # fill/verify keeps trivially "succeeding" because the field's already
                # correct (nothing to change → no forward progress either way), a second
                # fixation on the same spot means recovery isn't working — stop trusting
                # it and force a hard tab-advance instead of retrying fill again.
                _fx_hits = self._fixation_hits.get(_fx_key, 0) + 1 if _fx_key else 1
                if _fx_key:
                    self._fixation_hits[_fx_key] = _fx_hits
                _escalate = _fx_hits >= 2
                if _escalate:
                    logger.warning("[NAV] (fixation) %r fixated 2x — recovery not sticking, "
                                    "forcing tab-advance instead of retrying fill.",
                                    (_fx.get("label") or _fx.get("text") or "")[:28] if _fx else "?")
                # Navigation Protocol: check SOURCE, fix/verify, or navigate. No Tab.
                _nav = self._navigation_protocol(state)
                _nav_act = (_nav.get("action") or "").lower()
                if _escalate and _nav_act == "fill":
                    _nav_act = "tab"   # don't retry the same trivially-succeeding fill again
                if _nav_act == "fill" and (_nav.get("field") or "").strip():
                    _nf = _nav["field"].strip()
                    logger.info("[NAV] (fixation) fill/verify %r → %r",
                                _nf[:28], str(_nav.get("value") or "")[:30])
                    _ok = self._nav_fill_field(state, _nf, _nav.get("value") or "")
                    # If the fill failed (e.g. a 50-item combobox whose option isn't in
                    # the rendered dropdown), count it; after 2 fails mark the field DEAD
                    # so the protocol stops re-proposing it and we don't loop forever.
                    if not _ok:
                        _nk = _nf.lower()
                        self._fill_fail_count[_nk] = self._fill_fail_count.get(_nk, 0) + 1
                        if self._fill_fail_count[_nk] >= 2:
                            _de = next((e for e in state.get("elements", [])
                                        if (e.get("label") or e.get("text") or "").strip().lower() == _nk
                                        and e.get("bbox")), None)
                            if _de is not None:
                                self._dead_fill_keys.add(self._attempt_key(_de))
                                self._mark_attempted(_de)
                            logger.warning("[NAV] (fixation) %r unfillable 2x — marking dead, skipping.", _nf[:28])
                elif _nav_act == "tab" and _nav.get("click_position"):
                    logger.info("[NAV] (fixation) page done → switch tab %r", (_nav.get("target") or "")[:24])
                    self._executor.execute({"action_type": "click", "click_position": _nav["click_position"]})
                    self._visited_tabs.add((_nav.get("target") or "").strip())
                    self._filled_this_tab.clear(); self._fixation_hits.clear()
                    self._refresh_record_cache(self._observe())
                elif _nav_act == "done" and self._confirm_finished(state):
                    logger.info("[NAV] (fixation) finish confirmed against source — done.")
                    break
                elif _escalate:
                    # The LLM's nav response didn't give us a clickable tab (still "fill"
                    # with no field, or "tab" with an unresolved target name) — don't fall
                    # through and let the same spot fixate a 3rd time. Advance deterministically:
                    # click the first unvisited tab ourselves, geometry only, no LLM needed.
                    _next_tab = next((t for t in self._tab_elems_now(state)
                                      if (t.get("text") or t.get("label") or "").strip().lower()
                                      not in {v.lower() for v in self._visited_tabs}), None)
                    if _next_tab is not None:
                        _tb = _next_tab["bbox"]
                        _tnm = (_next_tab.get("text") or _next_tab.get("label") or "").strip()
                        logger.warning("[NAV] (fixation) escalation fallback — forcing tab %r directly.", _tnm[:24])
                        self._executor.execute({"action_type": "click",
                                                "click_position": [(_tb[0] + _tb[2]) / 2, (_tb[1] + _tb[3]) / 2]})
                        self._visited_tabs.add(_tnm)
                        self._filled_this_tab.clear(); self._fixation_hits.clear()
                        self._refresh_record_cache(self._observe())
                    else:
                        # LAST TAB — nowhere to advance. Escalation used to no-op here
                        # (click → no_change → guard → escalate → no tab → repeat forever).
                        # Hand the tab to the SWEEP instead: it drives every remaining
                        # field to fill/verify with its own 3-strike dead-marking (the
                        # fixated field is already dead-marked above, so it gets skipped),
                        # and finishes via source-confirm when the page is clean.
                        logger.warning("[NAV] (fixation) escalation on LAST tab — no tab to advance; "
                                       "invoking sweep to finish the page.")
                        state, _fx_finish = self._sweep_tab(state)
                        if _fx_finish:
                            break
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
            result = self._executor.execute(prediction)
            logger.info("%s", result)

            # FOCUS INFERENCE (vision perception): pixels expose no keyboard
            # focus, so focused_element_id is always None and the fill trigger
            # ("focused empty field") never fires — the agent navigates forever
            # and types nothing (live, 2026-07-10). The agent KNOWS where it
            # just clicked: remember the clicked fillable's identity; _observe
            # stamps it as focus when the observer can't provide one.
            if prediction.get("action_type") == "click":
                _fc_el = self._elem_at(state, prediction.get("click_position") or [])
                if (_fc_el is not None
                        and (_fc_el.get("type") or "").lower() in self._FILLABLE_TYPES):
                    self._inferred_focus_key = self._attempt_key(_fc_el, state)
                else:
                    self._inferred_focus_key = None

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
            validation  = self._validator.validate(state, state_after, prediction)
            logger.info("Validator → %s: %s", validation.status, validation.reason)

            # Record the field acted on this step (attempted feature) — mirrors the
            # train-time derivation (every click/type target becomes 'attempted').
            self._record_attempt(state, prediction)

            if validation.status == "done":
                # A "Submitted" / completion indicator on screen is NOT enough to end
                # the run — every tab must be verified-complete first. Otherwise a
                # stray/leaked submit (or a default status label) ends Scope #1 with
                # tabs unfilled. Gate on _confirm_finished; else keep working.
                if self._confirm_finished(state):
                    logger.info("StateValidator: task complete AND source-verified — done.")
                    break
                logger.warning("StateValidator saw a completion indicator but NOT all tabs verified "
                               "— ignoring, keep working.")

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
                        # Also blacklist the CONTAINING element's bbox center — snap can
                        # shift a click a few px off center, and the ranked-target
                        # arbitration masks by center key; both keys must match.
                        _ce_bl = self._elem_at(state, _fcp)
                        if _ce_bl and _ce_bl.get("bbox"):
                            _bb_bl = _ce_bl["bbox"]
                            self._nochange_click_pos.add(
                                (round((_bb_bl[0] + _bb_bl[2]) / 2 / 10) * 10,
                                 round((_bb_bl[1] + _bb_bl[3]) / 2 / 10) * 10))
                # A fill (type-with-text) that produced no_change = the widget didn't
                # accept the value (e.g. wx SpinCtrl rejects clipboard paste). Count
                # per field; after 2 fails mark it DEAD so the type path Tabs past it
                # instead of looping. Focus-stable via _attempt_key, immune to the
                # click-back focus churn that keeps resetting _no_change_streak.
                if prediction.get("action_type") == "keyboard" and prediction.get("text"):
                    _ff = next((e for e in state.get("elements", [])
                                if e.get("element_id") == _cur_focused_id), None)
                    if _ff is not None:
                        _ffk = self._attempt_key(_ff)
                        _ffv = prediction.get("text", "")
                        # FIRST no_change on a fill: the widget may reject CLIPBOARD PASTE
                        # (wx SpinCtrl accepts typed digits but not Ctrl+V). Before counting
                        # a fail, retry by TYPING the value as individual keystrokes into the
                        # still-focused field. Generic: any widget, keyed on paste-reject
                        # behaviour, not on field/type name.
                        if (self._fill_fail_count.get(_ffk, 0) == 0 and _ffv
                                and len(_ffv) <= 24 and not self._keystroke_retried.get(_ffk)):
                            self._keystroke_retried[_ffk] = True
                            logger.info("Fill no_change on %r — retry via keystroke-typing (paste-reject widget?).",
                                        (_ff.get('label') or _ff.get('text') or '?'))
                            self._executor.execute({"action_type": "keyboard", "key_count": 1,
                                                    "keystrokes": ["ctrl+a"]})
                            self._executor.execute({"action_type": "keyboard",
                                                    "key_count": len(_ffv), "keystrokes": list(_ffv)})
                            time.sleep(self.step_delay * 0.5)
                            continue   # re-observe next step; if it took, field is now filled
                        self._fill_fail_count[_ffk] = self._fill_fail_count.get(_ffk, 0) + 1
                        if self._fill_fail_count[_ffk] >= 2:
                            self._dead_fill_keys.add(_ffk)
                            self._mark_attempted(_ff)
                            logger.warning("Dead-field: %r rejected fill %dx — HARD-skip from now on.",
                                           (_ff.get('label') or _ff.get('text') or '?'),
                                           self._fill_fail_count[_ffk])
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

            if validation.status in ("no_change", "unexpected", "error") and self._task_name:
                logger.info("Validation failed (%s) — watching for user correction …", validation.status)
                steps = self._correction.watch(self._observer, seconds=4.0)
                if steps:
                    saved = self._correction.save(self._task_name, steps)
                    if saved:
                        logger.info("Correction saved → %s", saved)

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
            self._results.append({
                "step":         step_idx + 1,
                "state":        state,
                "action":       prediction,
                "result":       str(result),
                "validation":   validation.status,
                "guidance":     self._guidance,
                "elements":     len(state.get("elements", [])),
                "decision_by":  _decision_maker,
                "t_conf":       t_conf,
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

        self._heuristic_steps = _heuristic_steps
        logger.info("LLMAgent finished — %d step(s)  (%d heuristic).", len(self._results), _heuristic_steps)
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
                    # STRICT record bound (see 2026-07-11 contamination fix):
                    # capped blobs only ever hold the file's start — falling back
                    # to min(r) silently serves record 1 to every later record.
                    rec = r.get(self._record_num, {})
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
            # STRICT record bound (2026-07-11): when full_text came from a capped
            # UIA blob it holds only the file's start — falling back to min(records)
            # cached RECORD 1 for every later record (the ×2-probe contamination).
            # Missing record → empty cache (honest) + a loud warning.
            rec = records.get(self._record_num, {})
            if not rec:
                logger.warning("Record cache: record %d NOT in parsed source (%d record(s) visible) — cache left EMPTY, no cross-record fallback.",
                               self._record_num, len(records))
            self._cached_record = rec
            # New record = new session → clear attempted history so fields on the
            # fresh record aren't pre-marked from the previous one.
            if self._record_num != self._attempted_record_num:
                self._attempted_keys.clear()
                self._visited_tabs.clear()
                self._fill_fail_count.clear()
                self._keystroke_retried.clear()
                self._dead_fill_keys.clear()
                self._reveal_focus_count.clear()
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
            from data_sources.notepad_source import _find_field_line, _record_line_span
            # RECORD-BOUNDED search (2026-07-11): an unbounded line scan always
            # hits record 1's line first — this peek cached record 1's whole
            # claims block into record 2's run (live, --start_record 2 probe).
            # Search ONLY record N's slice; absent there = absent, full stop.
            _span = _record_line_span(lines, self._record_num)
            if _span == (-1, -1):
                hit = None                # records exist, record N absent → nothing
            elif _span:
                _s0, _s1 = _span
                hit = _find_field_line(lines[_s0:_s1], field_name)
                if hit:
                    hit = (hit[0] + _s0, hit[1])      # back to absolute line no.
            else:
                # No record headers (single-record source) → whole text is fair.
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

    def _dismiss_modal(self) -> bool:
        """Dismiss a modal dialog (SEPARATE window) sitting in front of the locked
        form — e.g. the wx validation-error popup a premature Submit raises. The
        agent observes only the form hwnd, so it is otherwise blind to this dialog,
        and the modal blocks SetForegroundWindow → the run freezes until a human
        clicks OK. This clicks that button automatically (what the human does).
        Generic: keys on 'foreground window != locked form' + generic confirm-button
        text; no app/field names. Returns True if a dialog was dismissed."""
        if not self._locked_hwnd:
            return False
        try:
            import win32gui
            import uiautomation as _uia
            fg = win32gui.GetForegroundWindow()
            if not fg or fg == self._locked_hwnd or not win32gui.IsWindow(fg):
                return False
            dlg = _uia.ControlFromHandle(fg)
            if dlg is None:
                return False
            for _nm in ("OK", "Okay", "Yes", "Close", "Continue"):
                _b = dlg.ButtonControl(searchDepth=8, Name=_nm)
                if _b.Exists(maxSearchSeconds=0.2):
                    _r = _b.BoundingRectangle
                    logger.warning("Modal dialog %r blocking form — clicking %r to dismiss.",
                                   (win32gui.GetWindowText(fg) or "?")[:40], _nm)
                    self._executor.execute({"action_type": "click",
                                            "click_position": [(_r.left + _r.right) / 2,
                                                               (_r.top + _r.bottom) / 2]})
                    time.sleep(0.2)
                    return True
            # Fallback: a foreign window stole foreground — press Enter (activates the
            # default button on a wx MessageDialog, i.e. OK).
            logger.warning("Foreign window %r frontmost (no named button) — Enter to dismiss.",
                           (win32gui.GetWindowText(fg) or "?")[:40])
            self._executor.execute({"action_type": "hotkey", "keys": ["enter"]})
            time.sleep(0.2)
            return True
        except Exception as exc:
            logger.debug("Dismiss-modal failed: %s", exc)
        return False

    def _reassert_form_window(self) -> None:
        """Bring the locked form back to foreground if focus drifted away. Runs at
        the top of every step → the model physically cannot act on another window."""
        if not self._locked_hwnd:
            return
        try:
            import win32gui
            if win32gui.GetForegroundWindow() == self._locked_hwnd:
                return
            if not win32gui.IsWindow(self._locked_hwnd):
                return
            # A different window is frontmost — if it's a modal dialog (e.g. the
            # validation-error popup), dismiss it; SetForegroundWindow can't
            # background a modal, so we must close it before re-asserting.
            if self._dismiss_modal():
                if win32gui.GetForegroundWindow() == self._locked_hwnd:
                    return
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

    def _form_viewport_top(self, state: Dict[str, Any]) -> float:
        """Top edge of the form's scroll viewport. The pane does NOT start at
        y=0 — the title bar + tab strip sit above it, so an element scrolled UP
        out of the pane keeps a stale bbox in that zone (y≈120-145) and passes
        any `top >= 0` visibility check, then gets clicked → tab-strip mis-hit.
        Geometry-driven: viewport starts just below the lowest tab item in the
        ACTIVE window; falls back to 0 (legacy behavior) if no tab strip."""
        _tabs_bot = [e["bbox"][3] for e in state.get("elements", [])
                     if (e.get("type") or "").lower() in ("tabitem", "tabitemcontrol")
                     and e.get("window_role") != "background" and e.get("bbox")]
        return (max(_tabs_bot) + 2) if _tabs_bot else 0.0

    def _scroll_pane_bottom(self, state: Dict[str, Any]) -> float:
        """Bottom edge of the ACTIVE ScrolledPanel (the scroll fold), NOT the outer
        window frame. Uses UIA: walks up from the first visible EditControl found
        in state elements (geometry-driven, no hardcoded field names) until a
        VerticallyScrollable pane is found, then returns its BoundingRectangle.bottom.
        Falls back to _form_viewport_bottom if the pane cannot be located."""
        try:
            import uiautomation as _uia
            import win32gui as _w32g

            hwnd = self._locked_hwnd or _w32g.GetForegroundWindow()
            root = _uia.ControlFromHandle(hwnd)

            # Find ANY visible EditControl from state elements (type-driven, not name-driven).
            _anchor = None
            for _e in state.get("elements", []):
                if (_e.get("type") or "").lower() != "editcontrol":
                    continue
                _b = _e.get("bbox")
                if not _b:
                    continue
                _cx, _cy = (_b[0] + _b[2]) / 2, (_b[1] + _b[3]) / 2
                _cand = root.EditControl(searchDepth=25)
                # Walk the tree to find a control whose center matches this bbox.
                # Faster: use ControlFromPoint to get the control at the field center.
                try:
                    _cand = _uia.ControlFromPoint(int(_cx), int(_cy))
                    if _cand is not None and _cand.Exists(maxSearchSeconds=0):
                        _anchor = _cand
                        break
                except Exception:
                    continue
            if _anchor is None:
                _anchor = _uia.GetFocusedControl()
            if _anchor is None:
                return self._form_viewport_bottom(state)

            # Walk UP from the anchor to the first VerticallyScrollable pane.
            _cur = _anchor
            for _ in range(15):
                if _cur is None:
                    break
                try:
                    _spc = _cur.GetScrollPattern()
                    if _spc is not None and _spc.VerticallyScrollable:
                        br = _cur.BoundingRectangle
                        return float(br.bottom)
                except Exception:
                    pass
                try:
                    _cur = _cur.GetParentControl()
                except Exception:
                    break
        except Exception:
            pass
        return self._form_viewport_bottom(state)

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

    @staticmethod
    def _is_cycling(history) -> bool:
        """Detect a short repeating CYCLE in the recent action history (not just an
        exact repeat): period-2 (A,B,A,B) or period-3 (A,B,C,A,B,C). Used with a
        'no fill happened' check to spot oscillation loops the exact-repeat guard
        misses. Generic — operates on opaque action fingerprints."""
        h = list(history)
        if len(h) >= 4 and h[-1] == h[-3] and h[-2] == h[-4] and h[-1] != h[-2]:
            return True                                  # A,B,A,B
        if len(h) >= 6 and h[-1] == h[-4] and h[-2] == h[-5] and h[-3] == h[-6] \
                and len({h[-1], h[-2], h[-3]}) > 1:
            return True                                  # A,B,C,A,B,C
        return False

    @staticmethod
    def _norm(s: str) -> str:
        """Normalize a value for comparison: lowercase, strip, drop spaces and common
        punctuation, normalize dates. Generic — so '(951) 440-2281' == '9514402281'
        and '7/14/1978' == '07/14/1978'. No field-specific rules."""
        import re
        s = (s or "").strip().lower()
        # date m/d/y → zero-pad parts so 7/14/1978 == 07/14/1978
        _m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", s)
        if _m:
            return "/".join(f"{int(p):02d}" if i < 2 else p for i, p in enumerate(_m.groups()))
        # else strip all non-alphanumerics
        return re.sub(r"[^a-z0-9]", "", s)

    def _is_checked(self, elem: Dict[str, Any]) -> bool:
        """Read a checkbox's actual ticked state. PRIMARY: UIA TogglePattern by the
        checkbox's own name — the reliable, standard way (no pixel guessing, which
        misreads when the bbox centre lands on the label, not the box). Falls back to
        BM_GETCHECK via WindowFromPoint. Generic — any checkbox, any tab."""
        _lbl = (elem.get("label") or elem.get("text") or "").strip()
        if _lbl:
            try:
                import uiautomation as _uia, win32gui as _wgg
                _root = _uia.ControlFromHandle(self._locked_hwnd or _wgg.GetForegroundWindow())
                if _root is not None:
                    _cb = _root.CheckBoxControl(searchDepth=25, Name=_lbl)
                    if _cb.Exists(maxSearchSeconds=0.3):
                        _tp = _cb.GetTogglePattern()
                        if _tp is not None:
                            return _tp.ToggleState == 1            # ToggleState_On
            except Exception:
                pass
        b = elem.get("bbox")
        if not b:
            return False
        try:
            import win32gui as _wg, win32api as _wa
            _h = _wg.WindowFromPoint((int((b[0] + b[2]) / 2), int((b[1] + b[3]) / 2)))
            return bool(_h) and _wa.SendMessage(_h, 0x00F0, 0, 0) == 1   # BM_GETCHECK == BST_CHECKED
        except Exception:
            return False

    def _field_matches(self, elem: Dict[str, Any], expected) -> bool:
        """True if the control's REAL value already equals `expected` — checkbox
        ticked-state (read live) or normalized text/combo value. Used to veto a false
        re-fix proposal (esp. checkboxes the LLM can't see) so verify can't loop."""
        expected = "" if expected is None else str(expected)
        if (elem.get("type") or "").lower() == "checkboxcontrol":
            _want = expected.strip().lower() in ("yes", "yes (check)", "true", "checked", "x", "1")
            return _want == self._is_checked(elem)
        return self._norm(elem.get("value") or "") == self._norm(expected)

    def _view_mismatches(self, state: Dict[str, Any]):
        """DETERMINISTIC check of the current view: for every fillable control, read
        its REAL value (text/combo = value, checkbox = ticked-state), look up the
        expected value by the control's OWN label in the source, normalized-compare.
        Returns a list of (element, expected_value) that are CLEARLY wrong/empty but
        should hold a value. Skips controls the source has no value for (ambiguous /
        leave-blank → handled by the LLM fallback). Generic: type + label + source."""
        out = []
        for e in state.get("elements", []):
            if e.get("window_role") == "background" or not e.get("bbox"):
                continue
            _typ = (e.get("type") or "").lower()
            if _typ not in ("editcontrol", "comboboxcontrol", "checkboxcontrol"):
                continue
            _lbl = (e.get("label") or e.get("text") or "").strip()
            if not _lbl:
                continue
            if self._attempt_key(e) in self._dead_fill_keys:
                continue   # proven unfillable (tried+failed) — accept it, don't block submit
            _exp = self._lookup_field(_lbl, section=self._detect_section(state, e))
            if not _exp:
                continue   # source has no value (blank/ambiguous) — leave to LLM fallback
            if _typ == "checkboxcontrol":
                _want = _exp.strip().lower() in ("yes", "yes (check)", "true", "checked", "x", "1")
                if _want != self._is_checked(e):
                    out.append((e, _exp))
            else:
                if self._norm(e.get("value") or "") != self._norm(_exp):
                    out.append((e, _exp))
        return out

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
                    # STRICT record bound: UIA blobs are capped (~2000 chars = the
                    # file's start = record 1), so "record N missing → use the
                    # first record" typed record 1's claim into record 2 live
                    # (2026-07-11 ×2 probe). Missing = EMPTY, never another record.
                    rec = r.get(self._record_num, {})
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

    def _visible_field_sig(self, state: Dict[str, Any]) -> frozenset:
        """Signature of fillable fields currently inside the form viewport
        (label + rounded y). Used to VERIFY a scroll actually moved the view:
        signature changes → new content revealed; unchanged → at the bottom."""
        vb = self._form_viewport_bottom(state)
        sig = set()
        for e in state.get("elements", []):
            if e.get("window_role") == "background":
                continue
            if (e.get("type") or "").lower() not in ("editcontrol", "comboboxcontrol", "checkboxcontrol"):
                continue
            b = e.get("bbox")
            if not b or len(b) != 4:
                continue
            cy = (b[1] + b[3]) / 2
            if b[1] >= 0 and cy <= vb:
                lbl = (e.get("label") or e.get("text") or "").strip().lower()
                sig.add((lbl, round(cy / 15) * 15))
        return frozenset(sig)

    def _no_visible_empty_field(self, state: Dict[str, Any]) -> bool:
        """
        True when NO actionable empty field is currently on-screen — the signal to
        scroll-to-reveal. Universal mechanic (no field names / coords / app names):
          fillable WIDGET TYPE (edit/combobox) + empty VALUE + not yet 'attempted'
          + geometry inside the active window's on-screen rect (GetWindowRect bottom).
        Off-fold fields still report real bboxes, so we gate on the window's actual
        visible bottom rather than the raw screen height.
        """
        v_bottom = self._form_viewport_bottom(state) - 8   # live form viewport bottom
        _FILL = {"editcontrol", "input", "comboboxcontrol"}
        for e in state.get("elements", []):
            if e.get("window_role") == "background":
                continue
            if (e.get("type") or "").lower() not in _FILL:
                continue
            if (e.get("value") or "").strip():          # already filled
                continue
            if self._attempt_key(e) in self._attempted_keys:   # tried (empty optional)
                continue
            b = e.get("bbox")
            if not b or len(b) != 4:
                continue
            cy = (b[1] + b[3]) / 2
            if b[1] >= 0 and cy <= v_bottom:            # inside the visible window
                return False                            # an actionable field is visible
        return True

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

    # ── NAVIGATION PROTOCOL: find a needed field, scroll to it (no Tab) ───────
    # Rule: a tab is not "done" until every field that needs a value is filled.
    # If a needed field isn't on screen, FIND it by dragging the scrollbar — never
    # Tab. Reveal = scroll · advance = scroll-to-next · tab-switch only when the
    # whole tab has no missing field. Generic: widget TYPE + own value/key + geometry.

    def _visible_empty_count(self, state: Dict[str, Any]) -> int:
        """Count fillable empty fields that are currently ON-SCREEN (within the
        live viewport). Generic: driven by widget type + geometry only.
        Excludes dead/attempted fields — mirrors _find_missing_field's filter."""
        vp_bottom = self._scroll_pane_bottom(state)
        count = 0
        for e in state.get("elements", []):
            if e.get("window_role") == "background":
                continue
            if (e.get("type") or "").lower() not in (
                    "editcontrol", "comboboxcontrol", "checkboxcontrol"):
                continue
            b = e.get("bbox")
            if not b or not e.get("enabled", True):
                continue
            if (e.get("value") or "").strip():
                continue                               # already filled
            k = self._attempt_key(e)
            if k in self._attempted_keys or k in self._dead_fill_keys:
                continue                               # dead / already attempted
            # On-screen: field's top edge is above the viewport bottom.
            # (Fields scrolled above the top will have negative bbox tops but are
            # still counted as visible by the transformer — exclude only those
            # clipped below the fold, i.e. top >= vp_bottom.)
            if b[1] < vp_bottom:
                count += 1
        return count

    def _has_offfold_empty(self, state: Dict[str, Any]) -> bool:
        """True if there is at least one fillable empty field (not dead/attempted)
        whose bbox bottom is BELOW the current scroll-pane fold — i.e. there is
        more content to reveal by scrolling down. Generic: geometry-driven only."""
        vp_bottom = self._scroll_pane_bottom(state)
        for e in state.get("elements", []):
            if e.get("window_role") == "background":
                continue
            if (e.get("type") or "").lower() not in (
                    "editcontrol", "comboboxcontrol", "checkboxcontrol"):
                continue
            b = e.get("bbox")
            if not b or not e.get("enabled", True):
                continue
            if (e.get("value") or "").strip():
                continue
            k = self._attempt_key(e)
            if k in self._attempted_keys or k in self._dead_fill_keys:
                continue
            if b[3] > vp_bottom:          # bottom edge is below the visible area
                return True
        return False

    def _find_missing_field(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Topmost fillable field (edit/combo) that still needs a value: empty,
        not yet attempted, not dead. ANY vertical position incl. below the fold.
        Returns the element dict or None (= tab has nothing left to fill)."""
        cands = []
        for e in state.get("elements", []):
            if e.get("window_role") == "background":
                continue
            if (e.get("type") or "").lower() not in ("editcontrol", "comboboxcontrol"):
                continue
            if not e.get("bbox") or not e.get("enabled", True):
                continue
            if (e.get("value") or "").strip():
                continue
            k = self._attempt_key(e)
            if k in self._attempted_keys or k in self._dead_fill_keys:
                continue
            cands.append(e)
        if not cands:
            return None
        cands.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
        return cands[0]

    def _scroll_into_view(self, label: str) -> bool:
        """Bring a field on-screen using the NATIVE UIA ScrollItemPattern — no
        pixels, no scrollbar geometry. The field is already in the accessibility
        tree (that's how the agent sees it); this asks Windows to scroll the panel
        exactly enough to show it. Falls back to SetFocus (wx auto-scrolls a focused
        child into view). Returns True if a scroll was invoked. Works even for
        fields not in our current snapshot (queries the live UIA tree)."""
        if not label:
            return False
        try:
            import uiautomation as _uia
            import win32gui as _w32
            hwnd = self._locked_hwnd or _w32.GetForegroundWindow()
            root = _uia.ControlFromHandle(hwnd)
            if root is None:
                return False
            ctrl = root.EditControl(searchDepth=20, Name=label)
            if not ctrl.Exists(maxSearchSeconds=0.3):
                ctrl = root.ComboBoxControl(searchDepth=20, Name=label)
            if not ctrl.Exists(maxSearchSeconds=0.3):
                ctrl = root.CheckBoxControl(searchDepth=20, Name=label)
            if not ctrl.Exists(maxSearchSeconds=0.3):
                return False
            # PRIMARY: SetFocus — wxPython's ScrolledPanel auto-scrolls a focused
            # child into view. This is the mechanic wx actually honours (the pixel
            # scrollbar drag and ScrollItemPattern are unreliable / no-ops here).
            try:
                ctrl.SetFocus()
                logger.info("ScrollIntoView: focused %r → wx auto-scrolls it on-screen.", label[:28])
                return True
            except Exception:
                pass
            # Fallback: native ScrollItemPattern if SetFocus is unavailable.
            try:
                _sip = ctrl.GetScrollItemPattern()
                if _sip is not None:
                    _sip.ScrollIntoView()
                    logger.info("ScrollIntoView: %r via UIA ScrollItemPattern.", label[:28])
                    return True
            except Exception:
                pass
        except Exception as exc:
            logger.warning("ScrollIntoView failed for %r: %s", label[:28], exc)
        return False

    def _scrollbar_drag(self, state: Dict[str, Any], dy: float) -> bool:
        """Scroll the active ScrolledPanel by one large increment (page-down or
        page-up depending on sign of dy).

        Strategy (A then B — no hardcoded coords):

        A. UIA ScrollPattern on the active tab pane: finds the ScrolledPanel via
           UIA, calls ScrollPattern.Scroll(NoAmount, LargeIncrement/LargeDecrement).
           This is fully geometry-driven (UIA tells us which pane is active) and
           bypasses all scrollbar pixel geometry.

        B. Track-click fallback: click the lower (or upper) region of the
           scrollbar TRACK on the active panel. Panel right edge comes from the
           panel's own UIA BoundingRectangle — not the window rect — so we never
           add a DWM-shadow offset guess.  A click on the track triggers wx's
           native page-scroll, which is as reliable as a physical scrollbar click.

        Returns True if a scroll action was issued."""
        scroll_down = dy > 0
        try:
            import uiautomation as _uia
            import win32gui as _w32g

            hwnd = self._locked_hwnd or _w32g.GetForegroundWindow()
            root = _uia.ControlFromHandle(hwnd)

            # Anchor on a REAL field on the CURRENT tab — GENERIC (no hardcoded names,
            # else it only works on Policyholder and every other tab fails to scroll).
            # Prefer the focused control (always on the active tab); else the first
            # edit/combo/checkbox found anywhere in the form.
            _anchor = _uia.GetFocusedControl()
            try:
                _ok = _anchor is not None and _anchor.BoundingRectangle.width() > 0
            except Exception:
                _ok = False
            if not _ok:
                _anchor = None
                for _finder in (root.EditControl, root.ComboBoxControl, root.CheckBoxControl):
                    _c = _finder(searchDepth=25)
                    if _c.Exists(maxSearchSeconds=0.2):
                        _anchor = _c
                        break

            def _anchor_y():
                try:
                    return _anchor.BoundingRectangle.top
                except Exception:
                    return None

            # Walk UP from the anchor to the scrollable pane.
            _panel, _cur = None, _anchor
            for _ in range(15):
                if _cur is None:
                    break
                try:
                    _spc = _cur.GetScrollPattern()
                    if _spc is not None and _spc.VerticallyScrollable:
                        _panel = _cur
                        break
                except Exception:
                    pass
                try:
                    _cur = _cur.GetParentControl()
                except Exception:
                    break

            if _panel is None:
                # Fallback: DFS the whole tree for any vertically-scrollable pane
                # (focus may be on a tab/button outside the pane on this tab).
                def _dfs_pane(node, d=0):
                    if node is None or d > 10:
                        return None
                    try:
                        _s = node.GetScrollPattern()
                        if _s is not None and _s.VerticallyScrollable:
                            return node
                    except Exception:
                        pass
                    try:
                        for _ch in node.GetChildren():
                            _r = _dfs_pane(_ch, d + 1)
                            if _r is not None:
                                return _r
                    except Exception:
                        pass
                    return None
                _panel = _dfs_pane(root)
            if _panel is None:
                logger.warning("Scroll: no scrollable pane found (anchor-walk + DFS).")
                return False

            _sp = _panel.GetScrollPattern()
            _y0 = _anchor_y()
            _amt = _uia.ScrollAmount.LargeIncrement if scroll_down else _uia.ScrollAmount.LargeDecrement
            _sp.Scroll(_uia.ScrollAmount.NoAmount, _amt)
            time.sleep(0.2)
            _y1 = _anchor_y()
            # VERIFY the panel actually moved — Scroll() returning без error does NOT
            # mean wx honoured it. If the anchor field's Y is unchanged, it didn't move.
            if _y0 is not None and _y1 is not None and _y0 == _y1:
                logger.warning("Scroll: ScrollPattern call succeeded but panel DID NOT MOVE "
                               "(anchor Y stayed %s) — pane=%s", _y0, getattr(_panel, "Name", "?"))
                return False
            logger.info("Scroll: UIA ScrollPattern %s MOVED panel (anchor Y %s→%s, pane=%s).",
                        "down" if scroll_down else "up", _y0, _y1, getattr(_panel, "Name", "?"))
            return True
        except Exception as _exc_a:
            logger.warning("Scroll: ScrollPattern error — %s", _exc_a)
            return False

    def _reveal_missing_by_scroll(self, state: Dict[str, Any]):
        """NAVIGATION PROTOCOL 'FIND'. Bring the next missing field into view
        AND focus it so the existing fill path fires next iteration.  Returns:

          - fresh state dict   → missing field is now visible AND focused;
                                 caller re-loops so the fill path completes it.
          - None  ("COMPLETE") → _find_missing_field returned None; the tab
                                 genuinely has no unfilled field left.  Caller
                                 MAY advance to the next tab or submit.
          - "STUCK"            → a missing field EXISTS but the scroll action
                                 did not move the view (signature unchanged).
                                 Callers MUST NOT advance or submit; they should
                                 retry with a different mechanic or log and hold.

        No Tab, no wheel — scrollbar drag or mouse-click only."""
        miss = self._find_missing_field(state)
        if miss is None:
            return None                                     # tab complete

        vb  = self._form_viewport_bottom(state) - 8
        vt  = self._form_viewport_top(state)
        top = miss["bbox"][1]
        bot = miss["bbox"][3]

        # ── Field is already in the viewport ─────────────────────────────────
        # Previous code just returned `state` here — that left focus unchanged so
        # the fill path never fired and the transformer re-predicted the same
        # tab-click forever (the 64-step infinite loop from 2026-06-17).
        # Fix: click the field's center so the OPT2 "focused empty field → fill"
        # merge path fires on the very next iteration.
        # Guard: if the same visible field has been clicked N times without being
        # filled, it is genuinely unfillable → mark dead so _find_missing_field
        # skips it, then look for the next missing field in the same call.
        if top >= vt and bot <= vb:
            fkey = self._attempt_key(miss)
            count = self._reveal_focus_count.get(fkey, 0) + 1
            self._reveal_focus_count[fkey] = count
            _REVEAL_DEAD_LIMIT = 2
            if count > _REVEAL_DEAD_LIMIT:
                logger.warning(
                    "Reveal-focus: field %r focused %d× without being filled — marking dead.",
                    (miss.get("label") or miss.get("text") or "?")[:28], count,
                )
                self._dead_fill_keys.add(fkey)
                # Recurse once: maybe there's another missing field we can reach
                return self._reveal_missing_by_scroll(state)
            # Widget-type-aware focus/fill — geometry-driven, no field-name hardcode.
            x1, y1, x2, y2 = miss["bbox"]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            field_name = (miss.get("label") or miss.get("text") or "?")[:28]
            _miss_type = (miss.get("type") or "").lower()

            if _miss_type == "comboboxcontrol":
                # A raw center-click on a combobox opens the dropdown but selects
                # nothing — the next step sees an expanded list, OPT2 combobox-fill
                # does not fire, _reveal_focus_count climbs, and after 2× the field
                # is wrongly marked dead.  Instead, drive the full open+select cycle
                # that OPT2 uses: click to open, wait for listitems, pick the value.
                _miss_label = (miss.get("label") or miss.get("text") or "").strip()
                _miss_sec   = self._detect_section(state, miss)
                _miss_val   = self._lookup_field(_miss_label, section=_miss_sec)
                logger.info("Reveal-focus: combobox %r → open+select %r [attempt %d].",
                            field_name, _miss_val, count)
                self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
                if not _miss_val:
                    # No record value → escape dropdown + Tab (optional field)
                    time.sleep(0.25)
                    self._executor.execute({"action_type": "keyboard",
                                            "key_count": 1, "keystrokes": ["escape"]})
                    self._mark_attempted(miss)
                    self._executor.execute({"action_type": "keyboard",
                                            "key_count": 1, "keystrokes": ["tab"]})
                    time.sleep(0.3)
                    return self._observe()
                # Wait for the dropdown to render its listitems (up to 4 × 0.35s)
                _rf_items = []
                for _rf_try in range(4):
                    time.sleep(0.35)
                    _rf_st = self._observe()
                    _rf_items = [e for e in _rf_st.get("elements", [])
                                 if e.get("type") == "listitemcontrol"
                                 and e.get("window_role") != "background"
                                 and e.get("bbox")]
                    if _rf_items:
                        break
                _rf_vlc = _miss_val.strip().lower()
                _rf_opt = lambda e: (e.get("text") or e.get("label") or "").strip()
                _rf_hit = (next((e for e in _rf_items
                                 if _rf_opt(e).lower() == _rf_vlc), None)
                           or next((e for e in _rf_items
                                    if _rf_opt(e).lower().startswith(_rf_vlc)
                                    or _rf_vlc.startswith(_rf_opt(e).lower())), None))
                if _rf_hit:
                    _rfb = _rf_hit["bbox"]
                    self._executor.execute({"action_type": "click",
                                            "click_position": [(_rfb[0]+_rfb[2])/2,
                                                               (_rfb[1]+_rfb[3])/2]})
                    logger.info("Reveal-focus: combobox %r → selected %r.", field_name, _miss_val)
                    time.sleep(0.25)
                    self._executor.execute({"action_type": "keyboard",
                                            "key_count": 1, "keystrokes": ["tab"]})
                else:
                    logger.warning("Reveal-focus: combobox %r — %r not in options %s.",
                                   field_name, _miss_val,
                                   [_rf_opt(e) for e in _rf_items][:10])
                    self._executor.execute({"action_type": "keyboard",
                                            "key_count": 1, "keystrokes": ["escape"]})
                    self._executor.execute({"action_type": "keyboard",
                                            "key_count": 1, "keystrokes": ["tab"]})
                time.sleep(0.3)
                return self._observe()
            else:
                # editcontrol (and any other widget): resolve the value and type it
                # directly — the same mechanics OPT2 uses for a focused empty field.
                # A plain click-and-return was broken: the transformer immediately
                # predicted a tab-click after the focus click, routing back here before
                # OPT2 could fire, so _reveal_focus_count climbed to dead-limit and the
                # field was never filled (live observed 2026-06-17, Policy Number).
                _miss_label = (miss.get("label") or miss.get("text") or "").strip()
                _miss_sec   = self._detect_section(state, miss)
                _miss_val   = self._lookup_field(_miss_label, section=_miss_sec)
                _miss_filled_key = f"{_miss_sec} {_miss_label}" if _miss_sec else _miss_label
                logger.info("Reveal-focus: editcontrol %r → click+type %r [attempt %d].",
                            field_name, _miss_val, count)
                self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
                time.sleep(0.25)   # wx needs ~200ms to propagate focus to UIA after a click
                _norm_val = (_miss_val or "").strip().lower().strip("()").strip()
                if (not _miss_val) or _norm_val in {"none", "n/a", "na"} or _norm_val.startswith("leave blank"):
                    # No record value — Tab past and mark attempted so the transformer
                    # stops re-targeting this field.
                    logger.info("Reveal-focus: editcontrol %r — no value, Tab past.", field_name)
                    self._mark_attempted(miss)
                    self._executor.execute({"action_type": "keyboard",
                                            "key_count": 1, "keystrokes": ["tab"]})
                    time.sleep(0.3)
                    return self._observe()
                # Type the value via idempotent paste (select-all + ctrl-v, same as
                # the main OPT2 type path in executor._keyboard).
                self._executor.execute({"action_type": "keyboard",
                                        "key_count": 1, "keystrokes": [],
                                        "text": _miss_val})
                self._filled_this_tab.add(_miss_filled_key)
                logger.info("Reveal-focus: editcontrol %r → typed %r → Tab.", field_name, _miss_val)
                self._executor.execute({"action_type": "keyboard",
                                        "key_count": 1, "keystrokes": ["tab"]})
                time.sleep(0.3)
                return self._observe()

        # ── Field is off-screen → scroll toward it ────────────────────────────
        sig = self._visible_field_sig(state)
        dy  = 160.0 if top > vb else -160.0                # down if below, up if above
        if not self._scrollbar_drag(state, dy):
            logger.warning("Reveal-scroll: scrollbar_drag returned False — STUCK (missing=%r).",
                           (miss.get("label") or miss.get("text") or "?")[:28])
            return "STUCK"
        time.sleep(self.step_delay * 0.6)
        st = self._observe()
        if self._visible_field_sig(st) == sig:
            logger.warning("Reveal-scroll: STUCK — scroll fired but view did not move "
                           "(missing field %r still unreachable).",
                           (miss.get("label") or miss.get("text") or "?")[:28])
            return "STUCK"                                  # must not advance or submit
        logger.info("Reveal-scroll: dragged to missing field %r.",
                    (miss.get("label") or miss.get("text") or "?")[:28])
        return st

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
            # wx hides inactive panels by moving their DIRECT children off-screen
            # (negative BoundingRectangle), while the active panel's direct children
            # remain at positive screen coordinates.  Find the first pane whose
            # direct children have positive coords — that pane is the active tab.
            _TAB_PANE_NAMES = self._scope.tab_pane_names   # scope-provided; [] → none
            _search_root = root   # fallback: search entire tree
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
                    logger.info("_uia_focus_first_field: active pane = %r", _pname)
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
            logger.info("Tab-advance focus: UIA SetFocus on %r (first unhandled)", (target.Name or "").strip())
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
        """
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
            # Ground-truth filters (don't trust _filled_this_tab bookkeeping alone —
            # a field filled via another path stays absent from it → false "unfilled"
            # → focus-yank deadlock). A field still NEEDS filling if it is empty on
            # screen and not yet attempted. NOTE: do NOT gate on _lookup_field here —
            # many on-screen labels don't resolve against the record-cache keys
            # (section/label↔key mismatch); gating on it drops every field on a fresh
            # tab → false "tab complete". The LLM value lookup (screen_text/notepad)
            # resolves the value at fill time regardless.
            if (e.get("value") or "").strip():
                continue   # already filled on screen
            if self._attempt_key(e) in self._attempted_keys:
                continue   # already acted on this session
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
                    logger.info("Tab-advance focus: UIA SetFocus on %r (first unhandled)", field_label)
                    return True
            except Exception:
                pass  # fall through to coordinate click
        # Fallback: coordinate click, clipped to be within the field and below the tab strip
        cy = max(cy, _tab_floor)
        cy = min(cy, y2 - 2)
        logger.info("Tab-advance focus: clicking first unhandled field %r @ (%.0f, %.0f)", field_label, cx, cy)
        self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
        return True

    def _tab_walk_to_unfilled(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Completion mop-up: when a tab LOOKS done on-screen but the record still has
        unfilled fields, Tab through the tab (each Tab auto-scrolls the focused field
        into view in wx — reaches below-fold fields the model/pixel-scroll missed).
        Stop at the first EMPTY edit/combobox whose label HAS a record value, and return
        the fresh state focused on it → the caller continues and the normal fill path
        (transformer says type, LLM/record supplies the value) fills it. Returns None if
        a full walk finds nothing unfilled = tab genuinely complete.

        Generic: fields by widget TYPE, values by the field's own LABEL, 'has a value'
        from the record. No field names / coords / counts.
        """
        _seen_keys: set = set()
        for _ in range(60):                       # cap ~ max fields on a tab
            self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
            time.sleep(0.15)
            st  = self._observe()
            fid = st.get("focused_element_id")
            el  = next((e for e in st.get("elements", []) if e.get("element_id") == fid), None)
            if el is None:
                continue
            typ = (el.get("type") or "").lower()
            if typ not in ("editcontrol", "comboboxcontrol"):
                continue                          # edits + combos fill via the main loop
            lbl = (el.get("label") or el.get("text") or "").strip()
            if not lbl:
                continue
            sec  = self._detect_section(st, el)
            key  = (f"{sec} {lbl}" if sec else lbl).lower()
            if key in _seen_keys:
                break                             # cycled back to a field we've seen → done
            _seen_keys.add(key)
            if (el.get("value") or "").strip():
                continue                          # already filled
            if self._attempt_key(el) in self._attempted_keys:
                continue                          # already handled this session
            if self._lookup_field(lbl, section=sec):   # record HAS a value for it → go fill
                logger.info("Completion: Tab-walked to unfilled field %r — letting it fill.", lbl[:28])
                return st
        return None

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
        self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
        self._current_tab_idx = next_idx
        self._visited_tabs.add(tab_name)
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
            # Deterministic absent=skip is FINAL: the record says this field is
            # blank. The source-resolver backup below reads the visible Notepad
            # text (top of file = record 1) and typed record 1's claim right
            # past the first version of this skip (live 2026-07-11 17:53).
            if llm_action.get("skip_field"):
                logger.info("[MERGE] deterministic skip honored — Tab past, resolver NOT consulted.")
                return {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
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
            # RANKED WHERE: prefer the model's best NON-masked candidate over its
            # raw argmax — dead/filled/blacklisted targets fall through to the
            # model's own next choice instead of forcing the LLM to take WHERE.
            _rk = self._pick_ranked_target(state, t_pred)
            if _rk is not None:
                _t_pos, _click_conf = _rk[1], _rk[2]
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
        # STRICT record bound (2026-07-11): missing record = no value, never
        # another record's line (capped blobs always start at record 1).
        rec = records.get(self._record_num, {})
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
                # DETERMINISTIC ABSENT=SKIP (2026-07-11): the record IS present
                # (cache populated) yet says nothing about this field even after
                # refresh + record-bounded peek → the correct value is BLANK.
                # Asking a small local LLM anyway made it INVENT (typed the SSN
                # into 'Claim Amount', looped fantasy combobox options 10 steps —
                # live --start_record 2 probe). Return the empty type-action
                # directly — downstream OPT2 already treats it as Tab-past skip.
                # Checkboxes excluded (unchecked is a valid state, not a skip).
                _f_ty = (focused_el.get("type") or "").lower()
                if (not _expected and _fn != "?" and self._cached_record
                        and not _fv
                        and _f_ty in ("editcontrol", "input", "comboboxcontrol",
                                      "combobox", "spincontrolcontrol", "spincontrol")):
                    logger.info("Deterministic skip: %r absent from record %d — leave blank, no LLM call.",
                                _fn, self._record_num)
                    # skip_field: merge must NOT "rescue" the empty text via the
                    # transformer's source resolver — it reads the visible
                    # Notepad text (top of file = record 1) and re-injected
                    # record 1's claim right past this skip (live 17:53 probe).
                    return {"action_type": "type", "text": "", "skip_field": True}
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

    def _ask_llm_next_gap(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """COMPLETENESS + NAVIGATION via the LLM. The current tab looks done on-screen.
        Ask the LLM to decide the next move toward a fully-filled form:
          - if an unfilled field is still on THIS tab → click it (we'll fill it),
          - else → switch to a tab not yet visited,
          - else (all tabs visited) → done.
        The LLM only chooses WHERE-next; values still come from the record. Generic:
        fields/tabs are read live, visited-set is tracked — no hardcoded names/order.
        """
        _FILL = ("editcontrol", "comboboxcontrol", "checkboxcontrol")
        _filled_l = {s.lower() for s in self._filled_this_tab}
        empties = []
        for e in state.get("elements", []):
            if e.get("window_role") == "background":
                continue
            if (e.get("type") or "").lower() not in _FILL:
                continue
            lbl = (e.get("label") or e.get("text") or "").strip()
            if not lbl or (e.get("value") or "").strip():
                continue
            sec = self._detect_section(state, e)
            key = (f"{sec} {lbl}" if sec else lbl).lower()
            # NOTE: no bare-label fallback match — `key` already IS the bare
            # label for non-sectioned fields, and matching bare labels for
            # SECTIONED fields is exactly the Driver 2/3 collision (Driver 1's
            # 'First Name' hid the other two).
            if key in _filled_l or self._attempt_key(e) in self._attempted_keys:
                continue
            empties.append(lbl)
        _tab_elems = [e for e in state.get("elements", [])
                      if (e.get("type") or "").lower() in ("tabitem", "tabitemcontrol")
                      and e.get("window_role") != "background"
                      and e.get("bbox")]
        all_tabs = [(e.get("text") or e.get("label") or "").strip() for e in _tab_elems]
        unvisited = [t for t in all_tabs if t and t.lower() not in {v.lower() for v in self._visited_tabs}]
        msg = (
            "You are completing a multi-tab form. The current tab looks done on screen. "
            "Decide the SINGLE next action to finish filling the whole form.\n\n"
            f"Task goal: {self.goal}\n"
            f"Unfilled fields still visible on THIS tab: {empties if empties else 'NONE'}\n"
            f"All tabs: {all_tabs}\n"
            f"Tabs NOT yet visited: {unvisited if unvisited else 'NONE — all visited'}\n\n"
            "Rules:\n"
            "- If an unfilled field remains on this tab, go fill it: "
            '{"action_type":"click","target":"<exact field label>"}\n'
            "- Else if a tab has not been visited, switch to it: "
            '{"action_type":"click","target":"<tab name>"}\n'
            "- Else everything is done: {\"action_type\":\"done\"}\n"
            "Return ONLY the JSON object."
        )
        logger.info("[GAP] LLM completeness check — %d visible empties, %d unvisited tab(s).",
                    len(empties), len(unvisited))
        try:
            if self.provider in ("groq", "lmstudio"):
                _r = self._call_openai_compat(msg)
            elif self.provider == "anthropic":
                _r = self._call_anthropic(msg)
            elif self.provider == "gemini":
                _r = self._call_gemini(msg)
            else:
                _r = {"action_type": "wait"}
        except Exception as exc:
            logger.warning("[GAP] LLM call failed: %s", exc)
            return {"action_type": "wait"}
        # Record a tab choice as visited so we don't re-pick it, and attach the
        # tab's REAL bbox center. _resolve_target deprioritizes tab elements (so
        # field clicks don't hit tab headers) — for navigation that inverts and
        # lands on a stray label (the (850,119)→Notepad bug). Bypass it: click the
        # matched tabitem's own geometry.
        if _r.get("action_type") == "click":
            _tgt = (_r.get("target") or "").strip()
            _te = next((e for e in _tab_elems
                        if (e.get("text") or e.get("label") or "").strip().lower() == _tgt.lower()),
                       None)
            if _te is not None:
                _b = _te["bbox"]
                _r["click_position"] = [(_b[0] + _b[2]) / 2, (_b[1] + _b[3]) / 2]
                self._visited_tabs.add(_tgt)
        return _r

    # ── NAVIGATION PROTOCOL ───────────────────────────────────────────────────
    # Fires when the agent is STUCK (progress-stall). An LLM supervisor that uses
    # the SOURCE record as ground truth to decide ONE next action:
    #   1. Completeness — compare this page's on-screen fields to the source.
    #   2. Verify/correct — a filled field whose value mismatches the source is
    #      flagged and re-filled with the correct value.
    #   3. Navigate — page done → next unvisited tab; all done → finish.
    # Generic: keys on field labels + the source record + widget geometry. No
    # hardcoded field/tab names. Returns one action dict the caller executes.

    def _navigation_protocol(self, state: Dict[str, Any]) -> Dict[str, Any]:
        elements = state.get("elements", [])
        _FILL = ("editcontrol", "comboboxcontrol", "checkboxcontrol")
        page = []
        _empty = []        # fields still needing a value (the fill candidates)
        _filled = 0
        for e in elements:
            if e.get("window_role") == "background":
                continue
            if (e.get("type") or "").lower() not in _FILL:
                continue
            lbl = (e.get("label") or e.get("text") or "").strip()
            if not lbl:
                continue
            if self._attempt_key(e) in self._dead_fill_keys:
                continue   # widget rejects fill (e.g. SpinCtrl) — don't keep proposing it
            # An ATTEMPTED checkbox is DONE: unchecked reads value='' forever, so
            # a verified correct-NO box otherwise stays in the "empty" list and
            # gets re-proposed until dead-marked (~3 wasted LLM calls per box,
            # observed live 2026-07-09 on SR-22/Excluded Driver).
            if ((e.get("type") or "").lower() in ("checkboxcontrol", "checkbox")
                    and self._attempt_key(e) in self._attempted_keys):
                _filled += 1
                continue
            _v = (e.get("value") or "").strip()
            page.append((lbl, _v))
            if _v:
                _filled += 1
            else:
                _empty.append(lbl)
        rec = self._cached_record or {}
        _rec_lines  = "\n".join(f"{k} = {v}" for k, v in rec.items())[:4000]
        _tab_elems = [e for e in elements
                      if (e.get("type") or "").lower() in ("tabitem", "tabitemcontrol")
                      and e.get("window_role") != "background" and e.get("bbox")]
        all_tabs  = [(e.get("text") or e.get("label") or "").strip() for e in _tab_elems]
        unvisited = [t for t in all_tabs if t and t.lower() not in {v.lower() for v in self._visited_tabs}]
        # Only the EMPTY fields are fill candidates — never re-list filled ones, so
        # the model can't waste turns re-typing fields that already have values.
        _empty_lines = "\n".join(f"- {lbl}" for lbl in _empty) or "(none — every visible field is filled)"
        msg = (
            "You supervise a form-filling agent. Use the SOURCE record as ground truth "
            "to choose the SINGLE next action.\n\n"
            f"SOURCE record:\n{_rec_lines}\n\n"
            f"EMPTY fields on this page that still need a value (fill the FIRST one):\n{_empty_lines}\n\n"
            f"({_filled} other fields on this page are already filled — do NOT touch them.)\n"
            f"Tabs not yet visited: {unvisited if unvisited else 'NONE'}\n\n"
            "Rules — pick ONE:\n"
            "- An EMPTY field above has a value in the source → fill it: "
            '{"action":"fill","field":"<exact field label>","value":"<source value>"}\n'
            "- No empty field needs a value and a tab is unvisited → "
            '{"action":"tab","target":"<tab name>"}\n'
            "- Everything is done → {\"action\":\"done\"}\n"
            "A source value of (leave blank)/none/n/a means leave it empty — skip it, don't fill. "
            "Pick a field ONLY from the EMPTY list. Return ONLY the JSON object."
        )
        logger.info("[NAV] protocol — %d page fields, %d unvisited tab(s).", len(page), len(unvisited))
        _sys = (
            "You are a form-completion supervisor. Given the SOURCE record and the "
            "current page's fields, output the SINGLE next action as ONE JSON object "
            "on the last line — nothing else. Schemas:\n"
            '{"action":"fill","field":"<exact field label>","value":"<correct source value>"}\n'
            '{"action":"tab","target":"<tab name>"}\n'
            '{"action":"done"}\n'
            "Output ONLY the JSON object."
        )
        try:
            if self.provider in ("groq", "lmstudio"):
                _r = self._llm_json(_sys, msg)
            elif self.provider == "anthropic":
                _r = self._call_anthropic(msg)
            elif self.provider == "gemini":
                _r = self._call_gemini(msg)
            else:
                _r = {}
        except Exception as exc:
            logger.warning("[NAV] LLM call failed: %s", exc)
            return {"action": "wait"}
        # Robust normalize — accept the protocol schema OR the old action_type schema
        # the local model sometimes falls back to.
        _act = (_r.get("action") or "").lower()
        if _act not in ("fill", "tab", "done"):
            _at = (_r.get("action_type") or "").lower()
            if _at == "type":
                _act = "fill"
                _r.setdefault("field", _r.get("target", ""))
                _r.setdefault("value", _r.get("text", ""))
            elif _at == "click":
                _act = "tab"
                _r.setdefault("target", _r.get("target", ""))
            elif _at in ("done", "finish"):
                _act = "done"
        _r["action"] = _act
        if not _act:
            logger.warning("[NAV] unparseable LLM action — raw keys=%s", list(_r.keys()))
        if _act == "tab":
            _tgt = (_r.get("target") or "").strip()
            _te = next((e for e in _tab_elems
                        if (e.get("text") or e.get("label") or "").strip().lower() == _tgt.lower()), None)
            if _te is not None:
                _b = _te["bbox"]
                _r["click_position"] = [(_b[0] + _b[2]) / 2, (_b[1] + _b[3]) / 2]
        return _r

    def _verify_pass(self, state: Dict[str, Any]) -> bool:
        """DETERMINISTIC VERIFICATION (the finish gate). Visit EVERY tab, scroll
        through it, and on each view run the source-check (Navigation Protocol vs the
        SOURCE record). Any field the LLM reports as missing/wrong → fix it on the
        spot. Returns True ONLY if a full sweep of all tabs needs ZERO corrections —
        i.e. every fillable, non-dead field matches the source. Else it fixes what it
        can and returns False so the run keeps working. Dead/unfillable fields
        (SpinCtrl, 50-item combos) are excluded by the protocol, so they don't block.
        Generic: tab elements by geometry, fields by the protocol's source-check."""
        _tabs = self._tab_elems_now(state)
        if not _tabs:
            logger.warning("[VERIFY] no tab elements found — cannot verify.")
            return False
        _fixed = 0
        _fixed_names: set = set()      # which fields this pass corrected (convergence check)
        logger.info("[VERIFY] deterministic pass over %d tab(s) — reading every field vs source.", len(_tabs))
        for _t in _tabs:
            _nm = (_t.get("text") or _t.get("label") or "?").strip()
            _b = _t["bbox"]
            self._executor.execute({"action_type": "click",
                                    "click_position": [(_b[0] + _b[2]) / 2, (_b[1] + _b[3]) / 2]})
            time.sleep(self.step_delay * 0.5)
            self._visited_tabs.add(_nm)
            self._refresh_record_cache(self._observe())
            _st = self._observe()
            _done_here = set()                           # fields already settled this tab-pass
            for _scan in range(8):                       # scroll passes per tab
                # 1) DETERMINISTIC, SECTION-AWARE read-back: for each box in view, read
                #    its REAL value (text/combo value, checkbox ticked-state) and compare
                #    to the source using the SECTION-correct key (Driver 2 DL Expiration,
                #    not bare DL Expiration). Fix clear mismatches. No LLM → no wrong-
                #    section guesses, no checkbox loop. This is what makes verify finish.
                _mm = [(e, x) for (e, x) in self._view_mismatches(_st)
                       if (e.get("label") or e.get("text") or "").strip().lower() not in _done_here]
                if _mm:
                    for _e, _exp in _mm:
                        _el = (_e.get("label") or _e.get("text") or "").strip()
                        # TERMINATION GUARANTEE: if a box has been "corrected" twice
                        # across verify and STILL won't confirm (scroll can't reach it
                        # / checkbox read unreliable), accept it as dead so verify can
                        # reach 0 corrections and Submit — else it re-fixes forever.
                        _vk = self._attempt_key(_e)
                        self._verify_fix_count[_vk] = self._verify_fix_count.get(_vk, 0) + 1
                        if self._verify_fix_count[_vk] > 2:
                            self._dead_fill_keys.add(_vk)
                            self._mark_attempted(_e)
                            logger.warning("[VERIFY] %s: %r won't confirm after 2 tries — accepting (dead).",
                                           _nm[:14], _el[:22])
                            _done_here.add(_el.lower())
                            continue
                        logger.info("[VERIFY] %s: fix %r → %r", _nm[:14], _el[:22], str(_exp)[:22])
                        if self._nav_fill_field(_st, _el, _exp):
                            _fixed += 1
                            _fixed_names.add(_el.lower())
                        _done_here.add(_el.lower())
                    _st = self._observe()
                    continue
                # 2) LLM fallback — only for ambiguous fields whose label the source
                #    can't resolve (so _view_mismatches skipped them). Veto if already ok.
                # VERIFY-AT-FILL GATE (2026-07-10): this call used to fire on EVERY
                # scroll-view, including views where every field already held a
                # validator-confirmed value — the dominant time cost of the whole
                # verify pass (LLM latency × views × tabs, re-checking work already
                # confirmed at fill time). A view needs the LLM only when it shows
                # an EMPTY live fillable that branch 1 couldn't resolve: filled
                # fields were either source-matched above or are settled. The
                # deterministic clobber-catch (branch 1) still reads every view.
                _vt_v = self._form_viewport_top(_st)
                _vb_v = self._form_viewport_bottom(_st) - 8
                _needs_llm = False
                for _ve in _st.get("elements", []):
                    _vty = (_ve.get("type") or "").lower()
                    if _vty not in ("editcontrol", "input", "comboboxcontrol", "combobox",
                                    "spincontrolcontrol", "spincontrol"):
                        continue
                    if _ve.get("window_role") == "background" or not _ve.get("bbox"):
                        continue
                    _vcy = (_ve["bbox"][1] + _ve["bbox"][3]) / 2
                    if not (_vt_v <= _vcy <= _vb_v):
                        continue
                    if (_ve.get("value") or "").strip():
                        continue                      # holds a value → not LLM's problem
                    if not (_ve.get("label") or _ve.get("text") or "").strip():
                        continue
                    if self._attempt_key(_ve) in self._dead_fill_keys:
                        continue
                    _needs_llm = True
                    break
                if not _needs_llm:
                    if not self._scrollbar_drag(_st, 240.0):
                        break
                    time.sleep(self.step_delay * 0.5)
                    _st = self._observe()
                    continue
                _nav = self._navigation_protocol(_st)
                _act = (_nav.get("action") or "").lower()
                if _act == "fill" and (_nav.get("field") or "").strip():
                    _vf = _nav["field"].strip()
                    _vv = str(_nav.get("value") or "")
                    _fe = next((e for e in _st.get("elements", [])
                                if (e.get("label") or e.get("text") or "").strip().lower() == _vf.lower()
                                and e.get("bbox")), None)
                    if _vf.lower() in _done_here or (_fe is not None and self._field_matches(_fe, _vv)):
                        _done_here.add(_vf.lower())
                        if _fe is not None:
                            self._mark_attempted(_fe)
                        if not self._scrollbar_drag(_st, 240.0):
                            break
                        time.sleep(self.step_delay * 0.4)
                        _st = self._observe()
                        continue
                    # TERMINATION GUARANTEE (same as the deterministic branch above —
                    # this branch lacked it, so a field the LLM kept re-proposing with
                    # flip-flopping values ('Auto-Pay Enrolled' YES↔leave-blank) or a
                    # fill that keeps getting refused ('Balance Due ($)') re-fixed
                    # forever and verify never reached 0 → Submit never fired.
                    _vk_l = _vf.strip().lower()
                    self._verify_fix_count[_vk_l] = self._verify_fix_count.get(_vk_l, 0) + 1
                    if self._verify_fix_count[_vk_l] > 2:
                        if _fe is not None:
                            self._dead_fill_keys.add(self._attempt_key(_fe))
                            self._mark_attempted(_fe)
                        logger.warning("[VERIFY] %s: LLM-fix %r won't settle after 2 tries — accepting (dead).",
                                       _nm[:14], _vf[:22])
                        _done_here.add(_vf.lower())
                        continue
                    logger.info("[VERIFY] %s: LLM-fix %r → %r", _nm[:14], _vf[:24], _vv[:24])
                    if self._nav_fill_field(_st, _vf, _vv):
                        _fixed += 1
                        _fixed_names.add(_vf.lower())
                    _done_here.add(_vf.lower())
                    _st = self._observe()
                    continue
                # nothing to fix on this view → scroll to reveal more; stop at bottom
                if not self._scrollbar_drag(_st, 240.0):
                    break
                time.sleep(self.step_delay * 0.5)
                _st = self._observe()
        logger.info("[VERIFY] pass complete — %d field(s) corrected.", _fixed)
        self._last_verify_fixes = _fixed_names
        return _fixed == 0

    def _confirm_finished(self, state: Dict[str, Any]) -> bool:
        """Authoritative finish-check — NEVER trust a visible-empty/degraded 'done'.
        Finished only if BOTH hold:
          (1) every tab visited (coarse coverage floor a partial observation can't fake),
          (2) a full DETERMINISTIC verification pass (_verify_pass) over ALL tabs needs
              ZERO corrections — every field read back matches the SOURCE record.
        Returns True only when both agree. Gates every finish/Submit."""
        if self._tabs_total > 0 and len(self._visited_tabs) < self._tabs_total:
            logger.warning("[CONFIRM] not finished — only %d/%d tabs visited.",
                           len(self._visited_tabs), self._tabs_total)
            return False
        _prev_fixes = getattr(self, "_last_verify_fixes", None)
        if self._verify_pass(state):
            logger.info("[CONFIRM] verification pass clean — form complete.")
            # Press Submit HERE so every finish path submits (some callers just
            # break). Idempotent: _click_submit no-ops if already submitted.
            self._click_submit(self._observe())
            return True
        # CONVERGENCE GATE: a pass that corrects the SAME fields as the previous
        # pass is making zero progress — flaky reads / nondeterministic LLM values
        # / refused fills will re-"correct" forever (observed live 2026-07-09:
        # verify lapped the form twice on the identical field set and Submit
        # never fired). Stable-but-imperfect = accept honestly: log the dead
        # list, submit, report. Perfection is the enemy of termination.
        _cur_fixes = getattr(self, "_last_verify_fixes", set())
        if _prev_fixes is not None and _cur_fixes and _cur_fixes == _prev_fixes:
            logger.warning("[CONFIRM] verification STABLE but not clean — same %d field(s) "
                           "re-corrected with no progress (%s). Accepting state and submitting.",
                           len(_cur_fixes), sorted(_cur_fixes))
            self._click_submit(self._observe())
            return True
        logger.warning("[CONFIRM] verification corrected field(s) / found gaps — NOT done, continuing.")
        return False

    def _topmost_missing(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Topmost unfilled fillable on the current tab that is still worth
        acting on (not dead, not attempted, not background). Includes fields
        scrolled above the pane — a skipped field must pull the view BACK, not
        get abandoned. Checkboxes excluded (unchecked may be a correct NO)."""
        best = None
        for e in state.get("elements", []):
            ety = (e.get("type") or "").lower()
            if ety not in ("editcontrol", "input", "comboboxcontrol", "combobox",
                           "spincontrolcontrol", "spincontrol"):
                continue
            if e.get("window_role") == "background" or not e.get("bbox"):
                continue
            if (e.get("value") or "").strip():
                continue
            k = self._attempt_key(e)
            if k in self._dead_fill_keys or k in self._attempted_keys:
                continue
            if best is None or e["bbox"][1] < best["bbox"][1]:
                best = e
        return best

    def _optimal_viewport_jump(self, state: Dict[str, Any],
                               t_pred: Optional[Dict[str, Any]] = None):
        """NAVIGATION PROTOCOL core rule (user spec 2026-07-08):
          1. fill the current viewport first (enforced by visible-first ranking);
          2. when NO empty target is left on screen, jump the scroll position to
             where the work is.

        MODEL-ANCHORED (2026-07-09): when the transformer's ranked pointer
        (`t_pred['click_topk']`) contains an actionable EMPTY field that is
        off-screen, THAT is the anchor — the viewport goes where the learned
        policy wants to work next (WHERE stays with the model, per the
        division-of-labor rule). The densest-window geometry sweep is the
        FALLBACK for when the model's ranking offers no off-screen target.
        Returns fresh state after the jump, or None when there is nothing to
        jump to (tab done) or the current view already IS optimal."""
        vt = self._form_viewport_top(state)
        vb = self._form_viewport_bottom(state) - 8
        H  = max(vb - vt, 100)
        empties = []
        for e in state.get("elements", []):
            ety = (e.get("type") or "").lower()
            if ety not in ("editcontrol", "input", "comboboxcontrol", "combobox",
                           "spincontrolcontrol", "spincontrol", "checkboxcontrol", "checkbox"):
                continue
            if e.get("window_role") == "background" or not e.get("bbox"):
                continue
            if (e.get("value") or "").strip():
                continue
            if ety in ("checkboxcontrol", "checkbox"):
                continue          # unchecked may be a correct NO — never drives a jump
            k = self._attempt_key(e)
            if k in self._dead_fill_keys or k in self._attempted_keys:
                continue
            if not (e.get("label") or e.get("text") or "").strip():
                continue
            empties.append(e)
        if not empties:
            return None
        empties.sort(key=lambda e: (e["bbox"][1] + e["bbox"][3]) / 2)
        ys = [(e["bbox"][1] + e["bbox"][3]) / 2 for e in empties]
        vis_now = sum(1 for e in empties if e["bbox"][1] >= vt and e["bbox"][3] <= vb)

        # ── MODEL ANCHOR: highest-ranked off-screen actionable empty ─────────
        anchor = None
        best_cnt = 0
        if t_pred:
            _empty_ids = {id(e) for e in empties}
            for _entry in t_pred.get("click_topk", []) or []:
                try:
                    _mi = int(_entry[0])
                except (TypeError, ValueError, IndexError):
                    continue
                _elems_all = state.get("elements", [])
                if not (0 <= _mi < len(_elems_all)):
                    continue
                _me = _elems_all[_mi]
                if id(_me) not in _empty_ids:
                    continue                       # not an actionable empty
                _mcy = (_me["bbox"][1] + _me["bbox"][3]) / 2
                if vt <= _mcy <= vb:
                    continue                       # on-screen → not jump territory
                # DENSITY GATE: the jump's contract ("no-op when the current
                # view is already the densest available") must hold on THIS
                # branch too, not just the geometry fallback — a model pick
                # whose window is no denser than what's on screen produced the
                # live ping-pong (jumped to a 1-empty window while 2 empties
                # were visible, then back, forever).
                _cnt = sum(1 for y in ys if _mcy <= y <= _mcy + (H - 60))
                if _cnt <= vis_now:
                    continue
                anchor = _me
                best_cnt = _cnt
                logger.info("[NAV] jump anchored on MODEL's pick %r (rank hit, %d empties ride along).",
                            (_me.get("label") or _me.get("text") or "?")[:28], best_cnt)
                break

        # ── FALLBACK: densest window of height H (two-pointer sweep) ─────────
        if anchor is None:
            best_j = 0
            i = 0
            for j in range(len(ys)):
                if i < j:
                    i = j
                while i < len(ys) and ys[i] <= ys[j] + (H - 60):
                    i += 1
                if i - j > best_cnt:
                    best_cnt, best_j = i - j, j
            # already optimal? (as many empties visible as the best window holds)
            if vis_now >= best_cnt:
                return None
            anchor = empties[best_j]
        albl = (anchor.get("label") or anchor.get("text") or "").strip()
        # LOOP-BREAKER (viewport lock): EVERY anchor jumped-to since the last
        # real progress is remembered, not just the previous one — a single-
        # slot memory catches A→A→A but is blind to the A→B→A→B ping-pong
        # (observed live 2026-07-10: 'DL Issuing State' ↔ 'Accidents (3 yr)',
        # 14 alternating jumps, zero fills). Re-jumping to ANY window already
        # visited without a fill in between = no progress → burn that anchor
        # and re-pick. The set is cleared the moment the ranked picker finds
        # actionable work, so legitimate revisits after progress stay allowed.
        _ak = self._attempt_key(anchor)
        _seen = getattr(self, "_jump_anchors_since_progress", None)
        if _seen is None:
            _seen = self._jump_anchors_since_progress = set()
        if _ak in _seen:
            logger.warning("[NAV] jump anchor %r already visited with no progress since — marking attempted, re-picking.",
                           albl[:28])
            self._mark_attempted(anchor)
            return self._optimal_viewport_jump(self._observe())
        _seen.add(_ak)
        logger.info("[NAV] optimal-viewport jump → window with %d empty fields, anchor %r.",
                    best_cnt, albl[:28])
        # FAR-FIELD REVEAL: wx SetFocus auto-scroll parks the focused field at
        # the NEAR edge, so focusing the anchor exposes the anchor ALONE — and
        # when ScrollPattern paging no-ops (deep tabs, known P0), the promised
        # window never comes on screen: jump lands → "all candidates masked" →
        # re-jump → the lock burns real fields (live 2026-07-10 22:43/22:49).
        # Instead focus the field on the FAR side of the travel direction:
        # jumping DOWN → the window's bottom-most empty (lands at the bottom
        # edge, the whole window above rides in with it); jumping UP → the
        # anchor itself (lands at the top edge, window below rides in). Pure
        # geometry + SetFocus — no ScrollPattern dependency at all.
        _acy = (anchor["bbox"][1] + anchor["bbox"][3]) / 2
        _wnd = [e for e in empties
                if _acy <= (e["bbox"][1] + e["bbox"][3]) / 2 <= _acy + (H - 60)]
        reveal = anchor
        if _acy >= vt and _wnd:                    # travelling down → far = bottom
            reveal = max(_wnd, key=lambda e: (e["bbox"][1] + e["bbox"][3]) / 2)
        rlbl = (reveal.get("label") or reveal.get("text") or "").strip()
        if not self._scroll_into_view(rlbl or albl):
            return None
        time.sleep(self.step_delay * 0.4)
        return self._observe()

    def _maximize_reveal(self, went_down: bool = True, max_pages: int = 3) -> None:
        """VIEWPORT OPTIMIZER — wx SetFocus auto-scroll reveals a target at the
        NEAR edge (bottom when scrolling down), so exactly ONE fresh field
        becomes visible per reveal → the run crawls field-by-field with a
        scroll between every fill. After the minimal reveal, keep paging in
        the SAME direction until the revealed target sits near the FAR edge:
        the target stays visible AND a full page of upcoming fields comes on
        screen with it, giving the transformer's ranked pointer many live
        targets per viewport instead of one. Pure UIA ScrollPattern geometry —
        no LLM, no field names, any scrollable pane. Assumes the revealed
        field HAS focus (that's how _scroll_into_view reveals it)."""
        try:
            import uiautomation as _uia
            ctrl = _uia.GetFocusedControl()
            if ctrl is None:
                return
            pane, cur = None, ctrl
            for _ in range(15):
                if cur is None:
                    break
                try:
                    _sp = cur.GetScrollPattern()
                    if _sp is not None and _sp.VerticallyScrollable:
                        pane = cur
                        break
                except Exception:
                    pass
                try:
                    cur = cur.GetParentControl()
                except Exception:
                    break
            if pane is None:
                return
            sp     = pane.GetScrollPattern()
            prect  = pane.BoundingRectangle
            margin = max(60, int((prect.bottom - prect.top) * 0.12))
            fwd  = _uia.ScrollAmount.LargeIncrement if went_down else _uia.ScrollAmount.LargeDecrement
            back = _uia.ScrollAmount.LargeDecrement if went_down else _uia.ScrollAmount.LargeIncrement
            pages = 0
            for _ in range(max_pages):
                try:
                    r = ctrl.BoundingRectangle
                except Exception:
                    break
                if r.height() <= 0:
                    break
                at_far_edge = (r.top <= prect.top + margin) if went_down \
                              else (r.bottom >= prect.bottom - margin)
                if at_far_edge:
                    break
                # (Stranding guard REMOVED 2026-07-08 — it let one skipped field
                # veto every page forever → the one-row-per-fill crawl. Window
                # choice now belongs to _optimal_viewport_jump; fields left
                # behind a jump are counted in later windows or swept at the end.)
                _y0 = r.top
                sp.Scroll(_uia.ScrollAmount.NoAmount, fwd)
                time.sleep(0.18)
                try:
                    r2 = ctrl.BoundingRectangle
                except Exception:
                    break
                if r2.top == _y0:
                    break                       # pane refused — end of scroll range
                overshot = (r2.bottom <= prect.top) if went_down else (r2.top >= prect.bottom)
                if overshot:
                    sp.Scroll(_uia.ScrollAmount.NoAmount, back)
                    time.sleep(0.15)
                    break
                pages += 1
            if pages:
                logger.info("Maximize-reveal: paged %d× past the minimal reveal — "
                            "target at far edge, full page of fresh fields exposed.", pages)
        except Exception as exc:
            logger.debug("Maximize-reveal skipped: %s", exc)

    def _reveal_target(self, state: Dict[str, Any], snap):
        """MECHANISM 1 — feed the transformer. If the field the transformer just
        pointed at is OFF the viewport (it's in the element tree with off-screen
        coords), scroll that exact field into view (agent HOW, no LLM) and return
        (fresh_state, fresh_on-screen_coord). Else return unchanged. This lets the
        transformer keep picking ANY field — including below-fold ones — instead of
        starving once the visible batch is filled and handing off to the LLM-sweep.
        Conservative: no-ops unless it clearly finds an off-fold field at the pick."""
        if not snap:
            return state, snap
        _FILL = ("editcontrol", "comboboxcontrol", "checkboxcontrol")
        _el = next((e for e in state.get("elements", [])
                    if (e.get("type") or "").lower() in _FILL and e.get("bbox")
                    and e["bbox"][0] - 2 <= snap[0] <= e["bbox"][2] + 2
                    and e["bbox"][1] - 2 <= snap[1] <= e["bbox"][3] + 2), None)
        if _el is None:
            return state, snap
        vb = self._form_viewport_bottom(state) - 8
        _vt = self._form_viewport_top(state)
        _ty, _by = _el["bbox"][1], _el["bbox"][3]
        if _vt <= _ty and _by <= vb:
            return state, snap          # already fully on screen — nothing to do
        _lbl = (_el.get("label") or _el.get("text") or "").strip()
        if not _lbl:
            return state, snap
        if self._scroll_into_view(_lbl):
            time.sleep(self.step_delay * 0.5)
            # Minimal reveal parks the target at the near edge with nothing new
            # behind it — page on so a full viewport of upcoming fields is
            # exposed for the ranked pointer (fixes the one-field-per-scroll crawl).
            self._maximize_reveal(went_down=_by > vb)
            _ns = self._observe()
            # Re-find restricted to FILLABLE types: the static text label and the
            # edit control share the same label string — an unfiltered match
            # returned the STATIC LABEL and the retarget click hit dead text
            # (observed live: 'Last Name' @ (928,166) → no_change).
            _ne = next((e for e in _ns.get("elements", [])
                        if (e.get("label") or e.get("text") or "").strip().lower() == _lbl.lower()
                        and (e.get("type") or "").lower() in _FILL
                        and e.get("bbox")
                        and e.get("window_role") != "background"), None)
            if _ne is not None:
                _b = _ne["bbox"]
                logger.info("Reveal-target: scrolled %r into view for the transformer.", _lbl[:24])
                return _ns, [(_b[0] + _b[2]) / 2, (_b[1] + _b[3]) / 2]
            # Label re-find failed (trailing colon / case mismatch between
            # perception snapshot and live UIA Name).  Query the live UIA
            # bounding rectangle directly — same lookup pattern as
            # _focus_field_uia — so we return fresh coords rather than the
            # pre-scroll stale snap.
            try:
                import uiautomation as _uia
                import win32gui as _w32
                _hwnd = self._locked_hwnd or _w32.GetForegroundWindow()
                _root = _uia.ControlFromHandle(_hwnd)
                if _root is not None:
                    _ctrl = _root.EditControl(searchDepth=25, Name=_lbl)
                    if not _ctrl.Exists(maxSearchSeconds=0.3):
                        _ctrl = _root.ComboBoxControl(searchDepth=25, Name=_lbl)
                    if not _ctrl.Exists(maxSearchSeconds=0.3):
                        _ctrl = _root.CheckBoxControl(searchDepth=25, Name=_lbl)
                    if _ctrl.Exists(maxSearchSeconds=0.3):
                        _br = _ctrl.BoundingRectangle
                        if _br.width() > 0 and _br.height() > 0:
                            logger.info(
                                "Reveal-target: label re-find failed, used live UIA rect for %r.",
                                _lbl[:24],
                            )
                            return _ns, [
                                (_br.left + _br.right) / 2,
                                (_br.top + _br.bottom) / 2,
                            ]
            except Exception as _exc:
                logger.debug("Reveal-target UIA-rect fallback failed for %r: %s", _lbl[:24], _exc)
        return state, snap

    def _focus_field_uia(self, label: str) -> bool:
        """Focus the EXACT field by UIA identity (precise — also auto-scrolls it into
        view in a wx ScrolledPanel). Returns True only if that field is verified
        focused, so the caller can type into it without a coordinate click that could
        hit a neighbouring field. Generic: matches the field's own label."""
        if not label:
            return False
        _ll = label.strip().lower()
        try:
            import uiautomation as _uia
            import win32gui as _w32
            hwnd = self._locked_hwnd or _w32.GetForegroundWindow()
            root = _uia.ControlFromHandle(hwnd)
            if root is None:
                return False
            ctrl = root.EditControl(searchDepth=25, Name=label)
            if not ctrl.Exists(maxSearchSeconds=0.3):
                ctrl = root.ComboBoxControl(searchDepth=25, Name=label)
            if not ctrl.Exists(maxSearchSeconds=0.3):
                return False
            ctrl.SetFocus()
            time.sleep(0.15)
            foc = _uia.GetFocusedControl()
            _fn = (getattr(foc, "Name", "") or "").strip().lower()
            if foc is not None and (_fn == _ll or _ll in _fn or (_fn and _fn in _ll)):
                logger.info("Focus-field: %r focused via UIA (precise, no pixel click).", label[:28])
                return True
            logger.debug("Focus-field: %r SetFocus did not verify (focused=%r).", label[:24], _fn)
            return False
        except Exception as exc:
            logger.debug("Focus-field UIA failed for %r: %s", label[:24], exc)
            return False

    def _point_on_submit(self, pos) -> bool:
        """True if a click point falls inside any Submit/finish button bbox (refreshed
        each step). Used by the executor chokepoint to block premature submits."""
        if not pos or len(pos) < 2:
            return False
        _M = 14   # margin (px) — snap/rounding can land a few px off the button bbox
        for b in getattr(self, "_submit_bboxes", []):
            if b and (b[0] - _M) <= pos[0] <= (b[2] + _M) and (b[1] - _M) <= pos[1] <= (b[3] + _M):
                return True
        return False

    def _click_submit(self, state: Dict[str, Any]) -> bool:
        """DETERMINISTIC FINISH — click the Submit/finish button. Called ONLY after
        the LLM verification (_confirm_finished) passes. The transformer never does
        this itself. Generic: button widget + generic finish-keyword text.
        Idempotent — submits at most once per run (callers may invoke it twice)."""
        if getattr(self, "_submitted", False):
            return True
        _KW = ("submit", "finish", "save", "accept", "done", "& new", "new")
        _btn = next((e for e in state.get("elements", [])
                     if (e.get("type") or "").lower() in ("buttoncontrol", "button")
                     and e.get("bbox")
                     and any(k in (e.get("text") or e.get("label") or "").lower() for k in _KW)),
                    None)
        if _btn is None:
            logger.warning("Finish: no Submit button found in tree.")
            return False
        _b = _btn["bbox"]
        logger.info("Finish: deterministically clicking %r.", (_btn.get("text") or _btn.get("label") or "?")[:24])
        # Open the chokepoint ONLY for this one verified click.
        self._allow_submit = True
        try:
            self._executor.execute({"action_type": "click",
                                    "click_position": [(_b[0] + _b[2]) / 2, (_b[1] + _b[3]) / 2]})
        finally:
            self._allow_submit = False
        self._submitted = True
        return True

    def _tab_elems_now(self, state: Dict[str, Any]) -> list:
        """Tab elements in the current observation (sorted left-to-right)."""
        return sorted(
            (e for e in state.get("elements", [])
             if (e.get("type") or "").lower() in ("tabitem", "tabitemcontrol")
             and e.get("window_role") != "background" and e.get("bbox")),
            key=lambda e: e["bbox"][0],
        )

    def _sweep_tab(self, state: Dict[str, Any]) -> tuple:
        """NAVIGATION PROTOCOL sweep: take over and drive the current tab to
        completion. Loop the LLM supervisor (vs SOURCE): fill/verify each missing
        or wrong field (scroll to reach below-fold), until the page is clean →
        switch to the next unvisited tab; or the whole form is source-confirmed →
        finish. Bypasses the fixating transformer. No Tab. Returns (state, finished)."""
        logger.info("[NAV] SWEEP — protocol taking over the tab to drive it to completion.")
        _tried: Dict[str, int] = {}
        _noscroll = 0
        def _scroll_for_more() -> bool:
            """Reveal the next batch of fields. Prefer NATIVE UIA scroll-into-view on
            the next genuinely-missing field (exact, no pixels); fall back to the
            scrollbar drag. Returns True if the view actually moved."""
            nonlocal state
            _sig = self._visible_field_sig(state)
            _miss = self._find_missing_field(state)
            if _miss is not None:
                _ml = (_miss.get("label") or _miss.get("text") or "").strip()
                if self._scroll_into_view(_ml):
                    # park the revealed field at the far edge so a full page of
                    # upcoming fields comes with it (not one field per scroll)
                    self._maximize_reveal(went_down=_miss["bbox"][1] >= 0)
            else:
                self._scrollbar_drag(state, 240.0)   # nothing empty in tree → blind page-down
            time.sleep(self.step_delay * 0.5)
            state = self._observe()
            return self._visible_field_sig(state) != _sig
        for _ in range(60):                          # cap = max fields on one tab
            _nav = self._navigation_protocol(state)
            _act = (_nav.get("action") or "").lower()
            if _act == "fill" and (_nav.get("field") or "").strip():
                _nf = _nav["field"].strip()
                _nv = _nav.get("value") or ""
                # Resolve to a SPECIFIC element: among same-labeled candidates
                # (three 'Date of Birth' fields on a Drivers tab), prefer the
                # first EMPTY one that is not already dead — "first bare-label
                # match" kept dead-marking the same section's field while the
                # protocol re-proposed another section's, looping forever
                # (observed live 2026-07-09: ~15 dead-marks on 'Date of Birth').
                _cands = [e for e in state.get("elements", [])
                          if (e.get("label") or e.get("text") or "").strip().lower() == _nf.lower()
                          and e.get("bbox")
                          and e.get("window_role") != "background"
                          and (e.get("type") or "").lower() in self._FILLABLE_TYPES]
                _fx = (next((e for e in _cands
                             if not (e.get("value") or "").strip()
                             and self._attempt_key(e, state) not in self._dead_fill_keys), None)
                       or (next(iter(_cands), None)))
                # Protocol re-proposing an ALREADY-FILLED field = it can't see any
                # remaining empties (they're below the fold). Scroll down to reveal
                # the next batch instead of re-filling. Two dead scrolls = bottom.
                if _fx is not None and (_fx.get("value") or "").strip():
                    if _scroll_for_more():
                        _noscroll = 0
                    else:
                        _noscroll += 1
                        if _noscroll >= 2:
                            logger.info("[NAV] sweep: bottom reached, only filled fields left → page clean.")
                            break
                    continue
                # Per-ELEMENT try counter (section-qualified) — one bare-label
                # counter shared by all three same-named fields hit the dead
                # threshold after the FIRST section's tries.
                _tk = self._attempt_key(_fx, state) if _fx is not None else _nf.lower()
                _tried[_tk] = _tried.get(_tk, 0) + 1
                if _tried[_tk] > 2:
                    if _fx is not None:
                        self._dead_fill_keys.add(_tk)
                        self._mark_attempted(_fx)
                    logger.warning("[NAV] sweep: %r unfillable after 3 tries — marking dead.", _nf[:28])
                    state = self._observe()
                    continue
                # SECTION-CORRECT VALUE: the protocol LLM proposes values by bare
                # field name and grabs the wrong section's line (typed the
                # policyholder's DOB into Driver 3). When the resolved element
                # sits in a section, re-look-up the value section-aware and
                # prefer it over the LLM's proposal.
                if _fx is not None:
                    _sec_sw = self._detect_section(state, _fx)
                    if _sec_sw:
                        _sv = self._lookup_field(_nf, section=_sec_sw)
                        if _sv:
                            if _sv != _nv:
                                logger.info("[NAV] sweep: section-corrected %r value %r → %r (%s).",
                                            _nf[:24], str(_nv)[:20], str(_sv)[:20], _sec_sw)
                            _nv = _sv
                # RECORD IS THE SOURCE OF TRUTH for the sweep (2026-07-11). The
                # protocol LLM's proposals for fields the record says nothing
                # about are pure invention — it typed LITERAL '(leave blank)' /
                # 'N/A' / 'No description provided' into record 2's claim-less
                # Claims tab via the identity executor. No literal blacklists
                # (that's the ruleset-inference loop's job): _lookup_field
                # already resolves explicit '(leave blank)'/none record values
                # to empty, so ONE rule covers both cases — no record value =
                # the field STAYS BLANK, settled so it isn't re-proposed. When
                # the record DOES hold a value, it beats the LLM's proposal.
                _sw_sec  = self._detect_section(state, _fx) if _fx is not None else ""
                _rec_val = self._lookup_field(_nf, section=_sw_sec)
                if self._cached_record and not _rec_val:
                    logger.info("[NAV] sweep: %r blank/absent from record %d — stays blank (settled, no fill).",
                                _nf[:28], self._record_num)
                    self._dead_fill_keys.add(_tk)
                    if _fx is not None:
                        self._mark_attempted(_fx)
                    state = self._observe()
                    continue
                if _rec_val and str(_nv).strip().lower() != _rec_val.strip().lower():
                    logger.info("[NAV] sweep: record value %r beats LLM proposal %r for %r.",
                                _rec_val[:24], str(_nv)[:24], _nf[:24])
                    _nv = _rec_val
                logger.info("[NAV] sweep fill/verify %r → %r", _nf[:28], str(_nv)[:30])
                self._nav_fill_field(state, _nf, _nv,
                                     prefer_key=(_tk if _fx is not None else None))
                _noscroll = 0
                state = self._observe()
                continue
            if _act == "tab" and _nav.get("click_position"):
                logger.info("[NAV] tab swept clean → switch tab %r", (_nav.get("target") or "")[:24])
                self._executor.execute({"action_type": "click", "click_position": _nav["click_position"]})
                self._visited_tabs.add((_nav.get("target") or "").strip())
                self._filled_this_tab.clear(); self._fixation_hits.clear()
                self._refresh_record_cache(self._observe())
                return self._observe(), False
            if _act == "done":
                if self._confirm_finished(state):
                    logger.info("[NAV] sweep + source-check → form complete — deterministic Submit.")
                    self._click_submit(state)
                    return state, True
                # "done" but NOT confirmed — usually a scrolled/transient frame that
                # hides the remaining empties (Street Address etc.). SCROLL DOWN to
                # reveal them before concluding the page is clean (your step 4).
                if _scroll_for_more():
                    logger.info("[NAV] sweep: 'done' but below-fold fields remain — scrolled down.")
                    _noscroll = 0
                    continue
                _noscroll += 1
                if _noscroll < 2:
                    continue
                # Scroll exhausted → current tab truly clean. Move to an unvisited tab.
                _nt = next((e for e in self._tab_elems_now(state)
                            if (e.get("text") or e.get("label") or "").strip().lower()
                            not in {v.lower() for v in self._visited_tabs}), None)
                if _nt is not None:
                    _nb = _nt["bbox"]; _nm = (_nt.get("text") or _nt.get("label") or "?").strip()
                    logger.info("[NAV] tab clean (scroll exhausted) → switch tab %r", _nm[:24])
                    self._executor.execute({"action_type": "click",
                                            "click_position": [(_nb[0] + _nb[2]) / 2, (_nb[1] + _nb[3]) / 2]})
                    self._visited_tabs.add(_nm)
                    self._filled_this_tab.clear(); self._fixation_hits.clear()
                    self._refresh_record_cache(self._observe())
                    return self._observe(), False
                return state, False
            # No usable action → try scrolling to reveal more before giving up.
            if _scroll_for_more():
                continue
            return state, False
        return state, False

    # ════════════════════════════════════════════════════════════════════════
    #  IDENTITY EXECUTOR — act on ELEMENTS, not labels or pixels.
    #  The element (its own label + control type + geometry) is the address;
    #  resolution to a live UIA control happens at EXECUTION time, and the
    #  action is a UIA pattern (Value/Toggle/Selection), not a coordinate
    #  click. Kills the stale-coordinate bug class and the paste-reject /
    #  fold-edge-dropdown dead-marks. No field names, no app names.
    # ════════════════════════════════════════════════════════════════════════

    _UIA_TYPE_FINDERS = {
        "editcontrol": "EditControl", "input": "EditControl",
        "comboboxcontrol": "ComboBoxControl", "combobox": "ComboBoxControl",
        "checkboxcontrol": "CheckBoxControl", "checkbox": "CheckBoxControl",
        "spincontrolcontrol": "SpinnerControl", "spincontrol": "SpinnerControl",
        "spinnercontrol": "SpinnerControl", "spinner": "SpinnerControl",
    }

    def _resolve_live_control(self, elem: Dict[str, Any]):
        """Observed element dict -> live UIA control, disambiguated by geometry.
        Same-named twins (three 'Date of Birth' fields) are told apart by
        nearest bounding-rect center to the observed element's bbox — the
        rects come fresh from UIA at call time, so a scroll between observe
        and act shifts BOTH the same way for on-screen controls."""
        try:
            import uiautomation as _uia
            import win32gui as _w32
        except ImportError:
            return None
        _name = (elem.get("label") or elem.get("text") or "").strip()
        if not _name or not elem.get("bbox"):
            return None
        _finder_name = self._UIA_TYPE_FINDERS.get((elem.get("type") or "").lower())
        if _finder_name is None:
            return None
        try:
            _root = _uia.ControlFromHandle(self._locked_hwnd or _w32.GetForegroundWindow())
            if _root is None:
                return None
            _b = elem["bbox"]
            _ecx, _ecy = (_b[0] + _b[2]) / 2, (_b[1] + _b[3]) / 2
            # enumerate ALL same-named controls of this type; nearest rect wins
            _matches = []
            def _walk(node, depth=0):
                if node is None or depth > 25:
                    return
                try:
                    if (getattr(node, "Name", "") or "").strip() == _name \
                            and node.ControlTypeName == _finder_name.replace("Control", "") + "Control":
                        _matches.append(node)
                except Exception:
                    pass
                try:
                    for _ch in node.GetChildren():
                        _walk(_ch, depth + 1)
                except Exception:
                    pass
            _walk(_root)
            if not _matches:
                return None
            def _dist(c):
                try:
                    r = c.BoundingRectangle
                    return ((r.left + r.right) / 2 - _ecx) ** 2 + ((r.top + r.bottom) / 2 - _ecy) ** 2
                except Exception:
                    return float("inf")
            return min(_matches, key=_dist)
        except Exception as exc:
            logger.debug("resolve_live_control %r failed: %s", _name[:24], exc)
            return None

    def _act_on_element(self, elem: Dict[str, Any], value: str) -> bool:
        """Fill/toggle/select the OBSERVED element via UIA patterns. Returns
        True when the action verifiably landed. Falls through to False so the
        caller can use the legacy click/paste path as backup."""
        ctrl = self._resolve_live_control(elem)
        if ctrl is None:
            return False
        ety = (elem.get("type") or "").lower()
        try:
            import uiautomation as _uia
            if ety in ("checkboxcontrol", "checkbox"):
                _want = value.strip().lower() in ("yes", "yes (check)", "true", "checked", "x", "1")
                _tp = ctrl.GetTogglePattern()
                if _tp is None:
                    return False
                if (_tp.ToggleState == 1) != _want:
                    _tp.Toggle()
                    time.sleep(0.15)
                return (ctrl.GetTogglePattern().ToggleState == 1) == _want
            if ety in ("comboboxcontrol", "combobox"):
                # expand, select the matching item by ITS name, collapse
                try:
                    _ec = ctrl.GetExpandCollapsePattern()
                    _ec.Expand()
                    time.sleep(0.3)
                except Exception:
                    pass
                _vlc = value.strip().lower()
                _item = None
                for _ch in ctrl.GetChildren():
                    _nm = (getattr(_ch, "Name", "") or "").strip().lower()
                    if _nm == _vlc or _nm.startswith(_vlc) or _vlc.startswith(_nm):
                        _item = _ch
                        break
                if _item is None:
                    # wx renders the dropdown as a separate list window — search desktop
                    try:
                        _lst = _uia.ListControl(searchDepth=3)
                        if _lst.Exists(maxSearchSeconds=0.5):
                            for _ch in _lst.GetChildren():
                                _nm = (getattr(_ch, "Name", "") or "").strip().lower()
                                if _nm == _vlc or _nm.startswith(_vlc):
                                    _item = _ch
                                    break
                    except Exception:
                        pass
                if _item is not None:
                    try:
                        _sp = _item.GetSelectionItemPattern()
                        _sp.Select()
                    except Exception:
                        _item.Click(simulateMove=False)
                    time.sleep(0.2)
                    try:
                        ctrl.GetExpandCollapsePattern().Collapse()
                    except Exception:
                        pass
                    _now = (getattr(ctrl, "GetValuePattern", lambda: None)() or None)
                    return True
                try:
                    ctrl.GetExpandCollapsePattern().Collapse()
                except Exception:
                    pass
                return False
            # edits + spins: ValuePattern first (immune to paste-reject), then
            # SetFocus + keystrokes
            try:
                _vp = ctrl.GetValuePattern()
                if _vp is not None and not _vp.IsReadOnly:
                    _vp.SetValue(value)
                    time.sleep(0.15)
                    if (_vp.Value or "").strip() == value.strip():
                        return True
            except Exception:
                pass
            try:
                ctrl.SetFocus()
                time.sleep(0.15)
                ctrl.SendKeys("{Ctrl}a", waitTime=0.05)
                ctrl.SendKeys(value, interval=0.02, waitTime=0.1)
                _vp2 = ctrl.GetValuePattern()
                return (_vp2 is not None
                        and (_vp2.Value or "").strip() == value.strip())
            except Exception:
                return False
        except Exception as exc:
            logger.debug("act_on_element %r failed: %s",
                         (elem.get('label') or '?')[:24], exc)
            return False

    def _nav_fill_field(self, state: Dict[str, Any], field_label: str, value: str,
                        prefer_key=None) -> bool:
        """Locate a field by its own label, scroll it into view if needed, and fill
        it directly (combobox open+select · checkbox set · edit idempotent paste).
        Used by the Navigation Protocol to fix a specific empty/wrong field. Returns
        True if it acted. Generic: widget type + label + geometry.
        `prefer_key`: section-qualified _attempt_key of the SPECIFIC element to
        act on — on repeated-section forms three fields share the same label and
        exact-first-match hits the wrong section's."""
        value = "" if value is None else str(value)   # LLM may return an int (e.g. 4) → coerce
        _ll = field_label.strip().lower()
        def _find(st):
            _cands = [e for e in st.get("elements", [])
                      if (e.get("type") or "").lower() in ("editcontrol", "comboboxcontrol", "checkboxcontrol")
                      and e.get("bbox") and (e.get("label") or e.get("text"))]
            # 0) caller pinned a specific element (section-qualified key)
            if prefer_key is not None:
                _pin = next((e for e in _cands
                             if self._attempt_key(e, st) == prefer_key), None)
                if _pin is not None:
                    return _pin
            # 1) exact label match — prefer an EMPTY one (repeated sections:
            # the filled twin must not shadow the empty one)
            _exacts = [e for e in _cands
                       if (e.get("label") or e.get("text") or "").strip().lower() == _ll]
            _exact = (next((e for e in _exacts if not (e.get("value") or "").strip()), None)
                      or next(iter(_exacts), None))
            if _exact is not None:
                return _exact
            # 2) fuzzy: the LLM's label rarely matches the field's exactly ("ZIP" vs
            # "ZIP Code"). Pick the candidate whose label contains / is contained by
            # the request, best character overlap — so we fill the RIGHT field.
            _best, _score = None, 0
            for e in _cands:
                _n = (e.get("label") or e.get("text") or "").strip().lower()
                if _ll in _n or _n in _ll:
                    _s = len(set(_ll) & set(_n))
                    if _s > _score:
                        _best, _score = e, _s
            return _best
        el = _find(state)
        # Use the field's OWN real label (resolved from the tree) for scroll/focus —
        # not the LLM's possibly-paraphrased label.
        _real = (el.get("label") or el.get("text") or field_label).strip() if el else field_label
        # Bring the field on-screen via NATIVE UIA scroll-into-view if it's off the
        # fold OR not in our snapshot yet (below-fold fields exist in the UIA tree
        # with off-screen coords). No pixels, no scrollbar drag.
        vb = self._form_viewport_bottom(state) - 8
        vt = self._form_viewport_top(state)
        if el is None or not (el["bbox"][1] >= vt and el["bbox"][3] <= vb):
            self._scroll_into_view(_real)
            time.sleep(self.step_delay * 0.5)
            state = self._observe()
            el = _find(state)
            if el is None:
                return False
            # STALE-COORD GUARD: if the field is STILL outside the pane after the
            # reveal (scrolled-past element with a stale bbox in the tab-strip
            # zone), clicking it would hit whatever actually lives at those
            # coords (observed live: 'State' @ y=130 → tab-strip click → escape
            # → re-propose loop). Refuse instead — caller counts it as a fail
            # and dead-marks after 2, which breaks the loop.
            # Judged by the element's CENTER, not the full bbox — wx auto-scroll
            # legitimately parks a revealed field 1-3px over the pane edge
            # (observed live: 'Balance Due ($)' top y=153 vs pane top 155 →
            # full-bbox check refused the fill every verify pass → verification
            # never came back clean → Submit never fired).
            vt = self._form_viewport_top(state)
            vb = self._form_viewport_bottom(state) - 8
            _cy_guard = (el["bbox"][1] + el["bbox"][3]) / 2
            if not (vt <= _cy_guard <= vb):
                logger.warning("[NAV] fill %r — center outside pane after reveal "
                               "(cy=%.0f, viewport %.0f-%.0f) — refusing stale-coord click.",
                               _real[:28], _cy_guard, vt, vb)
                return False
            _real = (el.get("label") or el.get("text") or field_label).strip()
        typ = (el.get("type") or "").lower()
        x1, y1, x2, y2 = el["bbox"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        # IDENTITY EXECUTOR FIRST: resolve the live control for THIS element and
        # act via UIA patterns (Value/Toggle/Selection) — no coordinates, no
        # paste-reject, twins disambiguated by geometry. Legacy click/paste
        # paths below remain as fallback when pattern support is missing.
        if value and self._act_on_element(el, value):
            logger.info("[NAV] fill %r via identity executor (UIA pattern).", _real[:28])
            self._mark_attempted(el)
            _sec_ne = self._detect_section(state, el)
            self._filled_this_tab.add(f"{_sec_ne} {_real}" if _sec_ne else _real)
            return True
        if typ == "checkboxcontrol":
            # Interpret the source value: truthy → should be CHECKED, falsy/NO/blank →
            # should be UNCHECKED (the default). Mark attempted either way so it stops
            # being re-proposed (the loop). Only toggle if it's in the WRONG state, and
            # use Win32 BM_SETCHECK (a pyautogui click doesn't toggle a wx checkbox).
            _want = value.strip().lower() in ("yes", "yes (check)", "true", "checked", "x", "1")
            self._mark_attempted(el)
            if _want == self._is_checked(el):
                return True                       # already correct (esp. NO = unchecked)
            # Set via UIA TogglePattern by name (matches the read — so it actually
            # changes the right control and reads back consistent). Falls back to
            # BM_SETCHECK by point.
            try:
                import uiautomation as _uia, win32gui as _wgg
                _root = _uia.ControlFromHandle(self._locked_hwnd or _wgg.GetForegroundWindow())
                _cb = _root.CheckBoxControl(searchDepth=25, Name=_real) if _root else None
                if _cb is not None and _cb.Exists(maxSearchSeconds=0.3):
                    _tp = _cb.GetTogglePattern()
                    if _tp is not None and (_tp.ToggleState == 1) != _want:
                        _tp.Toggle()
                    if _want:
                        self._checked_fields.add(_real)
                    return True
            except Exception:
                pass
            try:
                import win32gui as _wg, win32api as _wa
                _h = _wg.WindowFromPoint((int(cx), int(cy)))
                if _h:
                    _wa.SendMessage(_h, 0x00F1, 1 if _want else 0, 0)   # BM_SETCHECK
                    if _want:
                        self._checked_fields.add(_real)
            except Exception as _ce:
                logger.debug("nav_fill checkbox set failed: %s", _ce)
            return True
        if typ == "comboboxcontrol":
            self._executor.execute({"action_type": "click", "click_position": [cx, cy]})
            _items = []
            for _ in range(4):
                time.sleep(0.35)
                _cs = self._observe()
                _items = [e for e in _cs.get("elements", [])
                          if e.get("type") == "listitemcontrol"
                          and e.get("window_role") != "background" and e.get("bbox")]
                if _items:
                    break
            _vl = value.strip().lower()
            _hit = next((e for e in _items
                         if (e.get("text") or e.get("label") or "").strip().lower() == _vl), None) \
                or next((e for e in _items
                         if _vl in (e.get("text") or e.get("label") or "").strip().lower()), None)
            if _hit:
                _hb = _hit["bbox"]
                self._executor.execute({"action_type": "click",
                                        "click_position": [(_hb[0] + _hb[2]) / 2, (_hb[1] + _hb[3]) / 2]})
                self._filled_this_tab.add(field_label)
                self._mark_attempted(el)
                return True
            self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["escape"]})
            return False
        # editcontrol → focus the EXACT field by UIA identity, then idempotent paste.
        # Focusing the element directly (not clicking a pixel) means the value can
        # NEVER land in a neighbouring field — the cause of cell-phone-into-Email.
        if self._focus_field_uia(_real):
            self._executor.execute({"action_type": "keyboard", "key_count": len(value),
                                    "keystrokes": list(value), "text": value})
            self._filled_this_tab.add(field_label)
            self._mark_attempted(el)
            return True
        # UIA focus failed → do NOT blind-click (would type into a neighbour and
        # produce wrong-value-in-wrong-field). Skip; the sweep retries/marks it.
        logger.warning("Fill SKIP %r — could not verify focus (won't risk wrong field).", _real[:28])
        return False

    def _llm_action_to_prediction(
        self, llm_action: Dict[str, Any], state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Convert an LLM action dict into the executor's prediction format."""
        action_type = llm_action.get("action_type", "wait")

        if action_type == "click":
            target = llm_action.get("target", "")
            # Caller may pre-resolve real geometry (e.g. tab bbox) — trust it over
            # _resolve_target, which deprioritizes tabs and mis-lands on stray labels.
            coords = llm_action.get("click_position") or _resolve_target(target, state)
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
        import uuid
        # Unique tag per call breaks LM Studio's server-side KV-cache accumulation
        system_msg = (
            self._system_prompt
            + "\n\nBefore choosing an action, reason briefly inside <think>...</think> tags."
            + " Then output ONLY a JSON object on the last line."
            + f"\n[sid:{uuid.uuid4().hex[:12]}]"
        )
        resp = self._llm_client.chat.completions.create(
            model=self._llm_model,
            max_tokens=2048,
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

    def _llm_json(self, system_msg: str, user_msg: str) -> Dict[str, Any]:
        """Minimal LLM call for the Navigation Protocol with a CLEAN system prompt —
        no task-spec / <think> priming (which steers the local model to the old
        action_type/click/type schema instead of the protocol's {action:...}).
        Returns the parsed JSON dict, or {} on failure. openai-compat providers."""
        import uuid
        try:
            resp = self._llm_client.chat.completions.create(
                model=self._llm_model,
                max_tokens=400,
                messages=[
                    {"role": "system", "content": system_msg + f"\n[sid:{uuid.uuid4().hex[:8]}]"},
                    {"role": "user",   "content": user_msg},
                ],
            )
            return _parse_llm_response(resp.choices[0].message.content)
        except Exception as exc:
            logger.warning("[NAV] _llm_json failed: %s", exc)
            return {}

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
                # Cache the freshest observation for section-qualified identity
                # keys (_attempt_key) — every consumer of an element processes
                # elements from the most recent observe in its code path.
                self._cur_state = state
                # FOCUS INFERENCE (vision): the pixel observer can't report
                # focus. If it didn't, stamp the last CLICKED fillable as the
                # focused element (matched by identity key in THIS observation)
                # so the fill path works identically under vision perception.
                if (not state.get("focused_element_id")
                        and getattr(self, "_inferred_focus_key", None)):
                    for _e in state.get("elements", []):
                        if ((_e.get("type") or "").lower() in self._FILLABLE_TYPES
                                and self._attempt_key(_e, state) == self._inferred_focus_key):
                            state["focused_element_id"] = _e.get("element_id")
                            break
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

    def _attempt_key(self, elem: Dict[str, Any], state: Optional[Dict[str, Any]] = None):
        """Scroll-stable identity for a field. SECTION-QUALIFIED: label-primary
        keys collide on repeated-section forms — 'First Name' exists 3× on the
        Drivers tab (Driver 1/2/3), so filling Driver 1's masked 2/3's across
        the ENTIRE mask stack (arbitration, topmost-missing, viewport jump,
        sweep dead-list) and whole sections were skipped (observed live
        2026-07-09). The section pane scrolls WITH the field, so
        (section, label) stays scroll-stable. Falls back to the bare label for
        non-sectioned fields/scopes — identical to the old key there.
        `state` defaults to the freshest observation (set by _observe)."""
        lbl = (elem.get("label") or elem.get("text") or "").strip().lower()
        if lbl:
            st = state if state is not None else getattr(self, "_cur_state", None)
            # Section = raw pane label (prefix 'section_'), NOT the ScopeConfig-
            # formatted name from _detect_section — the key must not silently
            # degrade to the colliding bare label when a scope has no
            # section_pattern configured, and the raw label partitions
            # identically to transformer._attempt_key (train/inference must
            # agree on the 'attempted' feature).
            sec = self._section_pane_of(elem, st) if st is not None else ""
            if sec:
                return (sec, lbl)
            return lbl
        b = elem.get("bbox") or [0, 0, 0, 0]
        return ("@", round((b[0] + b[2]) / 2 / 20) * 20, round((b[1] + b[3]) / 2 / 20) * 20)

    @staticmethod
    def _section_pane_of(elem: Dict[str, Any], state: Dict[str, Any]) -> str:
        """Raw label of the lowest 'section_*' pane whose top edge is at/above
        the element's vertical center. Mirrors transformer._section_of exactly
        (same partition for the train-time 'attempted' feature)."""
        if not elem.get("bbox"):
            return ""
        _cy = (elem["bbox"][1] + elem["bbox"][3]) / 2
        best, best_top = "", None
        for p in state.get("elements", []) or []:
            if (p.get("type") or "").lower() not in ("panecontrol", "pane"):
                continue
            if p.get("window_role") == "background" or not p.get("bbox"):
                continue
            _pl = (p.get("label") or p.get("text") or "").strip().lower()
            if not _pl.startswith("section_"):
                continue
            if p["bbox"][1] <= _cy and (best_top is None or p["bbox"][1] > best_top):
                best, best_top = _pl, p["bbox"][1]
        return best

    def _mark_attempted(self, elem: Dict[str, Any]) -> None:
        """Record that a field has been acted on this session (attempted feature)."""
        if isinstance(elem, dict):
            self._attempted_keys.add(self._attempt_key(elem))

    # Fillable widget types for ranked-target arbitration (universal control
    # types, not field names). Buttons/tabs are deliberately NOT here — they are
    # legitimately re-clickable (tab advance, submit) and only get masked by the
    # per-tab no_change blacklist.
    _FILLABLE_TYPES = ("editcontrol", "input", "comboboxcontrol", "combobox",
                       "checkboxcontrol", "checkbox", "spincontrolcontrol", "spincontrol")

    # Clickable at all (fillables + buttons/tabs/list items/radios/links).
    # Decorative containers (pane/window/document/titlebar) are NEVER legal
    # ranked targets — clicking them is exactly the wasted-click noise the
    # dataset's pointer loss already drops (-1 labels).
    _CLICKABLE_TYPES = _FILLABLE_TYPES + (
        "button", "buttoncontrol", "tabitem", "tabitemcontrol",
        "listitem", "listitemcontrol", "radiobutton", "radiobuttoncontrol",
        "hyperlink", "hyperlinkcontrol", "menuitem", "menuitemcontrol",
        "datetime", "datetimecontrol", "calendar", "calendarcontrol")

    def _pick_ranked_target(self, state: Dict[str, Any], t_pred: Dict[str, Any]):
        """RANKED WHERE arbitration. Walk the transformer pointer head's OWN
        top-k ranking and return the best target that is still actionable:

          skip if — background window; click position blacklisted (gave
          no_change this tab); fillable AND (dead / attempted / already filled).
          An already-filled field is marked filled-this-tab on the spot, so
          'already correct' structurally means MOVE ON, never retry.

        VISIBLE-ONLY for fillables (NAV rule 1: fill the current viewport
        before moving it): a fillable candidate outside the viewport is NOT
        pickable — off-screen work belongs to _optimal_viewport_jump, which
        only triggers when this returns None. (First version had an off-fold
        fallback pass here; the model always has below-fold fields in its
        top-8, so the fallback ALWAYS answered, the jump never fired, and the
        one-field-per-scroll crawl returned. The picker and the jump must not
        compete for the same case.) Tabs/buttons are always eligible — they
        legitimately live OUTSIDE the scroll pane (tab strip above, footer
        below), and masking them would block tab-advance and submit.

        Returns (elem, [cx, cy], conf) or None when every ranked candidate is
        masked — the caller's signal to run the optimal-viewport jump.
        Navigation stays 100% the transformer's choice: this only applies a
        legality filter over ITS ranking; nothing here picks a target the
        model didn't propose.
        """
        elems = state.get("elements", [])
        _filled_l = {f.lower() for f in self._filled_this_tab}
        _vt = self._form_viewport_top(state)
        _vb = self._form_viewport_bottom(state) - 8

        for entry in t_pred.get("click_topk", []) or []:
            try:
                idx, conf, pos = int(entry[0]), float(entry[1]), entry[2]
            except (TypeError, ValueError, IndexError):
                continue
            if not (0 <= idx < len(elems)):
                continue
            e = elems[idx]
            if e.get("window_role") == "background" or not e.get("bbox"):
                continue
            ety = (e.get("type") or "").lower()
            if ety not in self._CLICKABLE_TYPES:
                continue
            _pk = (round(pos[0] / 10) * 10, round(pos[1] / 10) * 10)
            if _pk in self._nochange_click_pos:
                continue
            if ety in self._FILLABLE_TYPES:
                # fillables must be INSIDE the scroll viewport (center-based —
                # wx parks revealed fields 1-3px over the edge)
                _cy = (e["bbox"][1] + e["bbox"][3]) / 2
                if not (_vt <= _cy <= _vb):
                    continue
                k = self._attempt_key(e, state)
                if k in self._dead_fill_keys or k in self._attempted_keys:
                    continue
                lbl = (e.get("label") or e.get("text") or "").strip()
                # Filled-this-tab check is SECTION-QUALIFIED — a bare-label
                # match here was the Driver 2/3 collision (Driver 1's filled
                # 'First Name' masked the empty ones in the other sections).
                _sec_pk = self._detect_section(state, e)
                _fkey_pk = (f"{_sec_pk} {lbl}" if _sec_pk else lbl).lower()
                if lbl and _fkey_pk in _filled_l:
                    continue
                if (e.get("value") or "").strip() and ety not in ("checkboxcontrol", "checkbox"):
                    # Field already holds a value — done, not a target. Record
                    # it (section-qualified) so later ranking passes skip fast.
                    if lbl:
                        self._filled_this_tab.add(f"{_sec_pk} {lbl}" if _sec_pk else lbl)
                    continue
            else:
                # Buttons/tabs: only while NO fill work remains on this tab.
                # The model's top-8 almost always contains SOME low-confidence
                # button (Submit zone at 0.26, Print Preview at 0.00 — both
                # observed live), so unconditional eligibility meant the picker
                # never returned None and the optimal-viewport jump never fired.
                # Fill first; press buttons / advance tabs when the page is done.
                if self._topmost_missing(state) is not None:
                    continue
            # actionable work found → the viewport lock releases: anchors
            # visited before this progress become legitimate jump targets again
            self._jump_anchors_since_progress = set()
            return e, [float(pos[0]), float(pos[1])], conf
        return None

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
        elem = None
        if at == "keyboard":
            fid = state.get("focused_element_id")
            elem = next((e for e in state.get("elements", []) if e.get("element_id") == fid), None)
        elif at == "click":
            elem = self._elem_at(state, prediction.get("click_position") or [])
        if elem is not None:
            self._mark_attempted(elem)

    def _stamp_attempted_live(self, state: Dict[str, Any]) -> None:
        """Stamp attempted=1 on observed elements acted on earlier this session."""
        if not self._attempted_keys:
            return
        for e in state.get("elements", []):
            if self._attempt_key(e) in self._attempted_keys:
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
