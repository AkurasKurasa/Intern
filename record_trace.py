#!/usr/bin/env python3
"""
record_trace.py  —  ScreenObserver launcher

Run this script to start recording your screen, mouse, and keyboard.
Press Ctrl+C at any time to stop.  A trace JSON will be saved automatically.

Usage:
    python record_trace.py [options]

Options:
    --output    Output directory for trace JSONs  (default: data/output/traces/live)
    --interval  Seconds between screen captures   (default: 1.0)
    --type      Trace type: web | excel | gui     (default: gui)
    --app       Application name tag              (optional)
    --duration  Auto-stop after N seconds         (optional, default: manual Ctrl+C)

Examples:
    python record_trace.py
    python record_trace.py --interval 2 --type excel --output data/output/traces/excel
    python record_trace.py --duration 30
"""

import argparse
import sys
import os

# ── make sure the project root is on the path ─────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))   # Intern/
_COMP = os.path.join(_ROOT, "components")             # components/
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_env_path = os.path.join(_ROOT, ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from recorder.recorder import ScreenObserver


def parse_args():
    parser = argparse.ArgumentParser(
        description="Record screen + inputs and output trace JSONs."
    )
    parser.add_argument(
        "--output", default="data/output/traces/live",
        help="Directory where trace JSONs will be saved."
    )
    parser.add_argument(
        "--interval", type=float, default=2.0,
        help="Seconds between screen captures (default: 2.0)."
    )
    parser.add_argument(
        "--type", dest="trace_type", default="gui",
        choices=["web", "excel", "gui"],
        help="Type of trace (default: gui)."
    )
    parser.add_argument(
        "--app", default=None,
        help="Application name to tag in trace metadata."
    )
    parser.add_argument(
        "--duration", type=float, default=None,
        help="Auto-stop after this many seconds (omit for manual Ctrl+C stop)."
    )
    parser.add_argument(
        "--goal", default="Fill the car insurance form using data from the open text file",
        help="Task goal — used by rule extractor to correct the task spec."
    )
    parser.add_argument(
        "--task", default="form_filling",
        help="Task name — identifies the persistent spec file."
    )
    parser.add_argument(
        "--provider", default="lmstudio",
        choices=["lmstudio", "anthropic", "groq", "none"],
        help="LLM provider for rule extraction (default: lmstudio)."
    )
    parser.add_argument(
        "--no-extract", action="store_true",
        help="Skip rule extraction after recording."
    )
    return parser.parse_args()


def main():
    args = parse_args()

    observer = ScreenObserver(
        output_dir=args.output,
        trace_type=args.trace_type,
        application=args.app,
    )

    if args.duration:
        # ── timed recording ───────────────────────────────────────────────────
        print(f"Recording for {args.duration}s …")
        traces = observer.record(duration_sec=args.duration, interval_sec=args.interval)
    else:
        # ── manual stop via Ctrl+C ────────────────────────────────────────────
        observer.start(interval_sec=args.interval)
        try:
            while True:
                pass   # keep main thread alive; Ctrl+C triggers KeyboardInterrupt
        except KeyboardInterrupt:
            traces = observer.stop()

    session_dir = observer.output_dir
    print(f"\nDone — {len(traces)} trace(s) saved to: {session_dir}")

    # ── correctional rule extraction ──────────────────────────────────────────
    if not args.no_extract and args.provider != "none" and len(traces) >= 5:
        print("\nRunning correctional rule extraction …")
        try:
            from intelligence.rule_extractor import RuleExtractor
            api_key = os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("GROQ_API_KEY", "")
            extractor = RuleExtractor(
                provider   = args.provider,
                api_key    = api_key,
                output_dir = "tasks/form_filling",
            )
            extractor.correct(
                session_dir = session_dir,
                goal        = args.goal,
                task_name   = args.task,
            )
        except Exception as exc:
            print(f"Rule extraction failed: {exc}")
    elif len(traces) < 5:
        print("(Skipping rule extraction — session too short, < 5 traces)")


if __name__ == "__main__":
    main()
