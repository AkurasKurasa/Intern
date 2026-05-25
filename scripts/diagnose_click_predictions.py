"""
diagnose_click_predictions.py — answer two questions about the plateau:

  D1. How concentrated is the click TARGET distribution?
      If one target accounts for ~22% of clicks, "22% accuracy" could just
      be the majority-baseline and the model may have collapsed to predicting
      that one target.

  D2. What does the trained model actually predict on the val set?
      If it predicts only a handful of unique indices, it has collapsed to a
      degenerate solution. If it predicts broadly but gets most wrong, it is
      genuinely confused, which is a different problem.

Usage:
    python scripts/diagnose_click_predictions.py \
        --data_dir C:/Users/Ralph/Downloads/fourpointfive-k \
        --checkpoint tasks/form_filling/model.pt
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

# Allow running as `python scripts/diagnose_click_predictions.py` from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import torch
from torch.utils.data import DataLoader, random_split

from components.intelligence.model.transformer import (
    ACTION_CLICK, ELEM_FEATURES, TrajectoryDataset,
    TransformerAgentNetwork, _find_click_elem_idx,
)


def elem_signature(elem):
    """Identity key — text[:40] + control_type. Position is excluded so that
    the same field at different indices still counts as the same identity."""
    text = (elem.get("text") or "").strip().lower()[:40]
    ctype = (elem.get("type") or "").lower()
    return (ctype, text)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data_dir", default="C:/Users/Ralph/Downloads/fourpointfive-k")
    p.add_argument("--checkpoint", default="tasks/form_filling/model.pt")
    p.add_argument("--max_elements", type=int, default=128)
    p.add_argument("--hist_len", type=int, default=4)
    p.add_argument("--val_split", type=float, default=0.2)
    p.add_argument("--batch_size", type=int, default=16)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    # Build the same dataset the trainer used, with the same split seed
    print("[D1] Building dataset to recover the exact train/val split the trainer used ...")
    dataset = TrajectoryDataset(
        args.data_dir, max_elements=args.max_elements, hist_len=args.hist_len,
    )
    torch.manual_seed(args.seed)
    n_val = max(1, int(len(dataset) * args.val_split))
    n_train = len(dataset) - n_val
    g = torch.Generator().manual_seed(args.seed)
    train_ds, val_ds = random_split(dataset, [n_train, n_val], generator=g)

    print(f"    dataset size = {len(dataset)},  train = {n_train},  val = {n_val}")

    # -------- D1: click target concentration --------
    # Walk every training sample, resolve the click_idx to an element identity
    # using the same trace source the dataset built its labels from. Since
    # TrajectoryDataset already stashes samples with tgt_click_idx but NOT the
    # raw state, we instead iterate raw traces to get a clean identity count
    # over the click-only population (which is what actually trains the head).
    print()
    print("=" * 70)
    print("D1. Click target identity distribution (over ALL click samples)")
    print("=" * 70)
    import json
    from glob import glob
    import os

    identity_counts = Counter()
    all_click_samples = 0
    unmatched = 0
    for session in sorted(os.listdir(args.data_dir)):
        spath = Path(args.data_dir) / session
        if not spath.is_dir():
            continue
        for f in sorted(glob(str(spath / "*.json"))):
            try:
                t = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            mouse = t.get("mouse", {})
            if not any(a.get("type") in ("click", "double_click")
                       for a in mouse.get("actions", [])):
                continue
            all_click_samples += 1
            state = t.get("state", {})
            idx = _find_click_elem_idx(mouse, state, args.max_elements)
            if idx == -1:
                unmatched += 1
                continue
            elems = state.get("elements", [])[: args.max_elements]
            if idx >= len(elems):
                unmatched += 1
                continue
            identity_counts[elem_signature(elems[idx])] += 1

    matched = sum(identity_counts.values())
    unique_identities = len(identity_counts)
    print(f"Total click samples (matched): {matched}   (unmatched: {unmatched})")
    print(f"Distinct click target identities: {unique_identities}")
    print()
    # Show top N
    top = identity_counts.most_common(15)
    print(f"Top 15 most-clicked identities:")
    print(f"  {'count':>6} {'frac':>6}  identity (ctype, text)")
    for sig, c in top:
        frac = c / max(matched, 1)
        ctype, text = sig
        print(f"  {c:>6} {frac:>6.3f}  ({ctype}, {text!r})")

    top1_frac = top[0][1] / max(matched, 1) if top else 0.0
    top5_frac = sum(c for _, c in top[:5]) / max(matched, 1)
    top10_frac = sum(c for _, c in top[:10]) / max(matched, 1)
    print()
    print(f"Top-1 identity share : {top1_frac:.3f}   (majority-baseline accuracy)")
    print(f"Top-5 identity share : {top5_frac:.3f}")
    print(f"Top-10 identity share: {top10_frac:.3f}")

    # -------- D2: what does the trained model actually predict? --------
    print()
    print("=" * 70)
    print("D2. Trained model prediction behavior on the val set")
    print("=" * 70)

    ckpt_path = Path(args.checkpoint)
    if not ckpt_path.exists():
        print(f"[!] Checkpoint not found at {ckpt_path} — skipping D2")
        return

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    ckpt = torch.load(ckpt_path, map_location=device, weights_only=True)
    hp = ckpt.get("hyperparams", {})
    model = TransformerAgentNetwork(
        elem_features=hp.get("elem_features", ELEM_FEATURES),
        max_elements=hp.get("max_elements", args.max_elements),
        d_model=hp.get("d_model", 128),
        nhead=hp.get("nhead", 4),
        num_layers=hp.get("num_layers", 4),
        dim_feedforward=hp.get("dim_feedforward", 256),
        dropout=hp.get("dropout", 0.1),
        hist_len=hp.get("hist_len", args.hist_len),
    ).to(device)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    print(f"Loaded checkpoint from {ckpt_path}  (saved val_loss={ckpt.get('val_loss'):.4f})")

    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False)

    pred_counter = Counter()
    target_counter = Counter()
    correct = 0
    total = 0
    # 3x3 confusion matrix for type head: rows = true, cols = predicted
    # Row/col order: [no_op(0), click(1), keyboard(2)]
    type_cm = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
    n_click_labels_valid = 0   # tgt_click_idx != -1
    n_click_labels_ignored = 0 # tgt_click_idx == -1 on rows where tgt_type == CLICK
    with torch.no_grad():
        for states, p_types, p_cont, tgt_types, tgt_click_idx, tgt_keys, tgt_src in val_loader:
            states, p_types, p_cont = states.to(device), p_types.to(device), p_cont.to(device)
            tgt_types = tgt_types.to(device)
            tgt_click_idx = tgt_click_idx.to(device)
            out = model(states, p_types, p_cont)

            # Type confusion matrix
            type_preds = out.type_logits.argmax(-1)
            for t, p in zip(tgt_types.tolist(), type_preds.tolist()):
                if 0 <= t < 3 and 0 <= p < 3:
                    type_cm[t][p] += 1

            # Count how many click-type samples have a valid click_idx label
            click_rows = tgt_types == 1   # ACTION_CLICK
            n_click_labels_valid   += int(((tgt_click_idx != -1) & click_rows).sum().item())
            n_click_labels_ignored += int(((tgt_click_idx == -1) & click_rows).sum().item())

            valid = tgt_click_idx != -1
            if not valid.any():
                continue
            preds = out.click_elem.argmax(-1)
            preds_valid = preds[valid]
            targets_valid = tgt_click_idx[valid]
            correct += (preds_valid == targets_valid).sum().item()
            total += int(valid.sum().item())
            for p in preds_valid.tolist():
                pred_counter[p] += 1
            for t in targets_valid.tolist():
                target_counter[t] += 1

    print(f"Val click samples: {total}")
    print(f"Val click accuracy (recomputed): {correct / max(total, 1):.3f}")
    print()
    print(f"Distinct element indices the model EVER predicts on val: {len(pred_counter)}")
    print(f"Distinct element indices that APPEAR as val targets:     {len(target_counter)}")
    print()
    print(f"Top 10 indices the model predicts most often on val:")
    print(f"  {'idx':>5} {'n_pred':>7} {'frac_pred':>10}  {'n_target':>9} {'frac_tgt':>9}")
    for idx, n_pred in pred_counter.most_common(10):
        frac_pred = n_pred / max(total, 1)
        n_tgt = target_counter.get(idx, 0)
        frac_tgt = n_tgt / max(total, 1)
        print(f"  {idx:>5} {n_pred:>7} {frac_pred:>10.3f}  {n_tgt:>9} {frac_tgt:>9.3f}")

    print()
    top1_pred_frac = pred_counter.most_common(1)[0][1] / max(total, 1) if pred_counter else 0.0
    top5_pred_frac = sum(c for _, c in pred_counter.most_common(5)) / max(total, 1)
    print(f"Top-1 predicted index share: {top1_pred_frac:.3f}")
    print(f"Top-5 predicted index share: {top5_pred_frac:.3f}")

    # Histogram overlap
    all_indices = set(pred_counter.keys()) | set(target_counter.keys())
    overlap_mass = 0.0
    for idx in all_indices:
        overlap_mass += min(
            pred_counter.get(idx, 0) / max(total, 1),
            target_counter.get(idx, 0) / max(total, 1),
        )
    print(f"Prediction-vs-target histogram overlap (0..1): {overlap_mass:.3f}")

    # -------- D3: Type-head confusion matrix --------
    print()
    print("=" * 70)
    print("D3. Type-head confusion matrix on val")
    print("=" * 70)
    labels = ["no_op", "click", "kbd"]
    row_totals = [sum(row) for row in type_cm]
    n_total = sum(row_totals)
    print(f"                  predicted -->")
    print(f"    true |       {labels[0]:>6}  {labels[1]:>6}  {labels[2]:>6}   row_total")
    for i, row in enumerate(type_cm):
        print(f"    {labels[i]:<10}  {row[0]:>6}  {row[1]:>6}  {row[2]:>6}   {row_totals[i]:>8}")
    print()
    # Per-class recall (how often is each true class correctly predicted?)
    for i, label in enumerate(labels):
        if row_totals[i] == 0:
            continue
        recall = type_cm[i][i] / row_totals[i]
        print(f"    recall({label}): {type_cm[i][i]}/{row_totals[i]} = {recall:.3f}")
    if n_total > 0:
        overall_acc = sum(type_cm[i][i] for i in range(3)) / n_total
        print(f"    overall type acc: {overall_acc:.3f}")
    # Majority baseline for reference
    if row_totals[1] > 0:
        majority_baseline = row_totals[1] / n_total
        print(f"    majority-click baseline (always predict click): {majority_baseline:.3f}")

    # -------- D4: click-label rejection stats --------
    print()
    print("=" * 70)
    print("D4. How many click-type samples got click_idx = -1 (ignored)?")
    print("=" * 70)
    n_click_total = n_click_labels_valid + n_click_labels_ignored
    print(f"Val click-type samples total:           {n_click_total}")
    print(f"  with valid click_idx (used by loss):  {n_click_labels_valid}")
    print(f"  with click_idx = -1 (dropped by loss):{n_click_labels_ignored}")
    if n_click_total > 0:
        print(f"  rejection rate:                        {n_click_labels_ignored / n_click_total:.3f}")

    # Pred vs. target index histogram overlap — how often does the model pick
    # the same index as the true target, AGGREGATED across the dataset?
    # (Different from per-sample accuracy.)
    all_indices = set(pred_counter.keys()) | set(target_counter.keys())
    overlap_mass = 0.0
    for idx in all_indices:
        overlap_mass += min(
            pred_counter.get(idx, 0) / max(total, 1),
            target_counter.get(idx, 0) / max(total, 1),
        )
    print(f"Prediction-vs-target histogram overlap (0..1): {overlap_mass:.3f}")
    print("   (1.0 = perfect distribution match; 0 = fully disjoint. Does NOT imply accuracy.)")


if __name__ == "__main__":
    main()
