import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage
import reply_features as rf


def _msg(sender_email="alice@vendor.com", subject="Invoice question", body="Can you resend the invoice?"):
    return EmailMessage(
        id="m1", thread_id="t1", sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text=body, received_at="2026-08-27T00:00:00Z",
    )


class TestEmbedContext:
    def test_returns_correct_dims(self):
        vec = rf.embed_context("Hello", "Just checking in.")
        assert len(vec) == len(rf.embed_context("Anything", "Else"))

    def test_empty_text_returns_zero_vector(self):
        vec = rf.embed_context("", "")
        assert all(v == 0.0 for v in vec)


class TestPairFeatures:
    def test_returns_correct_length(self):
        msg = _msg()
        vec = rf.embed_context(msg.subject, msg.body_text)
        example = {"sender_email": "alice@vendor.com", "subject": "Invoice question"}
        result = rf.pair_features(msg, vec, example, vec)
        assert len(result) == rf.DIMS

    def test_identical_context_gives_high_cosine(self):
        msg = _msg()
        vec = rf.embed_context(msg.subject, msg.body_text)
        example = {"sender_email": "alice@vendor.com", "subject": msg.subject}
        result = rf.pair_features(msg, vec, example, vec)
        idx = rf.FEATURE_NAMES.index("context_cosine_sim")
        assert result[idx] > 0.99

    def test_same_sender_flag_set_when_senders_match(self):
        msg = _msg(sender_email="bob@vendor.com")
        vec = rf.embed_context(msg.subject, msg.body_text)
        example = {"sender_email": "bob@vendor.com", "subject": "unrelated"}
        result = rf.pair_features(msg, vec, example, vec)
        idx = rf.FEATURE_NAMES.index("same_sender")
        assert result[idx] == 1.0

    def test_same_sender_flag_zero_when_senders_differ(self):
        msg = _msg(sender_email="bob@vendor.com")
        vec = rf.embed_context(msg.subject, msg.body_text)
        example = {"sender_email": "carol@other.com", "subject": "unrelated"}
        result = rf.pair_features(msg, vec, example, vec)
        idx = rf.FEATURE_NAMES.index("same_sender")
        assert result[idx] == 0.0

    def test_subject_overlap_is_one_for_identical_subjects(self):
        msg = _msg(subject="please resend the invoice")
        vec = rf.embed_context(msg.subject, msg.body_text)
        example = {"sender_email": "x@y.com", "subject": "please resend the invoice"}
        result = rf.pair_features(msg, vec, example, vec)
        idx = rf.FEATURE_NAMES.index("subject_overlap")
        assert result[idx] == 1.0

    def test_subject_overlap_is_zero_for_disjoint_subjects(self):
        msg = _msg(subject="invoice question")
        vec = rf.embed_context(msg.subject, msg.body_text)
        example = {"sender_email": "x@y.com", "subject": "totally different topic here"}
        result = rf.pair_features(msg, vec, example, vec)
        idx = rf.FEATURE_NAMES.index("subject_overlap")
        assert result[idx] == 0.0

    def test_missing_subjects_dont_crash(self):
        msg = _msg(subject="")
        vec = rf.embed_context(msg.subject, msg.body_text)
        example = {"sender_email": "x@y.com"}
        result = rf.pair_features(msg, vec, example, vec)
        assert len(result) == rf.DIMS
