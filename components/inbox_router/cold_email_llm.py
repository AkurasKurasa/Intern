"""
components/inbox_router/cold_email_llm.py
==============================================
Generates a cold email's subject/body via LM Studio -- the one
deliberate exception to this project's "never invent text, only the
human's own words" rule, made explicitly and only for Cold Email, on
direct instruction: "Break that rule for Scope #3." Every other
decision (Reply, Forward, Schedule) still requires a human's own real
typed words; this is the sole place an LLM's own generated text is
what actually gets sent.

Uses the same OpenAI-compatible LM Studio client construction
llm_classifier.py already uses (base_url="http://localhost:1234/v1",
api_key="lm-studio") -- no new client library. The model id is read from
LM Studio itself (client.models.list()) rather than hardcoded, so this
keeps working regardless of which model is actually loaded.

Who is writing (direct request 2026-10-05: "Put [My Name] as Kevin"): the
drafts said "[Your Name]", "[Your Company]", or an invented "Alex
Thompson", because the model was never told who the sender is. The name now
comes from data/sender_profile.json -- a setting, not code -- and the prompt
forbids placeholders and invented names or companies. A name placeholder
that still slips through is filled with the real name; any other bracketed
blank gets one retry, then the draft is refused rather than saved with a
hole in it.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional, Tuple

_LMSTUDIO_URL = "http://localhost:1234/v1"
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SENDER_PROFILE_PATH = os.path.join(_THIS_DIR, "data", "sender_profile.json")

_SYSTEM_PROMPT = (
    "You write brief, professional cold outreach emails on behalf of {sender}. "
    "Given a recipient's name and a short reason for reaching out, write a "
    "real subject line and a short (3-5 sentence) email body, signed "
    "\"{sender}\". Write as {sender}; never invent another name, a company "
    "name, a job title or any fact you were not given, and never use "
    "placeholders in square brackets such as [Your Name] or [Your Company]. "
    "Reply with exactly this shape, nothing else:\n"
    "Subject: <the subject line>\n"
    "<the body, on the following lines>"
)

# Placeholders that stand for the sender's name -- filled with the real name.
_NAME_PLACEHOLDER = re.compile(r"\[(?:your|my|sender'?s?)(?: full)? name\]", re.IGNORECASE)
# Anything else in square brackets is a blank the model could not fill.
_ANY_PLACEHOLDER = re.compile(r"\[[^\]\n]{1,60}\]")


def sender_name(path: str = DEFAULT_SENDER_PROFILE_PATH) -> str:
    """The name cold emails are written and signed as; "" if not set."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return str(json.load(f).get("name", "")).strip()
    except Exception:  # noqa: BLE001 -- no profile: no name
        return ""


def fill_placeholders(text: str, name: str) -> str:
    """Replace name placeholders the model left despite being told not to."""
    return _NAME_PLACEHOLDER.sub(name, text) if name else text


def has_placeholder(text: str) -> bool:
    return bool(_ANY_PLACEHOLDER.search(text or ""))


def generate_cold_email(name: str, context_line: str,
                        profile_path: str = DEFAULT_SENDER_PROFILE_PATH,
                        ask=None) -> Tuple[str, str]:
    """Returns (subject, body), or ("", "") if LM Studio isn't reachable,
    has no model loaded, or the response can't be parsed into a real
    subject and body -- fails closed, same as every other LLM call in
    this project when nothing's available to answer. Also ("", "") when a
    bracketed blank survives the retry. `ask` is injectable for tests."""
    ask = ask or _ask
    sender = sender_name(profile_path)
    system = _SYSTEM_PROMPT.format(sender=sender or "the sender")
    reason = context_line.strip() or "a potential partnership"
    user_msg = f"Recipient name: {name}\nReason for reaching out: {reason}"

    for attempt in range(2):
        msg = user_msg if attempt == 0 else user_msg + "\nUse no square-bracket placeholders at all."
        raw = ask(system, msg)
        if raw is None:
            return "", ""
        subject, body = _parse_subject_and_body(raw)
        if not subject or not body:
            return "", ""
        subject, body = fill_placeholders(subject, sender), fill_placeholders(body, sender)
        if not has_placeholder(subject) and not has_placeholder(body):
            return subject, body
    return "", ""


def _ask(system: str, user_msg: str) -> Optional[str]:
    """The model's raw reply, or None when LM Studio can't answer."""
    try:
        from openai import OpenAI
    except ImportError:
        return None
    try:
        client = OpenAI(base_url=_LMSTUDIO_URL, api_key="lm-studio")
        models = client.models.list()
        model_id = models.data[0].id if models.data else None
        if not model_id:
            return None
        resp = client.chat.completions.create(
            model=model_id, max_tokens=300,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return None


def _parse_subject_and_body(raw: str) -> Tuple[str, str]:
    lines = raw.split("\n", 1)
    if not lines or not lines[0].strip():
        return "", ""
    subject = lines[0].strip()
    if subject.lower().startswith("subject:"):
        subject = subject[len("subject:"):].strip()
    body = lines[1].strip() if len(lines) > 1 else ""
    if not subject or not body:
        return "", ""
    return subject, body
