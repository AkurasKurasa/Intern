"""
components/inbox_router/reply_features.py
=============================================
Feature extraction for the reply-matching model (step 2 of the
learned-autonomous-reply plan). Different question than inbox_features.py:
that module asks "what should I DO with this email" (one of 6 fixed
decision classes); this one asks "which of my past real replies, if any,
best fits this new email" -- a growing, unbounded set of candidates, not a
fixed classifier output.

Reuses components/scope2/features/encoders.py exactly like
inbox_features.py does -- same pretrained sentence-transformer, no second
copy of the model loaded.
"""
from __future__ import annotations

import os
import re
import sys
from typing import List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
_SCOPE2_DIR = os.path.join(_ROOT, "components", "scope2")
for _p in (_THIS_DIR, _SCOPE2_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from features import encoders  # noqa: E402
from gmail_client import EmailMessage  # noqa: E402

VERSION = "reply-features-v1"

FEATURE_NAMES = [
    "context_cosine_sim",   # how similar the two emails' subject+body are
    "same_sender",          # 1.0 if the new email and the example share a sender
    "subject_overlap",      # word-overlap ratio between the two subjects
]

DIMS = len(FEATURE_NAMES)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _context_text(subject: str, body_text: str) -> str:
    return f"{subject or ''}\n{body_text or ''}".strip()


def embed_context(subject: str, body_text: str) -> List[float]:
    """Text embedding of an email's subject+body -- the same shape
    inbox_features.py's compute_centroids()/extract() use for their own
    semantic-similarity features."""
    text = _context_text(subject, body_text)
    return encoders.encode(text) if text else [0.0] * encoders.DIMS


def _subject_overlap(subject_a: str, subject_b: str) -> float:
    words_a = set(_WORD_RE.findall((subject_a or "").lower()))
    words_b = set(_WORD_RE.findall((subject_b or "").lower()))
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def pair_features(new_message: EmailMessage, new_message_vec: List[float],
                   example: dict, example_vec: List[float]) -> List[float]:
    """Feature vector for one (new email, candidate past-reply example)
    pair. The trained model (reply_model.py) combines these into a learned
    match score -- this function only ever produces raw signals, never the
    decision itself."""
    cos = encoders.cosine(new_message_vec, example_vec)
    same_sender = 1.0 if (new_message.sender_email and
                           new_message.sender_email == example.get("sender_email")) else 0.0
    overlap = _subject_overlap(new_message.subject, example.get("subject", ""))
    return [cos, same_sender, overlap]
