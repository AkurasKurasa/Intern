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
import decision_recorder as rec


def _msg():
    return EmailMessage(
        id="m1", thread_id="t1", sender="Alice <alice@vendor.com>", sender_email="alice@vendor.com",
        subject="Hello", snippet="", body_text="Just checking in.", received_at="2026-08-25T00:00:00Z",
    )


class TestRecordExample:
    def test_appends_correct_shape(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        rec.record_example(_msg(), "reply", source="live", path=path)
        lines = open(path, encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["message_id"] == "m1"
        assert row["subject"] == "Hello"
        assert row["sender_email"] == "alice@vendor.com"
        assert row["body_text"] == "Just checking in."
        assert row["decision"] == "reply"
        assert row["source"] == "live"
        assert "recorded_at" in row

    def test_appends_multiple_lines(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        rec.record_example(_msg(), "reply", source="live", path=path)
        rec.record_example(_msg(), "forward", source="bootstrap", path=path)
        lines = open(path, encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 2

    def test_invalid_source_raises(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        with pytest.raises(ValueError):
            rec.record_example(_msg(), "reply", source="bogus", path=path)

    def test_creates_parent_directory(self, tmp_path):
        path = str(tmp_path / "nested" / "dir" / "examples.jsonl")
        rec.record_example(_msg(), "reply", source="live", path=path)
        assert os.path.exists(path)


class TestLoadExamples:
    def test_missing_file_returns_empty_list(self, tmp_path):
        assert rec.load_examples(str(tmp_path / "nope.jsonl")) == []

    def test_reads_back_recorded_examples(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        rec.record_example(_msg(), "reply", source="live", path=path)
        rec.record_example(_msg(), "forward", source="bootstrap", path=path)
        examples = rec.load_examples(path)
        assert len(examples) == 2
        assert examples[0]["decision"] == "reply"
        assert examples[1]["decision"] == "forward"
