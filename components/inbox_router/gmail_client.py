"""
components/inbox_router/gmail_client.py
========================================
Everything that talks to Gmail lives behind GmailClientBase — router.py,
rules.py and llm_classifier.py only ever see EmailMessage/GmailClientBase,
never a concrete class. get_gmail_client() is the entire Phase A -> Phase B
swap: MockGmailClient until credentials/client_secret.json exists on disk,
RealGmailClient once it does. No code above this file changes either way.

GmailClientBase deliberately has no send()/send_message() method anywhere —
not a flag, not a "dry_run" toggle on a send() that exists. There is no
method here capable of putting a real email in front of a real person; the
furthest this interface goes is create_draft(), which leaves a draft sitting
in Gmail for a human to read and send themselves.
"""
from __future__ import annotations

import base64
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import List, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(_THIS_DIR, "data")
DEFAULT_CREDENTIALS_DIR = os.path.join(_THIS_DIR, "credentials")

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
PROCESSED_LABEL_NAME = "Intern/Processed"
FLAGGED_LABEL_NAME = "Intern/Flagged"


@dataclass
class EmailMessage:
    id: str
    thread_id: str
    sender: str            # "Display Name <address@domain>"
    sender_email: str
    subject: str
    snippet: str
    body_text: str
    received_at: str       # ISO 8601
    labels: List[str] = field(default_factory=list)
    to: str = ""           # recipient address -- only meaningful on Sent messages;
                            # empty on inbox messages. Lets the pattern-profile
                            # bootstrap tell "replied to the original sender"
                            # apart from "forwarded to someone else."


class GmailClientBase(ABC):
    @abstractmethod
    def list_inbox_unprocessed(self, max_results: int = 25) -> List[EmailMessage]: ...

    @abstractmethod
    def get_message(self, message_id: str) -> Optional[EmailMessage]: ...

    @abstractmethod
    def list_sent(self, since: str, max_results: int = 200) -> List[EmailMessage]: ...

    @abstractmethod
    def list_recent_inbox(self, since: str, max_results: int = 200) -> List[EmailMessage]:
        """ALL inbox messages since a date, processed or not — distinct from
        list_inbox_unprocessed() (used by the live poll loop). Only the
        pattern-profile bootstrap needs this: it has to see threads the
        user already handled to learn what "handled" looked like."""
        ...

    @abstractmethod
    def create_draft(self, to: str, subject: str, body: str, thread_id: str = "") -> str:
        """Creates a Gmail draft, returns its draft id. Never sends anything."""
        ...

    @abstractmethod
    def mark_processed(self, message_id: str) -> None: ...

    @abstractmethod
    def apply_flag_label(self, message_id: str) -> None:
        """The real, concrete action a "flag" decision takes: applies a
        real, visible Gmail label so a flagged message actually shows up
        somewhere for a human to find -- not just a decision recorded in
        this project's own local history with nothing to show for it."""
        ...


