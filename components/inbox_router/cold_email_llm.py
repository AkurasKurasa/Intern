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
api_key="lm-studio") -- no new client library, no new config. The
model id is read from LM Studio itself (client.models.list()) rather
than hardcoded, so this keeps working regardless of which model is
actually loaded.
"""
from __future__ import annotations

from typing import Tuple

_LMSTUDIO_URL = "http://localhost:1234/v1"

_SYSTEM_PROMPT = (
    "You write brief, professional cold outreach emails. Given a "
    "recipient's name and a short reason for reaching out, write a "
    "real subject line and a short (3-5 sentence) email body. "
    "Reply with exactly this shape, nothing else:\n"
    "Subject: <the subject line>\n"
    "<the body, on the following lines>"
)


def generate_cold_email(name: str, context_line: str) -> Tuple[str, str]:
    """Returns (subject, body), or ("", "") if LM Studio isn't reachable,
    has no model loaded, or the response can't be parsed into a real
    subject and body -- fails closed, same as every other LLM call in
    this project when nothing's available to answer."""
    try:
        from openai import OpenAI
    except ImportError:
        return "", ""

    try:
        client = OpenAI(base_url=_LMSTUDIO_URL, api_key="lm-studio")
        models = client.models.list()
        model_id = models.data[0].id if models.data else None
        if not model_id:
            return "", ""

        reason = context_line.strip() or "a potential partnership"
        user_msg = f"Recipient name: {name}\nReason for reaching out: {reason}"
        resp = client.chat.completions.create(
            model=model_id, max_tokens=300,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception:
        return "", ""

    return _parse_subject_and_body(raw)


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
