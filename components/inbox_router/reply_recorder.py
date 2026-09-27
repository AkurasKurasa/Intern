"""
components/inbox_router/reply_recorder.py
=============================================
Saves the ACTUAL text a human typed as a reply -- never anything an AI
wrote. This is the one and only place a real reply example gets saved;
it's the raw material a real trained model will later learn from, the
same way training_examples.jsonl is the raw material InboxDecisionNet
learns from. No LLM, no engine, no generated text ever passes through
here -- callers are responsible for only ever giving this real, human-
typed text.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPLY_EXAMPLES_PATH = os.path.join(_THIS_DIR, "data", "reply_examples.jsonl")

VALID_SOURCES = {"live", "bootstrap"}


def record_reply_example(message, reply_body: str, source: str,
                          path: str = DEFAULT_REPLY_EXAMPLES_PATH) -> None:
    """Saves one real (original email, the reply a human actually wrote)
    pair. Silently does nothing if reply_body is blank -- there is
    nothing real to save, and this must never invent a placeholder."""
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {VALID_SOURCES}, got {source!r}")
    if not reply_body or not reply_body.strip():
        return
    row = {
        "message_id": message.id,
        "subject": message.subject,
        "sender_email": message.sender_email,
        "body_text": message.body_text,
        "reply_body": reply_body,
        "source": source,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def load_reply_examples(path: str = DEFAULT_REPLY_EXAMPLES_PATH) -> List[dict]:
    if not os.path.exists(path):
        return []
    examples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    return examples
