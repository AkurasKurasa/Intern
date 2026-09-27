"""
components/inbox_router/autonomous_watcher.py
==================================================
Scope #3's actual point: a system that watches your real inbox and acts
on it itself when real work shows up, instead of waiting for you to
open Inbox Dispatch and click through it. Runs InboxRouter's real
classification pipeline (rule layer -> trained InboxDecisionNet -> LLM
fallback -- the same one Inbox Dispatch and automate_inbox.py already
use) continuously against real new mail.

For a "reply"/"forward" decision, this asks the trained ReplyAgent
(step 2 of the learned-autonomous-reply plan) whether one of your own
past replies confidently fits the new email. If so, it creates the
Gmail draft for you automatically -- gmail_client.py deliberately has
no send() method anywhere in this project, so the furthest this ever
goes is a draft sitting in Gmail waiting for you to read and send it
yourself. That boundary doesn't move either.

    python autonomous_watcher.py                  # runs until Ctrl+C
    python autonomous_watcher.py --once            # one pass, then exit
    python autonomous_watcher.py --poll 30         # check every 30s when idle
"""
from __future__ import annotations

import argparse
import json
import os
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

AUTO_DRAFT_LOG_PATH = Path(_THIS_DIR) / "data" / "autonomous_drafts.jsonl"


def _append_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def handle_entry(entry: dict, reply_agent=None, gmail_client=None,
                  auto_draft_log_path: Path = AUTO_DRAFT_LOG_PATH) -> dict:
    """Decides what to do with one freshly-classified email. Pure
    dispatch logic, kept separate from the polling loop so every branch
    is testable without a real process or a real timer. The log path
    defaults to the real project file but is injectable so tests write
    to a tmp_path instead of polluting real project data on every test
    run.

    reply_agent/gmail_client default to None -- omit both (as most
    tests that don't care about replies do) and a reply/forward
    decision just falls through to left_pending exactly like before
    this feature existed."""
    decision = entry.get("decision")

    if decision in ("reply", "forward") and reply_agent is not None and gmail_client is not None:
        message = gmail_client.get_message(entry.get("message_id"))
        suggestion = reply_agent.suggest_reply(message) if message is not None else None
        if suggestion is not None and suggestion.reply_body:
            to = entry.get("forward_to", "") if decision == "forward" else message.sender_email
            subject = ("Fwd: " if decision == "forward" else "Re: ") + message.subject
            draft_id = gmail_client.create_draft(to=to, subject=subject, body=suggestion.reply_body,
                                                  thread_id=message.thread_id)
            outcome = {
                "action": "auto_drafted", "message_id": entry.get("message_id"),
                "subject": entry.get("subject"), "decision": decision,
                "draft_id": draft_id, "confidence": suggestion.confidence,
                "source_message_id": suggestion.source_message_id,
                "at": datetime.now(timezone.utc).isoformat(),
            }
            _append_jsonl(auto_draft_log_path, outcome)
            return outcome

    return {
        "action": "left_pending", "message_id": entry.get("message_id"),
        "subject": entry.get("subject"), "decision": decision,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def watch(router,
          poll_interval: float = 30.0, max_iterations: Optional[int] = None,
          stop_when_idle: bool = False,
          sleep=time.sleep,
          reply_agent=None, gmail_client=None,
          auto_draft_log_path: Path = AUTO_DRAFT_LOG_PATH) -> list:
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
        outcome = handle_entry(entry,
                                reply_agent=reply_agent, gmail_client=gmail_client,
                                auto_draft_log_path=auto_draft_log_path)
        outcomes.append(outcome)
        if outcome["action"] == "auto_drafted":
            print(f"  DRAFT READY: '{entry.get('subject')}' -- reply drafted for you to review and send "
                  f"(confidence {outcome.get('confidence', 0.0):.0%})")
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
    from reply_agent import ReplyAgent  # noqa: E402 -- lazy, same reason
    from gmail_client import get_gmail_client  # noqa: E402

    router = build_router()
    reply_agent = ReplyAgent()
    gmail_client = get_gmail_client()

    print("Watching for new mail" + ("" if args.once else " -- Ctrl+C to stop") + ".\n")
    try:
        outcomes = watch(router, poll_interval=args.poll,
                          stop_when_idle=args.once,
                          reply_agent=reply_agent, gmail_client=gmail_client)
        if args.once:
            print(f"\nProcessed {len(outcomes)} email(s).")
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
