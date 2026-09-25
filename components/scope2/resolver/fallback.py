"""The two tiers around the matcher: label lookup before it, the LLM after it.

    lookup  ->  matcher (resolver/assign.py, unchanged)  ->  LLM

WHY THIS SHAPE
--------------
Direct request: make Scope #2 work the way Scope #1 does -- read the label,
look it up, fall back to the LLM -- while keeping the trained matcher as the
middle tier, keeping rule induction for derived fields, and letting the LLM
refuse. Scope #1 answers "what goes in this field" with a cheap deterministic
lookup first and an LLM only when that misses. Scope #2 now asks the same
question the same way, with its learned matcher in between.

Only the MAPPING decision changes. Values are still written with Playwright's
fill(), the same direct-write manoeuvre as Scope #1's WM_SETTEXT, and the
derived Remarks field is still decided by rule induction.

WHAT EACH TIER IS ALLOWED TO SEE
--------------------------------
Only what a person would see on the page: a field's label, input type,
options, placeholder, min/max and whether it is required. NEVER truth_key --
that is the portal's data-key, the answer -- and never column_key either,
because nothing guarantees it is not derived from the same. This is the same
invariant test_features.py::test_no_demonstration_only_signal pins for the
matcher, and tests/scope2/test_fallback.py pins it here.

REFUSAL IS A RESULT
-------------------
Both tiers prefer leaving a field empty to guessing. Lookup only accepts a
fuzzy match when exactly one column fits, unlike Scope #1's first-match-wins
fallback. The LLM may answer NONE, an answer that is not exactly one of the
offered columns counts as NONE, and a column claimed by two fields is refused
for both. The same principle the matcher's tau/delta thresholds encode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence

VIA_LOOKUP = "lookup"
VIA_MATCHER = "matcher"
VIA_LLM = "llm"


@dataclass
class TierDecision:
    """One field's outcome from a tier: a column, or a reason there is none."""
    field: str
    column: Optional[str]
    via: str
    reason: str = ""

    def to_assignment(self):
        """The same shape the executor reads from a mapping's assignments."""
        return {"source_header": self.column, "target_label": self.field,
                "score": None, "margin": None, "status": "auto", "via": self.via}


# ------------------------------------------------------------------ lookup


def words(text: str) -> List[str]:
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()


def lookup_tier(fields, columns, exclude: Iterable[str] = ()) -> List[TierDecision]:
    """Scope #1's lookup, with the one change that makes it safe to trust.

    Exact match on normalised words first. Failing that, a word-subset match
    in either direction -- the same test Scope #1's _get_fuzzy uses -- but
    accepted ONLY when exactly one column fits. Scope #1 takes the first hit in
    dictionary order and cannot notice a second; here a second hit is a reason
    to pass the field on rather than a tie to break silently.

    One-to-one: a column claimed by two fields is claimed by neither.
    """
    exclude = set(exclude)
    pool = [c for c in columns if c.header and c.header not in exclude]
    proposed = {}

    for field in fields:
        target = words(field.label)
        if not target:
            continue
        exact = [c for c in pool if words(c.header) == target]
        if len(exact) == 1:
            proposed[field.label] = exact[0].header
            continue
        if exact:
            continue                                   # ambiguous: pass it on
        t = set(target)
        loose = [c for c in pool
                 if (cw := set(words(c.header))) and (t <= cw or cw <= t)]
        # A loose hit is only trusted when it has no RIVAL -- no other column
        # that shares even one word with the field. Found running V2: the
        # label "Final Rating 0-100" loosely matched the one-word column FINAL
        # (its only word is inside the label) and NOT the right one, FINAL
        # GRADE (whose "grade" is not). A subset test alone therefore picked
        # the decoy, uniquely and confidently -- and because lookup runs
        # before the matcher, it overrode the matcher too. Requiring no rival
        # is the matcher's own margin rule applied here: a close runner-up
        # means the name cannot tell them apart, so pass the field on.
        rivals = [c for c in pool if t & set(words(c.header))]
        if len(loose) == 1 and len(rivals) == 1:
            proposed[field.label] = loose[0].header

    claims = {}
    for field, column in proposed.items():
        claims.setdefault(column, []).append(field)

    return [TierDecision(field=f, column=c, via=VIA_LOOKUP, reason="label match")
            for f, c in proposed.items() if len(claims[c]) == 1]


# --------------------------------------------------------------------- LLM


