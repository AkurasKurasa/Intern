"""
components/inbox_router/decision_recorder.py
================================================
Record step of Scope #3's Record -> Train -> Output pipeline. Every real
Confirm/Override in the Inbox Dispatch mockup (via router.py's
InboxRouter) and every bootstrap example from Sent-folder history (via
bootstrap_from_sent.py) becomes one labeled example appended here -- the
Scope #3 analog of a demo trace file: dumb, append-only, replayable, the
single source of truth train_inbox_agent.py reads from.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from gmail_client import EmailMessage
from inbox_features import DECISIONS_ORDER

DEFAULT_EXAMPLES_PATH = os.path.join(_THIS_DIR, "data", "training_examples.jsonl")

VALID_SOURCES = {"live", "bootstrap"}


def record_example(message: EmailMessage, decision: str, source: str,
                    path: str = DEFAULT_EXAMPLES_PATH) -> None:
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {VALID_SOURCES}, got {source!r}")
    if decision not in DECISIONS_ORDER:
        raise ValueError(f"decision must be one of {DECISIONS_ORDER}, got {decision!r}")
    row = {
        "message_id": message.id,
        "subject": message.subject,
        "sender_email": message.sender_email,
        "body_text": message.body_text,
        "decision": decision,
        "source": source,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def load_examples(path: str = DEFAULT_EXAMPLES_PATH) -> List[dict]:
    if not os.path.exists(path):
        return []
    examples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    return examples
