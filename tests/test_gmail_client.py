import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import MockGmailClient


def _write_fixture(data_dir, inbox=None):
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "mock_inbox.json"), "w", encoding="utf-8") as f:
        json.dump({"inbox": inbox or [], "sent": []}, f)


def _msg(mid):
    return {
        "id": mid, "thread_id": mid, "sender": "Someone <stranger@x.com>",
        "sender_email": "stranger@x.com", "subject": "subject", "snippet": "",
        "body_text": "body", "received_at": "2026-08-26T00:00:00Z",
    }


class TestApplyFlagLabel:
    def test_apply_flag_label_records_the_id_in_mock_state(self, tmp_path):
        data_dir = tmp_path / "data"
        _write_fixture(str(data_dir), inbox=[_msg("i1")])
        client = MockGmailClient(data_dir=str(data_dir))

        client.apply_flag_label("i1")

        state_path = data_dir / "mock_state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state.get("flagged_ids") == ["i1"]

    def test_apply_flag_label_is_idempotent(self, tmp_path):
        data_dir = tmp_path / "data"
        _write_fixture(str(data_dir), inbox=[_msg("i1")])
        client = MockGmailClient(data_dir=str(data_dir))

        client.apply_flag_label("i1")
        client.apply_flag_label("i1")

        state = json.loads((data_dir / "mock_state.json").read_text(encoding="utf-8"))
        assert state.get("flagged_ids") == ["i1"]

    def test_apply_flag_label_does_not_disturb_processed_ids(self, tmp_path):
        # flagged_ids and processed_ids are independent -- flagging a
        # message must not accidentally mark it processed (or vice versa),
        # since Flag is a real action on a message that's still pending.
        data_dir = tmp_path / "data"
        _write_fixture(str(data_dir), inbox=[_msg("i1"), _msg("i2")])
        client = MockGmailClient(data_dir=str(data_dir))
        client.mark_processed("i1")

        client.apply_flag_label("i2")

        state = json.loads((data_dir / "mock_state.json").read_text(encoding="utf-8"))
        assert state.get("processed_ids") == ["i1"]
        assert state.get("flagged_ids") == ["i2"]

    def test_apply_flag_label_multiple_messages_all_recorded(self, tmp_path):
        data_dir = tmp_path / "data"
        _write_fixture(str(data_dir), inbox=[_msg("i1"), _msg("i2")])
        client = MockGmailClient(data_dir=str(data_dir))

        client.apply_flag_label("i1")
        client.apply_flag_label("i2")

        state = json.loads((data_dir / "mock_state.json").read_text(encoding="utf-8"))
        assert state.get("flagged_ids") == ["i1", "i2"]
