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


def generate_reply(sender: str, subject: str, body_text: str) -> str:
    """Returns the reply body text, or "" if LM Studio isn't reachable,
    has no model loaded, returns nothing usable, or returns a malformed
    response containing CJK text (see _CJK_RE above) -- fails closed,
    same as every other LLM call in this project when nothing's
    available."""
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
        reply = (resp.choices[0].message.content or "").strip()
        if _CJK_RE.search(reply):
            return ""
        return reply
    except Exception:
        return ""
