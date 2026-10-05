"""
components/inbox_router/demo_reset.py
=====================================
Puts Scope #3's practice data back to the start, so every rehearsal and the
real demo begin the same way: every mock email waiting, nobody on the cold
email list contacted yet, no drafts or calendar events left from earlier runs.

Direct request 2026-10-05: reset "Automatically on Launch" (chosen over a
separate button). Found before it: 27 of 30 mock emails were already marked
handled, so Play had almost nothing left to show.

What is reset is only what a run USES UP. What Intern learned from the user
is never touched -- pattern_profile.json, reply_examples.jsonl,
training_examples.jsonl, schedule.txt, and routed_history.json (recorded
sessions are labelled from it by reply_trace_translator.py). Everything
reset is copied to data/demo_backups/<timestamp>/ first, so a Launch pressed
by mistake loses nothing.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(_THIS_DIR, "data")

# file -> its "nothing has happened yet" content
CONSUMABLE = {
    "mock_state.json": {"processed_ids": []},          # which mock emails were handled
    "cold_email_state.json": {"contacted_emails": []},  # who got a cold email
    "mock_drafts.json": {"drafts": []},                 # drafts created by runs
    "mock_calendar_events.json": {"events": []},        # events created by runs
}

# Learned from the user -- listed so a test can prove they are never touched.
NEVER_RESET = (
    "pattern_profile.json", "reply_examples.jsonl", "training_examples.jsonl",
    "schedule.txt", "routed_history.json", "mock_inbox.json", "task_list.txt",
)


def _empty_shape(name: str, path: str):
    """Keep the file's own top-level key if it differs from the default, so
    a reader that expects e.g. {"drafts": [...]} vs a bare list still works."""
    default = CONSUMABLE[name]
    try:
        with open(path, "r", encoding="utf-8") as f:
            current = json.load(f)
    except Exception:  # noqa: BLE001 -- unreadable: fall back to the default
        return default
    if isinstance(current, list):
        return []
    if isinstance(current, dict):
        return {k: ([] if isinstance(v, list) else v) for k, v in current.items()} or default
    return default


def uses_real_gmail(credentials_dir: str | None = None) -> bool:
    """Same switch get_gmail_client() uses. With a real account connected,
    resetting the contacted list would make Intern draft to real people
    again, so the reset is skipped."""
    from gmail_client import DEFAULT_CREDENTIALS_DIR
    return os.path.isfile(os.path.join(credentials_dir or DEFAULT_CREDENTIALS_DIR, "client_secret.json"))


def reset_demo_data(data_dir: str = DEFAULT_DATA_DIR, now: datetime | None = None) -> dict:
    """Reset every consumable file that exists. Returns {"backup": dir or
    None, "reset": [file names]}. Files that do not exist are left absent --
    their readers already treat a missing file as "nothing yet"."""
    now = now or datetime.now()
    present = [n for n in CONSUMABLE if os.path.exists(os.path.join(data_dir, n))]
    if not present:
        return {"backup": None, "reset": []}
    backup = os.path.join(data_dir, "demo_backups", now.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(backup, exist_ok=True)
    for name in present:
        path = os.path.join(data_dir, name)
        shutil.copy2(path, os.path.join(backup, name))
        empty = _empty_shape(name, path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(empty, f, indent=2)
    return {"backup": backup, "reset": present}
