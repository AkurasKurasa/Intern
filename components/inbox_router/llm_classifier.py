"""
components/inbox_router/llm_classifier.py
============================================
The fallback/tiebreaker layer -- only called when rules.py's RuleLayer
returns an empty decision. Same multi-provider shape as
components/agent/agent.py's LLMAgent (same .env-driven provider selection,
same markdown-fence/<think>-stripping JSON extraction), deliberately
re-implemented here rather than importing LLMAgent: LLMAgent's own
provider-calling methods are private, baked into an 8,700-line class shaped
entirely around GUI click/type actions, not a generic classification
prompt. This is the same "reuse the convention, not the class" choice this
project already made when Scope #2 was integrated (see components/scope2/).
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from gmail_client import EmailMessage
from pattern_profile import SenderPattern
from routing_rules import DECISIONS, RuleDecision

try:
    import anthropic as _anthropic
    _ANTHROPIC_OK = True
except ImportError:
    _ANTHROPIC_OK = False

try:
    from google import genai as _genai
    _GEMINI_OK = True
except ImportError:
    _GEMINI_OK = False

try:
    from openai import OpenAI as _OpenAI   # also used for Groq (OpenAI-compatible endpoint)
    _OPENAI_OK = True
except ImportError:
    _OPENAI_OK = False

_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "groq":      "llama-3.3-70b-versatile",
    "gemini":    "gemini-2.0-flash",
    "lmstudio":  "local-model",
}
_GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_SYSTEM_PROMPT = (
    "You triage a single email for a real person. Choose exactly one of these outcomes:\n"
    "  route_scope1 - hand off to a GUI form-filling assistant (data entry / intake tasks)\n"
    "  route_scope2 - hand off to a spreadsheet-to-portal matcher (grades/rosters/sheets)\n"
    "  reply        - the user should reply directly\n"
    "  forward      - the user should forward this to someone else\n"
    "  flag         - this needs a person's judgment, don't act automatically\n"
    "  leave_alone  - no action needed\n"
    "You are given the email, what's known about how this same sender has been "
    "handled before, and which capsules (if any) are registered. Respond with ONLY "
    "a JSON object: "
    '{"decision": "...", "confidence": 0.0-1.0, "rationale": "one short sentence", '
    '"capsule_name": "" , "forward_to": ""}. '
    "capsule_name is only meaningful for route_scope1/route_scope2 and must be one of "
    "the registered capsule names you were given, or empty if unsure."
)


@dataclass
class ClassificationResult:
    decision: str = "flag"
    confidence: float = 0.0
    rationale: str = ""
    capsule_name: str = ""
    forward_to: str = ""


def _parse_llm_response(raw: str) -> dict:
    """Same convention as agent.py's _parse_llm_response: strip markdown
    fences and <think> blocks, then json.loads()."""
    raw = raw.strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


class LLMClassifier:
    def __init__(self, provider: str = "none", api_key: str = "", model_id: str = "",
                 lmstudio_url: str = "http://localhost:1234/v1") -> None:
        self.provider = provider
        self._llm_model = model_id or _DEFAULT_MODELS.get(provider, "")
        self._llm_client = None
        self._init_provider(api_key, lmstudio_url)

    def _init_provider(self, api_key: str, lmstudio_url: str) -> None:
        p = self.provider
        if p == "anthropic":
            if not _ANTHROPIC_OK:
                return
            key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if key:
                self._llm_client = _anthropic.Anthropic(api_key=key)
        elif p == "groq":
            if not _OPENAI_OK:
                return
            key = api_key or os.environ.get("GROQ_API_KEY", "")
            if key:
                self._llm_client = _OpenAI(base_url=_GROQ_BASE_URL, api_key=key)
        elif p == "gemini":
            if not _GEMINI_OK:
                return
            key = api_key or os.environ.get("GEMINI_API_KEY", "")
            if key:
                self._llm_client = _genai.Client(api_key=key)
        elif p == "lmstudio":
            if not _OPENAI_OK:
                return
            self._llm_client = _OpenAI(base_url=lmstudio_url, api_key="lm-studio")
        # "none" or unknown provider -> self._llm_client stays None

    @property
    def available(self) -> bool:
        return self._llm_client is not None

    def classify(self, message: EmailMessage, pattern: Optional[SenderPattern],
                 rule_hint: RuleDecision, capsule_hints: List[dict]) -> ClassificationResult:
        if not self.available:
            return ClassificationResult(
                decision="flag", confidence=0.0,
                rationale="No LLM provider configured — flagged for a person to decide.",
            )
        user_msg = self._build_prompt(message, pattern, capsule_hints)
        try:
            if self.provider == "anthropic":
                raw = self._call_anthropic(user_msg)
            elif self.provider in ("groq", "lmstudio"):
                raw = self._call_openai_compat(user_msg)
            elif self.provider == "gemini":
                raw = self._call_gemini(user_msg)
            else:
                raw = "{}"
            parsed = _parse_llm_response(raw)
        except Exception as exc:
            return ClassificationResult(
                decision="flag", confidence=0.0,
                rationale=f"LLM classification failed ({exc}) — flagged for a person to decide.",
            )
        decision = parsed.get("decision", "flag")
        if decision not in DECISIONS:
            decision = "flag"
        return ClassificationResult(
            decision=decision,
            confidence=float(parsed.get("confidence", 0.5) or 0.5),
            rationale=str(parsed.get("rationale", "")),
            capsule_name=str(parsed.get("capsule_name", "") or ""),
            forward_to=str(parsed.get("forward_to", "") or ""),
        )

    def draft_message(self, message: EmailMessage, decision: str, forward_to: str = "") -> str:
        """Only called at confirm-time (see router.py), never during
        classify() -- generating draft text is a separate, more expensive
        step from deciding WHAT to do, and it's wasted work for every
        suggestion the user never confirms."""
        if not self.available:
            return ""
        if decision == "forward":
            prompt = (f"Write a brief one-sentence forwarding note for this email, "
                      f"to be sent to {forward_to or 'a colleague'}.\n\n"
                      f"Subject: {message.subject}\n\n{message.body_text}")
        else:
            prompt = (f"Write a brief, plain-language reply to this email, in the "
                      f"user's voice, 2-4 sentences.\n\n"
                      f"Subject: {message.subject}\n\n{message.body_text}")
        try:
            if self.provider == "anthropic":
                return self._call_anthropic_text(prompt)
            if self.provider in ("groq", "lmstudio"):
                return self._call_openai_compat_text(prompt)
            if self.provider == "gemini":
                return self._call_gemini_text(prompt)
        except Exception:
            return ""
        return ""

    def _build_prompt(self, message: EmailMessage, pattern: Optional[SenderPattern],
                       capsule_hints: List[dict]) -> str:
        pattern_desc = "No prior history with this sender."
        if pattern is not None and pattern.total() > 0:
            pattern_desc = (f"Prior history with {pattern.sender_domain}: "
                            f"replied {pattern.reply_count}x, forwarded {pattern.forward_count}x, "
                            f"left alone {pattern.ignore_count}x.")
        capsules_desc = "\n".join(
            f"  - {c.get('name')}: {c.get('description', '')}" for c in capsule_hints
        ) or "  (none registered)"
        return (
            f"EMAIL\nFrom: {message.sender}\nSubject: {message.subject}\n\n"
            f"{message.body_text[:2000]}\n\n"
            f"PATTERN PROFILE\n{pattern_desc}\n\n"
            f"REGISTERED CAPSULES\n{capsules_desc}"
        )

    def _call_anthropic(self, user_msg: str) -> str:
        resp = self._llm_client.messages.create(
            model=self._llm_model, max_tokens=256,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return resp.content[0].text

    def _call_openai_compat(self, user_msg: str) -> str:
        resp = self._llm_client.chat.completions.create(
            model=self._llm_model, max_tokens=300,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        return resp.choices[0].message.content

    def _call_gemini(self, user_msg: str) -> str:
        resp = self._llm_client.models.generate_content(
            model=self._llm_model, contents=f"{_SYSTEM_PROMPT}\n\n{user_msg}",
        )
        return resp.text

    def _call_anthropic_text(self, prompt: str) -> str:
        resp = self._llm_client.messages.create(
            model=self._llm_model, max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()

    def _call_openai_compat_text(self, prompt: str) -> str:
        resp = self._llm_client.chat.completions.create(
            model=self._llm_model, max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content.strip()

    def _call_gemini_text(self, prompt: str) -> str:
        resp = self._llm_client.models.generate_content(model=self._llm_model, contents=prompt)
        return resp.text.strip()
