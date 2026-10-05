"""
components/inbox_router/task_list_parser.py
=================================================
Reads components/inbox_router/data/task_list.txt -- a boss-style, plain
text file listing who Cold email should reach out to. Line/regex parsing
only, matching this project's "never guess, never invent" rule: a line
that doesn't match the expected shape is skipped, never interpreted by
an LLM.

File format:
    Cold email: <optional free text, becomes the pre-filled subject>
    Name <email@example.com>
    Name <email@example.com>

    Cold email: <a different context line>
    Name <email@example.com>

A "Cold email:" heading starts a new section; every following
"Name <email>" line until a blank line or a different heading belongs to
that heading's context_line. A malformed line inside a section that does
NOT end in a colon is skipped, not guessed at, and the section stays open.
A malformed line that DOES end in a colon (e.g. a stray note) is treated
as a different heading and ends the section -- it is never added as a
target either way.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TASK_LIST_PATH = os.path.join(_THIS_DIR, "data", "task_list.txt")

_HEADING_RE = re.compile(r"^Cold email:\s*(.*)$")
_ANY_HEADING_RE = re.compile(r".*:\s*$")
_TARGET_RE = re.compile(r"^(.+?)\s*<([^<>@\s]+@[^<>\s]+)>\s*$")


@dataclass
class ColdEmailTarget:
    name: str
    email: str
    context_line: str


def parse_cold_email_targets(path: str = DEFAULT_TASK_LIST_PATH) -> List[ColdEmailTarget]:
    if not os.path.isfile(path):
        return []
    targets: List[ColdEmailTarget] = []
    in_section = False
    context_line = ""
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            stripped = raw_line.strip()
            heading_match = _HEADING_RE.match(stripped)
            if heading_match:
                in_section = True
                context_line = heading_match.group(1).strip()
                continue
            if not stripped:
                in_section = False
                continue
            if _ANY_HEADING_RE.match(stripped):
                in_section = False
                continue
            if not in_section:
                continue
            target_match = _TARGET_RE.match(stripped)
            if not target_match:
                continue
            name, email = target_match.group(1).strip(), target_match.group(2).strip()
            targets.append(ColdEmailTarget(name=name, email=email, context_line=context_line))
    return targets


# ---------------------------------------------------------------- checks
#
# Direct request (2026-10-05): "Add more tasks for the Scope #3, more cold
# emails and general checking." Three kinds of check, each written as a
# plain line the boss can type, parsed by the same never-guess rules as
# Cold email above -- a line that does not match is skipped, never
# interpreted:
#
#     Check replies:                      did these people write back?
#     Name <email@example.com>
#
#     Check inbox: <topic words>          any mail about this?
#     Check schedule: <date and time>     am I free then?
#
# The topic and the time are kept as the literal text the boss wrote;
# checker.py decides what they mean, with rules, and says "unclear" rather
# than guessing when it cannot.

CHECK_REPLY = "reply"
CHECK_TOPIC = "topic"
CHECK_SCHEDULE = "schedule"

_CHECK_REPLIES_RE = re.compile(r"^Check replies:\s*$", re.IGNORECASE)
_CHECK_INLINE_RE = re.compile(r"^Check (inbox|schedule):\s*(\S.*)$", re.IGNORECASE)


@dataclass
class CheckTask:
    kind: str           # CHECK_REPLY / CHECK_TOPIC / CHECK_SCHEDULE
    query: str          # the topic or time as written; the email for a reply check
    name: str = ""      # reply checks only

    @property
    def label(self) -> str:
        if self.kind == CHECK_REPLY:
            return f"Did {self.name} reply?"
        if self.kind == CHECK_TOPIC:
            return f"Any mail about \"{self.query}\"?"
        return f"Am I free {self.query}?"


def parse_check_tasks(path: str = DEFAULT_TASK_LIST_PATH) -> List[CheckTask]:
    """Every check on the task list, in the order written."""
    if not os.path.isfile(path):
        return []
    tasks: List[CheckTask] = []
    in_replies = False
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            stripped = raw_line.strip()
            if _CHECK_REPLIES_RE.match(stripped):
                in_replies = True
                continue
            inline = _CHECK_INLINE_RE.match(stripped)
            if inline:
                in_replies = False
                kind = CHECK_TOPIC if inline.group(1).lower() == "inbox" else CHECK_SCHEDULE
                tasks.append(CheckTask(kind=kind, query=inline.group(2).strip()))
                continue
            if not stripped or _ANY_HEADING_RE.match(stripped) or _HEADING_RE.match(stripped):
                in_replies = False
                continue
            if not in_replies:
                continue
            target = _TARGET_RE.match(stripped)
            if target:
                tasks.append(CheckTask(kind=CHECK_REPLY, query=target.group(2).strip(),
                                       name=target.group(1).strip()))
    return tasks
