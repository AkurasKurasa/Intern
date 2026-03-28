"""
recorder/planner/planner.py
===================================
Reasoning layer that sits between screen observation and action execution.

The Planner combines two signals to decide the next action:
  1. TransformerAgentNetwork  — fast, learned prior from demonstrations
  2. LLM (optional)           — slow, reasoning-based override

When an LLM is configured the planner uses it to sanity-check the
transformer's prediction against the task goal and current screen
context.  If the LLM disagrees it can override the action type or
provide a different target.

When no LLM is configured (provider="none") the transformer prediction
is returned directly — this is the default zero-cost path.

Supported LLM providers (same as LLMAgent)
-------------------------------------------
  none       — transformer only, no API calls
  lmstudio   — local LM Studio (OpenAI-compatible, free, offline)
  anthropic  — Claude API (paid)
  groq       — Groq API, Llama 3.3 70B (free tier)
  gemini     — Google Gemini Flash (free tier)

Usage
-----
    planner = Planner(
        goal       = "Fill the car insurance form",
        model_path = "data/models/fill_insurance.pt",
        provider   = "lmstudio",
    )

    state    = observer.snapshot()
    decision = planner.plan(state)

    print(decision.action_type)   # "click" | "keyboard"
    print(decision.click_position)
    print(decision.text)
    print(decision.confidence)
    print(decision.reasoning)     # LLM explanation (empty if provider="none")
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── path setup ─────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))   # planner/
_SO   = os.path.dirname(_HERE)                        # recorder/
_COMP = os.path.dirname(_SO)                          # components/
_ROOT = os.path.dirname(_COMP)                        # Intern/
for _p in (_ROOT, _COMP,
           os.path.join(_COMP, "learning_models", "transformer")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── System prompt for the LLM ─────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are an automation planner.

You receive:
  - GOAL: the task the agent is trying to complete
  - STATE: visible UI elements (name, type, focused, value)
  - HISTORY: last few actions taken
  - PREDICTION: the transformer model's suggested next action

Your job is to decide whether to FOLLOW the prediction or OVERRIDE it.

Reply with a single JSON object:
{
  "decision":  "follow" | "override",
  "action_type": "click" | "keyboard",
  "reasoning": "one sentence explanation",
  "target_element": "element name or empty string",
  "text": "text to type, or empty string"
}

Rules:
- If the prediction looks correct given the goal and state, reply with "follow".
- Only override if the prediction is clearly wrong (wrong field, wrong app, stuck).
- Keep reasoning short (one sentence max).
- Return ONLY the JSON object. No markdown. No explanation outside the JSON."""


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class PlannerDecision:
    action_type:    str              = "no_op"
    click_position: List[float]      = field(default_factory=lambda: [0.0, 0.0])
    key_count:      int              = 0
    text:           str              = ""
    confidence:     float            = 0.0
    reasoning:      str              = ""
    source:         str              = "transformer"   # "transformer" | "llm"
    raw_prediction: Dict[str, Any]   = field(default_factory=dict)


# ── Planner ───────────────────────────────────────────────────────────────────

