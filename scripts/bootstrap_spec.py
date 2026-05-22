"""
bootstrap_spec.py
=================
Runs RuleExtractor.correct() on every existing recording session to build
the initial form_filling.md task spec from scratch.

Each session refines the spec — by the end, form_filling.md contains the
LLM's best understanding of the task derived from all human demonstrations.

Usage:
    python scripts/bootstrap_spec.py
    python scripts/bootstrap_spec.py --traces_dir data/output/traces/forms --provider lmstudio
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_COMP = _ROOT / "components"
for _p in (_ROOT, _COMP):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_env_path = _ROOT / ".env"
if _env_path.exists():
    with _env_path.open() as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

GOAL      = "Fill the car insurance form using data from the open text file"
TASK_NAME = "form_filling"


def main():
    parser = argparse.ArgumentParser(description="Bootstrap task spec from existing sessions.")
    parser.add_argument("--traces_dir", default="data/output/traces/forms")
    parser.add_argument("--provider",   default="lmstudio",
                        choices=["lmstudio", "anthropic", "groq"])
    parser.add_argument("--output_dir", default="data/output/rulesets")
    parser.add_argument("--min_traces", type=int, default=5,
                        help="Skip sessions with fewer than this many trace files.")
    args = parser.parse_args()

    from intelligence.rule_extractor import RuleExtractor

    api_key = os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("GROQ_API_KEY", "")
    extractor = RuleExtractor(
        provider   = args.provider,
        api_key    = api_key,
        output_dir = args.output_dir,
    )

    traces_root = _ROOT / args.traces_dir
    sessions = sorted([
        d for d in traces_root.iterdir()
        if d.is_dir() and d.name.startswith("session_")
    ])

    if not sessions:
        print(f"No session_* folders found in {traces_root}")
        sys.exit(1)

    print(f"Found {len(sessions)} session(s) in {traces_root}")
    print(f"Provider: {args.provider}")
    print(f"Output:   {args.output_dir}/{TASK_NAME}.md\n")

    skipped = 0
    processed = 0

    for i, session_dir in enumerate(sessions, 1):
        trace_files = [f for f in session_dir.glob("*.json")
                       if f.name != "session_manifest.json"]
        if len(trace_files) < args.min_traces:
            print(f"[{i}/{len(sessions)}] SKIP {session_dir.name} — only {len(trace_files)} traces")
            skipped += 1
            continue

        print(f"[{i}/{len(sessions)}] Processing {session_dir.name} ({len(trace_files)} traces)…")
        try:
            extractor.correct(
                session_dir = session_dir,
                goal        = GOAL,
                task_name   = TASK_NAME,
            )
            processed += 1
        except Exception as exc:
            print(f"  ERROR: {exc}")

    spec_path = _ROOT / args.output_dir / f"{TASK_NAME}.md"
    print(f"\nDone. Processed {processed} session(s), skipped {skipped}.")
    print(f"Spec saved -> {spec_path}")
    if spec_path.exists():
        size = len(spec_path.read_text(encoding="utf-8"))
        print(f"Spec size: {size} chars")


if __name__ == "__main__":
    main()
