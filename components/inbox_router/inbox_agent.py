"""
components/inbox_router/inbox_agent.py
==========================================
Output of Scope #3's Record -> Train -> Output pipeline. InboxAgent is the
single decision-maker: loads a trained InboxDecisionNet checkpoint (if one
exists) and fast-fills when confident, otherwise falls through to the
existing RuleLayer -> LLMClassifier chain as its own internal reasoning
step -- not as separate legacy plumbing sitting behind it.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import torch

from gmail_client import EmailMessage
from inbox_features import DECISIONS_ORDER, extract
from inbox_model import FeaturesMismatch, InboxDecisionNet, load as load_model
from llm_classifier import LLMClassifier
from pattern_profile import PatternProfile
from routing_rules import RuleDecision, RuleLayer

DEFAULT_CHECKPOINT_PATH = os.path.join(_THIS_DIR, "data", "inbox_model.pt")

from decision_modes import DECISION_MODES, DEFAULT_DECISION_MODE, UNDECIDED  # noqa: E402


@dataclass
class InboxDecision:
    decision: str
    confidence: float
    rationale: str
    layer: str            # "fast_fill" | "rule" | "llm"
    capsule_name: str = ""
    forward_to: str = ""


class InboxAgent:
    def __init__(self, profile: PatternProfile, rule_layer: RuleLayer,
                 llm_classifier: LLMClassifier,
                 checkpoint_path: str = DEFAULT_CHECKPOINT_PATH,
                 high_confidence: float = 0.75,
                 mode: str = DEFAULT_DECISION_MODE) -> None:
        self.mode = mode
        self._profile = profile
        self._rules = rule_layer
        self._llm = llm_classifier
        self._high_confidence = high_confidence
        self._model: Optional[InboxDecisionNet] = None
        self._centroids: dict = {}
        self._load_checkpoint(checkpoint_path)

    def _load_checkpoint(self, checkpoint_path: str) -> None:
        if not os.path.exists(checkpoint_path):
            return   # cold start -- no checkpoint yet, every decision reasons
        try:
            model, artifact = load_model(checkpoint_path)
        except (FeaturesMismatch, Exception) as exc:
            # FeaturesMismatch is itself an Exception subclass; both are
            # listed for readability -- a stale/corrupt checkpoint must
            # never crash startup, only fall back to cold-start reasoning.
            # Broad except clause is deliberate: we want to capture anything
            # that breaks checkpoint loading and continue gracefully.
            logger.warning(f"Failed to load checkpoint at {checkpoint_path}: {exc}")
            self._model = None
            return
        self._model = model
        self._centroids = artifact.get("centroids", {})

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in DECISION_MODES:
            raise ValueError(f"unknown decision mode {value!r}; expected one of {DECISION_MODES}")
        self._mode = value

    def decide(self, message: EmailMessage) -> InboxDecision:
        if self._mode == "reasoning":
            return self._llm_decision(message, pattern=None, rule_hint=RuleDecision())

        fast = self._try_fast_fill(message)
        if fast is not None:
            return fast

        rule_result = self._rules.classify(message)
        if rule_result.decision:
            return InboxDecision(
                decision=rule_result.decision, confidence=rule_result.confidence,
                rationale=rule_result.rationale, layer="rule",
                capsule_name=rule_result.capsule_name, forward_to=rule_result.forward_to,
            )

        if self._mode == "habits":
            return InboxDecision(
                decision=UNDECIDED, confidence=0.0,
                rationale="No confident habit for this sender yet, so it is left for you to decide.",
                layer="none",
            )
        return self._llm_decision(message, self._profile.pattern_for(message.sender_email), rule_result)

    def _try_fast_fill(self, message: EmailMessage) -> Optional[InboxDecision]:
        if self._model is None:
            return None
        try:
            pattern = self._profile.pattern_for(message.sender_email)
            feats = extract(message, pattern, self._centroids)
            x = torch.tensor([feats], dtype=torch.float32)
            probs = self._model.probabilities(x)[0]
            top_idx = int(torch.argmax(probs))
            top_conf = float(probs[top_idx])
            if top_conf < self._high_confidence:
                return None
            decision = DECISIONS_ORDER[top_idx]
            capsule_name, forward_to = "", ""
            if decision == "forward":
                forward_to = pattern.common_forward_targets[0] if pattern and pattern.common_forward_targets else ""
                if not forward_to:
                    return None   # can't fast-fill a forward with no known target -- fall through to reasoning
            return InboxDecision(
                decision=decision, confidence=top_conf,
                rationale=f"Trained model is {top_conf:.0%} confident, based on similar past emails.",
                layer="fast_fill", capsule_name=capsule_name, forward_to=forward_to,
            )
        except Exception as exc:
            logger.warning(f"Fast-fill scoring failed: {exc}")
            return None

    def _llm_decision(self, message: EmailMessage, pattern, rule_hint: RuleDecision) -> InboxDecision:
        llm_result = self._llm.classify(message, pattern, rule_hint)
        decision, capsule_name, rationale = llm_result.decision, llm_result.capsule_name, llm_result.rationale
        return InboxDecision(
            decision=decision, confidence=llm_result.confidence,
            rationale=rationale, layer="llm",
            capsule_name=capsule_name, forward_to=llm_result.forward_to,
        )