class Planner:
    """
    Combines transformer predictions with optional LLM reasoning.

    Parameters
    ----------
    goal       : Natural-language description of the task.
    model_path : Path to the trained transformer checkpoint (.pt file).
    provider   : LLM provider (see module docstring).  Default "none".
    api_key    : API key for cloud providers.
    lmstudio_url : Base URL for LM Studio.  Default http://localhost:1234.
    model_id   : LLM model identifier (provider-specific).
    llm_every  : Call the LLM every N steps (1 = every step).  Default 3.
    device_str : "auto" | "cpu" | "cuda".
    """

    def __init__(
        self,
        goal:          str   = "",
        model_path:    str   = "data/models/transformer_bc.pt",
        provider:      str   = "none",
        api_key:       str   = "",
        lmstudio_url:  str   = "http://localhost:1234",
        model_id:      str   = "",
        llm_every:     int   = 3,
        device_str:    str   = "auto",
    ):
        self.goal         = goal
        self.model_path   = model_path
        self.provider     = provider.lower()
        self.api_key      = api_key
        self.lmstudio_url = lmstudio_url
        self.model_id     = model_id
        self.llm_every    = llm_every
        self.device_str   = device_str

        self._step         = 0
        self._history:  List[Dict] = []
        self._llm_client           = None

        self._init_llm()

    # ── Main API ───────────────────────────────────────────────────────────────

    def plan(self, state: dict, history: Optional[List[dict]] = None) -> PlannerDecision:
        """
        Given the current screen state return the next action to take.

        Parameters
        ----------
        state   : Screen state dict from UIAutomationObserver or VisionObserver.
        history : Optional list of past action dicts (overrides internal history).
        """
        hist = history if history is not None else self._history

        # Step 1: transformer prediction
        raw = self._transformer_predict(state, hist)
        decision = self._raw_to_decision(raw, source="transformer")

        # Step 2: optional LLM override
        if self.provider != "none" and (self._step % self.llm_every == 0):
            decision = self._llm_evaluate(state, hist, decision)

        self._step += 1
        self._history.append(decision.raw_prediction)
        if len(self._history) > 10:
            self._history = self._history[-10:]

        return decision

    def reset(self):
        """Reset step counter and history (call before starting a new task run)."""
        self._step   = 0
        self._history.clear()

    # ── Transformer ────────────────────────────────────────────────────────────

    def _transformer_predict(self, state: dict, history: list) -> dict:
        try:
            from transformer import predict as _predict
            return _predict(
                state      = state,
                history    = history,
                model_path = self.model_path,
                device_str = self.device_str,
            )
        except Exception as exc:
            logger.warning("Planner: transformer predict failed — %s", exc)
            return {"action_type": "no_op", "click_position": [0, 0],
                    "key_count": 0, "text": ""}

    @staticmethod
    def _raw_to_decision(raw: dict, source: str = "transformer") -> PlannerDecision:
        return PlannerDecision(
            action_type    = raw.get("action_type", "no_op"),
            click_position = raw.get("click_position", [0.0, 0.0]),
            key_count      = raw.get("key_count", 0),
            text           = raw.get("text", ""),
            confidence     = raw.get("confidence", 0.0),
            reasoning      = "",
            source         = source,
            raw_prediction = raw,
        )

    # ── LLM evaluation ─────────────────────────────────────────────────────────

    def _llm_evaluate(
        self,
        state:    dict,
        history:  list,
        decision: PlannerDecision,
    ) -> PlannerDecision:
        """Ask the LLM whether to follow or override the transformer prediction."""
        prompt = self._build_prompt(state, history, decision)
        try:
            response = self._call_llm(prompt)
            parsed   = self._parse_llm(response)
        except Exception as exc:
            logger.warning("Planner: LLM call failed — %s", exc)
            return decision

        if parsed.get("decision") == "follow":
            decision.reasoning = parsed.get("reasoning", "")
            return decision

        # Override
        override = PlannerDecision(
            action_type    = parsed.get("action_type", decision.action_type),
            click_position = decision.click_position,   # LLM doesn't give coordinates
            key_count      = decision.key_count,
            text           = parsed.get("text", decision.text),
            confidence     = 1.0,
            reasoning      = parsed.get("reasoning", ""),
            source         = "llm",
            raw_prediction = decision.raw_prediction,
        )
        logger.info("Planner: LLM overrode transformer — %s", override.reasoning)
        return override

    # ── Prompt builders ────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        state:    dict,
        history:  list,
        decision: PlannerDecision,
    ) -> str:
        state_lines = []
        for e in state.get("elements", [])[:20]:
            if e.get("window_role") == "background":
                continue
            state_lines.append(
                f"  [{e.get('type','?')}] {e.get('text','')!r}"
                f"  focused={e.get('element_id') == state.get('focused_element_id')}"
                f"  value={e.get('value','')!r}"
            )

        hist_lines = []
        for h in history[-5:]:
            hist_lines.append(
                f"  {h.get('action_type','?')} "
                f"pos={h.get('click_position','')} "
                f"text={h.get('text','')!r}"
            )

        pred_line = (
            f"action_type={decision.action_type}  "
            f"click_position={decision.click_position}  "
            f"text={decision.text!r}"
        )

        return (
            f"GOAL: {self.goal}\n\n"
            f"STATE:\n" + "\n".join(state_lines or ["  (no elements)"]) + "\n\n"
            f"HISTORY:\n" + "\n".join(hist_lines or ["  (none)"]) + "\n\n"
            f"PREDICTION: {pred_line}"
        )

    @staticmethod
    def _parse_llm(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            return {"decision": "follow", "reasoning": "unparseable response"}

    # ── LLM provider dispatch ──────────────────────────────────────────────────

    def _init_llm(self):
        if self.provider == "none":
            return
        if self.provider == "lmstudio":
            try:
                from openai import OpenAI
                self._llm_client = OpenAI(
                    base_url = f"{self.lmstudio_url}/v1",
                    api_key  = "lm-studio",
                )
                logger.info("Planner: LM Studio client initialised at %s", self.lmstudio_url)
            except ImportError:
                logger.warning("Planner: openai package not found — pip install openai")
        elif self.provider == "anthropic":
            try:
                import anthropic
                self._llm_client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                logger.warning("Planner: anthropic package not found — pip install anthropic")
        elif self.provider in ("groq", "gemini"):
            logger.info("Planner: provider '%s' — using HTTP fallback", self.provider)

    def _call_llm(self, prompt: str) -> str:
        if self.provider == "lmstudio" and self._llm_client:
            resp = self._llm_client.chat.completions.create(
                model       = self.model_id or "local-model",
                messages    = [
                    {"role": "system",  "content": _SYSTEM_PROMPT},
                    {"role": "user",    "content": prompt},
                ],
                max_tokens  = 256,
                temperature = 0.0,
            )
            return resp.choices[0].message.content

        if self.provider == "anthropic" and self._llm_client:
            resp = self._llm_client.messages.create(
                model      = self.model_id or "claude-haiku-4-5-20251001",
                max_tokens = 256,
                system     = _SYSTEM_PROMPT,
                messages   = [{"role": "user", "content": prompt}],
            )
            return resp.content[0].text

        if self.provider == "groq":
            return self._http_llm(
                url     = "https://api.groq.com/openai/v1/chat/completions",
                model   = self.model_id or "llama-3.3-70b-versatile",
                prompt  = prompt,
                headers = {"Authorization": f"Bearer {self.api_key}"},
            )

        if self.provider == "gemini":
            return self._http_llm(
                url    = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{self.model_id or 'gemini-2.0-flash'}:generateContent"
                    f"?key={self.api_key}"
                ),
                model  = "",
                prompt = prompt,
                gemini = True,
            )

        raise RuntimeError(f"Planner: no LLM client for provider '{self.provider}'")

    @staticmethod
    def _http_llm(
        url:     str,
        model:   str,
        prompt:  str,
        headers: Optional[dict] = None,
        gemini:  bool = False,
    ) -> str:
        import urllib.request

        if gemini:
            body = json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 256},
            }).encode()
        else:
            body = json.dumps({
                "model":    model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                "max_tokens":  256,
                "temperature": 0.0,
            }).encode()

        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)

        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        if gemini:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        return data["choices"][0]["message"]["content"]
