"""
scripts/objectives_report.py
===============================
Objective 10: "integrate the perception, representation, learning,
adaptability, and execution components into a unified agentic automation
framework ... enabling coordinated end-to-end GUI task execution."

This is that integration point for MEASUREMENT: every other metrics script in
this repo writes to its own jsonl log (or is read fresh from run_task.py's
output). This script pulls the latest reading from each and prints one
dashboard, keyed by the thesis' 12 objectives, each with its target, current
actual, and pass/fail — so a single command answers "where do we stand
against the thesis right now."

Sources read (each optional — missing ones are reported as "no data yet"):
  data/output/run_metrics.jsonl              objectives 3, 5, 8, 9  (+ timing)
  data/output/bc_progress.jsonl               overall fidelity context
  data/output/transformer_training_log.jsonl  objectives 3, 5, 7
  data/output/perception_eval_log.jsonl       objective 1  (per environment)
  data/output/transition_validation_log.jsonl objective 4
  data/output/encoding_ambiguity_log.jsonl    objective 2
  data/output/setup_time_log.jsonl            objective 11 (Intern side only)

Objectives 6 (adaptation to unseen GUIs) and 11/12's RPA-baseline comparison
need dedicated runs (an unseen-environment test session; scripts/compare_
baseline.py with real baseline numbers) — this report says so explicitly
rather than guessing.

Usage
-----
  python scripts/objectives_report.py
  python scripts/objectives_report.py --recent 10     # average the last 10 runs, not just the latest
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
OUT = ROOT / "data" / "output"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _avg(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _fmt_pct(x: float | None) -> str:
    return f"{x*100:.1f}%" if x is not None else "no data"


def _fmt_sec(x: float | None) -> str:
    return f"{x:.1f}s" if x is not None else "no data"


def _row(num: str, name: str, target: str, actual: str, passed: bool | None) -> str:
    flag = "n/a" if passed is None else ("PASS" if passed else "FAIL")
    return f"  {num:<4} {name:<52} target {target:<10} actual {actual:<12} [{flag}]"


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Unified objectives dashboard (objective 10).")
    ap.add_argument("--recent", type=int, default=5, help="Average the last N run_metrics entries (default 5).")
    args = ap.parse_args()

    run_metrics   = _load_jsonl(OUT / "run_metrics.jsonl")[-args.recent:]
    bc_progress   = _load_jsonl(OUT / "bc_progress.jsonl")
    training_log  = _load_jsonl(OUT / "transformer_training_log.jsonl")
    perception    = _load_jsonl(OUT / "perception_eval_log.jsonl")
    transitions   = _load_jsonl(OUT / "transition_validation_log.jsonl")
    ambiguity     = _load_jsonl(OUT / "encoding_ambiguity_log.jsonl")
    setup_times   = _load_jsonl(OUT / "setup_time_log.jsonl")

    apa   = _avg([r.get("action_prediction_accuracy") for r in run_metrics])
    esr   = _avg([r.get("execution_success_rate") for r in run_metrics])
    eer   = _avg([r.get("execution_error_rate") for r in run_metrics])
    wsr   = _avg([r.get("wasted_step_rate") for r in run_metrics])
    tcr   = _avg([r.get("task_completion_rate") for r in run_metrics])
    interventions = _avg([r.get("manual_interventions") for r in run_metrics])
    run_dur = _avg([(r.get("timing") or {}).get("run_duration_sec") for r in run_metrics])
    llm_dep   = _avg([r.get("llm_dependency") for r in run_metrics])
    tform_dep = _avg([r.get("transformer_dependency") for r in run_metrics])

    latest_train = training_log[-1] if training_log else {}
    train_acc    = latest_train.get("best_val_acc")
    train_click  = latest_train.get("best_val_click_acc")

    latest_transition = transitions[-1] if transitions else {}
    transition_acc = latest_transition.get("overall_mapping_accuracy")

    latest_ambiguity = ambiguity[-1] if ambiguity else {}
    ambiguity_rate = latest_ambiguity.get("overall_ambiguity_rate")

    # Perception: break out by environment, and roll up an overall.
    envs: dict[str, list[float]] = {}
    for p in perception:
        env = p.get("environment") or "(unspecified)"
        envs.setdefault(env, []).append(p.get("detection_accuracy", 0.0))
    overall_detection = _avg([v for vals in envs.values() for v in vals])

    avg_setup = _avg([r.get("elapsed_sec") for r in setup_times])

    print(f"\n{'='*94}")
    print("  INTERN — OBJECTIVES DASHBOARD  (objective 10: one view across all components)")
    print(f"{'='*94}\n")

    print(f"  Core run metrics (avg over last {len(run_metrics)} run(s)):")
    print(f"    Task Completion Rate        {_fmt_pct(tcr)}")
    print(f"    Action Prediction Accuracy  {_fmt_pct(apa)}")
    print(f"    Execution Success Rate      {_fmt_pct(esr)}")
    print()

    print(_row("1", "Perception — detection accuracy (multi-environment)", ">=95%",
               _fmt_pct(overall_detection), overall_detection is not None and overall_detection >= 0.95))
    if envs:
        for env, vals in sorted(envs.items()):
            e_acc = _avg(vals)
            print(f"       - {env:<48} n={len(vals):<3} {_fmt_pct(e_acc)}")
    else:
        print("       No perception_eval_log.jsonl entries yet — run scripts/perception_eval.py --live --log")

    print(_row("2", "Representation — encoding ambiguity", "<5%",
               _fmt_pct(ambiguity_rate), ambiguity_rate is not None and ambiguity_rate <= 0.05))
    if not ambiguity:
        print("       No data — run scripts/encoding_ambiguity.py --log")

    print(_row("3", "Learning pipeline — action prediction accuracy", ">=90%",
               _fmt_pct(apa), apa is not None and apa >= 0.90))
    print(f"       Model Dependency -- LLM {_fmt_pct(llm_dep)} (target <5%)  |  "
          f"Transformer {_fmt_pct(tform_dep)} (target >95%)  "
          "-- is the BC model doing the work, or is the LLM carrying it?")

    print(_row("4", "State-transition mapping correctness", ">=90%",
               _fmt_pct(transition_acc), transition_acc is not None and transition_acc >= 0.90))
    if not transitions:
        print("       No data — run scripts/validate_transitions.py --log")

    print(_row("5", "BC model evaluation — action prediction accuracy", ">=90%",
               _fmt_pct(train_click), train_click is not None and train_click >= 0.90))

    print(_row("6", "Adaptation — success rate on UNSEEN GUI environments", ">=75%",
               "not instrumented", None))
    print("       Needs a dedicated held-out/perturbed-layout test session (different from training "
          "envs) — no auto flag for 'unseen' exists yet in eval_metrics.py.")

    print(_row("7", "Scalability — performance held as dataset grows", ">=90%",
               (f"{len(training_log)} training runs logged" if training_log else "no data"), None))
    print("       Run scripts/scaling_report.py for the full trend across dataset sizes.")

    print(_row("8", "Agentic integration — end-to-end completion, no intervention", ">=85%",
               _fmt_pct(tcr), tcr is not None and tcr >= 0.85))
    print(f"       Manual interventions (avg/run): {interventions if interventions is not None else 'no data'}")

    print(_row("9a", "Execution mechanism — execution error rate", "<=10%",
               _fmt_pct(eer), eer is not None and eer <= 0.10))
    print(_row("9b", "Execution mechanism — redundant (wasted) steps", "<=20%",
               _fmt_pct(wsr), wsr is not None and wsr <= 0.20))

    print(_row("10", "Unified integration (this report)", "qualitative",
               f"{sum(1 for x in [run_metrics, training_log, perception, transitions, ambiguity] if x)}/5 "
               "components reporting", None))

    print(_row("11/12", "vs. RPA baseline — setup/adaptability/error/workload", ">=10-20% + sig.",
               f"Intern setup avg {_fmt_sec(avg_setup)}", None))
    print("       Run scripts/compare_baseline.py --baseline <rpa_numbers.json> for the full comparison "
          "(needs separately-collected RPA-tool measurements).")

    if run_metrics:
        print(f"\n  Timing (avg over last {len(run_metrics)} run(s)):  "
              f"run duration {_fmt_sec(run_dur)}")

    print(f"\n{'='*94}\n")


if __name__ == "__main__":
    main()
