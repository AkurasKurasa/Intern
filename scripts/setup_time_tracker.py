"""
scripts/setup_time_tracker.py
================================
Objectives 11 & 12 need "setup time" — how long it takes to get Intern
working on a NEW task, to compare against how long the same task takes to
set up in a traditional RPA tool. That span crosses several separate
entrypoints (record demos -> clean_demos.py -> train.py -> build_capsule.py),
so there's no single process to time it from inside. This is a plain
start/stop stopwatch keyed by a label, persisted to disk so it survives
across those separate invocations.

Usage
-----
  python scripts/setup_time_tracker.py --start car_insurance_v2
  ... record demos, clean, train, build_capsule for that task ...
  python scripts/setup_time_tracker.py --stop car_insurance_v2

Elapsed time is appended to data/output/setup_time_log.jsonl, which
compare_baseline.py reads for the Intern side of the setup-time comparison.
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

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "data" / "output" / ".setup_timers"
LOG_PATH  = ROOT / "data" / "output" / "setup_time_log.jsonl"


def start(label: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    marker = STATE_DIR / f"{label}.json"
    if marker.exists():
        print(f"Timer {label!r} already running (started {json.loads(marker.read_text())['started_iso']}). "
              "Stop it first, or pick a new label.")
        sys.exit(1)
    import datetime as _dt
    marker.write_text(json.dumps({
        "label": label,
        "started_ts": time.time(),
        "started_iso": _dt.datetime.now().isoformat(),
    }))
    print(f"Started setup timer {label!r}.")


def stop(label: str, note: str = "") -> None:
    marker = STATE_DIR / f"{label}.json"
    if not marker.exists():
        print(f"No running timer named {label!r}. (python scripts/setup_time_tracker.py --start {label})")
        sys.exit(1)
    started = json.loads(marker.read_text())
    elapsed = time.time() - started["started_ts"]

    import datetime as _dt
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "label":        label,
            "started_iso":  started["started_iso"],
            "stopped_iso":  _dt.datetime.now().isoformat(),
            "elapsed_sec":  round(elapsed, 1),
            "note":         note,
        }) + "\n")
    marker.unlink()
    print(f"Stopped {label!r}: {elapsed:.1f}s ({elapsed/60:.1f} min) -> logged to {LOG_PATH}")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Stopwatch for Intern's task-setup time (objectives 11/12).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--start", metavar="LABEL", help="Start a timer for this task label.")
    g.add_argument("--stop",  metavar="LABEL", help="Stop the timer and log elapsed time.")
    ap.add_argument("--note", default="", help="Optional note to attach when stopping (e.g. 'n=20 demos').")
    args = ap.parse_args()

    if args.start:
        start(args.start)
    else:
        stop(args.stop, args.note)


if __name__ == "__main__":
    main()
