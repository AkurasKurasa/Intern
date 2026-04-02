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
    import google.generativeai as _genai
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
You are an automation agent for Intern, a desktop AI that fills forms by \
controlling the mouse and keyboard.

You will receive the current screen state with three sections:
  FORM LABELS      — the exact text of every visible label in the active window.
  FORM INPUTS      — every interactive element (inputs, buttons, dropdowns).
  BACKGROUND DATA  — raw text from background windows (Notepad etc.) containing
                     the values you must type into the form.

Decide the SINGLE next action and respond with a JSON object only:

{
  "action_type": "click" | "type" | "hotkey" | "scroll" | "done" | "wait",
  "target":      "<copy the EXACT string from FORM LABELS or FORM INPUTS>",
  "text":        "<exact value copied from BACKGROUND DATA — never invent values>",
  "keys":        ["tab"],
  "direction":   "down" | "up",
  "clicks":      3,
  "reason":      "<one sentence>"
}

Rules:
- The form is ALREADY the active window. Do NOT try to click the window title
  or focus the window — go straight to filling the first empty field.
- "click"  → focus a field OR toggle a checkbox OR open a dropdown OR switch tabs.
             Set "target" to the EXACT string from FORM LABELS or FORM INPUTS.
             Do not paraphrase, invent, or guess a name — copy it exactly.
- "type"   → type into the currently focused text field. Set "text" to the EXACT
             value from BACKGROUND DATA. Never invent or guess values.
- "hotkey" → keyboard shortcut. Use keys: ["tab"] to advance, ["down"] or ["up"]
             to cycle dropdown options, ["return"] to confirm a selection.
- "scroll" → scroll any window. Use "target" to aim at a window element, set
             direction "down" or "up" and clicks = amount.
- "done"   → all fields are fully filled and the task is complete.
- "wait"   → UI is still loading.

Field type patterns:
- Text field  → click field → type value  (Tab is sent automatically after type)
- Dropdown    → FIRST check if the current value already matches BACKGROUND DATA.
               If it matches → hotkey ["tab"] to skip (do NOT open the dropdown).
               If it does NOT match:
                 Step 1 — click the [comboboxcontrol] to open the list.
                 Step 2 — WAIT: the list items appear as [listitemcontrol] in FORM INPUTS.
                 Step 3 — click the EXACT [listitemcontrol] whose text matches BACKGROUND DATA.
               NEVER type into a dropdown — it will not work.
               NEVER click the current value label — click the TARGET value from the list.
- Checkbox    → click the checkbox to check it. Only click if value should be YES/checked.
- Tab switch  → after finishing all fields on the current tab, click the next tab
               name from FORM INPUTS (e.g. "Policyholder", "Vehicle", "Coverage"…).

