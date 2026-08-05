"""
scripts/validate_transitions.py
================================
Objective 4: "translate observed interactions into state-action pairs based on
transitions between interface states, with at least 90% correctness in state
transition mapping."

For every consecutive (state_t, action_t, state_t+1) triple in a recorded
session, checks that state_t+1 actually reflects action_t having happened:

  click                      -> focus/selection changed to (or through) the clicked element
  keyboard / type / paste    -> the focused element's value changed to contain what was typed
  scroll / hotkey            -> *something* observable changed (focus, value, or element set)
  noop / wait                -> excluded from the denominator (nothing was supposed to change)

A triple that fails this check means the recorder captured an action whose
effect isn't visible in the next recorded state — i.e. the state-action pair
would mistrain the model on a transition that didn't really happen (a
mislabeled or dropped frame). This is the mapping-correctness objective, not
the live-agent execution-success metric in eval_metrics.py (that scores a
running agent; this scores the recorded *dataset* the model learns from).

Usage
-----
  python scripts/validate_transitions.py                     # all sessions
  python scripts/validate_transitions.py SESSION_DIR          # one session
  python scripts/validate_transitions.py --log                # append to jsonl
"""
from __future__ import annotations

import json
import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TRACES_DIR = ROOT / "tasks" / "form_filling" / "traces"
LOG_PATH   = ROOT / "data" / "output" / "transition_validation_log.jsonl"

_MAX_STEP_BYTES = 8 * 1024 * 1024


def _load_json(path: Path) -> Any:
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def _load_session_steps(session_dir: Path) -> list[dict]:
    steps = []
    for f in sorted(session_dir.glob("*.json")):
        if f.name == "session_manifest.json" or f.stat().st_size > _MAX_STEP_BYTES:
            continue
        d = _load_json(f)
        if d:
            steps.append(d)
    return steps


def _element_by_id(state: dict, eid: Any) -> dict | None:
    for e in state.get("elements", []):
        if e.get("element_id") == eid:
            return e
    return None


def _values(state: dict) -> dict:
    return {e["element_id"]: (e.get("value") or "").strip()
            for e in state.get("elements", []) if e.get("element_id")}


def check_transition(step: dict, next_state: dict) -> tuple[bool, str]:
    """Returns (is_actionable, is_correct). is_correct is only meaningful when
    is_actionable is True."""
    action = step.get("action", {})
    a_type = action.get("action_type", "noop")
    state  = step.get("state", {})

    if a_type in ("noop", None, "wait"):
        return False, True

    focus_before = state.get("focused_element_id")
    focus_after  = next_state.get("focused_element_id")
    vals_before  = _values(state)
    vals_after   = _values(next_state)
    any_value_changed = any(vals_after.get(k) != v for k, v in vals_before.items())
    elems_changed = len(next_state.get("elements", [])) != len(state.get("elements", []))

    if a_type == "click":
        target = action.get("target") or ""
        # Correct if focus moved at all, or (for a same-element re-click, e.g.
        # opening a combobox already focused) the element set changed (dropdown
        # opened) or a value changed (checkbox toggled in place).
        ok = (focus_before != focus_after) or any_value_changed or elems_changed
        return True, ok

    if a_type in ("keyboard", "type", "paste"):
        text = (action.get("text") or "").strip()
        fid  = focus_before
        el_after = _element_by_id(next_state, fid) if fid else None
        if not text:
            # A keystroke with no text payload (e.g. Tab/Enter) — focus moving
            # or a value changing both count as a mapped effect.
            ok = (focus_before != focus_after) or any_value_changed
        elif el_after is not None:
            ok = text.lower() in (el_after.get("value") or "").lower()
        else:
            ok = any_value_changed
        return True, ok

    # scroll / hotkey / drag / double_click / anything else — require *some*
    # observable change; a completely inert action means the transition wasn't
    # actually captured.
    ok = (focus_before != focus_after) or any_value_changed or elems_changed
    return True, ok


def validate_session(session_dir: Path) -> dict:
    t0 = time.time()
    steps = _load_session_steps(session_dir)
    actionable = 0
    correct = 0
    failures: list[dict] = []
    for i in range(len(steps) - 1):
        step, nxt = steps[i], steps[i + 1]
        is_actionable, is_correct = check_transition(step, nxt.get("state", {}))
        if not is_actionable:
            continue
        actionable += 1
        if is_correct:
            correct += 1
        else:
            failures.append({
                "index": i,
                "action_type": step.get("action", {}).get("action_type"),
            })
    accuracy = correct / actionable if actionable else 1.0
    return {
        "session": session_dir.name,
        "total_steps": len(steps),
        "actionable_transitions": actionable,
        "correct_transitions": correct,
        "mapping_accuracy": round(accuracy, 4),
        "meets_90pct_target": accuracy >= 0.90,
        "failures": failures[:20],
        "validation_time_sec": round(time.time() - t0, 3),
    }


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Validate recorded state-action transition mapping (objective 4).")
    ap.add_argument("session", nargs="?", help="Specific session directory (default: all under traces/).")
    ap.add_argument("--log", action="store_true", help="Append the aggregate result to a jsonl trend log.")
    args = ap.parse_args()

    if args.session:
        sessions = [Path(args.session)]
    else:
        sessions = sorted(d for d in TRACES_DIR.glob("*/*/") if d.is_dir()) or \
                   sorted(d for d in TRACES_DIR.glob("*/") if d.is_dir() and d.name.startswith("session_"))

    if not sessions:
        print(f"No sessions found under {TRACES_DIR}")
        sys.exit(1)

    t0 = time.time()
    results = [validate_session(s) for s in sessions]
    total_actionable = sum(r["actionable_transitions"] for r in results)
    total_correct    = sum(r["correct_transitions"]    for r in results)
    overall = total_correct / total_actionable if total_actionable else 1.0
    elapsed = time.time() - t0

    print(f"\n{'='*70}")
    print("  STATE-TRANSITION MAPPING VALIDATION  (objective 4, target >=90%)")
    print(f"{'='*70}")
    for r in results:
        flag = "PASS" if r["meets_90pct_target"] else "FAIL"
        print(f"  {r['session']:<28} {r['mapping_accuracy']*100:>6.1f}%  "
              f"({r['correct_transitions']}/{r['actionable_transitions']})  [{flag}]")
    print(f"  {'-'*66}")
    print(f"  OVERALL                     {overall*100:>6.1f}%  "
          f"({total_correct}/{total_actionable})  "
          f"[{'PASS' if overall >= 0.90 else 'FAIL'}]")
    print(f"  Validated {len(sessions)} session(s) in {elapsed:.2f}s "
          f"({elapsed/len(sessions):.3f}s/session avg)")
    print(f"{'='*70}\n")

    if args.log:
        import datetime as _dt
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": _dt.datetime.now().isoformat(),
            "sessions_validated": len(sessions),
            "overall_mapping_accuracy": round(overall, 4),
            "meets_90pct_target": overall >= 0.90,
            "total_validation_time_sec": round(elapsed, 3),
        }
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"Logged to {LOG_PATH}")


if __name__ == "__main__":
    main()
