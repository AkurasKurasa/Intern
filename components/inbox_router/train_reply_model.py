"""
components/inbox_router/train_reply_model.py
=================================================
Train step for step 2 of the learned-autonomous-reply plan. Reads recorded
reply examples (components/inbox_router/data/reply_examples.jsonl by
default), builds positive/negative pairs via reply_features.pair_features(),
fits ReplyMatchNet, saves a checkpoint. Same CLI shape as
train_inbox_agent.py.

Training pairs, since there's no explicit "this reply is a good/bad fit
for this OTHER email" label anywhere: each example is its own positive
pair (a real reply, paired with the email it was actually written for --
the strongest signal available, real behavior, not a guess). Negatives are
formed the standard way retrieval/matching models are trained without
explicit negative labels: pair each example's email with a handful of
OTHER examples' reply-context, sampled at random -- across a real dataset
most such random pairings are poor fits.

Usage:
    python components/inbox_router/train_reply_model.py
    python components/inbox_router/train_reply_model.py --epochs 300 --save_path components/inbox_router/data/reply_model.pt
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from typing import List, Optional, Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
for _p in (_ROOT, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import torch
from torch import nn

from gmail_client import EmailMessage
from reply_features import DIMS, embed_context, pair_features
from reply_model import ReplyMatchNet, save as save_model
from reply_recorder import DEFAULT_REPLY_EXAMPLES_PATH, load_reply_examples

MIN_EXAMPLES = 6   # same bar train_inbox_agent.py holds its own model to
NEGATIVES_PER_POSITIVE = 3


class TooFewExamplesError(ValueError):
    pass


def _example_to_message(ex: dict) -> EmailMessage:
    return EmailMessage(
        id=ex.get("message_id", ""), thread_id="", sender="",
        sender_email=ex.get("sender_email", ""), subject=ex.get("subject", ""),
        snippet="", body_text=ex.get("body_text", ""), received_at="",
    )


def build_dataset(examples: List[dict], seed: int = 0) -> Tuple[torch.Tensor, torch.Tensor]:
    rng = random.Random(seed)
    vecs = [embed_context(ex.get("subject", ""), ex.get("body_text", "")) for ex in examples]
    xs, ys = [], []
    n = len(examples)
    for i, ex in enumerate(examples):
        msg = _example_to_message(ex)
        # Positive: the example paired with the real email it answered.
        xs.append(pair_features(msg, vecs[i], ex, vecs[i]))
        ys.append(1.0)
        # Negatives: the same email paired with other examples' context --
        # standard in-batch negative sampling, no explicit "bad fit" label
        # exists anywhere in the recorded data to draw on instead.
        others = [j for j in range(n) if j != i]
        rng.shuffle(others)
        for j in others[:NEGATIVES_PER_POSITIVE]:
            xs.append(pair_features(msg, vecs[i], examples[j], vecs[j]))
            ys.append(0.0)
    return torch.tensor(xs, dtype=torch.float32), torch.tensor(ys, dtype=torch.float32)


@torch.no_grad()
def _accuracy(model: ReplyMatchNet, x: torch.Tensor, y: torch.Tensor) -> Optional[float]:
    if len(x) == 0:
        return None
    model.eval()
    preds = (torch.sigmoid(model(x)) >= 0.5).float()
    return float((preds == y).float().mean())


def train(examples_path: str, save_path: str, epochs: int, lr: float) -> dict:
    examples = load_reply_examples(examples_path)
    if len(examples) < MIN_EXAMPLES:
        raise TooFewExamplesError(
            f"Only {len(examples)} recorded reply examples found at {examples_path} -- "
            f"need at least {MIN_EXAMPLES} to train a checkpoint that isn't noise."
        )
    x, y = build_dataset(examples)

    model = ReplyMatchNet(dims=DIMS)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.BCEWithLogitsLoss()

    for _epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        logits = model(x)
        loss = loss_fn(logits, y)
        loss.backward()
        optimizer.step()

    train_acc = _accuracy(model, x, y)
    save_model(model, save_path, metadata={"num_examples": len(examples), "train_acc": train_acc})
    return {"train_acc": train_acc, "num_examples": len(examples)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the reply-matching model (ReplyMatchNet).")
    parser.add_argument("--examples_path", default=DEFAULT_REPLY_EXAMPLES_PATH)
    parser.add_argument("--save_path", default=os.path.join(_THIS_DIR, "data", "reply_model.pt"))
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--lr", type=float, default=1e-2)
    args = parser.parse_args()

    result = train(args.examples_path, args.save_path, args.epochs, args.lr)
    print(f"Trained on {result['num_examples']} examples. train_acc={result['train_acc']:.2%}")
    print(f"Saved checkpoint to {args.save_path}")


if __name__ == "__main__":
    main()
