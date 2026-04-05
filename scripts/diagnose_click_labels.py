"""
diagnose_click_labels.py — answer two questions about the click-pointer training data:

  Q1. How many recorded clicks actually land inside some element's bbox?
      (i.e. what fraction of click samples get `click_idx != -1`?)
      If this is low, the recorder-vs-click timing gap is producing stale
      bboxes and we're losing training signal before the model even sees it.

  Q2. For a given element *index* in the padded list, how many distinct
      real-world *identities* does it point to across traces?
      Pointer heads predict an index; if index 37 means "First Name" in
      session A and "State" in session B, the labels are contradictory and
      no model can learn them. A high "distinct identities per index" count
      is the smoking gun for UIA element-ordering instability.

Usage:
    python scripts/diagnose_click_labels.py [--data_dir DIR] [--max_elements 128]
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from glob import glob
from pathlib import Path


def find_click_elem_idx(mouse, state, max_elements):
    clicks = [a for a in mouse.get("actions", []) if a.get("type") in ("click", "double_click")]
    if not clicks:
        return -1
    pos = clicks[0].get("position", [0, 0])
    px, py = float(pos[0]), float(pos[1])
    elements = state.get("elements", [])[:max_elements]
    best_idx = -1
    best_area = float("inf")
    for idx, elem in enumerate(elements):
        bbox = elem.get("bbox", [0, 0, 0, 0])
        if len(bbox) < 4:
            continue
        x1, y1, x2, y2 = (float(v) for v in bbox[:4])
        if x2 <= x1 or y2 <= y1:
            continue
        if x1 <= px <= x2 and y1 <= py <= y2:
            area = (x2 - x1) * (y2 - y1)
            if area < best_area:
                best_area = area
                best_idx = idx
    return best_idx


def elem_signature(elem):
    """
    Stable identity key for an element: (text[:40], control_type, bbox_center_snap).
    Elements with the same signature are treated as "the same UI thing".
    """
    text = (elem.get("text") or "").strip().lower()[:40]
    ctype = (elem.get("type") or "").lower()
    bbox = elem.get("bbox", [0, 0, 0, 0])
    if len(bbox) >= 4:
        cx = round((float(bbox[0]) + float(bbox[2])) / 2 / 50) * 50  # 50px grid
        cy = round((float(bbox[1]) + float(bbox[3])) / 2 / 50) * 50
    else:
        cx = cy = 0
    return (text, ctype, cx, cy)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data_dir", default="C:/Users/Ralph/Downloads/fourpointfive-k")
    p.add_argument("--max_elements", type=int, default=128)
    args = p.parse_args()

    root = Path(args.data_dir)
    sessions = sorted(d for d in os.listdir(root) if (root / d).is_dir())

    # -------- Q1: overall and per-session click match rate --------
    all_clicks = 0
    matched = 0
    per_session_match = defaultdict(lambda: [0, 0])  # session -> [matched, total]

    # -------- Q2: index -> identity distributions --------
    index_identities_global = defaultdict(Counter)             # idx -> Counter(sig -> count)
    per_session_index_identities = defaultdict(lambda: defaultdict(Counter))  # session -> idx -> Counter

    # Also track the reverse: identity -> set of indices it's seen under (across sessions)
    identity_indices = defaultdict(Counter)  # sig -> Counter(idx -> count)

    for s in sessions:
        for f in sorted(glob(str(root / s / "*.json"))):
            try:
                t = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            mouse = t.get("mouse", {})
            if not any(a.get("type") in ("click", "double_click") for a in mouse.get("actions", [])):
                continue
            all_clicks += 1
            per_session_match[s][1] += 1
            state = t.get("state", {})
            idx = find_click_elem_idx(mouse, state, args.max_elements)
            if idx == -1:
                continue
            matched += 1
            per_session_match[s][0] += 1
            elems = state.get("elements", [])[: args.max_elements]
            if idx < len(elems):
                sig = elem_signature(elems[idx])
                index_identities_global[idx][sig] += 1
                per_session_index_identities[s][idx][sig] += 1
                identity_indices[sig][idx] += 1

    # ============ REPORT ============
    print("=" * 70)
    print("Q1. Click-to-element match rate")
    print("=" * 70)
    print(f"Total clicks:  {all_clicks}")
    print(f"Matched:       {matched}  ({100 * matched / max(all_clicks, 1):.1f}%)")
    print(f"Unmatched:     {all_clicks - matched}  ({100 * (all_clicks - matched) / max(all_clicks, 1):.1f}%)")
    print()
    for s, (mc, tc) in sorted(per_session_match.items()):
        print(f"  {s}: {mc}/{tc}  ({100 * mc / max(tc, 1):.1f}%)")

    # --- Q2a: index -> distinct identities, globally ---
    print()
    print("=" * 70)
    print("Q2a. How many distinct identities does each *index* point to? (GLOBAL)")
    print("=" * 70)
    diverse = []
    for idx, ctr in index_identities_global.items():
        total = sum(ctr.values())
        if total < 10:  # ignore rarely-seen indices
            continue
        n_distinct = len(ctr)
        top_sig, top_count = ctr.most_common(1)[0]
        diverse.append((idx, total, n_distinct, top_count / total, top_sig))

    print(f"Indices seen >=10 times: {len(diverse)}")
    if diverse:
        avg_distinct = sum(d[2] for d in diverse) / len(diverse)
        print(f"Avg distinct identities per index: {avg_distinct:.2f}")
        print(f"Indices with >=2 distinct identities: {sum(1 for d in diverse if d[2] >= 2)} / {len(diverse)}")
        print(f"Indices where top identity is <50% of samples: {sum(1 for d in diverse if d[3] < 0.5)} / {len(diverse)}")
        print()
        print("Top 15 indices ranked by identity diversity (high = unstable):")
        print(f"  {'idx':>4} {'n':>6} {'distinct':>9} {'top_frac':>9}  top_signature (ctype, text, cx, cy)")
        for idx, total, n, top_frac, top_sig in sorted(diverse, key=lambda x: (-x[2], -x[1]))[:15]:
            text, ctype, cx, cy = top_sig
            print(f"  {idx:>4} {total:>6} {n:>9} {top_frac:>9.2f}  ({ctype}, {text!r}, {cx}, {cy})")

    # --- Q2b: are the "same" identities landing on different indices? ---
    print()
    print("=" * 70)
    print("Q2b. How many distinct *indices* does each identity appear at? (GLOBAL)")
    print("=" * 70)
    id_stats = []
    for sig, ctr in identity_indices.items():
        total = sum(ctr.values())
        if total < 10:
            continue
        n_distinct = len(ctr)
        top_idx, top_count = ctr.most_common(1)[0]
        id_stats.append((sig, total, n_distinct, top_count / total, top_idx))

    print(f"Identities seen >=10 times: {len(id_stats)}")
    if id_stats:
        avg_indices = sum(x[2] for x in id_stats) / len(id_stats)
        print(f"Avg distinct indices per identity: {avg_indices:.2f}")
        print(f"Identities with >=2 distinct indices: {sum(1 for x in id_stats if x[2] >= 2)} / {len(id_stats)}")
        print(f"Identities where top index is <50% of samples: {sum(1 for x in id_stats if x[3] < 0.5)} / {len(id_stats)}")
        print()
        print("Top 15 identities ranked by index drift (high = same thing, different indices):")
        print(f"  {'n':>6} {'distinct':>9} {'top_frac':>9} {'top_idx':>8}  identity (ctype, text, cx, cy)")
        for sig, total, n, top_frac, top_idx in sorted(id_stats, key=lambda x: (-x[2], -x[1]))[:15]:
            text, ctype, cx, cy = sig
            print(f"  {total:>6} {n:>9} {top_frac:>9.2f} {top_idx:>8}  ({ctype}, {text!r}, {cx}, {cy})")

    # --- Q2c: within-session stability (much more forgiving test) ---
    print()
    print("=" * 70)
    print("Q2c. Within each SESSION, is the index->identity mapping stable?")
    print("=" * 70)
    for s in sorted(per_session_index_identities.keys()):
        idx_map = per_session_index_identities[s]
        diverse_in_session = 0
        total_indices = 0
        for idx, ctr in idx_map.items():
            if sum(ctr.values()) < 5:
                continue
            total_indices += 1
            if len(ctr) >= 2:
                diverse_in_session += 1
        if total_indices > 0:
            print(f"  {s}: {diverse_in_session}/{total_indices} indices have >=2 identities "
                  f"({100 * diverse_in_session / total_indices:.1f}%)")


if __name__ == "__main__":
    main()
