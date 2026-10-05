"""Scope #3 keeps everything in one Notepad file: task_list.txt.

Direct request 2026-10-05: "Can you please unify both into one" / "Put it all
in one txt file please". Before, the boss' instructions lived in
task_list.txt and scheduled items in schedule.txt, and Launch opened only
schedule.txt. Now schedule entries go under a "Scheduled:" section at the end
of the task list, and Launch opens that one file.

Run:  python -m pytest tests/test_inbox_unified_notepad.py -q
"""
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import schedule_recorder  # noqa: E402
from gmail_client import EmailMessage  # noqa: E402
from task_list_parser import (  # noqa: E402
    DEFAULT_TASK_LIST_PATH, parse_check_tasks, parse_cold_email_targets,
)

TASKS = """Cold email: Intro
Ann Lee <ann@a.example.com>

Check replies:
Ann Lee <ann@a.example.com>
"""


def _msg(sender="Bo Kim <bo@b.example.com>", email="bo@b.example.com", subject="Call Tuesday?"):
    return EmailMessage(id="m1", thread_id="t1", sender=sender, sender_email=email,
                        subject=subject, snippet="", body_text="", received_at="2026-10-01T09:00:00")


def test_schedule_entries_go_to_the_task_list_by_default():
    assert Path(schedule_recorder.DEFAULT_SCHEDULE_LOG_PATH) == Path(DEFAULT_TASK_LIST_PATH)


def test_first_entry_adds_one_scheduled_section_after_the_instructions(tmp_path):
    path = tmp_path / "task_list.txt"
    path.write_text(TASKS, encoding="utf-8")
    schedule_recorder.record_schedule_entry(_msg(), "Call Tuesday at 2pm", path=str(path))
    schedule_recorder.record_schedule_entry(_msg(subject="Retro"), "Retro Friday 3pm", path=str(path))
    text = path.read_text(encoding="utf-8")
    assert text.startswith(TASKS)
    assert text.count("Scheduled:") == 1
    tail = text[len(TASKS):].splitlines()
    assert tail[0] == "" and tail[1] == "Scheduled:"
    assert "Call Tuesday at 2pm" in tail[2] and "Retro Friday 3pm" in tail[3]


def test_scheduled_entries_are_never_read_as_targets_or_checks(tmp_path):
    """An entry can even contain 'Name <email>' -- it must stay inert."""
    path = tmp_path / "task_list.txt"
    path.write_text(TASKS, encoding="utf-8")
    schedule_recorder.record_schedule_entry(
        _msg(), "Meet Eve Park <eve@e.example.com>", path=str(path))
    assert [t.email for t in parse_cold_email_targets(str(path))] == ["ann@a.example.com"]
    assert [t.query for t in parse_check_tasks(str(path))] == ["ann@a.example.com"]


def test_blank_note_still_writes_nothing(tmp_path):
    path = tmp_path / "task_list.txt"
    path.write_text(TASKS, encoding="utf-8")
    schedule_recorder.record_schedule_entry(_msg(), "   ", path=str(path))
    assert path.read_text(encoding="utf-8") == TASKS


def test_file_without_a_trailing_newline_gets_a_clean_section(tmp_path):
    path = tmp_path / "task_list.txt"
    path.write_text(TASKS.rstrip("\n"), encoding="utf-8")
    schedule_recorder.record_schedule_entry(_msg(), "Call", path=str(path))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[-3:-1] == ["", "Scheduled:"]


def test_the_real_task_list_still_parses_the_same_with_its_scheduled_section():
    text = Path(DEFAULT_TASK_LIST_PATH).read_text(encoding="utf-8")
    assert "Scheduled:" in text
    targets = parse_cold_email_targets()
    assert len(targets) == 10 and len({t.context_line for t in targets}) == 3
    assert len(parse_check_tasks()) == 12


def test_launch_opens_only_the_one_file():
    src = Path(_ROOT, "app_electron", "main.js").read_text(encoding="utf-8")
    block = src[src.index('ipcMain.handle("launch-test-tools"'):]
    block = block[:block.index("const targets = TEST_MOCKUPS")]
    assert '"task_list.txt"' in block
    assert '"schedule.txt"' not in block
