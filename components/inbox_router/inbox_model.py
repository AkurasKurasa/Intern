"""
components/inbox_router/inbox_model.py
==========================================
InboxDecisionNet -- the trained model in Scope #3's Record -> Train ->
Output pipeline. Directly mirrors components/scope2/model/matcher.py's
Matcher: small on purpose (few training examples expected -- a large
network would just memorize them), same save()/load() shape with a
version+dims mismatch guard so a stale checkpoint fails loudly instead of
silently scoring nonsense.
"""
from __future__ import annotations

import os
import sys
from typing import Optional, Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import torch
from torch import nn
from torch.nn import functional as F

from inbox_features import DECISIONS_ORDER, DIMS, FEATURE_NAMES, VERSION as FEATURES_VERSION

HIDDEN = 16
DROPOUT = 0.2


class InboxDecisionNet(nn.Module):
    def __init__(self, dims: int = DIMS, num_decisions: int = len(DECISIONS_ORDER)) -> None:
        super().__init__()
        self.dims = dims
        self.num_decisions = num_decisions
        self.net = nn.Sequential(
            nn.Linear(dims, HIDDEN),
            nn.ReLU(),
            nn.Dropout(DROPOUT),
            nn.Linear(HIDDEN, num_decisions),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Logits over DECISIONS_ORDER."""
        return self.net(x)

    @torch.no_grad()
    def probabilities(self, x: torch.Tensor) -> torch.Tensor:
        self.eval()
        return F.softmax(self(x), dim=-1)

    def parameter_count(self) -> int:
        return sum(p.numel() for p in self.parameters())


class FeaturesMismatch(RuntimeError):
    """The checkpoint was trained against a different feature layout than is loaded."""


def save(model: InboxDecisionNet, path: str, centroids: dict, metadata: Optional[dict] = None) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    torch.save({
        "state_dict": model.state_dict(),
        "dims": model.dims,
        "num_decisions": model.num_decisions,
        "features_version": FEATURES_VERSION,
        "feature_names": FEATURE_NAMES,
        "centroids": centroids,
        "metadata": metadata or {},
    }, path)
    return path


def load(path: str, allow_mismatch: bool = False) -> Tuple[InboxDecisionNet, dict]:
    artifact = torch.load(path, weights_only=False)
    if artifact["features_version"] != FEATURES_VERSION and not allow_mismatch:
        raise FeaturesMismatch(
            f"checkpoint was trained with {artifact['features_version']!r} but "
            f"{FEATURES_VERSION!r} is loaded; retrain before scoring"
        )
    if artifact["dims"] != DIMS and not allow_mismatch:
        raise FeaturesMismatch(
            f"checkpoint expects {artifact['dims']} features, extractor produces {DIMS}"
        )
    model = InboxDecisionNet(artifact["dims"], artifact["num_decisions"])
    model.load_state_dict(artifact["state_dict"])
    model.eval()
    return model, artifact
