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

_LMSTUDIO_URL = "http://localhost:1234/v1"

_SYSTEM_PROMPT = (
    "You write brief, professional email replies on behalf of a real "
    "person. Given the email they received, write a short (2-4 "
    "sentence) reply in a plain, direct voice. Reply with ONLY the "
    "reply body text, nothing else -- no subject line, no greeting "
    "like 'Dear', no signature block."
)


def generate_reply(sender: str, subject: str, body_text: str) -> str:
    """Returns the reply body text, or "" if LM Studio isn't reachable,
    has no model loaded, or returns nothing usable -- fails closed, same
    as every other LLM call in this project when nothing's available."""
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

        user_msg = f"From: {sender}\nSubject: {subject}\n\n{body_text[:2000]}"
        resp = client.chat.completions.create(
            model=model_id, max_tokens=200,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""
