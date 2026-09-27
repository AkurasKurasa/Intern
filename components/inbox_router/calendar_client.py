"""
components/inbox_router/calendar_client.py
==============================================
The Schedule decision's real output: a real Google Calendar event, not just
a line in a text file. Mirrors gmail_client.py's own Phase A (mock, no
credentials) / Phase B (real, once credentials/client_secret.json exists)
split exactly -- same reasoning, same shape.

Deliberately narrow: one method, create_event(). No update/delete/list --
this project has never added an "undo" path for a Reply/Forward draft
either, so a calendar event getting created is exactly as final as those
already are. A human can always go edit/cancel it directly in Calendar.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from datetime import datetime

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(_THIS_DIR, "data")
DEFAULT_CREDENTIALS_DIR = os.path.join(_THIS_DIR, "credentials")


def _to_rfc3339(value: str) -> str:
    """Normalizes a datetime-local input value (e.g. "2026-09-03T14:00",
    no seconds, no offset -- exactly what a browser's
    <input type="datetime-local"> produces) into a real RFC3339 string
    the Calendar API accepts. Interprets the naive value as the machine's
    own local wall-clock time (the honest reading of what a human typed
    into a local datetime field, not a guess) and attaches that machine's
    real UTC offset -- invents no time information the human didn't
    provide."""
    return datetime.fromisoformat(value).astimezone().isoformat()


class CalendarClientBase(ABC):
    @abstractmethod
    def create_event(self, summary: str, description: str,
                      start_iso: str, end_iso: str) -> str:
        """Creates a real calendar event, returns its event id. Only ever
        called by router.py when the human gave both a real start and end
        time -- never with a blank date."""
        ...


class MockCalendarClient(CalendarClientBase):
    """Backs onto a gitignored generated file (mock_calendar_events.json),
    same pattern gmail_client.py's MockGmailClient uses for
    mock_drafts.json -- buildable/testable without any real Google
    account."""

    def __init__(self, data_dir: str = DEFAULT_DATA_DIR) -> None:
        self._data_dir = data_dir
        self._events_path = os.path.join(data_dir, "mock_calendar_events.json")

    def create_event(self, summary: str, description: str,
                      start_iso: str, end_iso: str) -> str:
        events = self._load_events()
        event_id = f"mock-event-{len(events) + 1}"
        events.append({
            "event_id": event_id, "summary": summary, "description": description,
            "start": start_iso, "end": end_iso,
        })
        os.makedirs(self._data_dir, exist_ok=True)
        with open(self._events_path, "w", encoding="utf-8") as f:
            json.dump({"events": events}, f, indent=2)
        return event_id

    def _load_events(self) -> list:
        if not os.path.exists(self._events_path):
            return []
        try:
            with open(self._events_path, "r", encoding="utf-8") as f:
                return json.load(f).get("events", [])
        except Exception:
            return []


class RealCalendarClient(CalendarClientBase):
    """Uses the SAME client_secret.json/token.json OAuth files
    RealGmailClient already uses -- gmail_client.py's SCOPES list (see
    Step 2 above) now includes the Calendar scope alongside Gmail's, so
    one consent screen covers both APIs and both clients share one token
    file."""

    def __init__(self, credentials_dir: str = DEFAULT_CREDENTIALS_DIR) -> None:
        self._client_secret_path = os.path.join(credentials_dir, "client_secret.json")
        self._token_path = os.path.join(credentials_dir, "token.json")
        self._service = None
        print("=" * 70, flush=True)
        print("[Inbox Router] Using REAL Google Calendar -- this calendar is now live.", flush=True)
        print("=" * 70, flush=True)
        self._connect()

    def _connect(self) -> None:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from gmail_client import SCOPES

        creds = None
        if os.path.exists(self._token_path):
            creds = Credentials.from_authorized_user_file(self._token_path, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self._client_secret_path, SCOPES)
                creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(self._token_path), exist_ok=True)
            with open(self._token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        self._service = build("calendar", "v3", credentials=creds)

    def create_event(self, summary: str, description: str,
                      start_iso: str, end_iso: str) -> str:
        event = self._service.events().insert(
            calendarId="primary",
            body={
                "summary": summary, "description": description,
                "start": {"dateTime": _to_rfc3339(start_iso)},
                "end": {"dateTime": _to_rfc3339(end_iso)},
            },
        ).execute()
        return event["id"]


def get_calendar_client(root: str = _THIS_DIR) -> CalendarClientBase:
    """Same Phase A -> Phase B swap as gmail_client.py's
    get_gmail_client() -- checks the exact same client_secret.json path,
    so Gmail and Calendar always flip from mock to real together."""
    client_secret_path = os.path.join(DEFAULT_CREDENTIALS_DIR, "client_secret.json")
    if os.path.isfile(client_secret_path):
        return RealCalendarClient()
    return MockCalendarClient()
