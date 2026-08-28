"""
components/inbox_router/reply_model.py
==========================================
ReplyMatchNet -- the trained model in step 2 of the learned-autonomous-
reply plan. Directly mirrors inbox_model.py's InboxDecisionNet: small on
purpose (a handful of real reply examples expected, a large network would
just memorize them), same save()/load() shape with a version+dims mismatch
guard so a stale checkpoint fails loudly instead of silently scoring
nonsense.

What it scores is different from InboxDecisionNet, though: not "which of
6 fixed decisions," but "given a (new email, one candidate past reply)
pair's raw similarity signals (reply_features.py), how well does this
candidate fit." The similarity numbers are hand-computed inputs; the
weights that turn them into a single match score are learned, same rule
this whole project holds everywhere else -- math can feed a trained model,
it can't replace one.
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

from reply_features import DIMS, FEATURE_NAMES, VERSION as FEATURES_VERSION

HIDDEN = 8
DROPOUT = 0.2


class ReplyMatchNet(nn.Module):
    def __init__(self, dims: int = DIMS) -> None:
        super().__init__()
        self.dims = dims
        self.net = nn.Sequential(
            nn.Linear(dims, HIDDEN),
            nn.ReLU(),
            nn.Dropout(DROPOUT),
            nn.Linear(HIDDEN, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Match logit for each row of x."""
        return self.net(x).squeeze(-1)

    @torch.no_grad()
    def match_probability(self, x: torch.Tensor) -> torch.Tensor:
        self.eval()
        return torch.sigmoid(self(x))

    def parameter_count(self) -> int:
        return sum(p.numel() for p in self.parameters())


class FeaturesMismatch(RuntimeError):
    """The checkpoint was trained against a different feature layout than is loaded."""


def save(model: ReplyMatchNet, path: str, metadata: Optional[dict] = None) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    torch.save({
        "state_dict": model.state_dict(),
        "dims": model.dims,
        "features_version": FEATURES_VERSION,
        "feature_names": FEATURE_NAMES,
        "metadata": metadata or {},
    }, path)
    return path


def load(path: str, allow_mismatch: bool = False) -> Tuple[ReplyMatchNet, dict]:
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
    model = ReplyMatchNet(artifact["dims"])
    model.load_state_dict(artifact["state_dict"])
    model.eval()
    return model, artifact
