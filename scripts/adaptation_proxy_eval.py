"""
scripts/adaptation_proxy_eval.py
===================================
Objective 6: "the model's success rate when deployed to a new, unseen GUI
environment ... will be >=75%".

The literal thesis test needs a genuinely different/perturbed live
environment and a real run against it -- that's a live GUI-automation task,
which this project reserves for the human to run, not something this script
does. What THIS measures instead is a proxy: take real recorded states,
synthetically perturb their PRESENTATION (element list order, window
position) without changing the underlying task, and check whether the model
still identifies the same field it would have picked on the unperturbed
state. A model that can only navigate the exact pixel/order layout it
memorized would fail this; a model that's learned the actual task structure
should not.

This is NOT a substitute for the real unseen-GUI test -- it can't test
genuinely novel labels, control types, or tab structures, only robustness to
superficial layout drift on the known form. Treat a pass here as "not
obviously overfit to exact layout," not as "adapts to new GUIs." The real
test (scope1_completion_roadmap step 7 / adaptability_unseen_success_75)
still needs a dedicated held-out environment and a live run.

Perturbations applied per state (independently, each gets its own pass):
  - shuffle:      randomly reorder state['elements'] (tests robustness to
                   accessibility-tree traversal order, which the live agent
                   cannot control).
  - translate:    shift every element's bbox by the same random (dx, dy)
                   (tests robustness to the window being moved/resized,
                   which changes absolute but not relative geometry).
  - both:         shuffle + translate together (the harder, combined case).

Metric: success_rate = fraction of steps where the model's predicted click
(by (type, label) identity, not index -- shuffling invalidates index
comparison) on the PERTURBED state still names the same element as the
ground-truth click the human actually made on the ORIGINAL state.

Usage
-----
  python scripts/adaptation_proxy_eval.py                     # all eight_Tabs sessions
  python scripts/adaptation_proxy_eval.py <session_dir>
  python scripts/adaptation_proxy_eval.py --log
  python scripts/adaptation_proxy_eval.py --seed 7 --max-translate 200
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "components"))
sys.path.insert(0, str(_ROOT))

from intelligence.model.transformer import predict, _find_click_elem_idx  # noqa: E402

MODEL = str(_ROOT / "tasks" / "form_filling" / "model.pt")
DEFAULT_SESSIONS_ROOT = _ROOT / "data" / "demos" / "eight_Tabs"
LOG_PATH = _ROOT / "data" / "output" / "adaptation_proxy_log.jsonl"


def _label_of(e: dict) -> str:
    return ((e.get("label") or e.get("text") or "").strip()) if e else "?"


def _signature(e: dict) -> tuple:
    return ((e.get("type") or "").lower(), _label_of(e).lower())


def _perturb_shuffle(state: dict, rng: random.Random) -> dict:
    elements = list(state.get("elements", []))
    rng.shuffle(elements)
    return {**state, "elements": elements}


def _perturb_translate(state: dict, rng: random.Random, max_translate: int) -> dict:
    dx = rng.randint(-max_translate, max_translate)
    dy = rng.randint(-max_translate, max_translate)
    elements = []
    for e in state.get("elements", []):
        e2 = dict(e)
        b = e.get("bbox")
        if b and len(b) == 4:
            e2["bbox"] = [max(0, b[0] + dx), max(0, b[1] + dy),
                           max(0, b[2] + dx), max(0, b[3] + dy)]
        elements.append(e2)
    return {**state, "elements": elements}


PERTURBATIONS = {
    "shuffle":   lambda state, rng, mt: _perturb_shuffle(state, rng),
    "translate": lambda state, rng, mt: _perturb_translate(state, rng, mt),
    "both":      lambda state, rng, mt: _perturb_shuffle(_perturb_translate(state, rng, mt), rng),
}


def eval_session(session_dir: Path, rng: random.Random, max_translate: int) -> dict:
    files = sorted(glob.glob(os.path.join(str(session_dir), "live_step_*.json")))
    per_mode = {mode: {"hit": 0, "total": 0} for mode in PERTURBATIONS}
    baseline = {"hit": 0, "total": 0}
    history: list = []

    for f in files:
        try:
            t = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        state = t.get("state", {})
        els = state.get("elements", [])
        m = t.get("mouse", {}).get("actions", [])
        if not m or not els:
            continue
        actual_idx = _find_click_elem_idx(t.get("mouse", {}), state, len(els) or 128)
        if not (0 <= actual_idx < len(els)):
            continue
        gold_sig = _signature(els[actual_idx])
        if gold_sig[1] in ("", "?"):
            continue

        # device_str="cpu" pinned deliberately -- this script runs thousands of
        # single-state forward passes and is often run alongside a real GPU
        # training job (see DEVELOPERS.md); CPU inference avoids contending
        # for the GPU while still being fast enough for this workload.
        base_pred = predict(state=state, history=history[-3:], model_path=MODEL, device_str="cpu")
        bi = base_pred.get("click_elem_idx", -1)
        base_sig = _signature(els[bi]) if 0 <= bi < len(els) else None
        baseline["total"] += 1
        baseline["hit"] += 1 if base_sig == gold_sig else 0

        for mode, fn in PERTURBATIONS.items():
            pstate = fn(state, rng, max_translate)
            pels = pstate.get("elements", [])
            ppred = predict(state=pstate, history=history[-3:], model_path=MODEL, device_str="cpu")
            pi = ppred.get("click_elem_idx", -1)
            psig = _signature(pels[pi]) if 0 <= pi < len(pels) else None
            per_mode[mode]["total"] += 1
            per_mode[mode]["hit"] += 1 if psig == gold_sig else 0

        res = state.get("screen_resolution", [1920, 1080])
        W = float(res[0]) or 1920.0
        H = float(res[1]) or 1080.0
        pos = m[0].get("position", [0, 0])
        history.append({
            "state": state, "action_type": "click",
            "click_xy": [pos[0] / W, pos[1] / H], "key_count": 0,
        })

    def _rate(d: dict) -> float:
        return d["hit"] / d["total"] if d["total"] else 0.0

    return {
        "session": session_dir.name,
        "steps_evaluated": baseline["total"],
        "baseline_accuracy": round(_rate(baseline), 4),
        **{f"{mode}_success_rate": round(_rate(per_mode[mode]), 4) for mode in PERTURBATIONS},
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Objective 6 proxy: model robustness to synthetic layout perturbation (target >=75%).")
    ap.add_argument("session", nargs="?", help="Specific session dir (default: all under data/demos/eight_Tabs).")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-translate", type=int, default=150,
                     help="Max pixel shift per axis for the 'translate' perturbation (default 150).")
    ap.add_argument("--log", action="store_true", help="Append the aggregate result to a jsonl trend log.")
    args = ap.parse_args()

    if args.session:
        sessions = [Path(args.session)]
    else:
        sessions = sorted(d for d in DEFAULT_SESSIONS_ROOT.glob("session_*") if d.is_dir())

    if not sessions:
        print(f"No sessions found under {DEFAULT_SESSIONS_ROOT}")
        sys.exit(1)

    rng = random.Random(args.seed)
    t0 = time.time()
    results = [eval_session(s, rng, args.max_translate) for s in sessions]
    results = [r for r in results if r["steps_evaluated"] > 0]
    elapsed = time.time() - t0

    print(f"\n{'='*78}")
    print("  ADAPTATION PROXY — synthetic layout perturbation (objective 6, target >=75%)")
    print(f"{'='*78}")
    print(f"  {'session':<28} {'n':>5} {'baseline':>10} {'shuffle':>10} {'translate':>10} {'both':>10}")
    for r in results:
        print(f"  {r['session']:<28} {r['steps_evaluated']:>5} "
              f"{r['baseline_accuracy']*100:>9.1f}% {r['shuffle_success_rate']*100:>9.1f}% "
              f"{r['translate_success_rate']*100:>9.1f}% {r['both_success_rate']*100:>9.1f}%")
    print(f"  {'-'*74}")

    def _overall(key: str) -> float:
        total_hit = sum(r[key] * r["steps_evaluated"] for r in results)
        total_n = sum(r["steps_evaluated"] for r in results)
        return total_hit / total_n if total_n else 0.0

    overall_baseline  = _overall("baseline_accuracy")
    overall_shuffle   = _overall("shuffle_success_rate")
    overall_translate = _overall("translate_success_rate")
    overall_both      = _overall("both_success_rate")
    print(f"  {'OVERALL':<28} {sum(r['steps_evaluated'] for r in results):>5} "
          f"{overall_baseline*100:>9.1f}% {overall_shuffle*100:>9.1f}% "
          f"{overall_translate*100:>9.1f}% {overall_both*100:>9.1f}%")
    print(f"  Evaluated {len(results)} session(s) in {elapsed:.2f}s")
    print(f"  [{'PASS' if overall_both >= 0.75 else 'FAIL'}] combined perturbation vs. 75% target")
    print(f"{'='*78}\n")

    if args.log:
        import datetime as _dt
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": _dt.datetime.now().isoformat(),
            "sessions_checked": len(results),
            "overall_baseline_accuracy": round(overall_baseline, 4),
            "overall_shuffle_success_rate": round(overall_shuffle, 4),
            "overall_translate_success_rate": round(overall_translate, 4),
            "overall_both_success_rate": round(overall_both, 4),
            "meets_75pct_target": overall_both >= 0.75,
            "note": "synthetic-perturbation proxy, not a genuine unseen-GUI live test",
        }
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"Logged to {LOG_PATH}")


if __name__ == "__main__":
    main()
