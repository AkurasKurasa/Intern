"""
components/inbox_router/reply_agent.py
==========================================
Output of step 2 of the learned-autonomous-reply plan. ReplyAgent is the
single place that answers "if I had to reply to this email right now,
what would I say" -- by scoring every recorded past reply against the new
email with the trained ReplyMatchNet and, if one scores confidently
enough, handing back that reply's exact real text.

Never invents wording. Below the confidence bar, or with no checkpoint /
no recorded examples yet, it returns an empty suggestion -- the same
cold-start-safe, never-guess contract inbox_agent.py's InboxAgent holds
for decisions.
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
from reply_features import embed_context, pair_features
from reply_model import FeaturesMismatch, ReplyMatchNet, load as load_model
from reply_recorder import DEFAULT_REPLY_EXAMPLES_PATH, load_reply_examples

DEFAULT_CHECKPOINT_PATH = os.path.join(_THIS_DIR, "data", "reply_model.pt")


@dataclass
class ReplySuggestion:
    reply_body: str
    confidence: float
    source_message_id: str = ""


class ReplyAgent:
    def __init__(self, examples_path: str = DEFAULT_REPLY_EXAMPLES_PATH,
                 checkpoint_path: str = DEFAULT_CHECKPOINT_PATH,
                 high_confidence: float = 0.75) -> None:
        self._examples_path = examples_path
        self._high_confidence = high_confidence
        self._model: Optional[ReplyMatchNet] = None
        self._load_checkpoint(checkpoint_path)

    def _load_checkpoint(self, checkpoint_path: str) -> None:
        if not os.path.exists(checkpoint_path):
            return   # cold start -- no checkpoint yet, every suggestion is empty
        try:
            model, _artifact = load_model(checkpoint_path)
        except (FeaturesMismatch, Exception) as exc:
            # Broad except is deliberate, mirroring InboxAgent._load_checkpoint:
            # a stale/corrupt checkpoint must never crash startup, only fall
            # back to "no suggestion available."
            logger.warning(f"Failed to load reply checkpoint at {checkpoint_path}: {exc}")
            self._model = None
            return
        self._model = model

    def suggest_reply(self, message: EmailMessage) -> ReplySuggestion:
        examples = load_reply_examples(self._examples_path)
        if self._model is None or not examples:
            return ReplySuggestion(reply_body="", confidence=0.0)

        message_vec = embed_context(message.subject, message.body_text)
        best_score = 0.0
        best_example: Optional[dict] = None
        with torch.no_grad():
            for ex in examples:
                ex_vec = embed_context(ex.get("subject", ""), ex.get("body_text", ""))
                feats = pair_features(message, message_vec, ex, ex_vec)
                score = float(self._model.match_probability(torch.tensor([feats], dtype=torch.float32))[0])
                if score > best_score:
                    best_score = score
                    best_example = ex

        if best_example is not None and best_score >= self._high_confidence:
            return ReplySuggestion(
                reply_body=best_example.get("reply_body", ""),
                confidence=best_score,
                source_message_id=best_example.get("message_id", ""),
            )
        return ReplySuggestion(reply_body="", confidence=best_score)
