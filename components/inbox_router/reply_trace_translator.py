"""
components/inbox_router/reply_trace_translator.py
======================================================
Reads a finished DemoRecorder session (trace_type="web",
live_step_NNNN.json files) and pulls out real reply examples --
(which email, what was typed) -- recorded through the exact same
reply_recorder.record_reply_example() the direct HTTP-based capture
already uses. Manually run, same as train_reply_model.py:

    python components/inbox_router/reply_trace_translator.py --session-dir <path>

A step counts as a submitted reply when its own textarea value is
non-empty AND the step's own next_state contains the real success
status message the UI shows on a submit ("Confirmed." / "Overridden.").
DemoRecorder captures a before/after pair for every action within a
single step file: state is before, next_state is after for that exact
click or keystroke. The submitting click's own before/after pair,
captured within that one step, already shows the success message in
next_state — no special-casing for session position is needed.

Why a POSITIVE signal, not "the textarea disappeared" (fixed 2026-08-28)
-----------------------------------------------------------------------
The original check was negative: textarea present in state, absent in
next_state => submitted. That was wrong twice over.

  1. Back and Archive ALSO close the detail view and hide the textarea.
     A draft the human typed and then deliberately abandoned was being
     recorded as a real, sent reply -- the exact opposite of this file's
     entire purpose, which is to learn only from text a human actually
     stood behind and sent.
  2. It failed OPEN. If next_state was missing or empty for ANY reason
     (a snapshot that returned nothing, a truncated session, a recorder
     bug), "no textarea in next_state" was trivially true, so absence of
     evidence was read as proof of submission. A translator whose only
     job is honesty must fail CLOSED: no evidence => don't record.

Requiring the success status message inverts both. Confirm/Override are
the only two actions that produce it; Back produces nothing and Archive
produces a different message ("Archived."). Missing/empty next_state now
correctly means "no evidence", so nothing is written.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from typing import Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
for _p in (_ROOT, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import get_gmail_client
from reply_recorder import DEFAULT_REPLY_EXAMPLES_PATH, record_reply_example
from schedule_recorder import DEFAULT_SCHEDULE_LOG_PATH, record_schedule_entry


# The two literal strings local_ui/app.js shows in its #snackbar
# (role="status") when a decision is really submitted -- confirmCurrent()
# shows "Confirmed.", overrideCurrent() shows "Overridden.". Archive's own
# message ("Archived.") is deliberately NOT here: archiving is not a reply.
_SUBMIT_STATUS_TEXTS = {"Confirmed.", "Overridden."}

DEFAULT_HISTORY_PATH = os.path.join(_THIS_DIR, "data", "routed_history.json")


def _find_reply_textarea(state: dict) -> Optional[dict]:
    for el in state.get("elements", []):
        if el.get("control_type") == "textarea":
            return el
    return None


def _has_submit_status(state: dict) -> bool:
    """True only if this state actually contains one of the UI's real
    success status messages. Missing/empty state => False (fail closed)."""
    if not isinstance(state, dict):
        return False
    for el in state.get("elements", []) or []:
        text = (el.get("text") or el.get("label") or "").strip()
        if text in _SUBMIT_STATUS_TEXTS:
            return True
    return False


def _load_decision_by_message_id(history_path: str) -> dict:
    """Load the recorded decision (reply/schedule/etc) for each message_id
    from routed_history.json. Format: {"messages": [{message_id, decision, ...}, ...]}.
    Returns: {message_id: decision_string, ...}"""
    decisions = {}
    if not os.path.exists(history_path):
        return decisions
    try:
        with open(history_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return decisions

    for entry in data.get("messages", []):
        mid = entry.get("message_id")
        if mid:
            decisions[mid] = entry.get("decision", "")
    return decisions


def _load_steps(session_dir: str) -> list:
    paths = sorted(glob.glob(os.path.join(session_dir, "live_step_*.json")))
    steps = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            steps.append(json.load(f))
    return steps


def translate_session(session_dir: str, gmail_client, reply_examples_path: str = DEFAULT_REPLY_EXAMPLES_PATH,
                       history_path: str = DEFAULT_HISTORY_PATH,
                       schedule_log_path: str = DEFAULT_SCHEDULE_LOG_PATH) -> int:
    """Reads every live_step_NNNN.json in session_dir, in order, and
    records one reply/schedule example per submitted step found. Returns
    how many were written. Routes each to schedule.txt or reply_examples.jsonl
    based on the decision recorded in routed_history.json. Never raises on an
    unresolvable message_id or a missing/empty session -- both are normal,
    loggable outcomes."""
    steps = _load_steps(session_dir)
    decisions_by_id = _load_decision_by_message_id(history_path)
    written = 0

    for i, step in enumerate(steps):
        state = step.get("state", {})
        textarea = _find_reply_textarea(state)
        if textarea is None:
            continue
        text = (textarea.get("value") or "").strip()
        if not text:
            continue
        # `name` FIRST: local_ui/app.js sets replyBody.name = email.message_id,
        # and WebObserver now exposes the DOM name= attribute on its own key.
        # label/text are the human-readable display label, which for this
        # textarea is its placeholder prose, not an id -- they stay only as a
        # fallback for older traces recorded before the `name` key existed.
        message_id = textarea.get("name") or textarea.get("label") or textarea.get("text") or ""
        if not message_id:
            continue

        # DemoRecorder captures a before/after pair for every action within
        # ONE step file -- next_state is "right after this exact action."
        # Record ONLY when that after-state actually shows the UI's real
        # success message. A keystroke shows none; Back shows none; Archive
        # shows a different one. See this module's docstring for why the old
        # "textarea disappeared" test was both wrong and fail-open.
        next_state = step.get("next_state", {}) or {}
        if not _has_submit_status(next_state):
            continue

        message = gmail_client.get_message(message_id)
        if message is None:
            print(f"  [skip] step {i}: message_id {message_id!r} did not resolve to a real message")
            continue

        decision = decisions_by_id.get(message_id, "")
        if decision == "schedule":
            record_schedule_entry(message, text, path=schedule_log_path)
            written += 1
            print(f"  [recorded] {message_id} ({decision}): {text[:60]!r}")
        elif decision in ("reply", "forward"):
            record_reply_example(message, text, source="live", path=reply_examples_path)
            written += 1
            print(f"  [recorded] {message_id} ({decision}): {text[:60]!r}")
        else:
            # No decision recorded or unknown decision type -- fail closed: no evidence => don't record
            print(f"  [skip] step {i}: message_id {message_id!r} has no valid decision recorded ({decision!r})")
            continue

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate a recorded DemoRecorder session into real reply examples or scheduled tasks.")
    parser.add_argument("--session-dir", required=True, help="Path to a session_<timestamp> folder")
    parser.add_argument("--examples-path", default=DEFAULT_REPLY_EXAMPLES_PATH, help="Path to reply_examples.jsonl")
    parser.add_argument("--history-path", default=DEFAULT_HISTORY_PATH, help="Path to routed_history.json")
    parser.add_argument("--schedule-log-path", default=DEFAULT_SCHEDULE_LOG_PATH, help="Path to schedule.txt")
    args = parser.parse_args()

    gmail_client = get_gmail_client()
    count = translate_session(args.session_dir, gmail_client, args.examples_path,
                              history_path=args.history_path, schedule_log_path=args.schedule_log_path)
    print(f"\n{count} real example(s) written (to reply_examples.jsonl and/or schedule.txt)")


if __name__ == "__main__":
    main()
