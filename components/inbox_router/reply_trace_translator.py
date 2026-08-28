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
non-empty AND that same message_id-labeled textarea is absent from
the step's own next_state (indicating the action closed the detail
view). DemoRecorder captures a before/after pair for every action
within a single step file: state is before, next_state is after for
that exact click or keystroke. The submitting click's own before/after
state pair, captured within that one step, already shows the textarea
gone in next_state — no special-casing for session position is needed.
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


def _find_reply_textarea(state: dict) -> Optional[dict]:
    for el in state.get("elements", []):
        if el.get("control_type") == "textarea":
            return el
    return None


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
        message_id = textarea.get("label") or textarea.get("text") or ""
        if not message_id:
            continue

        # DemoRecorder captures a before/after pair for every action within
        # ONE step file -- next_state is "right after this exact action."
        # If the same textarea is still open in next_state, this action
        # didn't submit anything (e.g. it was itself a keystroke, not the
        # Confirm/Override click) -- more typing, not a submission.
        next_state = step.get("next_state", {})
        next_textarea = _find_reply_textarea(next_state)
        still_open = (
            next_textarea is not None
            and (next_textarea.get("label") or next_textarea.get("text")) == message_id
        )
        if still_open:
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
