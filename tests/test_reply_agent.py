import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage
import reply_agent as ra
import reply_recorder as rr
import train_reply_model as trainer


def _msg(mid="new1", sender_email="boss@work.com", subject="status update", body="Where do things stand?"):
    return EmailMessage(
        id=mid, thread_id="t1", sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text=body, received_at="2026-08-27T00:00:00Z",
    )


def _seed_examples(path):
    examples = [
        ("m1", "boss@work.com", "status update", "Here's where things stand.",
         "Thanks, that all looks on track."),
        ("m2", "boss@work.com", "quick question", "Do you have a minute?",
         "Sure, call me in 10."),
        ("m3", "boss@work.com", "fyi", "Thought you'd want to see this.",
         "Got it, thanks for flagging."),
        ("m4", "vendor@acme.com", "invoice #221", "Can you confirm receipt?",
         "Confirmed, invoice #221 received."),
        ("m5", "vendor@acme.com", "invoice #222", "Can you confirm receipt?",
         "Confirmed, invoice #222 received."),
        ("m6", "team@work.com", "fwd: doc", "Passing this along to you.",
         "Thanks for forwarding, will review today."),
    ]
    for mid, sender, subject, body, reply in examples:
        rr.record_reply_example(EmailMessage(
            id=mid, thread_id="", sender=sender, sender_email=sender,
            subject=subject, snippet="", body_text=body, received_at="2026-08-27T00:00:00Z",
        ), reply, source="live", path=path)
    return len(examples)


def _build_checkpoint(tmp_path):
    examples_path = str(tmp_path / "reply_examples.jsonl")
    _seed_examples(examples_path)
    save_path = str(tmp_path / "reply_model.pt")
    trainer.train(examples_path, save_path, epochs=300, lr=5e-2)
    return examples_path, save_path


class TestColdStart:
    def test_no_checkpoint_returns_empty_suggestion(self, tmp_path):
        agent = ra.ReplyAgent(examples_path=str(tmp_path / "no_examples.jsonl"),
                               checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"))
        result = agent.suggest_reply(_msg())
        assert result.reply_body == ""
        assert result.confidence == 0.0

    def test_no_recorded_examples_returns_empty_even_with_checkpoint(self, tmp_path):
        # A checkpoint could exist from a prior training run while the
        # examples file has since been cleared -- must still never invent
        # anything to suggest.
        examples_path, save_path = _build_checkpoint(tmp_path)
        agent = ra.ReplyAgent(examples_path=str(tmp_path / "empty.jsonl"), checkpoint_path=save_path)
        result = agent.suggest_reply(_msg())
        assert result.reply_body == ""
        assert result.confidence == 0.0

    def test_corrupt_checkpoint_falls_back_to_empty(self, tmp_path):
        checkpoint_path = str(tmp_path / "corrupt.pt")
        (tmp_path / "corrupt.pt").write_bytes(b"not a real checkpoint")
        examples_path = str(tmp_path / "reply_examples.jsonl")
        _seed_examples(examples_path)
        agent = ra.ReplyAgent(examples_path=examples_path, checkpoint_path=checkpoint_path)
        result = agent.suggest_reply(_msg())
        assert result.reply_body == ""
        assert result.confidence == 0.0


class TestSuggestReply:
    def test_confident_match_returns_real_past_reply_text(self, tmp_path):
        examples_path, save_path = _build_checkpoint(tmp_path)
        agent = ra.ReplyAgent(examples_path=examples_path, checkpoint_path=save_path, high_confidence=0.0)
        result = agent.suggest_reply(_msg(sender_email="boss@work.com", subject="status update",
                                           body="Here's where things stand."))
        # Text must be verbatim one of the real recorded replies -- never
        # anything generated or altered.
        recorded = {ex["reply_body"] for ex in rr.load_reply_examples(examples_path)}
        assert result.reply_body in recorded
        assert result.reply_body != ""

    def test_high_threshold_withholds_low_confidence_match(self, tmp_path):
        examples_path, save_path = _build_checkpoint(tmp_path)
        agent = ra.ReplyAgent(examples_path=examples_path, checkpoint_path=save_path, high_confidence=0.999999)
        result = agent.suggest_reply(_msg(sender_email="stranger@nowhere.com", subject="totally unrelated topic",
                                           body="Nothing like anything recorded."))
        assert result.reply_body == ""

    def test_confidence_is_reported_even_when_below_threshold(self, tmp_path):
        examples_path, save_path = _build_checkpoint(tmp_path)
        agent = ra.ReplyAgent(examples_path=examples_path, checkpoint_path=save_path, high_confidence=0.999999)
        result = agent.suggest_reply(_msg())
        assert 0.0 <= result.confidence <= 1.0
