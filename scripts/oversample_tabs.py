"""
oversample_tabs.py — emphasize the rare TAB-TRANSITION clicks.

A click on a tab (Policyholder / Vehicle / …) appears ~2% of the time (93 of 4219
click frames), so the model under-learns where the tabs are and its pointer drifts
off-form. This copies each tab-click's lead-in segment (the few frames ending on
the tab click) K extra times into the training dir, so the transition is
well-represented and the model LEARNS to navigate to the tabs itself — pure data,
automatic, no hardcoded tab coordinates.

Usage:
    python scripts/oversample_tabs.py <clean_src_dir> <dst_dir> [tail=6] [copies=10]
"""
from __future__ import annotations
import sys, os, glob, json, shutil


def elem_at(state, pos, role="active"):
    if not state or not pos:
        return None
    px, py = pos
    best, ba = None, 1e18
    for e in state.get("elements", []):
        if role and e.get("window_role") != role:
            continue
        b = e.get("bbox")
        if not b or len(b) != 4:
            continue
        if b[0] <= px <= b[2] and b[1] <= py <= b[3]:
            a = (b[2] - b[0]) * (b[3] - b[1])
            if a < ba:
                best, ba = e, a
    if best is None and role == "active":
        return elem_at(state, pos, None)
    return best


def main():
    if len(sys.argv) < 3:
        print("usage: python scripts/oversample_tabs.py <src> <dst> [tail=6] [copies=10]")
        return
    src, dst = sys.argv[1], sys.argv[2]
    tail   = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    copies = int(sys.argv[4]) if len(sys.argv) > 4 else 10

    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)   # keep all the real sessions

    n = 0
    by_tab: dict = {}
    for sess in sorted(glob.glob(os.path.join(src, "session_*"))):
        files = sorted(glob.glob(os.path.join(sess, "live_step_*.json")))
        frames = [json.load(open(f, encoding="utf-8")) for f in files]
        tab_idx = []
        for i, t in enumerate(frames):
            m = t.get("mouse", {}).get("actions", [])
            if not m:
                continue
            tgt = elem_at(t.get("next_state", {}), m[0].get("position")) or {}
            if "tabitem" in (tgt.get("type") or "").lower():
                tab_idx.append(i)
                lbl = (tgt.get("label") or tgt.get("text") or "").strip()
                by_tab[lbl] = by_tab.get(lbl, 0) + 1
        for ti in tab_idx:
            seg = frames[max(0, ti - tail + 1):ti + 1]
            if len(seg) < 4:           # need >= hist_len
                continue
            for _ in range(copies):
                out = os.path.join(dst, f"session_tab_{n:05d}")
                os.makedirs(out, exist_ok=True)
                for j, t in enumerate(seg):
                    json.dump(t, open(os.path.join(out, f"live_step_{j:04d}.json"),
                                      "w", encoding="utf-8"), ensure_ascii=False)
                n += 1
    print(f"base sessions + {n} oversampled tab-sessions -> {dst}")
    print(f"tab clicks found per tab: {by_tab}")


if __name__ == "__main__":
    main()
