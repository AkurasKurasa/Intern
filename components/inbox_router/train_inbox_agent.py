"""
components/inbox_router/train_inbox_agent.py
================================================
Train step of Scope #3's Record -> Train -> Output pipeline. Reads recorded
examples (components/inbox_router/data/training_examples.jsonl by default),
builds features via inbox_features.extract(), fits InboxDecisionNet, saves
a checkpoint. Same CLI shape as scripts/train.py.

Usage:
    python components/inbox_router/train_inbox_agent.py
    python components/inbox_router/train_inbox_agent.py --epochs 300 --save_path components/inbox_router/data/inbox_model.pt
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
for _p in (_ROOT, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import torch
from torch import nn

from decision_recorder import DEFAULT_EXAMPLES_PATH, load_examples
from gmail_client import EmailMessage
from inbox_features import DECISIONS_ORDER, DIMS, compute_centroids, extract
from inbox_model import InboxDecisionNet, save as save_model
from pattern_profile import PatternProfile
from routing_rules import RuleLayer

MIN_EXAMPLES = 6   # at least one per decision class, in spirit


class TooFewExamplesError(ValueError):
    pass


def _example_to_message(ex: dict) -> EmailMessage:
    return EmailMessage(
        id=ex.get("message_id", ""), thread_id="", sender="",
        sender_email=ex.get("sender_email", ""), subject=ex.get("subject", ""),
        snippet="", body_text=ex.get("body_text", ""), received_at="",
    )


def build_dataset(examples: list, profile: PatternProfile,
                   rule_layer: RuleLayer) -> Tuple[torch.Tensor, torch.Tensor, dict]:
    centroids = compute_centroids(examples)
    xs, ys = [], []
    for ex in examples:
        message = _example_to_message(ex)
        pattern = profile.pattern_for(message.sender_email)
        feats = extract(message, pattern, centroids, rule_layer)
        xs.append(feats)
        ys.append(DECISIONS_ORDER.index(ex["decision"]))
    return torch.tensor(xs, dtype=torch.float32), torch.tensor(ys, dtype=torch.long), centroids


@torch.no_grad()
def _accuracy(model: InboxDecisionNet, x: torch.Tensor, y: torch.Tensor):
    if len(x) == 0:
        return None
    model.eval()
    preds = model(x).argmax(dim=-1)
    return float((preds == y).float().mean())


def train(examples_path: str, save_path: str, epochs: int, lr: float, val_split: float,
          profile_path: str = None, registry_path: str = None) -> dict:
    """profile_path/registry_path default to PatternProfile/RuleLayer's own
    real defaults when omitted (the normal CLI path -- see main()). Tests
    pass isolated tmp_path values so a trained checkpoint's rule_hit_scope1/
    rule_hit_scope2 features are computed against the SAME capsule registry
    inference-time InboxAgent tests use -- training against the real
    project registry.json while testing against an empty one would be a
    train/inference skew, the exact hazard inbox_features.py's own
    docstring warns against."""
    examples = load_examples(examples_path)
    if len(examples) < MIN_EXAMPLES:
        raise TooFewExamplesError(
            f"Only {len(examples)} recorded examples found at {examples_path} -- "
            f"need at least {MIN_EXAMPLES} to train a checkpoint that isn't noise."
        )
    profile_kwargs = {"path": profile_path} if profile_path is not None else {}
    profile = PatternProfile(**profile_kwargs)
    registry_kwargs = {"registry_path": registry_path} if registry_path is not None else {}
    rule_layer = RuleLayer(profile, **registry_kwargs)
    x, y, centroids = build_dataset(examples, profile, rule_layer)

    n_val = max(1, int(len(examples) * val_split)) if len(examples) >= 10 else 0
    n_train = len(examples) - n_val
    x_train, y_train = x[:n_train], y[:n_train]
    x_val, y_val = x[n_train:], y[n_train:]

    model = InboxDecisionNet(dims=DIMS)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.CrossEntropyLoss()

    for _epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        logits = model(x_train)
        loss = loss_fn(logits, y_train)
        loss.backward()
        optimizer.step()

    train_acc = _accuracy(model, x_train, y_train)
    val_acc = _accuracy(model, x_val, y_val) if n_val else None

    save_model(model, save_path, centroids, metadata={
        "num_examples": len(examples), "train_acc": train_acc, "val_acc": val_acc,
    })
    return {"train_acc": train_acc, "val_acc": val_acc, "num_examples": len(examples)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Scope #3's InboxDecisionNet.")
    parser.add_argument("--examples_path", default=DEFAULT_EXAMPLES_PATH)
    parser.add_argument("--save_path", default=os.path.join(_THIS_DIR, "data", "inbox_model.pt"))
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--lr", type=float, default=1e-2)
    parser.add_argument("--val_split", type=float, default=0.15)
    args = parser.parse_args()

    result = train(args.examples_path, args.save_path, args.epochs, args.lr, args.val_split)
    val_acc_str = "n/a" if result["val_acc"] is None else f"{result['val_acc']:.2%}"
    print(f"Trained on {result['num_examples']} examples. "
          f"train_acc={result['train_acc']:.2%} val_acc={val_acc_str}")
    print(f"Saved checkpoint to {args.save_path}")


if __name__ == "__main__":
    main()
