"""
diagnose_click_rejections.py — for every click the matcher REJECTS (returns -1),
log what control types were actually under the click point. Tells us whether
we're rejecting legitimate interactive types that should be whitelisted, or
rejecting clicks that genuinely land only on decorative / empty space.

Usage:
    python scripts/diagnose_click_rejections.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from glob import glob
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from components.intelligence.model.transformer import (
    _CLICK_TARGET_CTRL_TYPES, _find_click_elem_idx,
)


def containing_element_ctypes(mouse: dict, state: dict, max_elements: int):
    """
    Return a list of (ctype, area) for EVERY element (regardless of type)
    whose bbox contains the click point, sorted by area ascending.
    """
    clicks = [a for a in mouse.get("actions", []) if a.get("type") in ("click", "double_click")]
    if not clicks:
        return []
    pos = clicks[0].get("position", [0, 0])
    px, py = float(pos[0]), float(pos[1])
    elements = state.get("elements", [])[:max_elements]
    hits = []
    for elem in elements:
        bbox = elem.get("bbox", [0, 0, 0, 0])
        if len(bbox) < 4:
            continue
        x1, y1, x2, y2 = (float(v) for v in bbox[:4])
        if x2 <= x1 or y2 <= y1:
            continue
        if x1 <= px <= x2 and y1 <= py <= y2:
            ctype = (elem.get("type") or "").lower()
            area = (x2 - x1) * (y2 - y1)
            hits.append((ctype, area))
    hits.sort(key=lambda h: h[1])
    return hits


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data_dir", default="C:/Users/Ralph/Downloads/fourpointfive-k")
    p.add_argument("--max_elements", type=int, default=128)
    args = p.parse_args()

    root = Path(args.data_dir)
    sessions = sorted(d for d in os.listdir(root) if (root / d).is_dir())

    total_clicks = 0
    matched_ok = 0
    rejected = 0
    # Stats on rejected clicks
    reject_no_hits = 0                    # click landed on zero elements
    reject_only_decorative = 0            # all hits were decorative
    smallest_hit_ctype = Counter()        # ctype of the smallest element under rejected click
    all_hit_ctypes = Counter()            # every ctype that appears under any rejected click
    first_decorative_text_samples = []    # for context

    for s in sessions:
        for f in sorted(glob(str(root / s / "*.json"))):
            try:
                t = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            mouse = t.get("mouse", {})
            if not any(a.get("type") in ("click", "double_click")
                       for a in mouse.get("actions", [])):
                continue
            total_clicks += 1
            state = t.get("state", {})
            idx = _find_click_elem_idx(mouse, state, args.max_elements)
            if idx != -1:
                matched_ok += 1
                continue

            rejected += 1
            hits = containing_element_ctypes(mouse, state, args.max_elements)
            if not hits:
                reject_no_hits += 1
                continue

            # At least one element was under the click but none were interactive
            reject_only_decorative += 1
            smallest_ctype = hits[0][0]
            smallest_hit_ctype[smallest_ctype] += 1
            for ctype, _ in hits:
                all_hit_ctypes[ctype] += 1

            # Capture a few rejected sample specimens for context
            if len(first_decorative_text_samples) < 5:
                state_elems = state.get("elements", [])[: args.max_elements]
                # re-find index of smallest hit
                for i, elem in enumerate(state_elems):
                    bbox = elem.get("bbox", [0, 0, 0, 0])
                    if len(bbox) < 4:
                        continue
                    x1, y1, x2, y2 = (float(v) for v in bbox[:4])
                    if x2 <= x1 or y2 <= y1:
                        continue
                    mouse_actions = mouse.get("actions", [])
                    clicks = [a for a in mouse_actions if a.get("type") in ("click", "double_click")]
                    if not clicks:
                        break
                    pos = clicks[0].get("position", [0, 0])
                    px, py = float(pos[0]), float(pos[1])
                    if x1 <= px <= x2 and y1 <= py <= y2:
                        area = (x2 - x1) * (y2 - y1)
                        if area == hits[0][1]:
                            first_decorative_text_samples.append({
                                "file": os.path.basename(f),
                                "click_px": [px, py],
                                "ctype": (elem.get("type") or "").lower(),
                                "text": (elem.get("text") or "")[:60],
                                "bbox": bbox,
                            })
                            break

    print("=" * 70)
    print("Click rejection reason analysis")
    print("=" * 70)
    print(f"Total click samples:         {total_clicks}")
    print(f"  matched (valid click_idx): {matched_ok}  ({100 * matched_ok / max(total_clicks, 1):.1f}%)")
    print(f"  rejected (click_idx = -1): {rejected}  ({100 * rejected / max(total_clicks, 1):.1f}%)")
    print()
    print(f"Breakdown of the {rejected} rejections:")
    print(f"  click landed on ZERO elements:        {reject_no_hits}  "
          f"({100 * reject_no_hits / max(rejected, 1):.1f}%)")
    print(f"  click landed only on NON-whitelisted: {reject_only_decorative}  "
          f"({100 * reject_only_decorative / max(rejected, 1):.1f}%)")
    print()
    print("Control type of the SMALLEST element under rejected clicks")
    print("(what the old matcher would have picked):")
    print(f"  {'count':>6} {'frac':>7}  ctype")
    for ctype, c in smallest_hit_ctype.most_common(20):
        frac = c / max(reject_only_decorative, 1)
        in_wl = "WL" if ctype in _CLICK_TARGET_CTRL_TYPES else "--"
        print(f"  {c:>6} {frac:>7.3f}  [{in_wl}] {ctype}")

    print()
    print("All control types that appear under any rejected click:")
    print(f"  {'count':>6}  ctype")
    for ctype, c in all_hit_ctypes.most_common(20):
        in_wl = "WL" if ctype in _CLICK_TARGET_CTRL_TYPES else "--"
        print(f"  {c:>6}  [{in_wl}] {ctype}")

    print()
    print("Sample rejected clicks (first 5):")
    for s in first_decorative_text_samples:
        print(f"  {s['file']}  ctype={s['ctype']}  text={s['text']!r}")
        print(f"     click_px={s['click_px']}  bbox={s['bbox']}")


if __name__ == "__main__":
    main()
