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

Where it goes (direct request 2026-10-05, "Can you please unify both into
one"): the boss' own task list, under a "Scheduled:" section at the end --
one Notepad file holding what was asked (cold emails, checks) and what got
scheduled, instead of a separate schedule.txt nobody saw the instructions
next to. task_list_parser.py treats "Scheduled:" like any other unknown
heading, so an entry here can never be read as a cold-email target or a
check (tests/test_inbox_unified_notepad.py).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from gmail_client import EmailMessage

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCHEDULE_LOG_PATH = os.path.join(_THIS_DIR, "data", "task_list.txt")

SCHEDULED_HEADING = "Scheduled:"


def _ensure_section(path: str) -> str:
    """Text to write before the first entry: the heading, separated from the
    instructions above by one blank line. Empty once the heading exists."""
    if not os.path.exists(path):
        return SCHEDULED_HEADING + "\n"
    with open(path, "r", encoding="utf-8") as f:
        existing = f.read()
    if any(line.strip() == SCHEDULED_HEADING for line in existing.splitlines()):
        return "" if existing.endswith("\n") or not existing else "\n"
    if not existing.strip():
        return SCHEDULED_HEADING + "\n"
    sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    return sep + SCHEDULED_HEADING + "\n"


def record_schedule_entry(message: EmailMessage, note: str,
                           path: str = DEFAULT_SCHEDULE_LOG_PATH) -> None:
    note = (note or "").strip()
    if not note:
        return
    note = note.replace("\n", " ")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    recorded_at = datetime.now(timezone.utc).isoformat()
    line = f"[{recorded_at}] {message.subject!r} ({message.sender_email}): {note}\n"
    prefix = _ensure_section(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(prefix + line)