def _numeric(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def candidate_columns(field, columns) -> list:
    """Drop columns that cannot possibly fit before the model ever sees them.

    Generic widget semantics only -- a number field with a declared range can
    only take a numeric column whose values sit inside that range. Nothing
    here names a field or a column. Fewer, better candidates is also what a
    4B model needs: the 'Arthur' failure was a near neighbour picked from a
    crowded screen.
    """
    if (field.input_type or "").lower() != "number":
        return list(columns)
    lo, hi = _numeric(field.min), _numeric(field.max)
    keep = []
    for c in columns:
        vals = [_numeric(v) for v in (c.samples or [])]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        if lo is not None and min(vals) < lo:
            continue
        if hi is not None and max(vals) > hi:
            continue
        keep.append(c)
    return keep


def _fmt(value) -> str:
    n = _numeric(value)
    if n is not None and n == int(n):
        return str(int(n))
    return str(value)


def build_prompt(field, candidates) -> str:
    """The question put to the model for one field.

    Reads ONLY what is on the page. truth_key and column_key are deliberately
    absent -- see the module docstring and test_the_prompt_never_sees_the_answer.
    """
    desc = [f'label: "{field.label}"', f"input type: {field.input_type}"]
    if field.min is not None or field.max is not None:
        desc.append(f"allowed range: {field.min} to {field.max}")
    if field.options:
        desc.append("options: " + ", ".join(str(o) for o in field.options))
    if field.placeholder:
        desc.append(f'placeholder: "{field.placeholder}"')
    if field.required:
        desc.append("required")

    lines = []
    for c in candidates:
        samples = ", ".join(_fmt(v) for v in (c.samples or [])[:4])
        lines.append(f"  - {c.header}: {samples}")

    return (
        "You are matching spreadsheet columns to fields on a web form.\n\n"
        "Form field:\n  " + "\n  ".join(desc) + "\n\n"
        "Candidate spreadsheet columns, with sample values:\n" + "\n".join(lines) + "\n\n"
        "Which ONE column holds the value for this field? If none of them "
        "clearly does, or two of them fit equally well, answer NONE rather "
        "than guessing.\n"
        "Answer with only the exact column name, or NONE."
    )


def parse_answer(text: Optional[str], candidates) -> Optional[str]:
    """The model's answer, or None. Strict on purpose.

    Only an answer that is exactly one offered column name (ignoring case,
    quotes and trailing punctuation) is accepted. Anything looser -- a
    sentence, a partial name, a column that was not offered -- counts as NONE.
    Substring matching would be worse than useless here: 'FINAL GRADE'
    contains 'FINAL', and those two are precisely the look-alikes this tier
    must not confuse.
    """
    if not text:
        return None
    answer = text.strip().splitlines()[0].strip()
    answer = re.sub(r"^(column|answer)\s*:\s*", "", answer, flags=re.I)
    answer = answer.strip(" \"'`.*").strip()
    if not answer or answer.upper() == "NONE":
        return None
    for c in candidates:
        if answer.lower() == c.header.lower():
            return c.header
    return None


def llm_tier(fields, columns, ask: Callable[[str], Optional[str]],
             exclude: Iterable[str] = ()) -> List[TierDecision]:
    """Ask the model about each field the earlier tiers could not decide.

    One question per field -- a handful per run, since Scope #2 maps COLUMNS
    once and then fills every row from that, unlike Scope #1 which has to ask
    per field per record.

    `ask` takes a prompt and returns the model's text, or None when the model
    is unreachable. Injected so tests never need LM Studio.
    """
    exclude = set(exclude)
    pool = [c for c in columns if c.header and c.header not in exclude]
    decisions = []

    for field in fields:
        candidates = candidate_columns(field, pool)
        if not candidates:
            decisions.append(TierDecision(field.label, None, VIA_LLM,
                                          "no compatible column left"))
            continue
        reply = ask(build_prompt(field, candidates))
        if reply is None:
            decisions.append(TierDecision(field.label, None, VIA_LLM, "LLM unavailable"))
            continue
        column = parse_answer(reply, candidates)
        decisions.append(TierDecision(
            field.label, column, VIA_LLM,
            "LLM matched" if column else "LLM answered NONE"))

    # One-to-one. Two fields claiming one column is the model failing to tell
    # them apart, which is exactly when a guess would be wrong half the time.
    claims = {}
    for d in decisions:
        if d.column:
            claims.setdefault(d.column, []).append(d)
    for column, ds in claims.items():
        if len(ds) > 1:
            for d in ds:
                d.column = None
                d.reason = f"refused: {column} claimed by {len(ds)} fields"
    return decisions


# -------------------------------------------------------- LM Studio client


class LMStudioAsker:
    """The real `ask`: LM Studio's OpenAI-compatible server on localhost.

    The same local server Scope #1's LLMAgent and Scope #3's classifier use.
    The model name is DISCOVERED from /v1/models rather than written here --
    Scope #3's classifier once failed on every call because it sent a
    placeholder model name. Any failure returns None, which the tier reports
    as "LLM unavailable" and leaves the field empty: a dead server must not
    stop a run whose other tiers already decided.
    """

    def __init__(self, base_url: str = "http://localhost:1234/v1", timeout: float = 60.0):
        self.base_url = base_url
        self.timeout = timeout
        self._client = None
        self._model = None
        self.error = ""

    def _connect(self) -> bool:
        if self._client is not None:
            return self._model is not None
        try:
            from openai import OpenAI
            self._client = OpenAI(base_url=self.base_url, api_key="lm-studio",
                                  timeout=self.timeout)
            ids = [m.id for m in self._client.models.list().data]
            chat = [i for i in ids if "embed" not in i.lower()]
            self._model = chat[0] if chat else None
            if not self._model:
                self.error = "no chat model loaded in LM Studio"
        except Exception as exc:  # noqa: BLE001
            self.error = f"LM Studio unreachable ({exc.__class__.__name__})"
            self._model = None
        return self._model is not None

    @property
    def model(self) -> Optional[str]:
        return self._model

    def __call__(self, prompt: str) -> Optional[str]:
        if not self._connect():
            return None
        try:
            reply = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=24,
            )
            return reply.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001
            self.error = f"LLM call failed ({exc.__class__.__name__})"
            return None


def remaining(items: Sequence, taken: Iterable[str], key) -> list:
    taken = set(taken)
    return [i for i in items if key(i) not in taken]
