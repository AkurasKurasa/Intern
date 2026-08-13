"""The trained matcher (3.7).

Roughly 1,100 parameters over a 17-dimensional feature vector. The size is the
point, not a compromise: three demonstrated rows across seven columns yield ~21
positives and ~126 negatives, and anything larger - or raw 384-dimensional
embeddings concatenated in - memorises that immediately. The embeddings are
input features; this is the part that is learned.

3.7 specifies Linear(16->32) because 3.6 heads its feature table "16 dims" while
listing seventeen. The list is authoritative, so the input width is 17 and
features.extractor.DIMS is the single source of it - the network is never given
a hard-coded width to disagree with.
"""

import sys
from pathlib import Path

import torch
from torch import nn

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from features.extractor import DIMS, FEATURE_NAMES, VERSION as EXTRACTOR_VERSION  # noqa: E402

HIDDEN = 32
BOTTLENECK = 16
DROPOUT = 0.2

# 3.7: positives weighted ~1:5 to offset the class imbalance the candidate grid
# creates - every column pairs with every field, so most pairs are negative.
POSITIVE_WEIGHT = 5.0


class Matcher(nn.Module):
    def __init__(self, dims=DIMS):
        super().__init__()
        self.dims = dims
        self.net = nn.Sequential(
            nn.Linear(dims, HIDDEN),
            nn.ReLU(),
            nn.Dropout(DROPOUT),
            nn.Linear(HIDDEN, BOTTLENECK),
            nn.ReLU(),
            nn.Linear(BOTTLENECK, 1),
        )

    def forward(self, x):
        """Logits. Sigmoid lives in the loss for numerical stability, and in
        `probability` for callers that want a score."""
        return self.net(x).squeeze(-1)

    @torch.no_grad()
    def probability(self, x):
        self.eval()
        return torch.sigmoid(self(x))

    def parameter_count(self):
        return sum(p.numel() for p in self.parameters())


def loss_function():
    return nn.BCEWithLogitsLoss(pos_weight=torch.tensor(POSITIVE_WEIGHT))


def save(model, path, metadata=None):
    """Persist with the extractor version (3.6): a feature change invalidates a
    trained matcher, and a model that cannot tell is worse than no model."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state_dict": model.state_dict(),
        "dims": model.dims,
        "extractor_version": EXTRACTOR_VERSION,
        "feature_names": FEATURE_NAMES,
        "metadata": metadata or {},
    }, path)
    return path


class ExtractorMismatch(RuntimeError):
    """The artifact was trained against different features than are loaded."""


def load(path, allow_mismatch=False):
    artifact = torch.load(path, weights_only=False)

    if artifact["extractor_version"] != EXTRACTOR_VERSION and not allow_mismatch:
        raise ExtractorMismatch(
            f"artifact was trained with {artifact['extractor_version']!r} but "
            f"{EXTRACTOR_VERSION!r} is loaded; retrain before scoring"
        )
    if artifact["dims"] != DIMS and not allow_mismatch:
        raise ExtractorMismatch(
            f"artifact expects {artifact['dims']} features, extractor produces {DIMS}"
        )

    model = Matcher(artifact["dims"])
    model.load_state_dict(artifact["state_dict"])
    model.eval()
    return model, artifact


if __name__ == "__main__":
    model = Matcher()
    print(f"input dims       {model.dims}")
    print(f"parameters       {model.parameter_count()}")
    print(f"extractor        {EXTRACTOR_VERSION}")
