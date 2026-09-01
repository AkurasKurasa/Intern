"""
components/inbox_router/cold_email_sender.py
=================================================
Output step for the "cold email" decision -- Reply/Forward's create_draft()
call, reused as-is, for a brand-new contact sourced from a task list
document instead of an existing inbox thread. Deliberately does NOT call
decision_recorder.record_example(): unlike Reply/Forward/Schedule/Flag,
there's no inbox-triage decision being learned here -- the task list
already decided WHO, there's no ambiguous message being classified.
"""
from __future__ import annotations

import json
import os
from typing import List

from task_list_parser import ColdEmailTarget, DEFAULT_TASK_LIST_PATH, parse_cold_email_targets

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COLD_EMAIL_STATE_PATH = os.path.join(_THIS_DIR, "data", "cold_email_state.json")


class ColdEmailSender:
    def __init__(self, gmail_client,
                 task_list_path: str = DEFAULT_TASK_LIST_PATH,
                 state_path: str = DEFAULT_COLD_EMAIL_STATE_PATH) -> None:
        self._gmail = gmail_client
        self._task_list_path = task_list_path
        self._state_path = state_path

    def list_pending_targets(self) -> List[ColdEmailTarget]:
        targets = parse_cold_email_targets(self._task_list_path)
        contacted_raw = self._load_state().get("contacted_emails", [])
        contacted = set(email.strip().lower() for email in contacted_raw)
        return [t for t in targets if t.email.strip().lower() not in contacted]

    def send_cold_email(self, email: str, subject: str, body: str) -> str:
        subject = (subject or "").strip()
        body = (body or "").strip()
        if not subject or not body:
            return ""  # never draft empty/invented content
        pending_emails = {t.email.strip().lower() for t in self.list_pending_targets()}
        if email.strip().lower() not in pending_emails:
            return ""  # not a known, still-pending target -- refuse silently, same no-op contract as blank content
        draft_id = self._gmail.create_draft(to=email, subject=subject, body=body, thread_id="")
        state = self._load_state()
        contacted = set(state.get("contacted_emails", []))
        contacted.add(email.strip().lower())
        state["contacted_emails"] = sorted(contacted)
        self._save_state(state)
        return draft_id

    def _load_state(self) -> dict:
        if not os.path.exists(self._state_path):
            return {}
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_state(self, state: dict) -> None:
        os.makedirs(os.path.dirname(self._state_path) or ".", exist_ok=True)
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
