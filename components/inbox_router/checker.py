"""
components/inbox_router/checker.py
==================================
Answers the boss' "Check ..." tasks (task_list_parser.parse_check_tasks):

    reply     did this person write to us?        -> yes / no
    topic     is there mail about these words?    -> yes / no
    schedule  is the calendar free at this time?  -> free / busy / unclear

Read-only by construction: it lists mail and calendar events and never
drafts, sends, labels or schedules anything.

Rules, not a language model -- Scope #3's standing rule is that Intern never
invents a fact, and a wrong "yes, they replied" is worse than none. Every
answer carries the evidence it was read from (the matching emails or
events), so a person can see why. When a check cannot be answered from
what is literally there -- a time with no clock hour, a calendar that
cannot be read -- the answer is "unclear", never a guess.

Generic: no sender, topic or date is named anywhere in this file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from schedule_extract import extract_event_time
from task_list_parser import CHECK_REPLY, CHECK_SCHEDULE, CHECK_TOPIC, CheckTask

# How long "am I free at 2pm?" is taken to ask about. A check names a start
# time only; half an hour matches the shortest events the schedule flow
# creates, so a check never reports "free" across the start of a meeting.
SLOT_MINUTES = 30

# Words too common to mean a topic on their own.
_STOPWORDS = {"the", "and", "for", "from", "any", "about", "with", "re", "fwd", "mail", "email"}


@dataclass
class CheckResult:
    kind: str
    label: str
    answer: str                       # yes / no / free / busy / unclear
    detail: str                       # one plain sentence
    evidence: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "answer": self.answer,
                "detail": self.detail, "evidence": self.evidence}


def _mail_evidence(messages) -> List[dict]:
    newest = sorted(messages, key=lambda m: m.received_at, reverse=True)
    return [{"subject": m.subject, "sender": m.sender, "received_at": m.received_at,
             "message_id": m.id} for m in newest]


def topic_words(query: str) -> List[str]:
    words = re.sub(r"[^a-z0-9 ]", " ", query.lower()).split()
    return [w for w in words if len(w) >= 3 and w not in _STOPWORDS]


def check_reply(task: CheckTask, gmail_client) -> CheckResult:
    email = task.query.lower()
    mine = [m for m in gmail_client.list_recent_inbox(since="")
            if (m.sender_email or "").lower() == email]
    if not mine:
        return CheckResult(task.kind, task.label, "no",
                           f"No email from {task.name} <{task.query}> in the inbox.")
    evidence = _mail_evidence(mine)
    latest = evidence[0]
    count = f"{len(mine)} emails" if len(mine) > 1 else "1 email"
    return CheckResult(task.kind, task.label, "yes",
                       f"{count} from {task.name}; latest: \"{latest['subject']}\".",
                       evidence)


def check_topic(task: CheckTask, gmail_client) -> CheckResult:
    words = topic_words(task.query)
    if not words:
        return CheckResult(task.kind, task.label, "unclear",
                           f"\"{task.query}\" has no words specific enough to search for.")
    hits = []
    for m in gmail_client.list_recent_inbox(since=""):
        text = f"{m.subject} {m.body_text}".lower()
        if all(w in text for w in words):
            hits.append(m)
    if not hits:
        return CheckResult(task.kind, task.label, "no",
                           f"No email mentions {' + '.join(words)}.")
    evidence = _mail_evidence(hits)
    count = f"{len(hits)} emails" if len(hits) > 1 else "1 email"
    return CheckResult(task.kind, task.label, "yes",
                       f"{count} found; latest: \"{evidence[0]['subject']}\".", evidence)


def check_schedule(task: CheckTask, calendar_client, now: Optional[datetime] = None) -> CheckResult:
    now = now or datetime.now()
    when = extract_event_time(task.query, "", now.isoformat())
    if when is None:
        return CheckResult(task.kind, task.label, "unclear",
                           f"\"{task.query}\" needs both a date and a clock time to check.")
    start = when.start
    end = start + timedelta(minutes=SLOT_MINUTES)
    start_iso, end_iso = start.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M")
    try:
        events = calendar_client.list_events(start_iso, end_iso)
    except NotImplementedError:
        return CheckResult(task.kind, task.label, "unclear",
                           "This calendar cannot be read, so free/busy is unknown.")
    stamp = start.strftime("%a %b %d, %Y %I:%M %p").replace(" 0", " ")
    if not events:
        return CheckResult(task.kind, task.label, "free", f"Nothing on the calendar at {stamp}.")
    evidence = [{"summary": e.get("summary", ""), "start": e.get("start", ""),
                 "end": e.get("end", "")} for e in events]
    return CheckResult(task.kind, task.label, "busy",
                       f"Busy at {stamp}: \"{evidence[0]['summary']}\".", evidence)


def run_check(task: CheckTask, gmail_client, calendar_client,
              now: Optional[datetime] = None) -> CheckResult:
    if task.kind == CHECK_REPLY:
        return check_reply(task, gmail_client)
    if task.kind == CHECK_TOPIC:
        return check_topic(task, gmail_client)
    if task.kind == CHECK_SCHEDULE:
        return check_schedule(task, calendar_client, now)
    return CheckResult(task.kind, task.label, "unclear", f"Unknown check kind {task.kind!r}.")
