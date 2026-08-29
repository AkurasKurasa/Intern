"""
components/inbox_router/bootstrap_from_sent.py
==================================================
One-time Record bootstrap: replays the same Sent-folder correlation
PatternProfile.observe_sent_history() already does, but emits one recorded
training example per correlated thread instead of (only) updating pattern
counters. Reply/forward only -- Sent history structurally cannot teach
schedule, cold_email, flag, or leave_alone, since none of those
decisions ever produce a sent message.

Usage:
    python components/inbox_router/bootstrap_from_sent.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from decision_recorder import DEFAULT_EXAMPLES_PATH, record_example
from gmail_client import EmailMessage, get_gmail_client
from pattern_profile import FORWARD_MARKERS, _domain

SENT_LOOKBACK_DAYS = 90


def bootstrap_examples(sent: List[EmailMessage], inbox: List[EmailMessage],
                        path: str = DEFAULT_EXAMPLES_PATH) -> int:
    by_thread = {m.thread_id: m for m in inbox}
    count = 0
    for s in sent:
        origin = by_thread.get(s.thread_id)
        if origin is None:
            continue
        domain = _domain(origin.sender_email)
        body_lower = (s.body_text or "").lower()
        is_forward = any(marker in body_lower for marker in FORWARD_MARKERS) or (
            s.to and _domain(s.to) != domain
        )
        decision = "forward" if is_forward else "reply"
        record_example(origin, decision, source="bootstrap", path=path)
        count += 1
    return count


def main() -> None:
    client = get_gmail_client()
    since_iso = (datetime.now(timezone.utc) - timedelta(days=SENT_LOOKBACK_DAYS)).isoformat()
    sent = client.list_sent(since_iso)
    inbox = client.list_recent_inbox(since_iso)
    count = bootstrap_examples(sent, inbox)
    print(f"Bootstrapped {count} training examples from {len(sent)} sent + {len(inbox)} inbox messages.")


if __name__ == "__main__":
    main()
