"""
llm/providers.py
================
Unified LLM provider interface for Intern.

Supported providers
-------------------
  anthropic  — Claude (claude-sonnet-4-6)          paid
  groq       — Llama 3.3 70B via Groq API          free tier
  gemini     — Google Gemini Flash                  free tier
  lmstudio   — Local LM Studio (OpenAI-compatible)  fully free, offline
  none       — No LLM, transformer-only fallback    free

All providers share one interface:
  provider.evaluate(goal, state_text, history_text) -> LLMDecision
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── optional SDK imports ───────────────────────────────────────────────────────
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
    from openai import OpenAI as _OpenAI
    _OPENAI_OK = True
except ImportError:
    _OPENAI_OK = False

# ── default model IDs ─────────────────────────────────────────────────────────
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "groq":      "llama-3.3-70b-versatile",
    "gemini":    "gemini-1.5-flash",
    "lmstudio":  "local-model",
}

SYSTEM_PROMPT = """\
You are an automation supervisor for Intern, an AI desktop agent.
You monitor screen state and decide whether the agent should continue, stop, or wait.

You receive:
  1. The task goal.
  2. Current screen elements (active window + background source window).
  3. Recent action history.

Respond with ONLY a JSON object:
{
  "status":   "continue" | "done" | "error" | "wait",
  "reason":   "<one sentence>",
  "guidance": "<one sentence for next action, or empty if done/error>"
}

- "done"     -> task goal fully completed.
- "error"    -> something went wrong (wrong app, unexpected dialog).
- "wait"     -> UI is loading; agent should pause.
- "continue" -> task in progress; give guidance.
- Max 20 words each for reason and guidance.
- No output outside the JSON object.
"""


@dataclass
class LLMDecision:
    status:   str   # continue | done | error | wait
    reason:   str
    guidance: str

    @classmethod
    def continue_(cls, guidance: str = "") -> "LLMDecision":
        return cls("continue", "proceeding", guidance)

    @classmethod
    def fallback(cls) -> "LLMDecision":
        return cls("continue", "llm unavailable", "")


class LLMProvider:
    """
    Unified LLM interface — swap providers without changing any other code.

    Parameters
    ----------
    provider     : "anthropic" | "groq" | "gemini" | "lmstudio" | "none"
    api_key      : API key (not needed for lmstudio / none).
    model_id     : Override default model for the provider.
    lmstudio_url : Base URL for LM Studio (default: http://localhost:1234/v1).
    """

    def __init__(
        self,
        provider:     str           = "none",
        api_key:      Optional[str] = None,
        model_id:     Optional[str] = None,
        lmstudio_url: str           = "http://localhost:1234/v1",
    ):
        self.provider = provider.lower().strip()
        self.model    = model_id or DEFAULT_MODELS.get(self.provider, "")
        self._client: Optional[Any] = None
        self._init(api_key or "", lmstudio_url)

    # ── public ────────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._client is not None

    def evaluate(self, goal: str, state_text: str, history_text: str) -> LLMDecision:
        """Ask the LLM to evaluate screen state against the goal."""
        if not self.available:
            return LLMDecision.fallback()

        user_msg = (
            f"Task goal: {goal}\n\n"
            f"Current screen:\n{state_text}\n\n"
            f"Recent actions:\n{history_text}"
        )
        try:
            raw = self._call(user_msg)
            data = _parse(raw)
            return LLMDecision(
                status   = data.get("status", "continue"),
                reason   = data.get("reason", ""),
                guidance = data.get("guidance", ""),
            )
        except Exception as exc:
            logger.warning("LLMProvider[%s] error: %s", self.provider, exc)
            return LLMDecision.fallback()

    def infer_goal(self, state_text: str) -> str:
        """
        Infer the task goal from what's currently visible on screen.
        Returns a natural-language goal string, or empty string if inference fails.
        """
        if not self.available:
            return ""
        prompt = (
            "Look at these open windows and infer what data entry or automation "
            "task the user is trying to accomplish. Reply with one clear sentence "
            "describing the goal, nothing else.\n\n"
            f"Screen state:\n{state_text}"
        )
        try:
            return self._call(prompt).strip()
        except Exception as exc:
            logger.warning("Goal inference failed: %s", exc)
            return ""

    # ── provider initialisation ───────────────────────────────────────────────

    def _init(self, api_key: str, lmstudio_url: str) -> None:
        p = self.provider

        if p == "anthropic":
            if not _ANTHROPIC_OK:
                logger.warning("anthropic package not installed.")
                return
            key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                logger.warning("No Anthropic API key.")
                return
            self._client = _anthropic.Anthropic(api_key=key)

        elif p == "groq":
            if not _GROQ_OK:
                logger.warning("groq package not installed.")
                return
            key = api_key or os.environ.get("GROQ_API_KEY", "")
            if not key:
                logger.warning("No Groq API key.")
                return
            self._client = _Groq(api_key=key)

        elif p == "gemini":
            if not _GEMINI_OK:
                logger.warning("google-generativeai not installed.")
                return
            key = api_key or os.environ.get("GEMINI_API_KEY", "")
            if not key:
                logger.warning("No Gemini API key.")
                return
            _genai.configure(api_key=key)
            self._client = _genai.GenerativeModel(
                model_name=self.model,
                system_instruction=SYSTEM_PROMPT,
            )

        elif p == "lmstudio":
            if not _OPENAI_OK:
                logger.warning("openai package not installed (needed for LM Studio).")
                return
            self._client = _OpenAI(base_url=lmstudio_url, api_key="lm-studio")

        elif p == "none":
            pass   # transformer-only, no LLM

        else:
            logger.warning("Unknown provider %r.", p)

        if self._client:
            logger.info("LLMProvider: %s  model=%s", p, self.model)

    # ── provider calls ────────────────────────────────────────────────────────

    def _call(self, user_msg: str) -> str:
        p = self.provider
        if p == "anthropic":
            resp = self._client.messages.create(
                model=self.model, max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            return resp.content[0].text

        elif p in ("groq", "lmstudio"):
            resp = self._client.chat.completions.create(
                model=self.model, max_tokens=256,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
            )
            return resp.choices[0].message.content

        elif p == "gemini":
            return self._client.generate_content(user_msg).text

        return ""


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
