"""
backfill_actions.py
===================
Backfill missing `action` fields in existing trace JSON files.

Reads every trace JSON under data/output/traces/, derives a structured
`action` dict from the raw mouse/keyboard/clipboard events already stored
in the trace, and writes the updated trace back in-place.

Usage:
    python scripts/backfill_actions.py
    python scripts/backfill_actions.py --dry-run
    python scripts/backfill_actions.py --trace-dir path/to/traces
"""
from __future__ import annotations

import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "components"))
sys.path.insert(0, _ROOT)

from recorder.recorder import ScreenObserver   # noqa: E402 — path set above


_IGNORE_KEYS = {
    "shift", "ctrl", "alt", "win", "caps lock",
    "tab", "esc", "escape", "up", "down", "left", "right",
    "page up", "page down", "home", "end", "insert", "delete",
    "f1","f2","f3","f4","f5","f6","f7","f8","f9","f10","f11","f12",
}


def _derive(trace: dict) -> dict:
    """Derive structured action from raw trace fields."""
    step_mouse     = trace.get("mouse",     {}).get("actions", [])
    step_kb_groups = trace.get("keyboard",  {}).get("actions", [])
    step_clipboard = trace.get("clipboard", {}).get("events",  [])

    # Flatten keyboard groups → individual strokes
    step_strokes = [
        stroke
        for group in step_kb_groups
        for stroke in group.get("strokes", [])
    ]

    return ScreenObserver._derive_action_from(step_mouse, step_strokes, step_clipboard)


def backfill(trace_dir: str, dry_run: bool = False) -> None:
    total = updated = skipped = errors = 0

    for root, _dirs, files in os.walk(trace_dir):
        for fname in sorted(files):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(root, fname)
            total += 1
            try:
                with open(path, encoding="utf-8") as f:
                    trace = json.load(f)
            except Exception as exc:
                print(f"  ERROR reading {path}: {exc}")
                errors += 1
                continue

            # Only backfill traces that have no action yet
            if trace.get("action") is not None:
                skipped += 1
                continue

            action = _derive(trace)
            trace["action"] = action

            if not dry_run:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(trace, f, indent=2, ensure_ascii=False)
                    updated += 1
                except Exception as exc:
                    print(f"  ERROR writing {path}: {exc}")
                    errors += 1
            else:
                print(f"  [DRY] {fname}  →  {action.get('action_type')} "
                      f"text={action.get('text', '')!r:.40}")
                updated += 1

    label = "[DRY RUN] Would update" if dry_run else "Updated"
    print(f"\nDone.  Total={total}  {label}={updated}  "
          f"Already-set={skipped}  Errors={errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill action fields in trace JSONs")
    parser.add_argument(
        "--trace-dir",
        default=os.path.join(_ROOT, "data", "output", "traces"),
        help="Root directory containing trace JSON files",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing files")
    args = parser.parse_args()

    print(f"Trace dir : {args.trace_dir}")
    print(f"Dry run   : {args.dry_run}\n")
    backfill(args.trace_dir, dry_run=args.dry_run)
