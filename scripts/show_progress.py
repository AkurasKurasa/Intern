"""
show_progress.py
================
Prints transformer learning progress across training runs and agent runs.

Usage:
    python scripts/show_progress.py

Reads:
    data/output/transformer_training_log.jsonl  — one row per train.py run
    data/output/run_metrics.jsonl               — one row per agent run
"""

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def _filter_scope1(rows):
    """data/output/run_metrics.jsonl is a SHARED trend log written by both
    Scope #1 (run_task.py) and Scope #2 (components/scope2/automate.py),
    tagged with a "scope" key. This script only understands Scope #1's row
    shape (provider, transformer_dependency, task_completion_rate, ...) --
    a Scope #2 row would render as a bogus/garbled Scope #1 run below.
    70 pre-existing legacy rows predate the "scope" key entirely and are
    treated as "scope1" (that's what they always were)."""
    return [r for r in rows if r.get("scope", "scope1") == "scope1"]


def main():
    train_log = _load_jsonl(os.path.join(_ROOT, "data", "output", "transformer_training_log.jsonl"))
    run_log   = _load_jsonl(os.path.join(_ROOT, "data", "output", "run_metrics.jsonl"))
    run_log   = _filter_scope1(run_log)

    # ── Training progress ─────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  TRANSFORMER TRAINING PROGRESS")
    print("=" * 72)
    if not train_log:
        print("  No training runs recorded yet.")
        print("  Run:  python scripts/train.py --trace_dir data/demos/human --epochs 50")
    else:
        print(f"  {'#':<3}  {'Date':<19}  {'Samples':>8}  {'val_loss':>9}  {'val_acc':>8}  {'click_acc':>10}  {'Data source'}")
        print(f"  {'-'*3}  {'-'*19}  {'-'*8}  {'-'*9}  {'-'*8}  {'-'*10}  {'-'*30}")
        for i, row in enumerate(train_log, 1):
            ts        = row.get("timestamp", "")[:19]
            n         = row.get("n_train", 0) + row.get("n_val", 0)
            val_loss  = row.get("best_val_loss", float("nan"))
            val_acc   = row.get("best_val_acc",  float("nan"))
            click_acc = row.get("best_val_click_acc", float("nan"))
            src       = os.path.basename(row.get("trace_dir", "?"))[:30]
            print(f"  {i:<3}  {ts:<19}  {n:>8}  {val_loss:>9.4f}  {val_acc:>8.3f}  {click_acc:>10.3f}  {src}")

        if len(train_log) >= 2:
            first = train_log[0]
            last  = train_log[-1]
            delta_acc   = last.get("best_val_acc", 0) - first.get("best_val_acc", 0)
            delta_click = last.get("best_val_click_acc", 0) - first.get("best_val_click_acc", 0)
            delta_loss  = last.get("best_val_loss", 0) - first.get("best_val_loss", 0)
            sign = lambda x: "+" if x >= 0 else ""
            print(f"\n  Trend (run 1 → run {len(train_log)}):  "
                  f"val_acc {sign(delta_acc)}{delta_acc:+.3f}  "
                  f"click_acc {sign(delta_click)}{delta_click:+.3f}  "
                  f"val_loss {delta_loss:+.4f}")

    # ── Inference progress ────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  AGENT RUN PROGRESS  (transformer dependency + task completion)")
    print("=" * 72)
    if not run_log:
        print("  No agent runs recorded yet.")
        print("  Run:  python run_task.py")
    else:
        print(f"  {'#':<3}  {'Date':<19}  {'Provider':<10}  {'T-Dep%':>7}  {'LLM-Dep%':>9}  {'Fields%':>7}  {'Steps':>6}  {'Conf':>6}")
        print(f"  {'-'*3}  {'-'*19}  {'-'*10}  {'-'*7}  {'-'*9}  {'-'*7}  {'-'*6}  {'-'*6}")
        for i, row in enumerate(run_log, 1):
            ts       = row.get("timestamp", "")[:19]
            prov     = row.get("provider", "?")[:10]
            t_dep    = row.get("transformer_dependency", 0.0) * 100
            l_dep_v  = row.get("llm_dependency")
            l_dep    = l_dep_v * 100 if l_dep_v is not None else float("nan")
            l_dep_s  = f"{l_dep:>8.1f}%" if l_dep == l_dep else "      n/a"
            fields   = row.get("task_completion_rate", 0.0) * 100
            steps    = row.get("total_steps", row.get("steps", "?"))
            conf     = row.get("avg_transformer_conf", float("nan"))
            conf_str = f"{conf:.3f}" if conf == conf else "  n/a"
            print(f"  {i:<3}  {ts:<19}  {prov:<10}  {t_dep:>6.1f}%  {l_dep_s}  {fields:>6.1f}%  {steps:>6}  {conf_str:>6}")

        if len(run_log) >= 2:
            first = run_log[0]
            last  = run_log[-1]
            delta_dep    = (last.get("transformer_dependency", 0) - first.get("transformer_dependency", 0)) * 100
            delta_llm    = ((last.get("llm_dependency") or 0) - (first.get("llm_dependency") or 0)) * 100
            delta_fields = (last.get("task_completion_rate",   0) - first.get("task_completion_rate",   0)) * 100
            print(f"\n  Trend (run 1 → run {len(run_log)}):  "
                  f"T-Dep {delta_dep:+.1f}%  "
                  f"LLM-Dep {delta_llm:+.1f}%  "
                  f"Fields {delta_fields:+.1f}%")

    print()


if __name__ == "__main__":
    main()
