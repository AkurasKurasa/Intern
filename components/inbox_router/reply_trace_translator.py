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


# The two literal strings local_ui/app.js shows in its #snackbar
# (role="status") when a decision is really submitted -- confirmCurrent()
# shows "Confirmed.", overrideCurrent() shows "Overridden.". Archive's own
# message ("Archived.") is deliberately NOT here: archiving is not a reply.
_SUBMIT_STATUS_TEXTS = {"Confirmed.", "Overridden."}


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


def _load_steps(session_dir: str) -> list:
    paths = sorted(glob.glob(os.path.join(session_dir, "live_step_*.json")))
    steps = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            steps.append(json.load(f))
    return steps


def translate_session(session_dir: str, gmail_client, reply_examples_path: str = DEFAULT_REPLY_EXAMPLES_PATH) -> int:
    """Reads every live_step_NNNN.json in session_dir, in order, and
    records one reply example per submitted-reply step found. Returns
    how many were written. Never raises on an unresolvable message_id
    or a missing/empty session -- both are normal, loggable outcomes."""
    steps = _load_steps(session_dir)
    written = 0

    for i, step in enumerate(steps):
        state = step.get("state", {})
        textarea = _find_reply_textarea(state)
        if textarea is None:
            continue
        reply_body = (textarea.get("value") or "").strip()
        if not reply_body:
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

        record_reply_example(message, reply_body, source="live", path=reply_examples_path)
        written += 1
        print(f"  [recorded] {message_id}: {reply_body[:60]!r}")

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate a recorded DemoRecorder session into real reply examples.")
    parser.add_argument("--session-dir", required=True, help="Path to a session_<timestamp> folder")
    parser.add_argument("--examples-path", default=DEFAULT_REPLY_EXAMPLES_PATH)
    args = parser.parse_args()

    gmail_client = get_gmail_client()
    count = translate_session(args.session_dir, gmail_client, args.examples_path)
    print(f"\n{count} real reply example(s) written to {args.examples_path}")


if __name__ == "__main__":
    main()
