import json
import os
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from calendar_client import MockCalendarClient, _to_rfc3339


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
    def test_returns_mock_when_no_credentials_file(self, tmp_path, monkeypatch):
        # get_calendar_client()'s `root` parameter is ignored (matches
        # get_gmail_client()'s own real behavior) -- the function actually
        # always checks the real components/inbox_router/credentials/
        # client_secret.json path. Passing root=str(tmp_path) used to only
        # pass because that real directory happened to be empty on this
        # developer's machine; monkeypatching the module's actual
        # DEFAULT_CREDENTIALS_DIR constant makes this deterministic instead,
        # so it keeps passing (mock, no browser OAuth flow) once real
        # credentials are set up for this project.
        from calendar_client import get_calendar_client, MockCalendarClient
        import calendar_client as calendar_client_module
        monkeypatch.setattr(calendar_client_module, "DEFAULT_CREDENTIALS_DIR", str(tmp_path))
        client = get_calendar_client()
        assert isinstance(client, MockCalendarClient)


class TestToRfc3339:
    """_to_rfc3339() is the fix for the datetime-local -> RFC3339 gap:
    <input type="datetime-local"> hands the UI a naive, second-less string
    (e.g. "2026-09-03T14:00") that the real Calendar API rejects outright.
    These tests exercise the standalone function directly -- no need to
    construct a RealCalendarClient, which requires real OAuth credentials."""

    def test_browser_datetime_local_string_gains_seconds_and_offset(self):
        result = _to_rfc3339("2026-09-03T14:00")
        assert "T14:00:00" in result
        reparsed = datetime.fromisoformat(result)
        assert reparsed.tzinfo is not None

    def test_already_offset_aware_string_still_parses(self):
        # Backward compatibility: the old fabricated test format used
        # elsewhere in this codebase (e.g. test_calendar_client.py's own
        # MockCalendarClient tests above) already includes a real offset.
        # _to_rfc3339() must not choke on it.
        result = _to_rfc3339("2026-09-03T14:00:00-07:00")
        reparsed = datetime.fromisoformat(result)
        assert reparsed.tzinfo is not None