CRITICAL RULES — follow exactly:
1. If a field's "current value" already matches the value in BACKGROUND DATA → hotkey ["tab"] to skip. Do NOT open it. Do NOT retype it.
2. If a field has no matching value in BACKGROUND DATA → hotkey ["tab"] to skip.
3. NEVER type explanatory text — that would corrupt the form.
4. Do NOT output anything outside the JSON object.
"""


# ══════════════════════════════════════════════════════════════════════════════
#  State / history → text helpers
# ══════════════════════════════════════════════════════════════════════════════

def _parse_records(raw_text: str) -> dict:
    """
    Parse a multi-record intake text into {record_num: {field: value}}.
    Expects records delimited by lines containing 'RECORD N OF M'.
    Returns {} if no record headers are found (single-record or plain text).
    """
    import re
    parts = re.split(r"={3,}[\s\S]*?RECORD\s+(\d+)\s+OF\s+\d+[\s\S]*?={3,}", raw_text)
    if len(parts) <= 1:
        return {}
    records = {}
    i = 1
    while i + 1 < len(parts):
        rn   = int(parts[i])
        body = parts[i + 1]
        data = {}
        for line in body.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                key = re.sub(r"[\[\]]", "", key).strip()
                val = re.sub(r"\s*\[.*", "", val)
                val = re.sub(r"\s*←.*", "", val).strip()
                if key:
                    cleaned = val.lower().strip()
                    # Only store the FIRST occurrence of each key.
                    # Records have policyholder fields first then driver/vehicle
                    # sections that repeat the same field names (e.g. "First Name").
                    # The form's Policyholder tab wants the policyholder value, not
                    # the driver's value — so first-one-wins is correct.
                    if key in data:
                        continue
                    if cleaned in {"(none)", "none"}:
                        data[key] = ""        # explicitly blank — agent should leave empty
                    elif val and cleaned != "":
                        data[key] = val
        records[rn] = data
        i += 2
    return records


def _state_to_text(state: Dict[str, Any], record_num: int = 1) -> str:
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
        _fn  = (focused_el.get("label") or focused_el.get("text") or "?").strip()
        _fv  = (focused_el.get("value") or "").strip()
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


    lines.append("FORM LABELS (use EXACT strings as 'target' when clicking a field):")
    for e in labels[:30]:
        focused = " [FOCUSED]" if e.get("focused") else ""
        lines.append(f"  \"{(e.get('text') or '').strip()}\"{focused}")

    lines.append(f"\nFORM INPUTS ({len(interactive)} interactive elements):")
    for e in interactive[:30]:
        focused = " [FOCUSED]" if e.get("focused") else ""
        val   = (e.get("value") or "").strip()
        text  = (e.get("text") or "").strip()
        label = text or val or "(empty)"
        lines.append(f"  [{e.get('type','?')}] \"{label}\"{focused}"
                     + (f"  current value: {val!r}" if val else ""))

    if data_elems:
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
                # Only output fields whose label appears as a VISIBLE form element.
                # This keeps the prompt small (≤40 fields) and avoids bloating
                # LM Studio's KV-cache with the full 157-field record every call.
                lines.append(f"\nBACKGROUND DATA (Record {record_num}):")
                for field, value in rec.items():
                    lines.append(f"  {field} : {value}")
            else:
                # Plain text — dump up to 3 000 chars to keep prompt small
                lines.append(f"\nBACKGROUND DATA (values to type — read from here):")
                lines.append(f"  {raw_text[:3000]}")

    return "\n".join(lines)


def _history_to_text(history: List[Dict[str, Any]]) -> str:
    if not history:
        return "No actions taken yet."
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
    return "\n".join(lines)


def _parse_llm_response(raw: str) -> Dict[str, Any]:
    """Strip markdown fences and parse JSON from any LLM response."""
    raw = raw.strip()
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

    def _center(e: Dict) -> List[float]:
        b = e.get("bbox", [0, 0, 0, 0])
        return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]

    # 1. Exact match on interactive element text
    for e in active_elems:
        if e.get("type") in _INTERACTIVE:
            txt = (e.get("text") or e.get("label") or e.get("value") or "").lower()
            if txt == tl:
                return _center(e)

    # 2. Exact match on label element → return nearest interactive element
    for e in active_elems:
        txt = (e.get("text") or e.get("label") or "").lower()
        if txt == tl:
            cx, cy = _center(e)
            best, best_dist = None, float("inf")
            for other in active_elems:
                if other.get("type") not in _INTERACTIVE:
                    continue
                ox, oy = _center(other)
                dist = ((ox - cx) ** 2 + (oy - cy) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best = other
            if best and best_dist < 300:
                return _center(best)

    # 3. Partial match on interactive element text
    for e in active_elems:
        if e.get("type") in _INTERACTIVE:
            txt = (e.get("text") or e.get("label") or e.get("value") or "").lower()
            if tl in txt or txt in tl:
                return _center(e)

    # 4. Partial match on label → nearest interactive
    for e in active_elems:
        txt = (e.get("text") or e.get("label") or "").lower()
        if tl in txt or txt in tl:
            cx, cy = _center(e)
            best, best_dist = None, float("inf")
            for other in active_elems:
                if other.get("type") not in _INTERACTIVE:
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
        if e.get("type") in _INTERACTIVE:
            val = (e.get("value") or "").lower().strip()
            if val and val == tl:
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
        model_path:    str            = "data/models/transformer_bc.pt",
        dry_run:       bool           = False,
        max_steps:     int            = 50,
        step_delay:    float          = 1.2,
        llm_every:     int            = 2,
        device_str:    str            = "auto",
        record_num:    int            = 1,
        use_ocr:       bool           = False,   # EXPERIMENTAL: merge VisionObserver OCR results
    ):
        self.goal       = goal
        self.provider   = provider.lower().strip()
        self.model_path = model_path
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
        self._observer          = UIAutomationObserver(
            max_elements_per_window=300,
            max_total_elements=700,
        )
        self._validator         = StateValidator()
        self._correction        = CorrectionHandler()
        self._record_num: int               = record_num
        self._history:  List[Dict[str, Any]] = []
        self._results:  List[Dict[str, Any]] = []
        self._checked_fields: set           = set()   # checkboxes already clicked this run
        self._filled_this_tab: set          = set()   # edit fields filled on current tab (prevents re-fill on cycling)
        self._current_tab_idx: int          = 0       # tracks which tab we're on
        self._guidance: str = ""
        self._task_name: str = ""   # set via run(task_name=...)
        self._cached_record: Dict[str, str] = {}     # full parsed record from Notepad (bypasses 2000-char UIA cap)
        self._ocr_cache: Dict[str, Any] = {}        # instance-level OCR cache (clears per record)

        # EXPERIMENTAL — OCR overlay via VisionObserver
        # Disabled by default; enable with use_ocr=True in LLMAgent(...)
        self._use_ocr: bool = use_ocr
        self._vision_observer: Optional[Any] = None
        if use_ocr:
            try:
                try:
                    from components.observers.vision_observer.vision_observer import VisionObserver
                except ImportError:
                    from observers.vision_observer.vision_observer import VisionObserver
                self._vision_observer = VisionObserver()
                logger.info("OCR overlay enabled (VisionObserver loaded)")
            except Exception as exc:
                logger.warning("OCR overlay requested but VisionObserver unavailable: %s", exc)

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
            _genai.configure(api_key=key)
            self._llm_client = _genai.GenerativeModel(
                model_name=self._llm_model,
                system_instruction=_SYSTEM_PROMPT,
            )
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
        _tab_just_switched: bool          = True   # True on first step to focus first empty field
        _last_auto_step:   int            = -1  # step_idx of last step where an auto-handler fired
        _DROUGHT_LIMIT:    int            = 10  # scroll form after this many non-fill steps in a row
        _tab_scroll_count: int            = 0   # scrolls performed on current tab
        _MAX_TAB_SCROLLS:  int            = 6   # max drought-scrolls per tab before giving up
        _steps_on_tab:     int            = 0   # steps spent on current tab — forces advance if too many
        _TAB_STEP_LIMIT:   int            = 40  # force tab advance after this many steps on same tab
        _pane_escape_last_field: str      = ""  # last field pane-escape tried to click
        _pane_escape_streak:     int      = 0   # consecutive tries on the same field without escaping

        _record_cache_loaded = False

        for step_idx in range(n):
          try:
            # 1. Observe
            state      = self._observe()
            llm_action: Dict[str, Any] = {}
            _steps_on_tab += 1
            logger.info("── Step %d/%d  (%d elements) ──", step_idx + 1, n, len(state.get("elements", [])))

            # Load record cache on first step
            if not _record_cache_loaded:
                self._refresh_record_cache(state)
                _record_cache_loaded = True

            # 1a. After a tab switch: scroll to top, re-observe, then click first empty field
            if _tab_just_switched:
                _tab_just_switched    = False
                _steps_on_tab         = 0
                _tab_scroll_count     = 0
                _no_change_streak     = 0
                _last_auto_step       = step_idx  # treat tab switch as an auto step
                _pane_escape_last_field = ""
                _pane_escape_streak     = 0
                self._filled_this_tab.clear()     # new tab — reset filled-field tracking
                self._scroll_form_to_top(state)
                time.sleep(0.6)
                state = self._observe()   # get updated positions after scroll
                self._focus_first_empty_field(state)
                time.sleep(self.step_delay)
                continue   # re-observe so auto-handlers run first before LLM gets a chance

            # 1b. Stuck guard: if no_change repeats OR too many steps on same tab, advance
            _stuck = (_no_change_streak >= _NO_CHANGE_LIMIT
                      or _steps_on_tab >= _TAB_STEP_LIMIT)
            if _stuck:
                if _steps_on_tab >= _TAB_STEP_LIMIT:
                    logger.info("Stuck guard: %d steps on tab — forcing advance.", _steps_on_tab)
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

            # 1b. Pane-focus escape: if focus is on a section header / container (panecontrol),
            # Tab will do nothing. Jump directly to the first empty edit field instead.
            _focused_id  = state.get("focused_element_id")
            _focused_el  = next((e for e in state.get("elements", []) if e.get("element_id") == _focused_id), None)
            if _focused_el and _focused_el.get("type") == "panecontrol":
                _pane_label = (_focused_el.get("label") or _focused_el.get("text") or "?")[:40]
                _pane_y     = _focused_el["bbox"][1] if _focused_el.get("bbox") else 0
                logger.info("Focus on pane %r — clicking first empty field to escape.", _pane_label)
                # Peek at which field we'd click before actually clicking it.
                # If we've clicked the same field 3+ times with no escape (loop!), scroll down first.
                _pane_candidates = sorted(
                    [_pe for _pe in state.get("elements", [])
                     if (_pe.get("window_role") != "background"
                         and _pe.get("type") == "editcontrol"
                         and not (_pe.get("value") or "").strip()
                         and _pe.get("bbox") and _pe.get("enabled", True)
                         and _pe["bbox"][1] >= max(100, _pane_y))],
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
                    continue
                if self._focus_first_empty_field(state, min_y=_pane_y):
                    time.sleep(self.step_delay * 0.5)
                    continue
                # No empty editcontrol found at/below pane. Check if the entire tab is done:
                # all editcontrols filled AND all required checkboxes handled.
                _active_edits = [e for e in state.get("elements", [])
                                 if e.get("window_role") != "background"
                                 and e.get("type") == "editcontrol"
                                 and e.get("enabled", True)]
                # A field is "pending" if the record has a value for it AND we
                # haven't filled it ourselves this tab pass.  Non-empty values from
                # a previous interrupted run still count as pending because they may
                # be wrong and we haven't verified/overwritten them yet.
                _pending_edits = [
                    (e.get("label") or e.get("text") or "").strip()
                    for e in _active_edits
                    if (e.get("label") or e.get("text") or "").strip() not in self._filled_this_tab
                    and self._lookup_field((e.get("label") or e.get("text") or "").strip())
                ]
                # Also check for checkboxes that still need to be clicked.
                # Use startswith("yes") to match "YES (check)", "yes", "Yes", etc.
                _active_checks = [e for e in state.get("elements", [])
                                  if e.get("window_role") != "background"
                                  and e.get("type") == "checkboxcontrol"
                                  and e.get("enabled", True)]
                _unhandled_checks = []
                for _chk_e in _active_checks:
                    _chk_name = (_chk_e.get("label") or _chk_e.get("text") or "").strip()
                    if _chk_name in self._checked_fields:
                        continue
                    _chk_exp = self._lookup_field(_chk_name)
                    if _chk_exp and _chk_exp.lower().strip().startswith("yes"):
                        _unhandled_checks.append(_chk_name)
                if not _pending_edits and _active_edits and not _unhandled_checks:
                    logger.info("Pane-escape: all %d editcontrols handled, %d checkboxes done — advancing tab.",
                                len(_active_edits), len(_active_checks))
                    if self._try_advance_tab(state):
                        _no_change_streak  = 0
                        _tab_just_switched = True
                        _tab_scroll_count  = 0
                        _last_auto_step    = step_idx
                        self._filled_this_tab.clear()
                        self._refresh_record_cache(state)
                        time.sleep(self.step_delay)
                        continue
                # Fall through so auto-check / LLM can handle the checkboxes.

            # 1c. Submit-button escape: focus landed on a Submit/button after all tab work done.
            # Pane-escape (1b) only fires for panecontrol; when focus is on a buttoncontrol
            # (e.g. "Submit  New") the same "all done → advance tab" logic must also run.
            elif _focused_el and _focused_el.get("type") == "buttoncontrol":
                _btn_name = (_focused_el.get("label") or _focused_el.get("text") or "").strip()
                if "submit" in _btn_name.lower():
                    _active_edits_b = [e for e in state.get("elements", [])
                                       if e.get("window_role") != "background"
                                       and e.get("type") == "editcontrol"
                                       and e.get("enabled", True)]
                    _pending_edits_b = [
                        (e.get("label") or e.get("text") or "").strip()
                        for e in _active_edits_b
                        if (e.get("label") or e.get("text") or "").strip() not in self._filled_this_tab
                        and self._lookup_field((e.get("label") or e.get("text") or "").strip())
                    ]
                    _active_checks_b = [e for e in state.get("elements", [])
                                        if e.get("window_role") != "background"
                                        and e.get("type") == "checkboxcontrol"
                                        and e.get("enabled", True)]
                    _unhandled_checks_b = []
                    for _chk_e in _active_checks_b:
                        _chk_name = (_chk_e.get("label") or _chk_e.get("text") or "").strip()
                        if _chk_name in self._checked_fields:
                            continue
                        _chk_exp = self._lookup_field(_chk_name)
                        if _chk_exp and _chk_exp.lower().strip().startswith("yes"):
                            _unhandled_checks_b.append(_chk_name)
                    if not _pending_edits_b and _active_edits_b and not _unhandled_checks_b:
                        logger.info("Submit-button escape: focus on %r, all %d edits done, %d checks done — advancing tab.",
                                    _btn_name, len(_active_edits_b), len(_active_checks_b))
                        if self._try_advance_tab(state):
                            _no_change_streak  = 0
                            _tab_just_switched = True
                            _tab_scroll_count  = 0
                            _last_auto_step    = step_idx
                            self._filled_this_tab.clear()
                            self._refresh_record_cache(state)
                            time.sleep(self.step_delay)
                            continue

            # 2. Auto-skip: if focused field already has the correct value, Tab past it
            if self._llm_client and self._auto_skip(state):
                logger.info("Auto-skip: focused field already has correct value — Tab.")
                self._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
                _no_change_streak = 0
                _steps_on_tab     = 0
                # NOTE: _last_auto_step is intentionally NOT reset here.
                # Resetting on auto-skip would mask LLM back-cycling (clicking already-filled
                # fields), preventing the drought guard from firing and scrolling down.
                time.sleep(self.step_delay)
                continue

            # 2a. Auto-fill: if focused field is empty (or has wrong leftover value), type correct value
            auto_text = self._auto_fill(state)
            if auto_text:
                field_name_log, text_val, needs_clear = auto_text
                self._peek_notepad(state, field_name_log)
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
                continue

            # 2a2. Auto-check: if focused checkbox should be checked per background data, click it
            auto_chk = self._auto_check(state)
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
                continue

            # 2b. Auto-fix combobox: if focused combobox has WRONG value, select correct option
            fix = self._combobox_needs_fix(state)
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
                        continue
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
                        continue
                        
            _drought = step_idx - _last_auto_step
            if _drought >= _DROUGHT_LIMIT:
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
                        continue   # re-observe with scrolled form + focused field
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

            # 2. Transformer always runs — learned behavioral engine
            llm_action = None
            # 2c. Drought check: if too many steps without any auto-handler, scroll form down.
            # Allowed up to _MAX_TAB_SCROLLS times per tab so multi-section tabs
            # (e.g. Policyholder with 20+ fields) get revealed incrementally.
            

            # 2. Transformer always runs — learns from user recordings
            t_pred = self._predict(state)
            t_type = t_pred.get("action_type", "no_op")
            t_conf = t_pred.get("confidence", max(t_pred.get("_scores", {}).values(), default=0.0))
            logger.info("Transformer → %-8s  conf=%.2f", t_type, t_conf)

            # Confidence-based routing:
            #   >= 0.80  → execute directly, skip LLM (transformer is sure)
            #   0.50–0.80 → LLM validates / may override
            #   <  0.50  → LLM decides (transformer is uncertain)
            _HIGH_CONF   = 0.80
            _MED_CONF    = 0.50

            # 2b. LLM only consulted when transformer is uncertain
            if self._llm_client and t_conf < _HIGH_CONF:
                llm_action = self._ask_llm(state)
                action_type = llm_action.get("action_type", "wait")
                logger.info("LLM[%s] → %s  reason=%r",
                            self.provider, action_type, llm_action.get("reason", ""))

                if action_type == "done":
                    logger.info("LLM: task complete.")
                    break
                if action_type == "wait":
                    time.sleep(2.0)
                    continue

                # ── Merge: LLM decides what, transformer decides where ──────
                prediction = self._merge(t_pred, t_conf, llm_action, state)

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

            # 2c. Transformer-only fallback (provider="none")
            else:
                prediction = t_pred

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

                    if _stuck_count >= _STUCK_LIMIT:
                        logger.warning("Loop detected @ %s %dx — forcing keyboard.", snap_tuple, _stuck_count)
                        prediction   = {"action_type": "keyboard", "key_count": 1, "keystrokes": []}
                        _stuck_count = 0
                else:
                    _stuck_pos, _stuck_count = None, 0

                if prediction.get("action_type") == "keyboard":
                    src_idx = prediction.get("source_elem_idx", -1)
                    text = self._text_resolver.resolve(state, source_elem_idx=src_idx)
                    if text:
                        prediction = dict(prediction)
                        prediction["text"] = text

            # 3. Execute — guard against typing into non-edit elements or clicking submit early
            _fid = state.get("focused_element_id")
            _fel = next((e for e in state.get("elements", []) if e.get("element_id") == _fid), None)
            _flabel = ((_fel.get("label") or _fel.get("text") or "?").strip() if _fel else "unknown")

            if prediction.get("action_type") == "keyboard" and prediction.get("text"):
                logger.info("Type target: focused=[%s] %r", _fel.get("type","?") if _fel else "?", _flabel)
                # If the focused element is NOT an editable text field, typing will do nothing
                # (or corrupt a pane/tab/button). Replace with Tab to advance focus instead.
                if _fel and _fel.get("type") not in ("editcontrol", "input"):
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
                    _chk_label = (_chk_at_cp.get("label") or _chk_at_cp.get("text") or "").strip()
                    if _chk_label in self._checked_fields:
                        # Use BM_GETCHECK (same as auto-check) — UIA toggle_state is often empty
                        # for wxPython checkboxes so we can't rely on element attributes.
                        _is_checked = False
                        try:
                            import win32gui as _wg2; import win32api as _wa2
                            _cx2 = (_chk_at_cp["bbox"][0] + _chk_at_cp["bbox"][2]) / 2
                            _cy2 = (_chk_at_cp["bbox"][1] + _chk_at_cp["bbox"][3]) / 2
                            _hwnd2 = _wg2.WindowFromPoint((int(_cx2), int(_cy2)))
                            if _hwnd2:
                                _is_checked = (_wa2.SendMessage(_hwnd2, 0x00F0, 0, 0) == 1)
                        except Exception:
                            pass
                        if _is_checked:
                            logger.warning("Blocking LLM re-click on checked checkbox %r — Tab instead.", _chk_label)
                            prediction = {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}

                _SUBMIT_KW = {"submit", "save", "ok", "accept", "done", "finish", "new"}
                _btn_at_cp = next(
                    (e for e in state.get("elements", [])
                     if e.get("type") in ("buttoncontrol", "button")
                     and e.get("window_role") != "background"
                     and e.get("bbox")
                     and any(kw in (e.get("text") or e.get("label") or "").lower()
                             for kw in _SUBMIT_KW)
                     and e["bbox"][0] <= _cp[0] <= e["bbox"][2]
                     and e["bbox"][1] <= _cp[1] <= e["bbox"][3]),
                    None
                )
                if _btn_at_cp:
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
                    logger.info("LLM clicked tab element %r — routing through _try_advance_tab.", _hit_name)
                    if self._try_advance_tab(state):
                        _no_change_streak  = 0
                        _tab_just_switched = True
                        _tab_scroll_count  = 0
                        _last_auto_step    = step_idx
                        self._filled_this_tab.clear()
                        self._refresh_record_cache(state)
                        time.sleep(self.step_delay)
                        continue
                    # Tab advance failed (already on last tab) — fall through to execute
                    prediction = {"action_type": "no_op"}

            result = self._executor.execute(prediction)
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
                        break

            # After a successful type, automatically press Tab to advance the field.
            # This prevents the LLM from getting stuck re-typing the same field.
            if (self._llm_client
                    and prediction.get("action_type") == "keyboard"
                    and prediction.get("text")):
                tab_pred = {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
                self._executor.execute(tab_pred)
                logger.info("Auto-Tab after type.")

            # 4. Validate
            state_after = self._observe()
            validation  = self._validator.validate(state, state_after, prediction)
            logger.info("Validator → %s: %s", validation.status, validation.reason)

            if validation.status == "done":
                logger.info("StateValidator: task appears complete.")
                break

            if validation.status == "no_change":
                _no_change_streak += 1
            else:
                _no_change_streak = 0

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
                "step":       step_idx + 1,
                "action":     prediction,
                "result":     str(result),
                "validation": validation.status,
                "guidance":   self._guidance,
                "elements":   len(state.get("elements", [])),
            })

            if not result.success:
                logger.error("Execution failed — halting: %s", result.error)
                break

            time.sleep(self.step_delay)

          except Exception as _step_exc:
            import traceback as _tb
            _tb_str = _tb.format_exc()
            logger.error("STEP %d CRASHED:\n%s", step_idx + 1, _tb_str)
            print(f"\n=== STEP {step_idx+1} CRASHED ===\n{_tb_str}", flush=True)
            break

        logger.info("LLMAgent finished — %d step(s).", len(self._results))
        return list(self._results)

    @property
    def results(self) -> List[Dict[str, Any]]:
        return list(self._results)

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

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

        fl = field_name.lower()
        expected = rec.get(field_name) or next((v for k, v in rec.items() if k.lower() == fl), None)
        if expected is None:
            return False   # field not in record — let LLM decide

        # Both blank → field is intentionally empty, skip
        if current == "" and expected == "":
            logger.info("Auto-skip: '%s' is blank as expected (none) — Tab.", field_name)
            return True

        if not current:
            return False   # empty field that needs filling — let auto-fill handle it

        match = current.lower() == expected.lower()
        if match:
            logger.info("Auto-skip: '%s' already = %r", field_name, current)
        return match

    def _read_notepad_full_text(self, state: Dict[str, Any]) -> str:
        """
        Read the full text from an open Notepad window via Win32 WM_GETTEXT.
        Returns empty string if Notepad is not found or unavailable.
        Bypasses the 2000-char UIA cap so the complete file is readable.
        """
        try:
            import win32gui, win32api
            WM_GETTEXTLENGTH = 0x000E
            WM_GETTEXT       = 0x000D
            _EDIT_CLASSES    = {"Edit", "RichEditD2DPT", "RichEdit20W", "RICHEDIT50W", "RICHEDIT60W"}
            _TERMINAL_HINTS  = {"powershell", "terminal", "command prompt", "cmd.exe"}

            elements = state.get("elements", [])
            bg_window_title = ""
            for e in elements:
                if e.get("window_role") != "background":
                    continue
                wt = (e.get("window_title") or "").strip()
                if ".txt" in wt or "notepad" in wt.lower():
                    bg_window_title = wt
                    break

            np_hwnd = None
            if bg_window_title:
                np_hwnd = win32gui.FindWindow(None, bg_window_title)
                if not np_hwnd:
                    np_hwnd = win32gui.FindWindow(None, bg_window_title + " - Notepad")
            if not np_hwnd:
                def _find_np(hwnd, _):
                    nonlocal np_hwnd
                    if np_hwnd: return
                    if not win32gui.IsWindowVisible(hwnd): return
                    title = win32gui.GetWindowText(hwnd)
                    cls   = win32gui.GetClassName(hwnd)
                    if cls == "Notepad" or ".txt" in title or "notepad" in title.lower():
                        np_hwnd = hwnd
                win32gui.EnumWindows(_find_np, None)
            if not np_hwnd:
                return ""

            edit_hwnd = None
            def _find_edit(hwnd, _):
                nonlocal edit_hwnd
                if edit_hwnd: return
                try:
                    if win32gui.GetClassName(hwnd) in _EDIT_CLASSES:
                        edit_hwnd = hwnd
                except Exception:
                    pass
            win32gui.EnumChildWindows(np_hwnd, _find_edit, None)
            if not edit_hwnd:
                child = win32gui.GetWindow(np_hwnd, 5)
                while child and not edit_hwnd:
                    win32gui.EnumChildWindows(child, _find_edit, None)
                    child = win32gui.GetWindow(child, 2)
            if not edit_hwnd:
                return ""

            length = win32api.SendMessage(edit_hwnd, WM_GETTEXTLENGTH, 0, 0)
            if length <= 0:
                return ""
            import ctypes
            buf = ctypes.create_unicode_buffer(length + 2)
            ctypes.windll.user32.SendMessageW(edit_hwnd, WM_GETTEXT, length + 1, buf)
            return buf.value
        except Exception:
            return ""

    def _refresh_record_cache(self, state: Dict[str, Any]) -> None:
        """
        Read the full Notepad text, parse all records, and cache the current
        record's field→value dict in self._cached_record.
        Called once at run start and after each tab advance.
        Falls back to background UIA/OCR elements if Win32 read fails.
        """
        import re
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

    def _peek_notepad(self, state: Dict[str, Any], field_name: str) -> None:
        """
        Scroll Notepad to the line containing field_name, then hover the
        mouse over it — gives the visual impression of reading.
        Uses win32 EM_LINESCROLL so focus never leaves the form.
        Works with both classic Notepad and Windows 11 Notepad (WinUI3).
        """
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
            target_line = 0
            fl = field_name.lower()
            for i, line in enumerate(lines):
                if fl in line.lower() and ":" in line:
                    target_line = i
                    break

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

            # ── Hover mouse over the Notepad window (purely visual) ───────────
            rect = win32gui.GetWindowRect(np_hwnd)
            cx   = (rect[0] + rect[2]) / 2
            cy   = (rect[1] + rect[3]) / 2
            orig = pyautogui.position()

            pyautogui.moveTo(cx, cy, duration=0.25)
            time.sleep(0.4)
            pyautogui.moveTo(orig.x, orig.y, duration=0.2)

        except Exception:
            pass   # never block the agent over a cosmetic action

    def _lookup_field(self, field_name: str) -> str:
        """
        Look up a field value from the cached record.
        Returns empty string if not found or if value is a placeholder.
        """
        if not self._cached_record:
            return ""
        _skip_vals = {"(none)", "none", "(leave blank)", "n/a", "yes (check)",
                      "leave blank — liability only", "leave blank — owned outright"}
        rec = self._cached_record
        fl  = field_name.lower()
        val = rec.get(field_name) or next((v for k, v in rec.items() if k.lower() == fl), "")
        if not val:
            return ""
        if val.lower().strip("()") in _skip_vals:
            return ""
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

        # Skip re-filling a field we already filled this tab (prevents cycling loops
        # where the form clears numeric/spin fields on re-focus).
        if field_name in self._filled_this_tab:
            return None

        if current:
            # Field has a value — check if it's wrong (leftover from a previous run).
            # If wrong, return it flagged for overwrite (Ctrl+A before typing).
            expected_check = self._lookup_field(field_name)
            _skip_check = {"(none)", "none", "(leave blank)", "n/a", "yes (check)",
                           "leave blank — liability only", "leave blank — owned outright"}
            if (expected_check
                    and expected_check.lower().strip("()") not in _skip_check
                    and current.lower() != expected_check.lower()):
                logger.info("Auto-fill: '%s' has wrong value %r — will overwrite with %r",
                            field_name, current, expected_check)
                return (field_name, expected_check, True)   # True = needs Ctrl+A clear first
            return None   # correct value or no expected — let auto_skip handle it

        # ── Primary: use the cached full record (bypasses 2000-char UIA cap) ──
        expected = self._lookup_field(field_name)

        # ── Fallback: parse bg_blobs from UIA state ───────────────────────────
        if not expected:
            bg_elems = [e for e in elements if e.get("window_role") == "background"]
            bg_blobs = [(e.get("value") or "").strip() for e in bg_elems if e.get("value")]
            for blob in sorted(bg_blobs, key=len, reverse=True):
                r = _parse_records(blob)
                if r:
                    rec = r.get(self._record_num, r.get(min(r), {}))
                    fl  = field_name.lower()
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

        return (field_name, expected, False)   # False = field is empty, no clear needed

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
        if not current:
            return None

        field_name = (focused.get("label") or focused.get("text") or "").strip()
        if not field_name:
            return None
        expected = self._lookup_field(field_name)
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
            # Filter to FORM control types only so the scroll lands on the form,
            # not the terminal window (which can grab window_role when LLM errors occur).
            _FORM_TYPES = {
                "editcontrol", "comboboxcontrol", "checkboxcontrol",
                "radiobuttoncontrol", "tabitemcontrol", "buttoncontrol",
            }
            active = [e for e in elements
                      if e.get("type") in _FORM_TYPES
                      and e.get("window_role") != "background"
                      and e.get("bbox")]
            # Fallback: any non-background element with bbox
            if not active:
                active = [e for e in elements
                          if e.get("window_role") != "background" and e.get("bbox")]
            if not active:
                return False
            # Use centroid of active form elements, capped to the middle of the
            # screen so repeated scrolls don't push the target off the bottom edge.
            xs = [(e["bbox"][0] + e["bbox"][2]) / 2 for e in active]
            ys = [(e["bbox"][1] + e["bbox"][3]) / 2 for e in active]
            cx = sum(xs) / len(xs)
            cy = min(sum(ys) / len(ys), state.get("screen_resolution", [1920, 1080])[1] * 0.55)
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

    def _scroll_form_to_top(self, state: Dict[str, Any]) -> None:
        """
        Scroll the active form window back to the top so that _focus_first_empty_field
        always starts from the first visible field, not mid-page.
        Uses Ctrl+Home then multiple large scroll-up passes to guarantee reaching the top.
        """
        try:
            import pyautogui
            elements = state.get("elements", [])
            _FORM_TYPES = {
                "editcontrol", "comboboxcontrol", "checkboxcontrol",
                "radiobuttoncontrol", "tabitemcontrol", "buttoncontrol",
            }
            active = [e for e in elements
                      if e.get("type") in _FORM_TYPES
                      and e.get("window_role") != "background"
                      and e.get("bbox")]
            if not active:
                active = [e for e in elements
                          if e.get("window_role") != "background" and e.get("bbox")]
            if not active:
                return
            xs = [(e["bbox"][0] + e["bbox"][2]) / 2 for e in active]
            ys = [(e["bbox"][1] + e["bbox"][3]) / 2 for e in active]
            cx = sum(xs) / len(xs)
            cy = sum(ys) / len(ys)
            orig = pyautogui.position()
            pyautogui.moveTo(cx, cy, duration=0.15)
            # Three large upward scrolls to handle tall tabs like Policyholder
            for _ in range(3):
                pyautogui.scroll(50)
                time.sleep(0.05)
            pyautogui.moveTo(orig.x, orig.y, duration=0.1)
            logger.info("Scroll-form-top: scrolled to top at (%.0f, %.0f)", cx, cy)
        except Exception as exc:
            logger.warning("Scroll-form-top: failed — %s", exc)

    def _focus_first_empty_field(
        self,
        state:        Dict[str, Any],
        after_scroll: bool = False,
        min_y:        float = 0,
    ) -> bool:
        """
        Click the first empty editcontrol on the current tab.
        Returns True if a field was found and clicked, False otherwise.

        after_scroll=True : skip fields in _filled_this_tab (already filled, may look empty in UIA).
        min_y             : only consider fields whose top-edge y >= min_y (used by pane-escape
                            to avoid jumping back to already-filled sections above the pane).
        """
        elements = state.get("elements", [])
        # Compute actual tab-strip bottom so clicks never land on the tab bar.
        # Used to clip cy (not to filter fields) because the field may start just
        # inside the tab strip but extend below it — the click should land lower.
        _tab_bottoms = [
            e["bbox"][3] for e in elements
            if e.get("type") in ("tabitem", "tabitemcontrol")
            and e.get("window_role") != "background"
            and e.get("bbox")
        ]
        _tab_floor = (max(_tab_bottoms) + 5) if _tab_bottoms else 110
        _min_y = max(100, min_y)   # field selection: exclude title/scrollbar area
        fillable = [
            e for e in elements
            if e.get("window_role") != "background"
            and e.get("type") == "editcontrol"
            and not (e.get("value") or "").strip()
            and e.get("bbox")
            and e.get("enabled", True)
            and e["bbox"][1] >= _min_y
        ]
        if after_scroll and self._filled_this_tab:
            filled_lower = {s.lower() for s in self._filled_this_tab}
            fillable = [
                e for e in fillable
                if (e.get("label") or e.get("text") or "").strip().lower() not in filled_lower
            ]
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
                _edit = _root.EditControl(searchDepth=10, Name=field_label)
                if _edit.Exists(maxSearchSeconds=0.3):
                    _edit.SetFocus()
                    logger.info("Tab-advance focus: UIA SetFocus on %r", field_label)
                    return True
            except Exception:
                pass  # fall through to coordinate click
        # Fallback: coordinate click, clipped to be within the field and below the tab strip
        cy = max(cy, _tab_floor)
        cy = min(cy, y2 - 2)
        logger.info("Tab-advance focus: clicking first empty field %r @ (%.0f, %.0f)", field_label, cx, cy)
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

        next_idx = self._current_tab_idx + 1
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

        # Hotkey / scroll — pure LLM reasoning, transformer can't help
        if l_type in ("hotkey", "scroll"):
            return self._llm_action_to_prediction(llm_action, state)

        # Type — LLM provides the value; transformer source_elem_idx as backup
        if l_type == "type":
            text = llm_action.get("text", "")
            if not text:
                # Try transformer's source pointer as backup
                src_idx = t_pred.get("source_elem_idx", -1)
                text = self._text_resolver.resolve(state, source_elem_idx=src_idx)
            if not text:
                # No value found — Tab to skip
                return {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
            return {"action_type": "keyboard", "key_count": len(text),
                    "keystrokes": list(text), "text": text}

        # Click — use transformer's learned position when confident,
        # but never let it snap to a tab/navigation element (that causes tab-jumping)
        if l_type == "click":
            _TRANSFORMER_CLICK_THRESHOLD = 0.55
            if (t_pred.get("action_type") == "click"
                    and t_conf >= _TRANSFORMER_CLICK_THRESHOLD
                    and t_pred.get("click_position")):
                pos     = t_pred["click_position"]
                snapped = self._snap(pos, state)
                coords  = snapped or pos
                logger.info("Merge: transformer click @ (%.0f,%.0f)  conf=%.2f",
                            coords[0], coords[1], t_conf)
                return {"action_type": "click", "click_position": coords}
            # Transformer not confident — fall back to LLM label resolution
            return self._llm_action_to_prediction(llm_action, state)

        # Fallback
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
        screen_text = _state_to_text(state, record_num=self._record_num)
        logger.debug("LLM screen context:\n%s", screen_text)

        # Build focused-field banner — put it FIRST so the LLM sees it immediately
        focused_banner = ""
        focused_id = state.get("focused_element_id")
        if focused_id:
            focused_el = next(
                (e for e in state.get("elements", []) if e.get("element_id") == focused_id), None
            )
            if focused_el:
                _fn = (focused_el.get("label") or focused_el.get("text") or "?").strip()
                _fv = (focused_el.get("value") or "").strip()
                _ft = focused_el.get("type", "?")
                focused_banner = (
                    f"⚠ CURRENTLY FOCUSED FIELD: [{_ft}] \"{_fn}\""
                    + (f" — current value: {_fv!r}" if _fv else " — EMPTY")
                    + "\nYou MUST act on THIS field only. Do NOT click other fields.\n\n"
                )

        # Build a compact already-filled summary so the LLM doesn't revisit fields
        filled_fields = []
        for e in state.get("elements", []):
            if e.get("window_role") == "background":
                continue
            val = (e.get("value") or "").strip()
            lbl = (e.get("label") or e.get("text") or "").strip()
            if val and lbl:
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
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return _parse_llm_response(resp.content[0].text)

    def _call_openai_compat(self, user_msg: str) -> Dict[str, Any]:
        """Handles both Groq and LM Studio — both use the OpenAI client format."""
        import uuid
        # Unique tag per call breaks LM Studio's server-side KV-cache accumulation
        system_msg = _SYSTEM_PROMPT + f"\n[sid:{uuid.uuid4().hex[:12]}]"
        resp = self._llm_client.chat.completions.create(
            model=self._llm_model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
        )
        return _parse_llm_response(resp.choices[0].message.content)

    def _call_gemini(self, user_msg: str) -> Dict[str, Any]:
        resp = self._llm_client.generate_content(user_msg)
        return _parse_llm_response(resp.text)

    # ── observer / transformer helpers ───────────────────────────────────────

    def _observe(self) -> Dict[str, Any]:
        try:
            state = self._observer.snapshot()
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

    def _predict(self, state: Dict[str, Any]) -> Dict[str, Any]:
        try:
            try:
                from components.intelligence.model.transformer import predict
            except ImportError:
                from intelligence.model.transformer import predict
            return predict(
                state=state,
                history=self._history[-3:],
                model_path=self.model_path,
                device_str=self.device_str,
            )
        except Exception as exc:
            logger.warning("Transformer error: %s — no_op.", exc)
            return {"action_type": "no_op"}
