"""
components/inbox_router/autonomous_watcher.py
==================================================
Scope #3's actual point: turns Scope #1 and #2 from tools you have to
remember to click Play on into a system that watches your real inbox
and runs them itself when real work shows up. Runs InboxRouter's real
classification pipeline (rule layer -> trained InboxDecisionNet -> LLM
fallback -- the same one Inbox Dispatch and automate_inbox.py already
use) continuously against real new mail. For each email the classifier
decides needs Scope #2, this actually launches that automation live --
no click from anyone.

Scope #1 needs your real screen, so it is NEVER auto-launched here --
this only ever surfaces "this needs Scope #1" and waits for you to
press Play on it yourself. That boundary doesn't move.

    python autonomous_watcher.py                  # runs until Ctrl+C
    python autonomous_watcher.py --once            # one pass, then exit
    python autonomous_watcher.py --poll 30         # check every 30s when idle
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
_COMP = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.capsule import CapsuleRegistry  # noqa: E402

NEEDS_ATTENTION_PATH = Path(_THIS_DIR) / "data" / "needs_attention.jsonl"
DISPATCH_LOG_PATH = Path(_THIS_DIR) / "data" / "dispatch_log.jsonl"


def _append_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def dispatch_scope2(entry: dict, registry: CapsuleRegistry, repo_root: str,
                     popen=subprocess.Popen) -> dict:
    """Actually launches the real Scope #2 automation matched to this
    email. `popen` is injectable so tests can verify the exact command
    that WOULD run without spawning a real process. Never raises -- a
    launch failure is a normal, loggable outcome, not fatal to the
    watcher's own loop."""
    capsule = next((c for c in registry.list_capsules() if c.name == entry.get("capsule_name")), None)
    if capsule is None:
        return {"ok": False, "reason": f"capsule '{entry.get('capsule_name')}' not found in registry"}
    try:
        argv, cwd = capsule.launch_command(repo_root)
    except FileNotFoundError as exc:
        return {"ok": False, "reason": str(exc)}
    process = popen(argv, cwd=cwd)
    return {"ok": True, "pid": process.pid, "argv": argv}


def handle_entry(entry: dict, registry: CapsuleRegistry, repo_root: str,
                  popen=subprocess.Popen,
                  dispatch_log_path: Path = DISPATCH_LOG_PATH,
                  needs_attention_path: Path = NEEDS_ATTENTION_PATH) -> dict:
    """Decides what to do with one freshly-classified email. Pure
    dispatch logic, kept separate from the polling loop so every branch
    is testable without a real process or a real timer. The two log
    paths default to the real project files but are injectable so tests
    write to a tmp_path instead of polluting real project data on every
    test run."""
    decision = entry.get("decision")
    capsule_name = entry.get("capsule_name")

    if decision == "route_scope2" and capsule_name:
        dispatch_result = dispatch_scope2(entry, registry, repo_root, popen=popen)
        outcome = {
            "action": "dispatched_scope2", "message_id": entry.get("message_id"),
            "subject": entry.get("subject"), "capsule_name": capsule_name,
            "dispatch_result": dispatch_result, "at": datetime.now(timezone.utc).isoformat(),
        }
        _append_jsonl(dispatch_log_path, outcome)
        return outcome

    if decision == "route_scope1" and capsule_name:
        outcome = {
            "action": "needs_attention", "message_id": entry.get("message_id"),
            "subject": entry.get("subject"), "capsule_name": capsule_name,
            "reason": "Scope #1 needs your real screen -- press Play on this capsule yourself.",
            "at": datetime.now(timezone.utc).isoformat(),
        }
        _append_jsonl(needs_attention_path, outcome)
        return outcome

    return {
        "action": "left_pending", "message_id": entry.get("message_id"),
        "subject": entry.get("subject"), "decision": decision,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def watch(router, registry: CapsuleRegistry, repo_root: str,
          poll_interval: float = 30.0, max_iterations: Optional[int] = None,
          stop_when_idle: bool = False,
          popen=subprocess.Popen, sleep=time.sleep,
          dispatch_log_path: Path = DISPATCH_LOG_PATH,
          needs_attention_path: Path = NEEDS_ATTENTION_PATH) -> list:
    """The continuous loop: process one newly-classified email at a
    time, react to it, repeat.

    stop_when_idle=True exits as soon as the inbox has nothing left to
    process, instead of sleeping and checking again -- this is what
    --once uses, and what tests use, so neither has to spin through a
    capped iteration count waiting for a no-op sleep to "pass time."
    False (the default) is the real, continuous, run-forever watcher.

    max_iterations caps how many emails get handled before returning
    regardless of what's still pending -- an independent safety valve
    from stop_when_idle, for tests that want to stop after N real
    dispatches without asserting the inbox ran dry.

    Returns the list of outcomes handled, in order."""
    outcomes = []
    while max_iterations is None or len(outcomes) < max_iterations:
        entry = router.process_next_unprocessed()
        if entry is None:
            if stop_when_idle:
                break
            sleep(poll_interval)
            continue
        outcome = handle_entry(entry, registry, repo_root, popen=popen,
                                dispatch_log_path=dispatch_log_path,
                                needs_attention_path=needs_attention_path)
        outcomes.append(outcome)
        if outcome["action"] == "dispatched_scope2":
            print(f"  DISPATCHED: '{entry.get('subject')}' -> {entry.get('capsule_name')} (Scope #2, running now)")
        elif outcome["action"] == "needs_attention":
            print(f"  NEEDS YOUR ATTENTION: '{entry.get('subject')}' -> {entry.get('capsule_name')} (Scope #1)")
        elif outcome["action"] == "left_pending":
            print(f"  left pending for manual review: '{entry.get('subject')}' ({entry.get('decision')})")
    return outcomes


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--poll", type=float, default=30.0,
                    help="seconds to wait between checks when the inbox is empty (default: 30)")
    ap.add_argument("--once", action="store_true",
                    help="process whatever's waiting right now, then exit, instead of running forever")
    args = ap.parse_args()

    from local_server import build_router  # noqa: E402 -- lazy, keeps torch out of module scope

    router = build_router()
    registry = CapsuleRegistry()

    print("Watching for new mail" + ("" if args.once else " -- Ctrl+C to stop") + ".\n")
    try:
        outcomes = watch(router, registry, _ROOT, poll_interval=args.poll,
                          stop_when_idle=args.once)
        if args.once:
            print(f"\nProcessed {len(outcomes)} email(s).")
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