class MockGmailClient(GmailClientBase):
    """Backs onto a committed fixture (mock_inbox.json) plus two small
    gitignored generated files (processed-id state, created drafts) so the
    whole rules/LLM/pattern-profile/UI pipeline is buildable and testable
    without any Gmail account at all."""

    def __init__(self, data_dir: str = DEFAULT_DATA_DIR) -> None:
        self._data_dir = data_dir
        self._fixture_path = os.path.join(data_dir, "mock_inbox.json")
        self._state_path = os.path.join(data_dir, "mock_state.json")
        self._drafts_path = os.path.join(data_dir, "mock_drafts.json")
        self._inbox: List[EmailMessage] = []
        self._sent: List[EmailMessage] = []
        self._load_fixture()

    def _load_fixture(self) -> None:
        if not os.path.exists(self._fixture_path):
            return
        with open(self._fixture_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self._inbox = [EmailMessage(**m) for m in raw.get("inbox", [])]
        self._sent = [EmailMessage(**m) for m in raw.get("sent", [])]

    def _load_state(self) -> dict:
        if not os.path.exists(self._state_path):
            return {"processed_ids": []}
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"processed_ids": []}

    def _save_state(self, state: dict) -> None:
        os.makedirs(self._data_dir, exist_ok=True)
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def list_inbox_unprocessed(self, max_results: int = 25) -> List[EmailMessage]:
        processed = set(self._load_state().get("processed_ids", []))
        return [m for m in self._inbox if m.id not in processed][:max_results]

    def get_message(self, message_id: str) -> Optional[EmailMessage]:
        for m in self._inbox + self._sent:
            if m.id == message_id:
                return m
        return None

    def list_sent(self, since: str, max_results: int = 200) -> List[EmailMessage]:
        # ISO-8601 timestamps sort/compare correctly as plain strings.
        return [m for m in self._sent if m.received_at >= since][:max_results]

    def list_recent_inbox(self, since: str, max_results: int = 200) -> List[EmailMessage]:
        return [m for m in self._inbox if m.received_at >= since][:max_results]

    def create_draft(self, to: str, subject: str, body: str, thread_id: str = "") -> str:
        drafts = self._load_drafts()
        draft_id = f"mock-draft-{len(drafts) + 1}"
        drafts.append({
            "draft_id": draft_id, "to": to, "subject": subject,
            "body": body, "thread_id": thread_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        os.makedirs(self._data_dir, exist_ok=True)
        with open(self._drafts_path, "w", encoding="utf-8") as f:
            json.dump({"drafts": drafts}, f, indent=2)
        return draft_id

    def _load_drafts(self) -> list:
        if not os.path.exists(self._drafts_path):
            return []
        try:
            with open(self._drafts_path, "r", encoding="utf-8") as f:
                return json.load(f).get("drafts", [])
        except Exception:
            return []

    def mark_processed(self, message_id: str) -> None:
        state = self._load_state()
        ids = set(state.get("processed_ids", []))
        ids.add(message_id)
        state["processed_ids"] = sorted(ids)
        self._save_state(state)

    def apply_flag_label(self, message_id: str) -> None:
        state = self._load_state()
        ids = set(state.get("flagged_ids", []))
        ids.add(message_id)
        state["flagged_ids"] = sorted(ids)
        self._save_state(state)


class RealGmailClient(GmailClientBase):
    """Standard google-auth-oauthlib Desktop-app OAuth flow. First
    construction (once credentials/client_secret.json exists) opens a real
    browser for the user's own Google consent — that first click is always
    the user's, never triggered by this codebase automatically. After that,
    token.json holds a refresh token so future runs don't need a browser."""

    def __init__(self, credentials_dir: str = DEFAULT_CREDENTIALS_DIR) -> None:
        self._client_secret_path = os.path.join(credentials_dir, "client_secret.json")
        self._token_path = os.path.join(credentials_dir, "token.json")
        self._service = None
        self._processed_label_id: Optional[str] = None
        self._flagged_label_id: Optional[str] = None
        # Loud, one-time, impossible-to-miss -- mirrors run_task.py's own
        # [EMERGENCY STOP] banner convention for "this is about to do
        # something real." Printed once per process, at construction, not
        # buried in a debug log.
        print("=" * 70, flush=True)
        print("[Inbox Router] Using REAL Gmail — this account is now live.", flush=True)
        print("=" * 70, flush=True)
        self._connect()

    def _connect(self) -> None:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

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
        self._service = build("gmail", "v1", credentials=creds)

    def _ensure_label(self, name: str) -> str:
        resp = self._service.users().labels().list(userId="me").execute()
        for lbl in resp.get("labels", []):
            if lbl["name"] == name:
                return lbl["id"]
        created = self._service.users().labels().create(
            userId="me",
            body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
        ).execute()
        return created["id"]

    def _ensure_processed_label(self) -> str:
        if self._processed_label_id:
            return self._processed_label_id
        self._processed_label_id = self._ensure_label(PROCESSED_LABEL_NAME)
        return self._processed_label_id

    def _ensure_flagged_label(self) -> str:
        if self._flagged_label_id:
            return self._flagged_label_id
        self._flagged_label_id = self._ensure_label(FLAGGED_LABEL_NAME)
        return self._flagged_label_id

    def _header(self, headers: list, name: str) -> str:
        for h in headers:
            if h.get("name", "").lower() == name.lower():
                return h.get("value", "")
        return ""

    def _extract_body_text(self, payload: dict) -> str:
        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        for part in payload.get("parts", []) or []:
            text = self._extract_body_text(part)
            if text:
                return text
        return ""

    def _to_email_message(self, raw: dict) -> EmailMessage:
        headers = raw.get("payload", {}).get("headers", [])
        sender = self._header(headers, "From")
        sender_email = sender.split("<")[-1].rstrip(">") if "<" in sender else sender
        return EmailMessage(
            id=raw["id"],
            thread_id=raw.get("threadId", ""),
            sender=sender,
            sender_email=sender_email,
            subject=self._header(headers, "Subject"),
            snippet=raw.get("snippet", ""),
            body_text=self._extract_body_text(raw.get("payload", {})),
            received_at=self._header(headers, "Date"),
            labels=raw.get("labelIds", []),
            to=self._header(headers, "To"),
        )

    def list_inbox_unprocessed(self, max_results: int = 25) -> List[EmailMessage]:
        query = f'-label:"{PROCESSED_LABEL_NAME}"'
        resp = self._service.users().messages().list(
            userId="me", labelIds=["INBOX"], q=query, maxResults=max_results,
        ).execute()
        out = []
        for ref in resp.get("messages", []):
            full = self._service.users().messages().get(userId="me", id=ref["id"], format="full").execute()
            out.append(self._to_email_message(full))
        return out

    def get_message(self, message_id: str) -> Optional[EmailMessage]:
        try:
            full = self._service.users().messages().get(userId="me", id=message_id, format="full").execute()
        except Exception:
            return None
        return self._to_email_message(full)

    def list_sent(self, since: str, max_results: int = 200) -> List[EmailMessage]:
        return self._list_by_label_since("SENT", since, max_results)

    def list_recent_inbox(self, since: str, max_results: int = 200) -> List[EmailMessage]:
        return self._list_by_label_since("INBOX", since, max_results)

    def _list_by_label_since(self, label: str, since: str, max_results: int) -> List[EmailMessage]:
        since_date = since.split("T")[0].replace("-", "/")
        resp = self._service.users().messages().list(
            userId="me", labelIds=[label], q=f"after:{since_date}", maxResults=max_results,
        ).execute()
        out = []
        for ref in resp.get("messages", []):
            full = self._service.users().messages().get(userId="me", id=ref["id"], format="full").execute()
            out.append(self._to_email_message(full))
        return out

    def create_draft(self, to: str, subject: str, body: str, thread_id: str = "") -> str:
        mime = MIMEText(body)
        mime["to"] = to
        mime["subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")
        message_body = {"raw": raw}
        if thread_id:
            message_body["threadId"] = thread_id
        draft = self._service.users().drafts().create(
            userId="me", body={"message": message_body},
        ).execute()
        return draft["id"]

    def mark_processed(self, message_id: str) -> None:
        label_id = self._ensure_processed_label()
        self._service.users().messages().modify(
            userId="me", id=message_id, body={"addLabelIds": [label_id]},
        ).execute()

    def apply_flag_label(self, message_id: str) -> None:
        label_id = self._ensure_flagged_label()
        self._service.users().messages().modify(
            userId="me", id=message_id, body={"addLabelIds": [label_id]},
        ).execute()


def get_gmail_client(root: str = _THIS_DIR) -> GmailClientBase:
    """The whole Phase A -> Phase B swap. Everything upstream of this
    function only ever touches GmailClientBase/EmailMessage."""
    client_secret_path = os.path.join(DEFAULT_CREDENTIALS_DIR, "client_secret.json")
    if os.path.isfile(client_secret_path):
        return RealGmailClient()
    return MockGmailClient()
