"""Scope #3 practice data resets automatically on Launch.

Direct request 2026-10-05: reset "Automatically on Launch". Found before:
27 of 30 mock emails were already marked handled, so Play had almost
nothing to show, and contacted cold-email targets drop off the list.

Run:  python -m pytest tests/test_inbox_demo_reset.py -q
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import demo_reset  # noqa: E402
from cold_email_sender import ColdEmailSender  # noqa: E402
from gmail_client import MockGmailClient  # noqa: E402


def _write(p, obj):
    p.write_text(json.dumps(obj), encoding="utf-8")


def _used_up(tmp_path):
    """A data folder after a few demo runs."""
    inbox = [{"id": f"m{i}", "thread_id": f"t{i}", "sender": "X <x@e.com>", "sender_email": "x@e.com",
              "subject": f"s{i}", "snippet": "", "body_text": "", "received_at": "2026-10-01T09:00:00",
              "labels": []} for i in range(3)]
    _write(tmp_path / "mock_inbox.json", {"inbox": inbox, "sent": []})
    _write(tmp_path / "mock_state.json", {"processed_ids": ["m0", "m1"]})
    _write(tmp_path / "cold_email_state.json", {"contacted_emails": ["a@e.com"]})
    _write(tmp_path / "mock_drafts.json", {"drafts": [{"draft_id": "mock-draft-1"}]})
    _write(tmp_path / "mock_calendar_events.json", {"events": [{"event_id": "e1"}]})
    learned = {
        "pattern_profile.json": '{"senders": {"x@e.com": 3}}',
        "reply_examples.jsonl": '{"reply": "thanks"}\n',
        "training_examples.jsonl": '{"x": 1}\n',
        "schedule.txt": "[2026-10-01] call\n",
        "routed_history.json": '{"messages": [{"message_id": "m0", "status": "confirmed"}]}',
        "task_list.txt": "Cold email: Hi\nAnn <a@e.com>\n",
    }
    for name, text in learned.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return learned


def test_reset_restores_every_consumable_file(tmp_path):
    _used_up(tmp_path)
    result = demo_reset.reset_demo_data(str(tmp_path), now=datetime(2026, 10, 5, 12, 0, 0))
    assert sorted(result["reset"]) == sorted(demo_reset.CONSUMABLE)
    assert json.loads((tmp_path / "mock_state.json").read_text()) == {"processed_ids": []}
    assert json.loads((tmp_path / "cold_email_state.json").read_text()) == {"contacted_emails": []}
    assert json.loads((tmp_path / "mock_drafts.json").read_text()) == {"drafts": []}
    assert json.loads((tmp_path / "mock_calendar_events.json").read_text()) == {"events": []}


def test_reset_never_touches_what_intern_learned(tmp_path):
    learned = _used_up(tmp_path)
    demo_reset.reset_demo_data(str(tmp_path))
    for name, text in learned.items():
        assert (tmp_path / name).read_text(encoding="utf-8") == text, name
    assert set(learned) <= set(demo_reset.NEVER_RESET) | {"task_list.txt"}
    assert not set(demo_reset.CONSUMABLE) & set(demo_reset.NEVER_RESET)


def test_everything_reset_is_backed_up_first(tmp_path):
    _used_up(tmp_path)
    result = demo_reset.reset_demo_data(str(tmp_path), now=datetime(2026, 10, 5, 12, 0, 0))
    backup = Path(result["backup"])
    assert backup == tmp_path / "demo_backups" / "20261005-120000"
    assert json.loads((backup / "mock_state.json").read_text()) == {"processed_ids": ["m0", "m1"]}
    assert json.loads((backup / "cold_email_state.json").read_text()) == {"contacted_emails": ["a@e.com"]}


def test_missing_files_stay_missing_and_nothing_is_backed_up(tmp_path):
    assert demo_reset.reset_demo_data(str(tmp_path)) == {"backup": None, "reset": []}
    assert not (tmp_path / "demo_backups").exists()


def test_after_reset_every_email_waits_and_every_target_is_back(tmp_path):
    """Integration: the real mock clients read the reset files."""
    _used_up(tmp_path)
    gmail = MockGmailClient(str(tmp_path))
    assert len(gmail.list_inbox_unprocessed()) == 1
    demo_reset.reset_demo_data(str(tmp_path))
    assert len(MockGmailClient(str(tmp_path)).list_inbox_unprocessed()) == 3
    sender = ColdEmailSender(MockGmailClient(str(tmp_path)),
                             task_list_path=str(tmp_path / "task_list.txt"),
                             state_path=str(tmp_path / "cold_email_state.json"))
    assert [t.email for t in sender.list_pending_targets()] == ["a@e.com"]


def test_real_gmail_is_detected_so_launch_skips_the_reset(tmp_path):
    assert demo_reset.uses_real_gmail(str(tmp_path)) is False
    (tmp_path / "client_secret.json").write_text("{}", encoding="utf-8")
    assert demo_reset.uses_real_gmail(str(tmp_path)) is True


def test_launch_resets_before_opening_the_window_and_skips_with_real_gmail(monkeypatch):
    import open_workspace
    calls = []
    monkeypatch.setattr(open_workspace, "reset_demo_data", lambda: calls.append("reset") or {"reset": [], "backup": None})
    monkeypatch.setattr(open_workspace, "ensure_server_running", lambda: calls.append("server"))
    monkeypatch.setattr(open_workspace, "open_workspace", lambda url: calls.append("open") or ("launched", None))

    monkeypatch.setattr(open_workspace, "uses_real_gmail", lambda: False)
    open_workspace.main()
    assert calls == ["reset", "server", "open"]

    calls.clear()
    monkeypatch.setattr(open_workspace, "uses_real_gmail", lambda: True)
    open_workspace.main()
    assert calls == ["server", "open"]
