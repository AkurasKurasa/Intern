"""
components/inbox_router/schedule_extract.py
===========================================
Reads the date and time of an event out of an email's own text, so a
"schedule" decision can be completed without anyone typing a time.

It is deliberately rule-based rather than a language model call. The
project's standing rule for Scope #3 is that Intern never invents a date:
a wrong calendar entry is worse than none. A model asked "when is this?"
will produce an answer even when the email gives none. These rules can
only return what the email literally states, and they return nothing at
all unless the email names BOTH a calendar date and a clock time, written
next to each other:

    "September 3rd at 2pm"            -> scheduled
    "Thursday, September 4th at 10am" -> scheduled
    "Aug 20, 11pm-1am PT"             -> scheduled, at the start time
    "due back by September 5th"       -> nothing (no time given)
    "September 11th, any time in the morning" -> nothing (no clock time)
    "Open enrollment begins Monday"   -> nothing (no date, no time)

Anything that returns nothing stays pending for a person, exactly as
before. The year is taken from when the email was received, rolling
forward a year if that would put the event months in the past.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DATE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)

_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)

# What may sit between a date and its time: nothing, a comma, or "at".
_JOINER = re.compile(r"^\s*(?:,\s*)?(?:at\s+|@\s*)?$", re.IGNORECASE)

# Rolling an event into next year only when it would otherwise be this far
# in the past relative to the email -- a December email about "Jan 5th".
_ROLLOVER_DAYS = 180


@dataclass(frozen=True)
class EventTime:
    start: datetime
    quote: str          # the exact words the time was read from

    def as_input_value(self) -> str:
        """The value a <input type="datetime-local"> expects."""
        return self.start.strftime("%Y-%m-%dT%H:%M")

    def describe(self) -> str:
        return self.start.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def _first_pair(text: str, received: datetime) -> Optional[EventTime]:
    dates = list(_DATE.finditer(text))
    for t in _TIME.finditer(text):
        # The date this time belongs to is the nearest one before it,
        # joined to it by nothing more than a comma or "at".
        before = [d for d in dates if d.end() <= t.start()]
        if not before:
            continue
        d = before[-1]
        if not _JOINER.match(text[d.end():t.start()]):
            continue

        month = _MONTHS[d.group(1)[:3].lower()]
        day = int(d.group(2))
        hour = int(t.group(1)) % 12 + (12 if t.group(3).lower() == "pm" else 0)
        minute = int(t.group(2) or 0)
        if not (1 <= day <= 31 and minute < 60):
            continue
        try:
            start = datetime(received.year, month, day, hour, minute)
        except ValueError:          # e.g. "Feb 30"
            continue
        if (received.replace(tzinfo=None) - start).days > _ROLLOVER_DAYS:
            start = start.replace(year=received.year + 1)
        return EventTime(start=start, quote=text[d.start():t.end()].strip())
    return None


def extract_event_time(subject: str, body: str, received_at: str = "") -> Optional[EventTime]:
    """The event's start, read from the email, or None when the email does
    not state both a date and a time. The body is read before the subject,
    because a follow-up's body ("move our call to September 9th at 1pm")
    is more specific than its subject line."""
    try:
        received = datetime.fromisoformat(received_at) if received_at else datetime.now()
    except ValueError:
        received = datetime.now()
    return _first_pair(body or "", received) or _first_pair(subject or "", received)
