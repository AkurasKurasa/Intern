"""
scripts/encoding_ambiguity.py
================================
Objective 2: "a structured representation of data composed of GUI states and
user actions suitable for machine learning, ensuring consistent encoding with
minimal ambiguity (<5% error rate) based on a defined dataset of recorded
workflows."

The model (and the LLM's target-matching) identifies an element by its
(type, label) signature — see components/agent/agent.py's pointer/label
matching and _TextResolver. If two interactive elements in the same state
share an identical (type, label) signature, the encoding is AMBIGUOUS: a
prediction that says "click the field labeled X" doesn't uniquely resolve to
one element. This walks the recorded dataset (same trace sessions used for
training) and measures how often that happens.

    ambiguity_rate = elements in a colliding (type,label) group / all
                     interactive elements, per state, averaged over the dataset

Usage
-----
  python scripts/encoding_ambiguity.py                  # all sessions
  python scripts/encoding_ambiguity.py SESSION_DIR       # one session
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
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TRACES_DIR = ROOT / "tasks" / "form_filling" / "traces"
LOG_PATH   = ROOT / "data" / "output" / "encoding_ambiguity_log.jsonl"
_MAX_STEP_BYTES = 8 * 1024 * 1024

_INTERACTIVE = {
    "editcontrol", "comboboxcontrol", "checkboxcontrol", "radiobuttoncontrol",
    "buttoncontrol", "splitbuttoncontrol", "tabitemcontrol", "listitemcontrol",
    "hyperlinkcontrol",
}


def _load_json(path: Path) -> Any:
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def state_ambiguity(state: dict) -> tuple[int, int]:
    """Returns (ambiguous_element_count, total_interactive_count) for one state."""
    elems = [e for e in state.get("elements", [])
             if (e.get("type") or "").lower() in _INTERACTIVE]
    if not elems:
        return 0, 0
    groups: dict[tuple, int] = defaultdict(int)
    for e in elems:
        sig = ((e.get("type") or "").lower(), (e.get("label") or e.get("text") or "").strip().lower())
        groups[sig] += 1
    ambiguous = sum(count for count in groups.values() if count > 1)
    return ambiguous, len(elems)


def validate_session(session_dir: Path) -> dict:
    t0 = time.time()
    ambiguous_total = 0
    interactive_total = 0
    states_checked = 0
    for f in sorted(session_dir.glob("*.json")):
        if f.name == "session_manifest.json" or f.stat().st_size > _MAX_STEP_BYTES:
            continue
        d = _load_json(f)
        if not d:
            continue
        state = d.get("state", d)
        amb, total = state_ambiguity(state)
        if total == 0:
            continue
        ambiguous_total += amb
        interactive_total += total
        states_checked += 1
    rate = ambiguous_total / interactive_total if interactive_total else 0.0
    return {
        "session": session_dir.name,
        "states_checked": states_checked,
        "ambiguous_elements": ambiguous_total,
        "total_interactive_elements": interactive_total,
        "ambiguity_rate": round(rate, 4),
        "meets_5pct_target": rate <= 0.05,
        "encode_time_sec": round(time.time() - t0, 3),
    }


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Measure state-action encoding ambiguity (objective 2, target <5%).")
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
    total_amb   = sum(r["ambiguous_elements"] for r in results)
    total_elems = sum(r["total_interactive_elements"] for r in results)
    overall = total_amb / total_elems if total_elems else 0.0
    elapsed = time.time() - t0

    print(f"\n{'='*70}")
    print("  ENCODING AMBIGUITY  (objective 2, target <5%)")
    print(f"{'='*70}")
    for r in results:
        if r["total_interactive_elements"] == 0:
            continue
        flag = "PASS" if r["meets_5pct_target"] else "FAIL"
        print(f"  {r['session']:<28} {r['ambiguity_rate']*100:>6.2f}%  "
              f"({r['ambiguous_elements']}/{r['total_interactive_elements']})  [{flag}]")
    print(f"  {'-'*66}")
    print(f"  OVERALL                     {overall*100:>6.2f}%  "
          f"({total_amb}/{total_elems})  [{'PASS' if overall <= 0.05 else 'FAIL'}]")
    print(f"  Encoded {len(sessions)} session(s) in {elapsed:.2f}s "
          f"({elapsed/len(sessions):.3f}s/session avg)")
    print(f"{'='*70}\n")

    if args.log:
        import datetime as _dt
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": _dt.datetime.now().isoformat(),
            "sessions_checked": len(sessions),
            "overall_ambiguity_rate": round(overall, 4),
            "meets_5pct_target": overall <= 0.05,
            "total_encode_time_sec": round(elapsed, 3),
        }
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"Logged to {LOG_PATH}")


if __name__ == "__main__":
    main()
