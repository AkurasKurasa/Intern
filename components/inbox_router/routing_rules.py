"""
components/inbox_router/routing_rules.py
==========================================
Deterministic first pass. If a rule confidently matches, that's the
decision -- no LLM call needed, cheaper and faster than the fallback layer.
The only rule left (Task 1 removed the capsule-registry keyword-matching
rule that used to live here) is a sender-domain pattern lopsided enough in
the profile to win outright. registry_path/DEFAULT_REGISTRY_PATH are kept
on RuleLayer's constructor for signature stability across existing call
sites, but nothing in this module reads tasks/registry.json anymore.

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

import os
import sys
from dataclasses import dataclass, field

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from gmail_client import EmailMessage
from pattern_profile import PatternProfile

_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
DEFAULT_REGISTRY_PATH = os.path.join(_ROOT, "tasks", "registry.json")

DECISIONS = {"reply", "forward", "schedule", "cold_email", "flag", "leave_alone"}


@dataclass
class RuleDecision:
    decision: str = ""       # "" = no confident rule fired, defer to the LLM
    confidence: float = 0.0
    rationale: str = ""
    capsule_name: str = ""   # unused now -- kept so callers matching on this field don't break
    forward_to: str = ""


class RuleLayer:
    def __init__(self, profile: PatternProfile, registry_path: str = DEFAULT_REGISTRY_PATH,
                 high_confidence: float = 0.75) -> None:
        self._profile = profile
        self._registry_path = registry_path
        self._high_confidence = high_confidence

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

        # 2) No confident signal -- defer to the LLM.
        return RuleDecision()


def decision_label(decision: str) -> str:
    return {"reply": "replied to", "forward": "forwarded", "leave_alone": "left alone"}.get(decision, decision)
