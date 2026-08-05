"""
scripts/compare_baseline.py
=============================
Objectives 11 & 12: compare Intern against a traditional rule-based RPA tool
on the same task — setup time, adaptability to interface changes, execution
errors, and (a proxy for) cognitive workload — and report both the percent
improvement and whether it's statistically significant.

Intern can't automatically run a third-party RPA tool, so this script
compares two *pre-collected* sets of per-run measurements:

  - Intern's side comes from real runs: pull it straight from
    data/output/run_metrics.jsonl (written by run_task.py every run) plus
    setup-time entries from scripts/setup_time_tracker.py.
  - The RPA baseline is manual: after timing/testing the RPA tool on the same
    task N times, fill its numbers into a JSON file shaped like:

    [
      {"setup_time_sec": 5400, "execution_error_rate": 0.18,
       "adaptability_success_rate": 0.20, "intervention_rate": 0.35},
      ...
    ]

  (one dict per run/trial — needs >=2 to test significance, more is better)

Per dimension: % improvement = (baseline - intern) / baseline for
"lower is better" metrics (setup_time_sec, execution_error_rate,
intervention_rate), and (intern - baseline) / baseline for "higher is
better" metrics (adaptability_success_rate). Significance via an
independent two-sample t-test (Welch's, unequal variance assumed) — use
--paired if the two tools ran the exact same task instances.

Usage
-----
  python scripts/compare_baseline.py --baseline rpa_baseline.json
  python scripts/compare_baseline.py --baseline rpa_baseline.json --paired
  python scripts/compare_baseline.py --baseline rpa_baseline.json --intern intern_runs.json
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
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RUN_METRICS_LOG = ROOT / "data" / "output" / "run_metrics.jsonl"
SETUP_TIME_LOG  = ROOT / "data" / "output" / "setup_time_log.jsonl"

# metric -> (label, "lower"|"higher" is better)
DIMENSIONS = {
    "setup_time_sec":            ("Setup Time",               "lower"),
    "execution_error_rate":      ("Execution Error Rate",      "lower"),
    "adaptability_success_rate": ("Adaptability to Changes",   "higher"),
    "intervention_rate":         ("Cognitive Workload (proxy: manual intervention rate)", "lower"),
}


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _load_json_array(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a JSON array of per-run dicts.")
    return data


def _collect_intern_runs(explicit_path: str | None) -> list[dict]:
    if explicit_path:
        return _load_json_array(Path(explicit_path))

    runs = _load_jsonl(RUN_METRICS_LOG)
    setup_rows = _load_jsonl(SETUP_TIME_LOG)
    avg_setup = (sum(r["elapsed_sec"] for r in setup_rows) / len(setup_rows)
                 if setup_rows else None)

    out = []
    for r in runs:
        out.append({
            "setup_time_sec":            avg_setup,  # same pipeline setup applies to all runs
            "execution_error_rate":      r.get("execution_error_rate"),
            "adaptability_success_rate": None,  # needs --unseen-tagged eval_metrics runs; fill manually if available
            "intervention_rate":         r.get("intervention_rate"),
        })
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def _significance(a: list[float], b: list[float], paired: bool) -> dict:
    """a = Intern, b = baseline. Returns t-stat, p-value, and a plain-English
    verdict. Falls back to a manual Welch's t-test if scipy isn't installed."""
    try:
        from scipy import stats
        if paired and len(a) == len(b):
            t, p = stats.ttest_rel(a, b)
        else:
            t, p = stats.ttest_ind(a, b, equal_var=False)
        return {"t_stat": round(float(t), 3), "p_value": round(float(p), 4),
                "significant_at_05": bool(p < 0.05), "method": "scipy"}
    except ImportError:
        # Manual Welch's t-test (independent samples); p-value omitted — install
        # scipy for an exact p-value (`pip install scipy`).
        ma, mb = _mean(a), _mean(b)
        sa, sb = _stdev(a), _stdev(b)
        na, nb = len(a), len(b)
        se = ((sa**2)/na + (sb**2)/nb) ** 0.5 if na and nb else 0
        t = (ma - mb) / se if se else float("inf")
        return {"t_stat": round(t, 3), "p_value": None,
                "significant_at_05": abs(t) > 2.0,  # rough rule of thumb, n>=~10
                "method": "manual (approximate — install scipy for a real p-value)"}


def compare(intern_runs: list[dict], baseline_runs: list[dict], paired: bool) -> dict:
    results = {}
    for key, (label, direction) in DIMENSIONS.items():
        a = [r[key] for r in intern_runs if r.get(key) is not None]
        b = [r[key] for r in baseline_runs if r.get(key) is not None]
        if not a or not b:
            results[key] = {"label": label, "skipped": True,
                             "reason": f"missing values (intern n={len(a)}, baseline n={len(b)})"}
            continue

        mean_a, mean_b = _mean(a), _mean(b)
        if direction == "lower":
            pct_improvement = (mean_b - mean_a) / mean_b if mean_b else 0.0
        else:
            pct_improvement = (mean_a - mean_b) / mean_b if mean_b else 0.0

        sig = _significance(a, b, paired) if len(a) >= 2 and len(b) >= 2 else \
              {"t_stat": None, "p_value": None, "significant_at_05": False,
               "method": "insufficient samples (need >=2 runs per side)"}

        results[key] = {
            "label": label,
            "direction": direction,
            "intern_mean": round(mean_a, 4),
            "baseline_mean": round(mean_b, 4),
            "intern_n": len(a),
            "baseline_n": len(b),
            "pct_improvement": round(pct_improvement * 100, 1),
            "meets_10_20pct_target": pct_improvement >= 0.10,
            **sig,
        }
    return results


def _print_report(results: dict) -> None:
    print(f"\n{'='*90}")
    print("  INTERN vs. RPA BASELINE  (objectives 11 & 12)")
    print(f"{'='*90}")
    for key, r in results.items():
        if r.get("skipped"):
            print(f"  {r['label']:<55} SKIPPED — {r['reason']}")
            continue
        arrow = "↓ better" if r["direction"] == "lower" else "↑ better"
        target = "PASS" if r["meets_10_20pct_target"] else "below 10-20% target"
        sig = ("significant (p<0.05)" if r.get("significant_at_05")
               else "not significant" if r.get("p_value") is not None
               else "significance unknown")
        print(f"  {r['label']} ({arrow})")
        print(f"    Intern:   {r['intern_mean']:>10}  (n={r['intern_n']})")
        print(f"    Baseline: {r['baseline_mean']:>10}  (n={r['baseline_n']})")
        print(f"    Improvement: {r['pct_improvement']:>6.1f}%   [{target}]   [{sig}]")
        if r.get("p_value") is not None:
            print(f"    t={r['t_stat']}  p={r['p_value']}")
        print()
    print(f"{'='*90}\n")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Compare Intern vs. an RPA baseline (objectives 11 & 12).")
    ap.add_argument("--baseline", required=True, help="JSON array of per-run RPA baseline measurements.")
    ap.add_argument("--intern", help="JSON array of per-run Intern measurements "
                                      "(default: derive from run_metrics.jsonl + setup_time_log.jsonl).")
    ap.add_argument("--paired", action="store_true",
                     help="Use a paired t-test (same task instances on both tools).")
    args = ap.parse_args()

    baseline_runs = _load_json_array(Path(args.baseline))
    intern_runs   = _collect_intern_runs(args.intern)

    if not intern_runs:
        print("No Intern run data found. Run run_task.py at least once first "
              "(or pass --intern with a JSON array).")
        sys.exit(1)

    results = compare(intern_runs, baseline_runs, args.paired)
    _print_report(results)


if __name__ == "__main__":
    main()
