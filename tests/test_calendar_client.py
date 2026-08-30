import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from calendar_client import MockCalendarClient


class TestMockCalendarClient:
    def test_create_event_writes_to_mock_file(self, tmp_path):
        client = MockCalendarClient(data_dir=str(tmp_path))
        event_id = client.create_event(
            summary="Vendor call", description="Discuss Q3 proposal",
            start_iso="2026-09-03T14:00:00-07:00", end_iso="2026-09-03T14:30:00-07:00")

        assert event_id
        events_path = tmp_path / "mock_calendar_events.json"
        assert events_path.exists()
        data = json.loads(events_path.read_text(encoding="utf-8"))
        assert len(data["events"]) == 1
        assert data["events"][0]["summary"] == "Vendor call"
        assert data["events"][0]["description"] == "Discuss Q3 proposal"
        assert data["events"][0]["start"] == "2026-09-03T14:00:00-07:00"
        assert data["events"][0]["end"] == "2026-09-03T14:30:00-07:00"
        assert data["events"][0]["event_id"] == event_id

    def test_create_event_multiple_events_all_recorded_with_unique_ids(self, tmp_path):
        client = MockCalendarClient(data_dir=str(tmp_path))
        id1 = client.create_event("First", "d1", "2026-09-01T10:00:00-07:00", "2026-09-01T10:30:00-07:00")
        id2 = client.create_event("Second", "d2", "2026-09-02T10:00:00-07:00", "2026-09-02T10:30:00-07:00")

        assert id1 != id2
        data = json.loads((tmp_path / "mock_calendar_events.json").read_text(encoding="utf-8"))
        assert len(data["events"]) == 2
        assert {e["summary"] for e in data["events"]} == {"First", "Second"}

    def test_create_event_creates_data_dir_if_missing(self, tmp_path):
        data_dir = tmp_path / "nested" / "data"
        client = MockCalendarClient(data_dir=str(data_dir))
        client.create_event("Test", "d", "2026-09-01T10:00:00-07:00", "2026-09-01T10:30:00-07:00")

        assert (data_dir / "mock_calendar_events.json").exists()


class TestGetCalendarClient:
    def test_returns_mock_when_no_credentials_file(self, tmp_path):
        from calendar_client import get_calendar_client, MockCalendarClient
        # No client_secret.json anywhere under tmp_path -- must fall back to mock.
        client = get_calendar_client(root=str(tmp_path))
        assert isinstance(client, MockCalendarClient)
