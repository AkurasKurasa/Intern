"""
scripts/eval_click_by_tab.py
=============================
Per-tab breakdown of click-target accuracy on the model's own held-out
validation split (no live GUI run needed — pure offline analysis).

Reconstructs the EXACT validation split used by the last small-batch
training run (same data_dir, seed, val_split) so this evaluates only
examples the model never trained on, then groups correct/incorrect click
predictions by which tab the true target field belongs to.

Known limitation: the tab-to-field map (bc_fidelity._LABEL_TO_KEY) only
covers Policy/Policyholder/Vehicle — Coverage/Drivers/Claims/Payment
targets fall into "Unknown". Still useful for the tabs it does cover.

Usage:
    python scripts/eval_click_by_tab.py
    python scripts/eval_click_by_tab.py --model tasks/form_filling/model.pt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "components"))
sys.path.insert(0, str(_ROOT / "scripts"))

import torch
from intelligence.model.transformer import TrajectoryDataset, predict, _load_embed_cache
from bc_fidelity import _LABEL_TO_KEY, _tab_of


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trace_dir", default="data/demos/eight_Tabs")
    ap.add_argument("--model", default="tasks/form_filling/model.pt")
    ap.add_argument("--val_split", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    data_dir = str(_ROOT / args.trace_dir) if not os.path.isabs(args.trace_dir) else args.trace_dir
    model_path = str(_ROOT / args.model) if not os.path.isabs(args.model) else args.model

    print(f"Loading dataset from {data_dir} (rebuilding val split: seed={args.seed}, val_split={args.val_split}) ...")
    dataset = TrajectoryDataset(data_dir, max_elements=128, hist_len=4, aug_drop_prob=0.1)

    n_val = max(1, int(len(dataset) * args.val_split))
    n_train = len(dataset) - n_val
    g = torch.Generator().manual_seed(args.seed)
    train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val], generator=g)
    val_indices = list(val_ds.indices)
    print(f"Validation set: {len(val_indices)} samples (of {len(dataset)} total)")

    _load_embed_cache(str(Path(model_path).with_name("embed_cache.pkl")))

    def _load_trace(fpath: Path):
        for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                return json.loads(fpath.read_text(encoding=enc))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        return None

    def _tab_of_label(label: str) -> str:
        key = _LABEL_TO_KEY.get((label or "").strip().lower())
        return _tab_of(key) if key else "Unknown (Coverage/Drivers/Claims/Payment/other)"

    per_tab_correct: Counter = Counter()
    per_tab_total: Counter = Counter()
    confusions: defaultdict = defaultdict(Counter)  # tab -> Counter of predicted labels when wrong
    n_evaluated = 0
    n_skipped = 0

    for i, idx in enumerate(val_indices):
        gi, win_start, p_types, p_cont, tgt_type, tgt_click_idx, tgt_key, src_idx = dataset._samples[idx]
        if tgt_click_idx < 0:
            continue  # not a click-target sample

        files = dataset._grouped_files[gi][win_start: win_start + dataset.hist_len]
        if len(files) < 2:
            n_skipped += 1
            continue

        traces = [_load_trace(f) for f in files]
        if any(t is None for t in traces):
            n_skipped += 1
            continue

        *ctx_traces, cur_trace = traces
        state = cur_trace.get("state", {})
        elements = state.get("elements", [])
        if not (0 <= tgt_click_idx < len(elements)):
            n_skipped += 1
            continue

        history = []
        for t in ctx_traces:
            action = t.get("action", {}) or {}
            history.append({
                "state": t.get("state", {}),
                "action_type": action.get("action_type") or "no_op",
                "click_xy": action.get("click_position") or [0.0, 0.0],
                "key_count": action.get("key_count") or 0,
            })

        result = predict(state, history=history, model_path=model_path, device_str=args.device)
        pred_idx = result.get("click_elem_idx", -1)

        true_label = (elements[tgt_click_idx].get("label") or elements[tgt_click_idx].get("text") or "").strip()
        tab = _tab_of_label(true_label)

        per_tab_total[tab] += 1
        if pred_idx == tgt_click_idx:
            per_tab_correct[tab] += 1
        else:
            pred_label = ""
            if 0 <= pred_idx < len(elements):
                pred_label = (elements[pred_idx].get("label") or elements[pred_idx].get("text") or "").strip()
            confusions[tab][f"{true_label!r} -> predicted {pred_label!r}"] += 1

        n_evaluated += 1
        if (i + 1) % 50 == 0:
            print(f"  ...{i+1}/{len(val_indices)}", flush=True)

    print(f"\n{'='*90}\n  PER-TAB CLICK ACCURACY (held-out validation set, n={n_evaluated}, skipped={n_skipped})\n{'='*90}")
    overall_correct = sum(per_tab_correct.values())
    overall_total = sum(per_tab_total.values())
    for tab in sorted(per_tab_total, key=lambda t: -per_tab_total[t]):
        c, tot = per_tab_correct[tab], per_tab_total[tab]
        pct = c / tot * 100 if tot else 0.0
        print(f"  {tab:<45} {c:>4}/{tot:<4} ({pct:5.1f}%)")
    print(f"  {'-'*86}")
    print(f"  {'OVERALL':<45} {overall_correct:>4}/{overall_total:<4} "
          f"({overall_correct/overall_total*100 if overall_total else 0:5.1f}%)")
    print(f"{'='*90}")

    print("\nSample confusions (top 5 per tab):")
    for tab, ctr in confusions.items():
        print(f"\n  {tab}:")
        for desc, n in ctr.most_common(5):
            print(f"    x{n}  {desc}")


if __name__ == "__main__":
    main()
