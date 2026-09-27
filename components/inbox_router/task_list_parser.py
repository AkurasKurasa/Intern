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
