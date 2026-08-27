import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage
import reply_recorder as rec


def _msg():
    return EmailMessage(
        id="m1", thread_id="t1", sender="Alice <alice@vendor.com>", sender_email="alice@vendor.com",
        subject="Hello", snippet="", body_text="Just checking in.", received_at="2026-08-25T00:00:00Z",
    )


class TestRecordReplyExample:
    def test_appends_correct_shape(self, tmp_path):
        path = str(tmp_path / "reply_examples.jsonl")
        rec.record_reply_example(_msg(), "Sounds good, I'll send it over Friday.", source="live", path=path)
        lines = open(path, encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["message_id"] == "m1"
        assert row["subject"] == "Hello"
        assert row["sender_email"] == "alice@vendor.com"
        assert row["body_text"] == "Just checking in."
        assert row["reply_body"] == "Sounds good, I'll send it over Friday."
        assert row["source"] == "live"
        assert "recorded_at" in row

    def test_blank_reply_body_saves_nothing(self, tmp_path):
        # The load-bearing property: never invent a placeholder example
        # for a reply that was never actually written.
        path = str(tmp_path / "reply_examples.jsonl")
        rec.record_reply_example(_msg(), "", source="live", path=path)
        rec.record_reply_example(_msg(), "   ", source="live", path=path)
        assert not os.path.exists(path)

    def test_invalid_source_raises(self, tmp_path):
        path = str(tmp_path / "reply_examples.jsonl")
        with pytest.raises(ValueError):
            rec.record_reply_example(_msg(), "Thanks!", source="bogus", path=path)

    def test_creates_parent_directory(self, tmp_path):
        path = str(tmp_path / "nested" / "dir" / "reply_examples.jsonl")
        rec.record_reply_example(_msg(), "Thanks!", source="live", path=path)
        assert os.path.exists(path)


class TestLoadReplyExamples:
    def test_missing_file_returns_empty_list(self, tmp_path):
        assert rec.load_reply_examples(str(tmp_path / "nope.jsonl")) == []

    def test_reads_back_recorded_examples(self, tmp_path):
        path = str(tmp_path / "reply_examples.jsonl")
        rec.record_reply_example(_msg(), "First reply.", source="live", path=path)
        rec.record_reply_example(_msg(), "Second reply.", source="bootstrap", path=path)
        examples = rec.load_reply_examples(path)
        assert len(examples) == 2
        assert examples[0]["reply_body"] == "First reply."
        assert examples[1]["reply_body"] == "Second reply."
