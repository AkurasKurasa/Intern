import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "components", "inbox_router"))

from gmail_client import EmailMessage
import schedule_recorder as sr


def _msg(mid="m1", sender_email="boss@work.com", subject="vendor call", body="Can we set up a call?"):
    return EmailMessage(
        id=mid, thread_id=mid, sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text=body, received_at="2026-08-28T00:00:00Z",
    )


class TestRecordScheduleEntry:
    def test_writes_a_real_line(self, tmp_path):
        path = str(tmp_path / "schedule.txt")
        sr.record_schedule_entry(_msg(), "Aug 30 -- vendor call re: pricing", path=path)

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Aug 30 -- vendor call re: pricing" in content

    def test_blank_note_saves_nothing(self, tmp_path):
        path = str(tmp_path / "schedule.txt")
        sr.record_schedule_entry(_msg(), "   ", path=path)

        assert not os.path.exists(path)

    def test_creates_parent_directory(self, tmp_path):
        path = str(tmp_path / "nested" / "dir" / "schedule.txt")
        sr.record_schedule_entry(_msg(), "real note", path=path)

        assert os.path.exists(path)

    def test_appends_multiple_entries(self, tmp_path):
        path = str(tmp_path / "schedule.txt")
        sr.record_schedule_entry(_msg(mid="m1"), "first note", path=path)
        sr.record_schedule_entry(_msg(mid="m2"), "second note", path=path)

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "first note" in content
        assert "second note" in content

    def test_default_path_is_under_data_dir(self):
        assert "data" in sr.DEFAULT_SCHEDULE_LOG_PATH
        assert sr.DEFAULT_SCHEDULE_LOG_PATH.endswith("schedule.txt")
