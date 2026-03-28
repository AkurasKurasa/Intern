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
  "action_type": "click" | "type" | "hotkey" | "done" | "wait",
  "target":      "<copy the EXACT string from FORM LABELS or FORM INPUTS>",
  "text":        "<exact value copied from BACKGROUND DATA — never invent values>",
  "keys":        ["ctrl", "a"],
  "reason":      "<one sentence>"
}

Rules:
- The form is ALREADY the active window. Do NOT try to click the window title
  or focus the window — go straight to filling the first empty field.
- "click"  → focus a field. Set "target" to the EXACT label text from FORM LABELS.
             Do not paraphrase, invent, or guess a name — copy it exactly.
- "type"   → type into the currently focused field. Set "text" to the EXACT value
             from BACKGROUND DATA. Never invent or guess values.
- "hotkey" → keyboard shortcut, e.g. keys: ["tab"] to advance to the next field.
- "done"   → all fields are fully filled and the task is complete.
- "wait"   → UI is still loading.
- Pattern: click label → type value → hotkey ["tab"] → repeat for next field.
- If a field has no matching value in BACKGROUND DATA, skip it with keys: ["tab"].
- Do NOT output anything outside the JSON object.
"""


# ══════════════════════════════════════════════════════════════════════════════
#  State / history → text helpers
# ══════════════════════════════════════════════════════════════════════════════

def _state_to_text(state: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"Active window: {state.get('application','?')} — {state.get('window_title','')!r}")

    for w in state.get("windows", []):
        lines.append(
            f"  [{w.get('role','?')}] {w.get('app','')} — "
            f"{w.get('title','')!r}  ({w.get('element_count',0)} elements)"
        )

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

    # Form elements = any window that contains interactive inputs.
    # This works even when the terminal has OS focus (form is "background").
    form_windows = {
        e.get("window_title") for e in all_elems
        if e.get("type") in _INTERACTIVE
    }

    # Fallback: if no interactive elements detected anywhere, treat the active
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
    lines.append(f"[debug] form element types: {all_types}")

    lines.append("FORM LABELS (use EXACT strings as 'target' when clicking a field):")
    for e in labels[:60]:
        focused = " [FOCUSED]" if e.get("focused") else ""
        lines.append(f"  \"{(e.get('text') or '').strip()}\"{focused}")

    lines.append(f"\nFORM INPUTS ({len(interactive)} interactive elements):")
    for e in interactive[:60]:
        focused = " [FOCUSED]" if e.get("focused") else ""
        val   = (e.get("value") or "").strip()
        text  = (e.get("text") or "").strip()
        label = text or val or "(empty)"
        lines.append(f"  [{e.get('type','?')}] \"{label}\"{focused}"
                     + (f"  current value: {val!r}" if val else ""))

    if data_elems:
        lines.append(f"\nBACKGROUND DATA (values to type — read from here):")
        for e in data_elems[:60]:
            text = (e.get("text") or e.get("value") or "").strip()
            if text:
                lines.append(f"  {text}")

    return "\n".join(lines)


def _history_to_text(history: List[Dict[str, Any]]) -> str:
    if not history:
        return "No actions taken yet."
    lines = []
    for i, h in enumerate(history[-8:], 1):
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
            from components.ui_observer.ui_observer import UIAutomationObserver
        except ImportError:
            from ui_observer.ui_observer import UIAutomationObserver

        try:
            from components.recorder.state_validator import StateValidator
            from components.recorder.correction_handler import CorrectionHandler
        except ImportError:
            from recorder.state_validator import StateValidator
            from recorder.correction_handler import CorrectionHandler

        self._executor          = ActionExecutor(dry_run=dry_run)
        self._text_resolver     = _TextResolver()
        self._observer          = UIAutomationObserver()
        self._validator         = StateValidator()
        self._correction        = CorrectionHandler()
        self._history:  List[Dict[str, Any]] = []
        self._results:  List[Dict[str, Any]] = []
        self._guidance: str = ""
        self._task_name: str = ""   # set via run(task_name=...)

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

        _stuck_pos:   Optional[tuple] = None
        _stuck_count: int             = 0
        _STUCK_LIMIT: int             = 3

        for step_idx in range(n):
            # 1. Observe
            state      = self._observe()
            llm_action: Dict[str, Any] = {}
            logger.info("── Step %d/%d  (%d elements) ──", step_idx + 1, n, len(state.get("elements", [])))

            # 2. LLM drives the action (if provider is set)
            if self._llm_client:
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

                prediction = self._llm_action_to_prediction(llm_action, state)

            # 2b. Transformer fallback (provider="none")
            else:
                prediction = self._predict(state)
                logger.info("Transformer → %s", prediction)

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

            # 3. Execute
            result = self._executor.execute(prediction)
            logger.info("%s", result)

            # 4. Validate
            state_after = self._observe()
            validation  = self._validator.validate(state, state_after, prediction)
            logger.info("Validator → %s: %s", validation.status, validation.reason)

            if validation.status == "done":
                logger.info("StateValidator: task appears complete.")
                break

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
                "target":      llm_action.get("target", "") if self._llm_client else "",
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

        logger.info("LLMAgent finished — %d step(s).", len(self._results))
        return list(self._results)

    @property
    def results(self) -> List[Dict[str, Any]]:
        return list(self._results)

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    # ── LLM dispatch ─────────────────────────────────────────────────────────

    def _ask_llm(self, state: Dict[str, Any]) -> Dict[str, Any]:
        screen_text = _state_to_text(state)
        logger.info("LLM screen context:\n%s", screen_text)
        user_msg = (
            f"Task goal: {self.goal}\n\n"
            f"Current screen:\n{screen_text}\n\n"
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
                logger.warning("LLM 'type' action has no text — skipping.")
                return {"action_type": "no_op"}
            return {"action_type": "keyboard", "key_count": len(text),
                    "keystrokes": list(text), "text": text}

        elif action_type == "hotkey":
            keys = llm_action.get("keys", [])
            if not keys:
                return {"action_type": "no_op"}
            return {"action_type": "keyboard", "key_count": len(keys),
                    "keystrokes": keys}

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
        resp = self._llm_client.chat.completions.create(
            model=self._llm_model,
            max_tokens=256,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
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
                return state
        except Exception as exc:
            logger.warning("Observer error: %s", exc)
        return {"elements": [], "screen_resolution": [1920, 1080]}

    def _predict(self, state: Dict[str, Any]) -> Dict[str, Any]:
        try:
            try:
                from components.learning_models.transformer.transformer import predict
            except ImportError:
                from learning_models.transformer.transformer import predict
            return predict(
                state=state,
                history=self._history[-3:],
                model_path=self.model_path,
                device_str=self.device_str,
            )
        except Exception as exc:
            logger.warning("Transformer error: %s — no_op.", exc)
            return {"action_type": "no_op"}
