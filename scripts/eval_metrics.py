"""
scripts/eval_metrics.py
========================
Post-run pipeline metric evaluator. Works on successful AND failed runs.

Metrics
-------
1. Task Completion Rate       -- records submitted / records in source
2. Execution Success Rate     -- steps with UI state change / actionable steps
3. Generalization Success Rate-- form tabs with >=1 field filled / total tabs
4. Action Prediction Accuracy -- clicks inside an interactive element bbox / total clicks
5. Click Position Error       -- avg px distance from click to nearest element center
6. LLM Dependency Ratio       -- non-noop steps / total steps (proxy; full tracking
                                 needs per-step LLM logging in agent.py)

Usage
-----
  python scripts/eval_metrics.py                         # latest session
  python scripts/eval_metrics.py SESSION_DIR             # specific session
  python scripts/eval_metrics.py --all                   # aggregate all sessions
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TRACES_DIR   = ROOT / "data" / "output" / "traces" / "live"
SUBMISSIONS_DIR = ROOT / "data" / "output" / "submissions"
SOURCE_FILE  = ROOT / "data_entry_tasks" / "data_entry_intake.txt"

# Interactive element types (from UIA observer)
_INTERACTIVE = {
    "input", "button", "checkbox", "radio", "combobox",
    "listitem", "tabitem", "splitbutton", "link", "list",
    "editcontrol", "buttoncontrol", "checkboxcontrol", "radiobuttoncontrol",
    "comboboxcontrol", "listitemcontrol", "tabitemcontrol", "listcontrol",
    "hyperlinkcontrol", "splitbuttoncontrol",
}

# Submission field prefixes → form tab names
_TAB_PREFIXES: dict[str, str] = {
    "policy_":  "Policy",
    "ph_":      "Policyholder",
    "v_":       "Vehicle",
    "cov_":     "Coverage",
    "d1_":      "Driver 1",
    "d2_":      "Driver 2",
    "d3_":      "Driver 3",
    "claim_":   "Claims",
    "pay_":     "Payment",
}


# ─── helpers ──────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> Any:
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


_MAX_STEP_BYTES = 8 * 1024 * 1024  # skip files >8MB (full-screen UIA dumps)


def _load_steps(session_dir: Path) -> list[dict]:
    files = sorted(session_dir.glob("live_step_*.json"))
    steps = []
    for f in files:
        if f.stat().st_size > _MAX_STEP_BYTES:
            continue
        d = _load_json(f)
        if d:
            steps.append(d)
    return steps


def _iter_steps(session_dir: Path):
    """Yield steps one at a time without holding all in memory."""
    for f in sorted(session_dir.glob("live_step_*.json")):
        if f.stat().st_size > _MAX_STEP_BYTES:
            continue
        d = _load_json(f)
        if d:
            yield d


def _bbox_center(bbox: list[int]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _inside_bbox(pt: list[int], bbox: list[int]) -> bool:
    x, y = pt
    x1, y1, x2, y2 = bbox
    return x1 <= x <= x2 and y1 <= y <= y2


def _distance(pt: list[int], center: tuple[float, float]) -> float:
    return math.sqrt((pt[0] - center[0]) ** 2 + (pt[1] - center[1]) ** 2)


def _count_source_records() -> int:
    if not SOURCE_FILE.exists():
        return 0
    text = SOURCE_FILE.read_text(encoding="utf-8", errors="ignore")
    matches = re.findall(r"RECORD\s+\d+\s+OF\s+(\d+)", text)
    return int(matches[0]) if matches else 0


def _interactive_elements(state: dict) -> list[dict]:
    return [e for e in state.get("elements", []) if e.get("type") in _INTERACTIVE]


def _focused_id(state: dict) -> Any:
    return state.get("focused_element_id")


def _element_values(state: dict) -> dict:
    return {
        e["element_id"]: (e.get("value") or "").strip()
        for e in state.get("elements", [])
        if e.get("element_id")
    }


# ─── metric computers ─────────────────────────────────────────────────────────

def task_completion_rate(total_source_records: int) -> dict:
    """Records submitted (has policy_number) / total records in source."""
    submissions = list(SUBMISSIONS_DIR.glob("*.json"))
    completed = 0
    for s in submissions:
        d = _load_json(s)
        if d and d.get("policy_number"):
            completed += 1
    total = total_source_records or max(len(submissions), 1)
    return {
        "metric": "Task Completion Rate",
        "value": completed / total,
        "detail": f"{completed} submitted / {total} in source",
    }


def execution_success_rate(steps: list[dict]) -> dict:
    """Steps where UI state changed (focus or value) after action / actionable steps.
    Compares consecutive steps — next_state was removed from step files to save space.
    """
    actionable = 0
    succeeded  = 0
    for i, step in enumerate(steps):
        action_type = step.get("action", {}).get("action_type", "noop")
        if action_type in ("noop", None):
            continue
        actionable += 1
        if i + 1 >= len(steps):
            continue
        state      = step.get("state", {})
        next_state = steps[i + 1].get("state", {})
        focus_changed  = _focused_id(state) != _focused_id(next_state)
        before_vals    = _element_values(state)
        after_vals     = _element_values(next_state)
        values_changed = any(
            after_vals.get(k) != v for k, v in before_vals.items()
        )
        if focus_changed or values_changed:
            succeeded += 1
    rate = succeeded / actionable if actionable else 0.0
    return {
        "metric": "Execution Success Rate",
        "value": rate,
        "detail": f"{succeeded} state-changing / {actionable} actionable steps",
    }


def generalization_success_rate() -> dict:
    """
    Tabs with >=1 non-blank field filled across all submissions / total expected tabs.
    Proxy for how broadly the agent covers the form's sections.
    """
    expected_tabs = set(_TAB_PREFIXES.values())
    covered_tabs: set[str] = set()

    submissions = list(SUBMISSIONS_DIR.glob("*.json"))
    for s in submissions:
        d = _load_json(s)
        if not d:
            continue
        for prefix, tab in _TAB_PREFIXES.items():
            fields = {k: v for k, v in d.items() if k.startswith(prefix)}
            filled = [v for v in fields.values() if v not in ("", None, False)]
            if filled:
                covered_tabs.add(tab)

    rate = len(covered_tabs) / len(expected_tabs) if expected_tabs else 0.0
    missing = expected_tabs - covered_tabs
    return {
        "metric": "Generalization Success Rate",
        "value": rate,
        "detail": (
            f"{len(covered_tabs)}/{len(expected_tabs)} tabs covered. "
            + (f"Missing: {sorted(missing)}" if missing else "All tabs reached.")
        ),
    }


def action_prediction_accuracy(steps: list[dict]) -> dict:
    """Clicks that land inside any interactive element bbox / total click actions."""
    total_clicks  = 0
    on_target_clicks = 0
    for step in steps:
        action = step.get("action", {})
        if action.get("action_type") != "click":
            continue
        pos = action.get("click_position")
        if not pos or len(pos) < 2:
            continue
        total_clicks += 1
        state    = step.get("state", {})
        elements = _interactive_elements(state)
        if any(_inside_bbox(pos, e["bbox"]) for e in elements if len(e.get("bbox", [])) == 4):
            on_target_clicks += 1
    rate = on_target_clicks / total_clicks if total_clicks else 0.0
    return {
        "metric": "Action Prediction Accuracy",
        "value": rate,
        "detail": f"{on_target_clicks} on-target / {total_clicks} total clicks",
    }


def click_position_error(steps: list[dict]) -> dict:
    """
    Average Euclidean distance (px) from click position to the nearest
    interactive element's center across all click actions.
    """
    errors: list[float] = []
    for step in steps:
        action = step.get("action", {})
        if action.get("action_type") != "click":
            continue
        pos = action.get("click_position")
        if not pos or len(pos) < 2:
            continue
        state    = step.get("state", {})
        elements = [e for e in _interactive_elements(state) if len(e.get("bbox", [])) == 4]
        if not elements:
            continue
        min_dist = min(_distance(pos, _bbox_center(e["bbox"])) for e in elements)
        errors.append(min_dist)
    avg_err = sum(errors) / len(errors) if errors else 0.0
    return {
        "metric": "Click Position Error",
        "value": avg_err,
        "detail": f"avg {avg_err:.1f}px over {len(errors)} clicks with visible elements",
    }


def llm_dependency_ratio(steps: list[dict]) -> dict:
    """
    Non-noop steps / total steps.

    Full accuracy requires per-step LLM logging in agent.py (provider + call count).
    This proxy counts steps that required a decision (not idle noops).
    When provider='none', ratio approaches 0 (transformer-only). With any LLM
    provider, ratio ≈ this value since every decision step calls the LLM.
    """
    total     = len(steps)
    non_noop  = sum(
        1 for s in steps
        if s.get("action", {}).get("action_type") not in ("noop", None)
    )
    rate = non_noop / total if total else 0.0
    return {
        "metric": "LLM Dependency Ratio",
        "value": rate,
        "detail": (
            f"{non_noop} decision steps / {total} total steps. "
            "Note: proxy only — add per-step provider logging for exact ratio."
        ),
    }


# ─── report ───────────────────────────────────────────────────────────────────

def _fmt(result: dict) -> str:
    pct = f"{result['value'] * 100:.1f}%" if result["metric"] != "Click Position Error" else f"{result['value']:.1f}px"
    return f"  {result['metric']:<32} {pct:<10}  {result['detail']}"


def evaluate_session(session_dir: Path, total_source_records: int) -> list[dict]:
    steps = _load_steps(session_dir)
    if not steps:
        print(f"  [warn] no steps loaded from {session_dir}")
        return []
    results = [
        execution_success_rate(steps),
        action_prediction_accuracy(steps),
        click_position_error(steps),
        llm_dependency_ratio(steps),
    ]
    return results


def _acc_step(step: dict, acc: dict, prev_step: dict | None = None) -> None:
    """Accumulate one step into running counters (no list growth).
    prev_step: the previous step dict, used as next_state substitute since
    next_state was removed from step files to save space.
    """
    acc["total"] += 1
    action = step.get("action", {})
    action_type = action.get("action_type", "noop")
    if action_type not in ("noop", None):
        acc["non_noop"] += 1
        acc["actionable"] += 1
        if prev_step is not None:
            # prev_step acted on its state; step["state"] is the resulting next state
            state      = prev_step.get("state", {})
            next_state = step.get("state", {})
            if (_focused_id(state) != _focused_id(next_state)
                    or any(
                        _element_values(next_state).get(k) != v
                        for k, v in _element_values(state).items()
                    )):
                acc["succeeded"] += 1
    if action_type == "click":
        pos = action.get("click_position")
        if pos and len(pos) >= 2:
            acc["clicks"] += 1
            state    = step.get("state", {})
            elements = [e for e in _interactive_elements(state) if len(e.get("bbox", [])) == 4]
            if elements:
                if any(_inside_bbox(pos, e["bbox"]) for e in elements):
                    acc["on_target"] += 1
                min_dist = min(_distance(pos, _bbox_center(e["bbox"])) for e in elements)
                acc["err_sum"]   += min_dist
                acc["err_count"] += 1


def report(session_dir: Path | None, aggregate_all: bool) -> None:
    total_source = _count_source_records()
    print(f"\n{'='*70}")
    print("  PIPELINE METRIC REPORT")
    print(f"  Source records : {total_source}")
    print(f"{'='*70}\n")

    # Cross-session metrics (use all submissions)
    cross = [
        task_completion_rate(total_source),
        generalization_success_rate(),
    ]
    print("Cross-session metrics (all submissions):")
    for r in cross:
        print(_fmt(r))

    print()

    if aggregate_all:
        sessions = sorted(TRACES_DIR.glob("session_*/"))
        if not sessions:
            print("No sessions found.")
            return
        # Stream per-session — 22GB total, can't load all at once
        acc = {
            "total": 0, "non_noop": 0,
            "actionable": 0, "succeeded": 0,
            "clicks": 0, "on_target": 0,
            "err_sum": 0.0, "err_count": 0,
        }
        for sd in sessions:
            prev = None
            for step in _iter_steps(sd):
                _acc_step(step, acc, prev)
                prev = step
        total_steps = acc["total"]
        print(f"Session-level metrics (aggregated, {len(sessions)} sessions, {total_steps} steps):")
        rate_esr = acc["succeeded"] / acc["actionable"] if acc["actionable"] else 0.0
        rate_apa = acc["on_target"] / acc["clicks"] if acc["clicks"] else 0.0
        avg_cpe  = acc["err_sum"] / acc["err_count"] if acc["err_count"] else 0.0
        rate_llm = acc["non_noop"] / total_steps if total_steps else 0.0
        for r in [
            {"metric": "Execution Success Rate",     "value": rate_esr,
             "detail": f"{acc['succeeded']} state-changing / {acc['actionable']} actionable steps"},
            {"metric": "Action Prediction Accuracy", "value": rate_apa,
             "detail": f"{acc['on_target']} on-target / {acc['clicks']} total clicks"},
            {"metric": "Click Position Error",       "value": avg_cpe,
             "detail": f"avg {avg_cpe:.1f}px over {acc['err_count']} clicks with visible elements"},
            {"metric": "LLM Dependency Ratio",       "value": rate_llm,
             "detail": f"{acc['non_noop']} decision steps / {total_steps} total steps. "
                       "Note: proxy only — add per-step provider logging for exact ratio."},
        ]:
            print(_fmt(r))
    else:
        if session_dir is None:
            sessions = sorted(TRACES_DIR.glob("session_*/"))
            session_dir = sessions[-1] if sessions else None
        if session_dir is None:
            print("No session directory found.")
            return
        steps = _load_steps(session_dir)
        print(f"Session: {session_dir.name}  ({len(steps)} steps)")
        for r in [
            execution_success_rate(steps),
            action_prediction_accuracy(steps),
            click_position_error(steps),
            llm_dependency_ratio(steps),
        ]:
            print(_fmt(r))

    print(f"\n{'='*70}\n")


# ─── entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline metric evaluator")
    parser.add_argument("session", nargs="?", help="Path to session directory")
    parser.add_argument("--all", action="store_true", help="Aggregate all sessions")
    args = parser.parse_args()

    session_dir: Path | None = None
    if args.session:
        session_dir = Path(args.session)
        if not session_dir.is_absolute():
            session_dir = ROOT / session_dir
        if not session_dir.exists():
            print(f"Session not found: {session_dir}")
            sys.exit(1)

    report(session_dir, args.all)


if __name__ == "__main__":
    main()
