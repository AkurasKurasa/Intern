#!/usr/bin/env python
"""
build_ruleset.py — infer the persistent task ruleset from RECORDED DEMOS.

A ruleset only means something across the *corpus* of past records: a rule
(e.g. "leave PIP blank on liability-only policies", fill order, conditional
skips) is only visible when the user did the same thing across MANY records.
So we run `RuleExtractor.correct()` over each demo session in turn — it reads
the existing spec + the new session and refines it — accumulating the rules the
user consistently applied.

Output: tasks/form_filling/ruleset.md  (loaded by the agent into the LLM's system
prompt at startup -> steers every value decision). No hardcode — rules come from
the user's own demonstrations.

Usage:
    python scripts/build_ruleset.py data/demos/eight_Tabs
    python scripts/build_ruleset.py data/demos/eight_Tabs --provider lmstudio \
        --goal "Fill the car insurance form using data from the open text file"
"""
import sys
import argparse
from pathlib import Path

# components/ on the path so `from intelligence.rule_extractor import ...` resolves
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "components"))

from intelligence.rule_extractor import RuleExtractor  # noqa: E402


def _sessions(demo_dir: Path):
    """Recorded sessions: each session_* subdir, else the flat dir if it holds traces."""
    subs = sorted(d for d in demo_dir.glob("session_*") if d.is_dir())
    if subs:
        return subs
    if any(demo_dir.glob("*.json")):
        return [demo_dir]
    return []


def main():
    ap = argparse.ArgumentParser(description="Infer ruleset.md from recorded demos.")
    ap.add_argument("demo_dir", help="dir of recorded sessions (e.g. data/demos/eight_Tabs)")
    ap.add_argument("--goal", default="Fill the car insurance form using data from the open text file")
    ap.add_argument("--provider", default="lmstudio", help="lmstudio | anthropic | groq")
    ap.add_argument("--task", default="form_filling")
    ap.add_argument("--out", default="tasks/form_filling", help="dir holding the persistent ruleset.md")
    a = ap.parse_args()

    demo_dir = Path(a.demo_dir)
    sessions = _sessions(demo_dir)
    if not sessions:
        print(f"No demo sessions found in {demo_dir}")
        sys.exit(1)

    rx = RuleExtractor(provider=a.provider, output_dir=a.out)
    print(f"Building ruleset from {len(sessions)} session(s) -> {rx._persistent_path}")
    print(f"Provider: {a.provider}  |  Goal: {a.goal}\n")

    for i, s in enumerate(sessions, 1):
        print(f"[{i}/{len(sessions)}] correct() over {s.name} ...", flush=True)
        try:
            rx.correct(session_dir=s, goal=a.goal, task_name=a.task)
        except Exception as exc:
            print(f"    WARNING: {s.name} failed — {exc}")

    print(f"\nDone. Inferred ruleset -> {rx._persistent_path}")
    print("The agent loads this at startup; the value-LLM follows it each run.")


if __name__ == "__main__":
    main()
