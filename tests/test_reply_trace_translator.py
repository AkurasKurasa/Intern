import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage, MockGmailClient
import reply_recorder as rr
import reply_trace_translator as translator


def _step(step_idx, textarea_value="", message_id="",
          include_textarea_in_state=True, include_textarea_in_next_state=None):
    """Build a step with state and next_state that can differ.

    If include_textarea_in_next_state is None, defaults to include_textarea_in_state
    (i.e., state and next_state are the same). Set explicitly to test transitions.
    """
    if include_textarea_in_next_state is None:
        include_textarea_in_next_state = include_textarea_in_state

    def _build_elements(include_textarea):
        elements = []
        if include_textarea:
            elements.append({
                "element_id": "web_0", "type": "input", "control_type": "textarea",
                "text": message_id, "value": textarea_value, "label": message_id,
                "enabled": True, "visible": True, "source": "web",
            })
        return elements

    return {
        "trace_id": f"live_step_{step_idx:04d}", "timestamp": "2026-08-28T00:00:00",
        "duration": 1.0, "type": "web",
        "state": {"application": "browser", "elements": _build_elements(include_textarea_in_state)},
        "mouse": {}, "keyboard": {},
        "next_state": {"application": "browser", "elements": _build_elements(include_textarea_in_next_state)},
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
        # One step: state has textarea with real text, next_state has no textarea.
        # The Confirm/Override action closed the detail view within this step's
        # before/after pair.
        steps = [
            _step(0, textarea_value="Thanks, that works for me.", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=False),
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

    def test_single_step_session_with_submitted_reply(self, tmp_path):
        # Single-file session where the action submits a reply: state has textarea,
        # next_state has no textarea. Confirms the algorithm works with just one
        # step file (no special "last step" logic needed).
        steps = [
            _step(0, textarea_value="Sure, call me in 10.", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=False),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "quick question")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 1

    def test_textarea_still_open_in_next_state_is_not_counted(self, tmp_path):
        # One step: state has textarea, next_state still has the same textarea
        # (value may have changed, but the detail view stayed open).
        # This action was a keystroke, not a submit.
        steps = [
            _step(0, textarea_value="typing...", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=True),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 0
        assert rr.load_reply_examples(examples_path) == []

    def test_empty_textarea_produces_no_example(self, tmp_path):
        # Even though next_state has no textarea (would normally indicate submission),
        # an empty reply_body is skipped (nothing real to save).
        steps = [
            _step(0, textarea_value="", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=False),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 0

    def test_unresolvable_message_id_is_skipped_without_raising(self, tmp_path):
        # A submitted reply (state has textarea, next_state doesn't) but the
        # message_id does not resolve in the inbox -- skipped silently with logging.
        steps = [
            _step(0, textarea_value="Thanks!", message_id="does-not-exist",
                  include_textarea_in_state=True, include_textarea_in_next_state=False),
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
