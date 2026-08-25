import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage
import bootstrap_from_sent as boot
import decision_recorder as rec


def _inbox_msg(mid, thread_id, sender_email):
    return EmailMessage(
        id=mid, thread_id=thread_id, sender=sender_email, sender_email=sender_email,
        subject="Original", snippet="", body_text="Original body.",
        received_at="2026-08-20T00:00:00Z",
    )


def _sent_msg(thread_id, to, body="Thanks, got it.", ):
    return EmailMessage(
        id="s-" + thread_id, thread_id=thread_id, sender="me@company.com",
        sender_email="me@company.com", subject="Re: Original", snippet="",
        body_text=body, received_at="2026-08-21T00:00:00Z", to=to,
    )


class TestBootstrapExamples:
    def test_reply_correlated_by_thread(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        inbox = [_inbox_msg("i1", "t1", "alice@vendor.com")]
        sent = [_sent_msg("t1", to="alice@vendor.com")]
        count = boot.bootstrap_examples(sent, inbox, path=path)
        assert count == 1
        examples = rec.load_examples(path)
        assert examples[0]["decision"] == "reply"

    def test_forward_detected_by_marker(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        inbox = [_inbox_msg("i1", "t1", "alice@vendor.com")]
        sent = [_sent_msg("t1", to="bob@other.com", body="---- Forwarded message ----\nSee below.")]
        count = boot.bootstrap_examples(sent, inbox, path=path)
        assert count == 1
        examples = rec.load_examples(path)
        assert examples[0]["decision"] == "forward"

    def test_forward_detected_by_third_party_recipient(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        inbox = [_inbox_msg("i1", "t1", "alice@vendor.com")]
        sent = [_sent_msg("t1", to="bob@other.com", body="Passing this along.")]
        count = boot.bootstrap_examples(sent, inbox, path=path)
        examples = rec.load_examples(path)
        assert examples[0]["decision"] == "forward"

    def test_sent_with_no_matching_thread_produces_nothing(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        inbox = [_inbox_msg("i1", "t1", "alice@vendor.com")]
        sent = [_sent_msg("t999", to="alice@vendor.com")]
        count = boot.bootstrap_examples(sent, inbox, path=path)
        assert count == 0
        assert rec.load_examples(path) == []

    def test_multiple_correlated_threads(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        inbox = [_inbox_msg("i1", "t1", "alice@vendor.com"), _inbox_msg("i2", "t2", "carol@vendor.com")]
        sent = [_sent_msg("t1", to="alice@vendor.com"), _sent_msg("t2", to="carol@vendor.com")]
        count = boot.bootstrap_examples(sent, inbox, path=path)
        assert count == 2
