"""
replicate.py — copy a recorded session N times (terminal, no GUI).

Usage:
    python scripts/replicate.py                 # newest session in data/demos/policy_clicks x10
    python scripts/replicate.py <session_dir>   # that session x10
    python scripts/replicate.py <session_dir> <count>
"""
from __future__ import annotations
import os, sys, glob, json, shutil
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "components"))
sys.path.insert(0, _ROOT)

try:
    from recorder.recorder import _semantic_desc as _sd
except Exception:
    _sd = None


def _newest_session():
    base = os.path.join(_ROOT, "data", "demos", "policy_clicks")
    cands = [d for d in glob.glob(os.path.join(base, "*")) if os.path.isdir(d)]
    return max(cands, key=os.path.getmtime) if cands else None


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else _newest_session()
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    if not src or not os.path.isdir(src):
        print(f"  no session found: {src!r}")
        return
    if not os.path.isabs(src):
        src = os.path.join(_ROOT, src)

    step_files = sorted(glob.glob(os.path.join(src, "live_step_*.json")))
    if not step_files:
        print(f"  no live_step_*.json in {src}")
        return

    print(f"\n  source : {src}")
    print(f"  steps  : {len(step_files)}")
    print(f"  copies : {n}\n")
    print("  --- steps being replicated ---")
    for si, f in enumerate(step_files):
        try:
            t = json.load(open(f, encoding="utf-8"))
            m = t.get("mouse", {}).get("actions", [])
            k = t.get("keyboard", {}).get("actions", [])
            if _sd:
                at = "click" if m else ("keyboard" if k else "?")
                cp = m[0].get("position") if m else None
                txt = "".join(s.get("pasted_text") or s.get("key", "")
                              for s in (k[0].get("strokes", []) if k else []))
                hk = k[0].get("hotkey", "") if k else ""
                desc = _sd(at, cp, txt, [], t.get("next_state", {}), hotkey=hk)
            else:
                desc = m[0].get("type") if m else "kbd"
            print(f"    [{si:04d}] {desc}")
        except Exception as e:
            print(f"    [{si:04d}] <parse error: {e}>")
    print("  ------------------------------\n")

    out_base = os.path.join(_ROOT, "data", "demos", "human")
    os.makedirs(out_base, exist_ok=True)
    made = 0
    for i in range(n):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dst = os.path.join(out_base, f"session_copy_{ts}_{i:03d}")
        try:
            shutil.copytree(src, dst)
            made += 1
            print(f"    [{i+1:>3}/{n}] -> {os.path.basename(dst)}")
        except Exception as e:
            print(f"    [{i+1:>3}/{n}] FAILED: {e}")
    print(f"\n  DONE — {made} copies x {len(step_files)} steps -> data/demos/human\n")


if __name__ == "__main__":
    main()
