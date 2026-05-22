"""
augment_traces.py
=================
Generates augmented copies of existing trace JSONs to multiply dataset size
without recording more sessions.

Each source trace produces N augmented variants per --copies flag.
Augmentations applied per variant (all within physical plausibility):

  - bbox jitter      : ±BBOX_JITTER px on each coordinate of interactive elements
  - click jitter     : ±CLICK_JITTER px on recorded mouse click positions
  - confidence noise : ±CONF_NOISE added to element confidence (clamped 0-1)

Keyboard strokes and text are left untouched — text is deterministic.
Structural fields (element_id, type, window_role, etc.) are unchanged.

Output mirrors the source session folder structure under a new root:
  data/output/traces/forms/session_XXXX/  →  data/output/traces/forms_aug/session_XXXX_augN/

Usage
-----
  python scripts/augment_traces.py
  python scripts/augment_traces.py --source data/output/traces/forms --dest data/output/traces/forms_aug --copies 4
"""

from __future__ import annotations

import argparse
import copy
import json
import random
from pathlib import Path

# ── augmentation knobs ────────────────────────────────────────────────────────
BBOX_JITTER  = 5     # px; applied to each bbox coordinate independently
CLICK_JITTER = 4     # px; applied to recorded click x/y positions
CONF_NOISE   = 0.03  # absolute; added to confidence values (clamped 0–1)


def _jitter(value: float, amount: float, rng: random.Random) -> float:
    return value + rng.uniform(-amount, amount)


def _jitter_bbox(bbox: list, rng: random.Random) -> list:
    if not bbox or len(bbox) < 4:
        return bbox
    x1, y1, x2, y2 = bbox[:4]
    dx1 = rng.uniform(-BBOX_JITTER, BBOX_JITTER)
    dy1 = rng.uniform(-BBOX_JITTER, BBOX_JITTER)
    dx2 = rng.uniform(-BBOX_JITTER, BBOX_JITTER)
    dy2 = rng.uniform(-BBOX_JITTER, BBOX_JITTER)
    # Preserve ordering: x1 < x2, y1 < y2
    nx1 = min(x1 + dx1, x2 + dx2 - 1)
    nx2 = max(x1 + dx1 + 1, x2 + dx2)
    ny1 = min(y1 + dy1, y2 + dy2 - 1)
    ny2 = max(y1 + dy1 + 1, y2 + dy2)
    return [round(nx1), round(ny1), round(nx2), round(ny2)]


def _augment_elements(elements: list, rng: random.Random) -> list:
    out = []
    for elem in elements:
        e = copy.deepcopy(elem)
        e["bbox"] = _jitter_bbox(e.get("bbox", []), rng)
        conf = float(e.get("confidence", 1.0))
        e["confidence"] = max(0.0, min(1.0, conf + rng.uniform(-CONF_NOISE, CONF_NOISE)))
        out.append(e)
    return out


def _augment_state(state: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(state)
    s["elements"] = _augment_elements(s.get("elements", []), rng)
    return s


def _augment_mouse(mouse: dict, rng: random.Random) -> dict:
    m = copy.deepcopy(mouse)
    for action in m.get("actions", []):
        pos = action.get("position")
        if pos and len(pos) >= 2:
            action["position"] = [
                round(_jitter(pos[0], CLICK_JITTER, rng)),
                round(_jitter(pos[1], CLICK_JITTER, rng)),
            ]
    return m


def _augment_action(action: dict, rng: random.Random) -> dict:
    a = copy.deepcopy(action)
    pos = a.get("click_position")
    if pos and len(pos) >= 2:
        a["click_position"] = [
            round(_jitter(pos[0], CLICK_JITTER, rng)),
            round(_jitter(pos[1], CLICK_JITTER, rng)),
        ]
    return a


def augment_trace(trace: dict, seed: int) -> dict:
    rng = random.Random(seed)
    t = copy.deepcopy(trace)
    t["state"]      = _augment_state(t.get("state", {}), rng)
    t["next_state"] = _augment_state(t.get("next_state", {}), rng)
    t["mouse"]      = _augment_mouse(t.get("mouse", {}), rng)
    t["action"]     = _augment_action(t.get("action", {}), rng)
    t["augmented"]  = True
    t["aug_seed"]   = seed
    return t


def augment_session(session_dir: Path, dest_root: Path, copies: int) -> int:
    trace_files = sorted(f for f in session_dir.glob("*.json") if "manifest" not in f.name)
    if not trace_files:
        return 0

    written = 0
    for copy_idx in range(1, copies + 1):
        out_dir = dest_root / f"{session_dir.name}_aug{copy_idx}"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Copy manifest unchanged if present
        manifest = session_dir / "session_manifest.json"
        if manifest.exists():
            (out_dir / "session_manifest.json").write_bytes(manifest.read_bytes())

        for fpath in trace_files:
            trace = json.loads(fpath.read_text(encoding="utf-8"))
            seed  = hash(f"{fpath.name}:{copy_idx}") & 0xFFFFFFFF
            aug   = augment_trace(trace, seed)
            out_path = out_dir / fpath.name
            out_path.write_text(json.dumps(aug, ensure_ascii=False), encoding="utf-8")
            written += 1

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Augment trace sessions")
    parser.add_argument("--source", default="data/output/traces/forms",
                        help="Source directory containing session_* folders")
    parser.add_argument("--dest",   default="data/output/traces/forms_aug",
                        help="Output directory for augmented sessions")
    parser.add_argument("--copies", default=4, type=int,
                        help="Augmented copies per session (default 4 → 5x total data)")
    args = parser.parse_args()

    source = Path(args.source)
    dest   = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    sessions = sorted(d for d in source.iterdir() if d.is_dir() and d.name.startswith("session_"))
    if not sessions:
        print(f"No session_* folders found in {source}")
        return

    total = 0
    for s in sessions:
        n = augment_session(s, dest, args.copies)
        print(f"  {s.name}: {n} augmented traces written")
        total += n

    print(f"\nDone. {len(sessions)} sessions × {args.copies} copies = {total} new trace files")
    print(f"Original traces: ~{sum(len(list(s.glob('*.json'))) for s in sessions)} files")
    print(f"Total after augmentation: ~{total + sum(len(list(s.glob('*.json'))) for s in sessions)} files")


if __name__ == "__main__":
    main()
