"""
components/inbox_router/inbox_reply_llm.py
================================================
Generates a real reply body via LM Studio for automate_inbox.py's own
--commit demo run -- a second, explicitly authorized exception to this
project's "never invent text, only the human's own words" rule, on
direct instruction after being told plainly what that rule means and
what breaking it costs: "Break it goddamnit, I literally need to see
it full functioning before I can deem this as complete."

Scoped exactly like cold_email_llm.py's own existing exception: this is
called ONLY from automate_inbox.py's own script, never from router.py's
confirm_suggestion()/override_decision() -- the real Inbox Dispatch page
a person clicks through by hand still requires that person's own real
typed words, completely untouched by this module. Only the automated
demo script gets AI-authored reply text, same boundary shape as Cold
Email's own script-only exception.

Same OpenAI-compatible LM Studio client construction llm_classifier.py
and cold_email_llm.py already use -- model id read from LM Studio
itself (client.models.list()), not hardcoded.
"""
from __future__ import annotations

import re

_LMSTUDIO_URL = "http://localhost:1234/v1"

# Found live: the small local model occasionally leaks its own
# meta-commentary into the reply text instead of just answering (one
# real response ended with a Chinese-language note telling itself not
# to use Chinese) -- a malformed generation, not a real reply. Every
# mock email and reply in this project is English, so any CJK
# (Chinese/Japanese/Korean) character is itself the signal that
# something went wrong, without needing to parse what the model said.
_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")

_SYSTEM_PROMPT = (
    "You write brief, professional email replies on behalf of a real "
    "person. Given the email they received, write a short (2-4 "
    "sentence) reply in a plain, direct voice. Reply with ONLY the "
    "reply body text, nothing else -- no subject line, no greeting "
    "like 'Dear', no signature block."
)

_FORWARD_SYSTEM_PROMPT = (
    "You write a brief, one-sentence forwarding note on behalf of a "
    "real person, to go along with an email they're forwarding to a "
    "colleague. Reply with ONLY that one sentence, nothing else -- no "
    "subject line, no greeting, no signature block."
)


def _call_lmstudio(system_prompt: str, user_msg: str, max_tokens: int) -> str:
    """Shared LM Studio call for generate_reply()/generate_forward_note().
    Returns "" on any failure (unreachable, no model loaded, malformed
    CJK-leaking response) -- fails closed, same as every other LLM call
    in this project when nothing usable is available."""
    try:
        from openai import OpenAI
    except ImportError:
        return ""

    try:
        client = OpenAI(base_url=_LMSTUDIO_URL, api_key="lm-studio")
        models = client.models.list()
        model_id = models.data[0].id if models.data else None
        if not model_id:
            return ""

        resp = client.chat.completions.create(
            model=model_id, max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        if _CJK_RE.search(text):
            return ""
        return text
    except Exception:
        return ""


def generate_reply(sender: str, subject: str, body_text: str) -> str:
    """Returns the reply body text, or "" on any failure -- see
    _call_lmstudio()'s own docstring for the fail-closed conditions."""
    user_msg = f"From: {sender}\nSubject: {subject}\n\n{body_text[:2000]}"
    return _call_lmstudio(_SYSTEM_PROMPT, user_msg, max_tokens=200)


def forward_recipient(sender_email: str) -> str:
    """A synthetic, deterministic internal address to forward to -- NOT
    LLM-invented. Forward genuinely needs a third-party recipient the
    email itself never supplies, and inventing a specific real-looking
    PERSON (unlike drafting reply text back to the sender who already
    emailed you) is a materially different kind of guess. This derives
    a clearly-synthetic team alias from the sender's own real domain
    instead, so nothing about the address itself is invented content --
    only the note that goes with it is."""
    domain = sender_email.split("@")[-1] if "@" in sender_email else "example.com"
    return f"team-lead@{domain}"


def generate_forward_note(sender: str, subject: str, body_text: str) -> str:
    """Returns a one-sentence forwarding note, or "" on any failure --
    see _call_lmstudio()'s own docstring for the fail-closed
    conditions."""
    user_msg = f"Forwarding this email from: {sender}\nSubject: {subject}\n\n{body_text[:2000]}"
    return _call_lmstudio(_FORWARD_SYSTEM_PROMPT, user_msg, max_tokens=80)
