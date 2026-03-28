"""
train.py — Standalone BCTrainer entry point
============================================
Trains (or retrains) the TransformerAgentNetwork from recorded trace sessions.

Usage
-----
    python train.py                                   # defaults
    python train.py --trace_dir data/output/traces/live --epochs 50
    python train.py --epochs 100 --batch_size 32 --device cpu

Arguments
---------
  --trace_dir   Directory containing trace JSONs / session_* sub-dirs
                (default: data/output/traces/live)
  --save_path   Where to write the trained checkpoint
                (default: data/models/transformer_bc.pt)
  --epochs      Training epochs (default: 50)
  --batch_size  Mini-batch size (default: 16)
  --lr          Learning rate (default: 1e-3)
  --val_split   Validation fraction (default: 0.15)
  --aug_drop    Element dropout augmentation probability (default: 0.1)
  --device      auto | cpu | cuda | mps (default: auto)
  --continual   After training, start ContinualLearner to watch for new traces
"""

from __future__ import annotations

import argparse
import os
import sys

# ── resolve paths so imports work from the project root ────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
_COMP = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    parser = argparse.ArgumentParser(
        description="Train Intern's TransformerAgentNetwork via Behavioral Cloning."
    )
    parser.add_argument("--trace_dir",  default="data/output/traces/live",
                        help="Trace directory (flat or session_* sub-dirs)")
    parser.add_argument("--save_path",  default="data/models/transformer_bc.pt",
                        help="Checkpoint output path")
    parser.add_argument("--epochs",     type=int,   default=50)
    parser.add_argument("--batch_size", type=int,   default=16)
    parser.add_argument("--lr",         type=float, default=1e-3)
    parser.add_argument("--val_split",  type=float, default=0.15)
    parser.add_argument("--aug_drop",   type=float, default=0.1)
    parser.add_argument("--device",     default="auto",
                        choices=["auto", "cpu", "cuda", "mps"])
    parser.add_argument("--continual",  action="store_true",
                        help="After training, start ContinualLearner to watch trace_dir")
    # Architecture — shrink these for small datasets to avoid overfitting
    parser.add_argument("--d_model",        type=int,   default=64,
                        help="Transformer hidden size (default: 64, use 128 for large datasets)")
    parser.add_argument("--num_layers",     type=int,   default=2,
                        help="Transformer encoder layers (default: 2)")
    parser.add_argument("--dim_feedforward",type=int,   default=128,
                        help="Feedforward dim (default: 128)")
    parser.add_argument("--dropout",        type=float, default=0.2,
                        help="Dropout rate (default: 0.2)")
    args = parser.parse_args()

    # Resolve relative paths from the project root
    trace_dir = args.trace_dir if os.path.isabs(args.trace_dir) \
        else os.path.join(_ROOT, args.trace_dir)
    save_path = args.save_path if os.path.isabs(args.save_path) \
        else os.path.join(_ROOT, args.save_path)

    if not os.path.isdir(trace_dir):
        print(f"[ERROR] Trace directory not found: {trace_dir}")
        sys.exit(1)

    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    from learning_models.intern_model.bc.behavioral_cloning import BCTrainer

    trainer = BCTrainer(
        trace_dir=trace_dir,
        save_path=save_path,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        val_split=args.val_split,
        aug_drop=args.aug_drop,
        device=args.device,
        d_model=args.d_model,
        num_layers=args.num_layers,
        dim_feedforward=args.dim_feedforward,
        dropout=args.dropout,
    )

    print(f"\n{'='*60}")
    print(f"  Intern — Behavioral Cloning Trainer")
    print(f"{'='*60}")
    print(f"  trace_dir  : {trace_dir}")
    print(f"  save_path  : {save_path}")
    print(f"  epochs     : {args.epochs}")
    print(f"  batch_size : {args.batch_size}")
    print(f"  device     : {args.device}")
    print(f"{'='*60}\n")

    trainer.train()

    print(f"\n  Checkpoint saved -> {save_path}")

    if args.continual:
        print("\n  Starting ContinualLearner — watching for new traces …")
        print("  Press Ctrl+C to stop.\n")
        from learning_models.intern_model.continual.learner import ContinualLearner
        cl = ContinualLearner(trace_dir=trace_dir, bc_trainer=trainer)
        cl.start()
        try:
            import time
            while True:
                stats = cl.stats
                print(
                    f"\r  [CL] known={stats['known_traces']}  "
                    f"queued={stats['new_queued']}  "
                    f"retrains={stats['retrain_count']}",
                    end="", flush=True,
                )
                time.sleep(5)
        except KeyboardInterrupt:
            cl.stop()
            print("\n  ContinualLearner stopped.")


if __name__ == "__main__":
    main()
