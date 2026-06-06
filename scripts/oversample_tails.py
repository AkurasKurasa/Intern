"""
oversample_tails.py — emphasize the end-of-cycle (… → Submit) transition.

The "form is fully filled → click Submit" frame appears once per cycle, so the
model under-learns it and stalls at the end instead of submitting. This copies
each cycle's TAIL (the last few frames ending on the Submit click) K extra times
into the training dir, so the finish is well-represented. Pure data, automatic —
the model LEARNS to submit (no hardcoded completion rule).

Usage:
    python scripts/oversample_tails.py <clean_src_dir> <dst_dir> [tail] [copies]
    python scripts/oversample_tails.py data/demos/bottom-top-clean data/demos/bottom-top-oversample 6 4
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
        print("usage: python scripts/oversample_tails.py <src> <dst> [tail=6] [copies=4]")
        return
    src, dst = sys.argv[1], sys.argv[2]
    tail   = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    copies = int(sys.argv[4]) if len(sys.argv) > 4 else 4

    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)   # keep all the real sessions

    n_tails = 0
    for sess in sorted(glob.glob(os.path.join(src, "session_*"))):
        files = sorted(glob.glob(os.path.join(sess, "live_step_*.json")))
        frames = [json.load(open(f, encoding="utf-8")) for f in files]
        sub_idx = []
        for i, t in enumerate(frames):
            m = t.get("mouse", {}).get("actions", [])
            if not m:
                continue
            tgt = elem_at(t.get("next_state", {}), m[0].get("position")) or {}
            lbl = (tgt.get("label") or tgt.get("text") or "").lower()
            if "submit" in lbl:
                sub_idx.append(i)
        for si in sub_idx:
            seg = frames[max(0, si - tail + 1):si + 1]
            if len(seg) < 4:           # need >= hist_len
                continue
            for _ in range(copies):
                out = os.path.join(dst, f"session_tail_{n_tails:04d}")
                os.makedirs(out, exist_ok=True)
                for j, t in enumerate(seg):
                    json.dump(t, open(os.path.join(out, f"live_step_{j:04d}.json"),
                                      "w", encoding="utf-8"), ensure_ascii=False)
                n_tails += 1
    print(f"base sessions + {n_tails} oversampled tail-sessions -> {dst}")


if __name__ == "__main__":
    main()
