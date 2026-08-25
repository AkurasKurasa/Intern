"""
components/inbox_router/routing_rules.py
==========================================
Deterministic first pass. If a rule confidently matches, that's the
decision -- no LLM call needed, cheaper and faster than the fallback layer.
Reads tasks/registry.json directly as JSON (same precedent as
app_electron/main.js's own readRegistry()) rather than importing
agent.capsule.CapsuleRegistry -- that import pulls in the whole ~8,700-line
agent.py and its torch/sentence-transformers dependency chain just to read
two plain-text list fields, which this module has no other reason to pay
for.

Named routing_rules.py, not rules.py -- components/scope2/rules/ is
already a real package that claims the bare top-level name "rules" the
moment components/scope2/ is on sys.path (its own established bare-import
convention, same as this file's). Both subsystems' directories can end up
on sys.path in the same process (e.g. one pytest run collecting both
tests/scope2/ and tests/test_inbox_router.py), and whichever imports first
would otherwise silently win the shared sys.modules["rules"] cache slot --
the same category of collision this project already hit once with
components/recorder/ vs. the coworker's own recorder/ (renamed to
coworker_recorder/ for the same reason).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from gmail_client import EmailMessage
from pattern_profile import PatternProfile

_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
DEFAULT_REGISTRY_PATH = os.path.join(_ROOT, "tasks", "registry.json")

DECISIONS = {"route_scope1", "route_scope2", "reply", "forward", "flag", "leave_alone"}


@dataclass
class RuleDecision:
    decision: str = ""       # "" = no confident rule fired, defer to the LLM
    confidence: float = 0.0
    rationale: str = ""
    capsule_name: str = ""   # only set for route_scope1 / route_scope2
    forward_to: str = ""


class RuleLayer:
    def __init__(self, profile: PatternProfile, registry_path: str = DEFAULT_REGISTRY_PATH,
                 high_confidence: float = 0.75) -> None:
        self._profile = profile
        self._registry_path = registry_path
        self._high_confidence = high_confidence

    def load_capsules(self) -> List[dict]:
        if not os.path.exists(self._registry_path):
            return []
        try:
            with open(self._registry_path, "r", encoding="utf-8") as f:
                return json.load(f).get("capsules", [])
        except Exception:
            return []

    def match_capsule(self, message: EmailMessage) -> Optional[dict]:
        haystack = f"{message.subject}\n{message.body_text}".lower()
        for capsule in self.load_capsules():
            keywords = capsule.get("trigger_keywords") or []
            apps = capsule.get("trigger_apps") or []
            if any(kw.lower() in haystack for kw in keywords) or \
               any(app.lower() in haystack for app in apps):
                return capsule
        return None

    def classify(self, message: EmailMessage) -> RuleDecision:
        # 1) A sender-domain pattern lopsided enough in the profile wins
        # outright -- this IS the "how this user has actually handled
        # similar messages before" signal the spec asks for.
        pattern = self._profile.pattern_for(message.sender_email)
        if pattern is not None:
            dominant = pattern.dominant_action(self._high_confidence)
            if dominant is not None:
                total = pattern.total()
                confidence = max(pattern.reply_count, pattern.forward_count, pattern.ignore_count) / total
                result = RuleDecision(
                    decision=dominant, confidence=confidence,
                    rationale=(f"You have {decision_label(dominant)} {total} of the last messages "
                               f"from {pattern.sender_domain} ({confidence:.0%})."),
                )
                if dominant == "forward" and pattern.common_forward_targets:
                    result.forward_to = pattern.common_forward_targets[0]
                return result

        # 2) Subject/body keyword match against a registered capsule's own
        # trigger_keywords/trigger_apps. kind == "script" (Scope #2's shape,
        # e.g. Sheet-to-Portal Matcher) -> route_scope2; anything else
        # (kind absent or "agent", Scope #1's shape) -> route_scope1. This
        # mirrors the real, current 1:1 correspondence in tasks/registry.json
        # -- if a third capsule of a genuinely new kind is ever registered,
        # this mapping is the one place that would need revisiting.
        capsule = self.match_capsule(message)
        if capsule is not None:
            is_scope2 = capsule.get("kind") == "script"
            return RuleDecision(
                decision="route_scope2" if is_scope2 else "route_scope1",
                confidence=0.7,
                rationale=f"Subject/body matched keywords registered for '{capsule.get('name','')}'.",
                capsule_name=capsule.get("name", ""),
            )

        # 3) No confident signal -- defer to the LLM.
        return RuleDecision()


def decision_label(decision: str) -> str:
    return {"reply": "replied to", "forward": "forwarded", "leave_alone": "left alone"}.get(decision, decision)
