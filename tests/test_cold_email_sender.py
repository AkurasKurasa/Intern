import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cold_email_sender import ColdEmailSender


class _FakeGmailClient:
    def __init__(self):
        self.drafts = []

    def create_draft(self, to, subject, body, thread_id=""):
        draft_id = f"fake-draft-{len(self.drafts) + 1}"
        self.drafts.append({"to": to, "subject": subject, "body": body, "thread_id": thread_id})
        return draft_id


def _write_task_list(tmp_path, content):
    path = tmp_path / "task_list.txt"
    path.write_text(content, encoding="utf-8")
    return str(path)


def _sender(tmp_path, task_list_content, gmail=None):
    task_list_path = _write_task_list(tmp_path, task_list_content)
    state_path = str(tmp_path / "cold_email_state.json")
    return ColdEmailSender(gmail or _FakeGmailClient(), task_list_path=task_list_path, state_path=state_path), state_path


_ONE_TARGET = "Cold email:\nDana Whitfield <dana@x.example.com>\n"
_TWO_TARGETS = "Cold email:\nDana Whitfield <dana@x.example.com>\nMarcus Oyelaran <marcus@x.example.com>\n"


def test_list_pending_targets_returns_everyone_not_yet_contacted(tmp_path):
    sender, _ = _sender(tmp_path, _TWO_TARGETS)
    pending = sender.list_pending_targets()
    assert [t.email for t in pending] == ["dana@x.example.com", "marcus@x.example.com"]


def test_list_pending_targets_excludes_already_contacted_emails(tmp_path):
    sender, state_path = _sender(tmp_path, _TWO_TARGETS)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"contacted_emails": ["dana@x.example.com"]}, f)
    pending = sender.list_pending_targets()
    assert [t.email for t in pending] == ["marcus@x.example.com"]


def test_send_cold_email_creates_a_real_draft_and_marks_contacted(tmp_path):
    gmail = _FakeGmailClient()
    sender, state_path = _sender(tmp_path, _ONE_TARGET, gmail=gmail)
    draft_id = sender.send_cold_email("dana@x.example.com", "Hi Dana", "Reaching out about a partnership.")
    assert draft_id == "fake-draft-1"
    assert gmail.drafts == [{"to": "dana@x.example.com", "subject": "Hi Dana",
                              "body": "Reaching out about a partnership.", "thread_id": ""}]
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    assert state["contacted_emails"] == ["dana@x.example.com"]
    assert sender.list_pending_targets() == []


def test_send_cold_email_with_blank_subject_is_a_no_op(tmp_path):
    gmail = _FakeGmailClient()
    sender, state_path = _sender(tmp_path, _ONE_TARGET, gmail=gmail)
    draft_id = sender.send_cold_email("dana@x.example.com", "   ", "A real message.")
    assert draft_id == ""
    assert gmail.drafts == []
    assert not os.path.exists(state_path)


def test_send_cold_email_with_blank_body_is_a_no_op(tmp_path):
    gmail = _FakeGmailClient()
    sender, state_path = _sender(tmp_path, _ONE_TARGET, gmail=gmail)
    draft_id = sender.send_cold_email("dana@x.example.com", "A real subject", "   ")
    assert draft_id == ""
    assert gmail.drafts == []
    assert not os.path.exists(state_path)


def test_send_cold_email_to_someone_not_on_the_task_list_is_a_no_op(tmp_path):
    gmail = _FakeGmailClient()
    sender, state_path = _sender(tmp_path, _ONE_TARGET, gmail=gmail)
    draft_id = sender.send_cold_email("nobody@not-on-the-list.example.com", "Hi", "A real message.")
    assert draft_id == ""
    assert gmail.drafts == []
    assert not os.path.exists(state_path)


def test_contacted_email_matching_is_case_insensitive(tmp_path):
    sender, state_path = _sender(tmp_path, _ONE_TARGET)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"contacted_emails": ["DANA@x.example.com"]}, f)
    assert sender.list_pending_targets() == []
