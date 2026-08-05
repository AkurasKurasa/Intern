"""
scripts/scaling_report.py
==========================
Objective 7: "a scalable learning and data management framework capable of
handling increasing volumes of user demonstrations ... while maintaining at
least 90% learning and execution performance."

Reads the training history already logged by transformer.py's train() to
data/output/transformer_training_log.jsonl (one row per training run: n_train,
best_val_acc, best_val_click_acc, total_train_time_sec, samples_per_sec) and
reports two trends as the dataset has grown across successive retrains:

  1. Does accuracy hold >=90% as n_train increases? (performance-at-scale)
  2. Does training time grow reasonably (roughly linearly, not blowing up) as
     n_train increases? (cost-at-scale — the other half of "scalable")

This is a report over EXISTING logged runs, not a new training run — retrain
at increasing dataset sizes first (train.py already appends one row per call),
then run this to see the trend.

Usage
-----
  python scripts/scaling_report.py
"""
from __future__ import annotations

import json
import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "data" / "output" / "transformer_training_log.jsonl"


def _load_rows() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    rows = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main() -> None:
    rows = _load_rows()
    if not rows:
        print(f"No training history at {LOG_PATH} — run train.py at least twice "
              "at different dataset sizes first.")
        sys.exit(1)

    rows.sort(key=lambda r: r.get("n_train", 0))

    print(f"\n{'='*88}")
    print("  SCALING REPORT  (objective 7 — performance & cost vs. dataset size)")
    print(f"{'='*88}")
    print(f"  {'n_train':>8}  {'val_acc':>8}  {'click_acc':>10}  {'>=90%?':>7}  "
          f"{'train_time':>11}  {'samples/s':>10}  {'timestamp':<20}")
    print(f"  {'-'*84}")

    perf_ok_count = 0
    for r in rows:
        n       = r.get("n_train", "?")
        acc     = r.get("best_val_acc")
        click   = r.get("best_val_click_acc")
        combined = (acc or 0) + (click or 0)  # matches the checkpoint-selection score in transformer.py
        ok      = combined / 2 >= 0.90
        perf_ok_count += ok
        t_time  = r.get("total_train_time_sec")
        sps     = r.get("samples_per_sec")
        ts      = (r.get("timestamp") or "")[:16].replace("T", " ")
        acc_s   = f"{acc*100:.1f}%" if acc is not None else "n/a"
        click_s = f"{click*100:.1f}%" if click is not None else "n/a"
        tt_s    = f"{t_time:.0f}s" if t_time is not None else "n/a"
        sps_s   = f"{sps:.1f}" if sps is not None else "n/a"
        print(f"  {n:>8}  {acc_s:>8}  {click_s:>10}  {'YES' if ok else 'no':>7}  "
              f"{tt_s:>11}  {sps_s:>10}  {ts:<20}")

    print(f"  {'-'*84}")

    # Trend: is throughput (samples/sec) roughly stable as n_train grows, or
    # collapsing (a sign the pipeline doesn't scale)?
    with_sps = [(r["n_train"], r["samples_per_sec"]) for r in rows
                if r.get("n_train") and r.get("samples_per_sec")]
    if len(with_sps) >= 2:
        first_n, first_sps = with_sps[0]
        last_n, last_sps = with_sps[-1]
        sps_drop = (first_sps - last_sps) / first_sps if first_sps else 0
        print(f"  Dataset grew {first_n} -> {last_n} training examples "
              f"({(last_n/first_n - 1)*100:+.0f}%).")
        print(f"  Throughput went {first_sps:.1f} -> {last_sps:.1f} samples/sec "
              f"({-sps_drop*100:+.1f}%).")
        if sps_drop > 0.30:
            print("  ⚠ Throughput dropped >30% as the dataset grew — training cost is NOT scaling well.")
        else:
            print("  Throughput held within 30% — training cost is scaling acceptably.")

    print(f"\n  {perf_ok_count}/{len(rows)} runs maintained >=90% combined val/click accuracy "
          f"as the dataset scaled.")
    print(f"  Objective 7: {'PASS' if perf_ok_count == len(rows) else 'PARTIAL' if perf_ok_count else 'FAIL'} "
          "(performance maintained at every logged dataset size)"
          if len(rows) > 1 else
          "  Need >=2 training runs at different dataset sizes to assess a scaling trend.")
    print(f"{'='*88}\n")


if __name__ == "__main__":
    main()
