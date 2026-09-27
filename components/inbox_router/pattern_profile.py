"""
components/inbox_router/pattern_profile.py
============================================
The "learned passively, no manual labeling" half of Inbox Router. Built once
from the user's own Sent folder correlated against recent inbox threads (no
labeling step — just observing what already happened), then nudged
incrementally every time the user confirms or overrides a routing decision.

Stored at components/inbox_router/data/pattern_profile.json — gitignored,
same as .env, since this holds real per-sender behavioral counts once
Phase B (a real Gmail account) is wired in.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Self-sufficient regardless of how this module was reached (router.py run
# directly as a script, or imported normally via the inbox_router package)
# -- same defensive precedent as components/agent/agent.py's own path setup.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from gmail_client import EmailMessage

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROFILE_PATH = os.path.join(_THIS_DIR, "data", "pattern_profile.json")

FORWARD_MARKERS = ("forwarded message", "fwd:", "fw:")


def _domain(email_address: str) -> str:
    return email_address.rsplit("@", 1)[-1].lower() if "@" in email_address else email_address.lower()


@dataclass
class SenderPattern:
    sender_domain: str
    reply_count: int = 0
    forward_count: int = 0
    ignore_count: int = 0
    common_forward_targets: List[str] = field(default_factory=list)
    last_updated: str = ""

    def total(self) -> int:
        return self.reply_count + self.forward_count + self.ignore_count

    def dominant_action(self, min_share: float = 0.75) -> Optional[str]:
        """Returns "reply"/"forward"/"leave_alone" if one action makes up at
        least min_share of everything observed for this sender, else None
        (not lopsided enough — RuleLayer should defer to the LLM)."""
        total = self.total()
        if total < 2:      # a single data point is not a pattern
            return None
        counts = {"reply": self.reply_count, "forward": self.forward_count,
                  "leave_alone": self.ignore_count}
        action, count = max(counts.items(), key=lambda kv: kv[1])
        return action if count / total >= min_share else None


class PatternProfile:
    def __init__(self, path: str = DEFAULT_PROFILE_PATH) -> None:
        self._path = path
        self._patterns: Dict[str, SenderPattern] = {}
        self.load()

    # ── persistence ───────────────────────────────────────────────────────
    def load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._patterns = {
                domain: SenderPattern(**fields) for domain, fields in raw.get("patterns", {}).items()
            }
        except Exception:
            self._patterns = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        payload = {"patterns": {domain: asdict(p) for domain, p in self._patterns.items()}}
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def pattern_for(self, sender_email: str) -> Optional[SenderPattern]:
        return self._patterns.get(_domain(sender_email))

    def _get_or_create(self, domain: str) -> SenderPattern:
        if domain not in self._patterns:
            self._patterns[domain] = SenderPattern(sender_domain=domain)
        return self._patterns[domain]

    # ── passive bootstrap ────────────────────────────────────────────────
    def observe_sent_history(self, sent: List[EmailMessage], inbox: List[EmailMessage]) -> None:
        """No manual labels anywhere in this method — every signal comes
        from correlating what the user already sent against the inbox
        threads those sent messages answered. A sent message whose 'to'
        matches the original sender is a reply; a sent message whose body
        carries a forwarded-message marker or whose 'to' is a third party
        is a forward. An inbox thread with no corresponding sent message at
        all is the closest honest proxy this data offers for "ignored" —
        imperfect, but it's a real, observed absence, not an invented one."""
        by_thread: Dict[str, EmailMessage] = {m.thread_id: m for m in inbox}
        answered_threads = set()

        for s in sent:
            origin = by_thread.get(s.thread_id)
            if origin is None:
                continue
            answered_threads.add(s.thread_id)
            domain = _domain(origin.sender_email)
            pattern = self._get_or_create(domain)
            body_lower = (s.body_text or "").lower()
            is_forward = any(marker in body_lower for marker in FORWARD_MARKERS) or (
                s.to and _domain(s.to) != domain
            )
            if is_forward:
                pattern.forward_count += 1
                if s.to and s.to not in pattern.common_forward_targets:
                    pattern.common_forward_targets.append(s.to)
            else:
                pattern.reply_count += 1
            pattern.last_updated = datetime.now(timezone.utc).isoformat()

        for thread_id, origin in by_thread.items():
            if thread_id in answered_threads:
                continue
            domain = _domain(origin.sender_email)
            pattern = self._get_or_create(domain)
            pattern.ignore_count += 1
            pattern.last_updated = datetime.now(timezone.utc).isoformat()

        self.save()

    # ── incremental correction (the "improves over time" loop) ──────────
    def record_confirmed_decision(self, message: EmailMessage, decision: str) -> None:
        """A real user confirmation is a real labeled data point -- folds
        straight back into the same counters observe_sent_history() built,
        so future similar mail leans the same way without ever asking the
        user to label anything up front."""
        domain = _domain(message.sender_email)
        pattern = self._get_or_create(domain)
        if decision == "reply":
            pattern.reply_count += 1
        elif decision == "forward":
            pattern.forward_count += 1
        elif decision == "leave_alone":
            pattern.ignore_count += 1
        # schedule/cold_email intentionally don't move these three
        # counters -- they're not reply/forward/ignore outcomes.
        pattern.last_updated = datetime.now(timezone.utc).isoformat()
        self.save()

    def record_override(self, message: EmailMessage, old_decision: str, new_decision: str) -> None:
        """A correction is worth more than a confirmation -- it's the
        signal that the automated layers got it wrong for this sender.
        Recorded as a normal confirmed-decision for the corrected action,
        so the counters self-correct in the direction the user pointed."""
        self.record_confirmed_decision(message, new_decision)
