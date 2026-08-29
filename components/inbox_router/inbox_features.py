"""
components/inbox_router/inbox_features.py
=============================================
Shared feature extraction for Scope #3's Record -> Train -> Output pipeline.
Mirrors components/scope2/features/extractor.py's role exactly: one
function, extract(), called identically at training time (over recorded
examples) and at inference time (over a live message) -- a feature that
exists only during training is a train/inference skew no accuracy number
will reveal, the same rule extractor.py states explicitly.

Reuses components/scope2/features/encoders.py (a pretrained
sentence-transformer, already a project dependency and already loaded
lazily/cached there) rather than loading a second copy of the model.
"""
from __future__ import annotations

import math
import os
import sys
from typing import Dict, List, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
_SCOPE2_DIR = os.path.join(_ROOT, "components", "scope2")
for _p in (_THIS_DIR, _SCOPE2_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from features import encoders  # noqa: E402
from gmail_client import EmailMessage  # noqa: E402
from pattern_profile import SenderPattern  # noqa: E402

VERSION = "inbox-features-v2-11d"

DECISIONS_ORDER = ["reply", "forward", "schedule", "cold_email", "flag", "leave_alone"]

FEATURE_NAMES = [
    "sem_sim_reply",          # 1
    "sem_sim_forward",        # 2
    "sem_sim_schedule",       # 3
    "sem_sim_cold_email",     # 4
    "sem_sim_flag",           # 5
    "sem_sim_leave_alone",    # 6
    "pattern_reply_ratio",    # 7
    "pattern_forward_ratio",  # 8
    "pattern_ignore_ratio",   # 9
    "body_length_scaled",     # 10
    "has_sender_history",     # 11
]

DIMS = len(FEATURE_NAMES)

_BODY_LENGTH_CAP = 5000


def compute_centroids(examples: List[dict]) -> Dict[str, List[float]]:
    """Averages the text embedding of every recorded example, grouped by
    decision. Computed once at train time and persisted in the checkpoint
    (see inbox_model.py) so inference uses the exact same centroids the
    model was trained against -- the same "skew is a bug" reasoning as
    FEATURES_VERSION."""
    buckets: Dict[str, List[List[float]]] = {}
    for ex in examples:
        text = f"{ex.get('subject', '')}\n{ex.get('body_text', '')}".strip()
        if not text:
            continue
        vec = encoders.encode(text)
        buckets.setdefault(ex["decision"], []).append(vec)
    centroids: Dict[str, List[float]] = {}
    for decision, vecs in buckets.items():
        dims = len(vecs[0])
        centroids[decision] = [sum(v[i] for v in vecs) / len(vecs) for i in range(dims)]
    return centroids


def extract(message: EmailMessage, pattern: Optional[SenderPattern],
            centroids: Dict[str, List[float]]) -> List[float]:
    text = f"{message.subject}\n{message.body_text}".strip()
    email_vec = encoders.encode(text) if text else [0.0] * encoders.DIMS

    sem_feats = [
        encoders.cosine(email_vec, centroids[decision]) if decision in centroids else 0.0
        for decision in DECISIONS_ORDER
    ]

    total = pattern.total() if pattern is not None else 0
    if total > 0:
        pattern_feats = [
            pattern.reply_count / total,
            pattern.forward_count / total,
            pattern.ignore_count / total,
        ]
    else:
        pattern_feats = [0.0, 0.0, 0.0]

    body_len = len(message.body_text or "")
    body_length_scaled = min(1.0, math.log1p(body_len) / math.log1p(_BODY_LENGTH_CAP))
    has_sender_history = 1.0 if total > 0 else 0.0

    return sem_feats + pattern_feats + [body_length_scaled, has_sender_history]
