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
the next step (Confirm/Override closed the detail view) -- or it's
the last step in the session (Stop was pressed right after
submitting). This avoids matching a raw click position against a DOM
element's bounding box: pynput's click_pos is in absolute screen
coordinates, WebObserver's bbox is viewport-relative, and the two
aren't directly comparable without also knowing the browser window's
on-screen position -- a problem this state-transition check sidesteps
entirely.
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

        is_last_step = (i == len(steps) - 1)
        if not is_last_step:
            next_state = steps[i + 1].get("state", {})
            next_textarea = _find_reply_textarea(next_state)
            still_open = (
                next_textarea is not None
                and (next_textarea.get("label") or next_textarea.get("text")) == message_id
            )
            if still_open:
                continue  # detail view stayed open -- nothing submitted yet
        else:
            # Last step: check if previous step had same message_id textarea (continuation)
            if i > 0:
                prev_state = steps[i - 1].get("state", {})
                prev_textarea = _find_reply_textarea(prev_state)
                if (prev_textarea is not None and
                    (prev_textarea.get("label") or prev_textarea.get("text")) == message_id):
                    continue  # continuation from previous step, not a new submission

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
