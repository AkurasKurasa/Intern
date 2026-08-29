"""
components/inbox_router/schedule_recorder.py
=================================================
Output step for the "schedule" decision. Unlike reply_recorder.py, this
has no matching/reuse concept -- a schedule note is new information
every time (a new date, a new task), nothing to usefully reuse from a
past note. So this is simply: whatever real text a human typed gets
appended to a plain text file, verbatim. Same honesty guarantee
reply_recorder.py already has -- a blank/whitespace-only note saves
nothing, never invents content.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from gmail_client import EmailMessage

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCHEDULE_LOG_PATH = os.path.join(_THIS_DIR, "data", "schedule.txt")


def record_schedule_entry(message: EmailMessage, note: str,
                           path: str = DEFAULT_SCHEDULE_LOG_PATH) -> None:
    note = (note or "").strip()
    if not note:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    recorded_at = datetime.now(timezone.utc).isoformat()
    line = f"[{recorded_at}] {message.subject!r} ({message.sender_email}): {note}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
