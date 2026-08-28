import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components" / "inbox_router"))

from gmail_client import EmailMessage, MockGmailClient
import reply_recorder as rr
import reply_trace_translator as translator


def _step(step_idx, textarea_value="", message_id="", include_textarea=True):
    elements = []
    if include_textarea:
        elements.append({
            "element_id": "web_0", "type": "input", "control_type": "textarea",
            "text": message_id, "value": textarea_value, "label": message_id,
            "enabled": True, "visible": True, "source": "web",
        })
    return {
        "trace_id": f"live_step_{step_idx:04d}", "timestamp": "2026-08-28T00:00:00",
        "duration": 1.0, "type": "web",
        "state": {"application": "browser", "elements": elements},
        "mouse": {}, "keyboard": {},
        "next_state": {"application": "browser", "elements": elements},
    }


def _write_session(tmp_path, steps):
    session_dir = tmp_path / "session_20260828_000000"
    session_dir.mkdir()
    for i, step in enumerate(steps):
        (session_dir / f"live_step_{i:04d}.json").write_text(json.dumps(step), encoding="utf-8")
    return str(session_dir)


def _build_gmail_client(tmp_path, inbox):
    data_dir = tmp_path / "data"
    os.makedirs(data_dir, exist_ok=True)
    (data_dir / "mock_inbox.json").write_text(json.dumps({"inbox": inbox, "sent": []}), encoding="utf-8")
    return MockGmailClient(data_dir=str(data_dir))


def _msg(mid, sender_email, subject, body="body text"):
    return {
        "id": mid, "thread_id": mid, "sender": f"Someone <{sender_email}>", "sender_email": sender_email,
        "subject": subject, "snippet": "", "body_text": body, "received_at": "2026-08-27T00:00:00Z",
        "labels": ["INBOX"],
    }


class TestTranslateSession:
    def test_submitted_reply_is_recorded(self, tmp_path):
        # Step 0: textarea has real text for message m1.
        # Step 1: that textarea is gone -- Confirm/Override closed the detail view.
        steps = [
            _step(0, textarea_value="Thanks, that works for me.", message_id="m1"),
            _step(1, include_textarea=False),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 1
        examples = rr.load_reply_examples(examples_path)
        assert len(examples) == 1
        assert examples[0]["message_id"] == "m1"
        assert examples[0]["reply_body"] == "Thanks, that works for me."
        assert examples[0]["source"] == "live"

    def test_last_step_in_session_with_text_still_counts(self, tmp_path):
        # No "next" step at all -- Stop was pressed right after submitting.
        steps = [_step(0, textarea_value="Sure, call me in 10.", message_id="m1")]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "quick question")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 1

    def test_textarea_still_present_in_next_step_is_not_counted(self, tmp_path):
        # Detail view stayed open -- nothing was submitted yet.
        steps = [
            _step(0, textarea_value="typing...", message_id="m1"),
            _step(1, textarea_value="typing... more", message_id="m1"),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 0
        assert rr.load_reply_examples(examples_path) == []

    def test_empty_textarea_produces_no_example(self, tmp_path):
        steps = [
            _step(0, textarea_value="", message_id="m1"),
            _step(1, include_textarea=False),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 0

    def test_unresolvable_message_id_is_skipped_without_raising(self, tmp_path):
        steps = [
            _step(0, textarea_value="Thanks!", message_id="does-not-exist"),
            _step(1, include_textarea=False),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[])  # empty inbox -- nothing resolves
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)  # must not raise

        assert count == 0

    def test_no_step_files_returns_zero(self, tmp_path):
        session_dir = tmp_path / "empty_session"
        session_dir.mkdir()
        gmail_client = _build_gmail_client(tmp_path, inbox=[])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(str(session_dir), gmail_client, examples_path)

        assert count == 0
