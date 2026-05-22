"""
build_capsule.py
================
Train a BC transformer on a task-specific trace folder and register it
as a named WorkflowCapsule so the agent loads it automatically.

Usage
-----
  python build_capsule.py --name form_filling \
      --trace_dir data/output/traces/forms \
      --keywords "form,fill,insurance,data entry" \
      --apps "Car Insurance" \
      --description "Fill GUI forms from a data source"

  python build_capsule.py --list          # show all registered capsules
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.abspath(__file__))
_COMP = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    parser = argparse.ArgumentParser(description="Build and register a WorkflowCapsule.")
    parser.add_argument("--name",        required=False, help="Capsule name (e.g. form_filling)")
    parser.add_argument("--trace_dir",   default="",     help="Folder containing trace session_* dirs")
    parser.add_argument("--keywords",    default="",     help="Comma-separated goal trigger keywords")
    parser.add_argument("--apps",        default="",     help="Comma-separated window title fragments")
    parser.add_argument("--description", default="",     help="Human-readable description")
    parser.add_argument("--epochs",      type=int, default=20)
    parser.add_argument("--batch_size",  type=int, default=16)
    parser.add_argument("--lr",          type=float, default=1e-3)
    parser.add_argument("--device",      default="auto")
    parser.add_argument("--list",        action="store_true", help="List all registered capsules")
    args = parser.parse_args()

    from agent.capsule import CapsuleRegistry, WorkflowCapsule
    registry = CapsuleRegistry()

    if args.list:
        capsules = registry.list_capsules()
        if not capsules:
            print("No capsules registered yet.")
        for c in capsules:
            print(f"\n  [{c.name}]")
            print(f"    description : {c.description}")
            print(f"    model_path  : {c.model_path}")
            print(f"    keywords    : {c.trigger_keywords}")
            print(f"    apps        : {c.trigger_apps}")
            print(f"    trace_dir   : {c.trace_dir}")
            print(f"    created     : {c.created}")
        return

    if not args.name:
        parser.error("--name is required unless --list is used")

    trace_dir = args.trace_dir if os.path.isabs(args.trace_dir) \
        else os.path.join(_ROOT, args.trace_dir)
    save_path = os.path.join(_ROOT, "data", "models", f"transformer_{args.name}.pt")
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    apps     = [a.strip() for a in args.apps.split(",")     if a.strip()]

    print(f"\n{'='*60}")
    print(f"  Building capsule: {args.name}")
    print(f"{'='*60}")
    print(f"  trace_dir  : {trace_dir}")
    print(f"  save_path  : {save_path}")
    print(f"  keywords   : {keywords}")
    print(f"  apps       : {apps}")
    print(f"  epochs     : {args.epochs}")
    print(f"{'='*60}\n")

    if not os.path.isdir(trace_dir):
        print(f"[ERROR] trace_dir not found: {trace_dir}")
        sys.exit(1)

    # Train
    from intelligence.training.bc.behavioral_cloning import BCTrainer
    trainer = BCTrainer(
        trace_dir=trace_dir,
        save_path=save_path,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        device=args.device,
    )
    trainer.train()
    print(f"\n  Checkpoint saved → {save_path}")

    # Register capsule
    capsule = WorkflowCapsule(
        name=args.name,
        description=args.description or args.name,
        model_path=save_path,
        trigger_keywords=keywords,
        trigger_apps=apps,
        trace_dir=trace_dir,
        created=datetime.now().isoformat(timespec="seconds"),
    )
    registry.register(capsule)
    print(f"  Capsule '{args.name}' registered in capsule registry.")
    print(f"  Agent will now load this model automatically for matching tasks.\n")


if __name__ == "__main__":
    main()
