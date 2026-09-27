"""How Scope #2 decides which sheet column goes into which portal field.

    source lookup  ->  LLM

WHY THIS SHAPE
--------------
Direct request: Scope #2 must be built the way Scope #1 fills its form. Scope
#1 reads a field's label, finds that label in its data source, and asks the
LLM only when the source has no match. It loads no embedding model and no
trained matcher to do it. Scope #2 now does exactly that: the source tier
(the grade sheet's own column names) decides most fields, the LLM is asked
per field only for what the source could not name, and nothing else runs.

The trained matcher (model/, features/) used to sit between the two. It is
gone from the run path because it was the only thing loading the embedding
model -- ~9 s at every Play, measured -- and Scope #1 has no such tier. The
code stays in the repo for eval/, which measures it as a research result.

Only the MAPPING decision is here. Values are still written with Playwright's
fill(), the same direct-write manoeuvre as Scope #1's WM_SETTEXT, and the
derived Remarks field is still decided by rule induction.

WHAT EACH TIER IS ALLOWED TO SEE
--------------------------------
Only what a person would see on the page: a field's label, input type,
options, placeholder, min/max and whether it is required. NEVER truth_key --
that is the portal's data-key, the answer -- and never column_key either,
because nothing guarantees it is not derived from the same.
tests/scope2/test_fallback.py pins this.

REFUSAL IS A RESULT
-------------------
Both tiers prefer leaving a field empty to guessing. The source tier only
accepts a fuzzy match when exactly one column fits, unlike Scope #1's
first-match-wins fallback. The LLM may answer NONE, an answer that is not
exactly one of the offered columns counts as NONE, and a column claimed by two
fields is refused for both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence

# "source" is Scope #1's word for the same tier, so both HUDs say the same thing.
VIA_SOURCE = "source"
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


def label_words(text: str) -> List[str]:
    """The words that NAME a field, without its format hint.

    Portal labels carry hints like 'Year 1-5' or 'Grade 0-100'; a person reads
    those as 'Year' and 'Grade'. Any token with a digit in it is a range or a
    format, never a name, so it is dropped. Generic: nothing here knows any
    particular label.
    """
    return [w for w in words(text) if not any(ch.isdigit() for ch in w)]


def lookup_tier(fields, columns, exclude: Iterable[str] = ()) -> List[TierDecision]:
    """The source tier: Scope #1's lookup, with two changes that make it safe.

    Exact match on the naming words first. Failing that, a word-subset match
    in either direction -- the same test Scope #1's _get_fuzzy uses -- but
    accepted ONLY when exactly one column fits. Scope #1 takes the first hit in
    dictionary order and cannot notice a second; here a second hit is a reason
    to pass the field to the LLM rather than a tie to break silently.

    A column is only considered if its values can go in the field at all
    (candidate_columns): a 1.00-5.00 grade field never takes a 0-100 column
    just because both are called 'grade'.

    One-to-one: a column claimed by two fields is claimed by neither.
    """
    exclude = set(exclude)
    pool = [c for c in columns if c.header and c.header not in exclude]
    proposed = {}

    for field in fields:
        target = label_words(field.label)
        if not target:
            continue
        fits = candidate_columns(field, pool)
        exact = [c for c in fits if label_words(c.header) == target]
        if len(exact) == 1:
            proposed[field.label] = exact[0].header
            continue
        if exact:
            continue                                   # ambiguous: pass it on
        t = set(target)
        loose = [c for c in fits
                 if (cw := set(label_words(c.header))) and (t <= cw or cw <= t)]
        # A loose hit is only trusted when it has no RIVAL -- no other column
        # that shares even one word with the field. Found running V2: the
        # label "Final Rating 0-100" loosely matched the one-word column FINAL
        # (its only word is inside the label) and NOT the right one, FINAL
        # GRADE (whose "grade" is not). A subset test alone therefore picked
        # the decoy, uniquely and confidently. A close runner-up means the
        # name cannot tell them apart, so the field goes to the LLM.
        rivals = [c for c in fits if t & set(label_words(c.header))]
        if len(loose) == 1 and len(rivals) == 1:
            proposed[field.label] = loose[0].header

    claims = {}
    for field, column in proposed.items():
        claims.setdefault(column, []).append(field)

    return [TierDecision(field=f, column=c, via=VIA_SOURCE, reason="label match")
            for f, c in proposed.items() if len(claims[c]) == 1]


# --------------------------------------------------------------------- LLM


def _numeric(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _all_numeric(samples) -> bool:
    """True when there are samples and every one is a number."""
    vals = [v for v in (samples or []) if v is not None and str(v).strip() != ""]
    return bool(vals) and all(_numeric(v) is not None for v in vals)


def candidate_columns(field, columns) -> list:
    """Drop columns that cannot possibly fit before the model ever sees them.

    Generic widget semantics only -- a number field with a declared range can
    only take a numeric column whose values sit inside that range. Nothing
    here names a field or a column. Fewer, better candidates is also what a
    4B model needs: the 'Arthur' failure was a near neighbour picked from a
    crowded screen.
    """
    kind = (field.input_type or "").lower()
    if kind == "textarea":
        # A multi-line box is for prose. A column of bare numbers never
        # belongs in one. Found on V2 and V6b: asked alone about
        # "Recommendations optional", the 4B model answered FINAL GRADE, and
        # a confirming question from the column's side said yes too -- shown
        # one option, it agrees with anything. Widget semantics settle it
        # before the model is asked.
        return [c for c in columns if not _all_numeric(c.samples)]
    if kind != "number":
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
    """Ask the model about each field the source could not decide.

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

    # One-to-one. Two fields claiming one column means each question, seeing
    # one field alone, could not tell which field the column really belongs
    # to. Found on V0: asked alone, "Recommendations optional" was answered
    # PROGRAM every time -- as was "Course" -- so both were refused and Course
    # went empty. Listing the other fields in the per-field prompt did not
    # fix it (still PROGRAM 3 times in 4). Asking once from the COLUMN's side,
    # with only the claimants offered, picked Course 4 times in 4. So: one
    # tie-break question per contested column; anything but exactly one
    # claimant still refuses them all.
    by_label = {f.label: f for f in fields}
    by_header = {c.header: c for c in pool}
    claims = {}
    for d in decisions:
        if d.column:
            claims.setdefault(d.column, []).append(d)
    for column, ds in claims.items():
        if len(ds) < 2:
            continue
        claimants = [by_label[d.field] for d in ds]
        reply = ask(build_tiebreak_prompt(by_header[column], claimants))
        winner = parse_field_answer(reply, claimants)
        for d in ds:
            if d.field == winner:
                d.reason = f"LLM matched ({column} contested by {len(ds)} fields)"
            else:
                d.column = None
                d.reason = (f"refused: {column} went to {winner}" if winner
                            else f"refused: {column} claimed by {len(ds)} fields")
    return decisions


def build_tiebreak_prompt(column, claimants) -> str:
    """The one question asked when several fields claimed the same column.

    Asked from the column's side, offering only the fields that claimed it.
    Same visibility rule as build_prompt: labels, types, placeholders, ranges
    -- never truth_key or column_key.
    """
    lines = []
    for f in claimants:
        bits = [f.input_type or "text"]
        if f.placeholder:
            bits.append(f'placeholder "{f.placeholder}"')
        if f.min is not None or f.max is not None:
            bits.append(f"range {f.min} to {f.max}")
        lines.append(f'  - "{f.label}" ({", ".join(bits)})')
    samples = ", ".join(_fmt(v) for v in (column.samples or [])[:4])
    return (
        "You are matching a spreadsheet column to a field on a web form.\n\n"
        f"Spreadsheet column: {column.header}\n"
        f"Sample values: {samples}\n\n"
        "Form fields that could take it:\n" + "\n".join(lines) + "\n\n"
        "Which ONE field should this column's values be written into? If none "
        "of them clearly fits, answer NONE.\n"
        "Answer with only the exact field label, or NONE."
    )


def parse_field_answer(text: Optional[str], claimants) -> Optional[str]:
    """The tie-break's answer: exactly one claimant's label, or None. As strict
    as parse_answer, for the same reason."""
    if not text:
        return None
    answer = text.strip().splitlines()[0].strip()
    answer = re.sub(r"^(field|answer)\s*:\s*", "", answer, flags=re.I)
    answer = answer.strip(" \"'`.*").strip()
    for f in claimants:
        if answer.lower() == f.label.lower():
            return f.label
    return None


# -------------------------------------------------------- LM Studio client


class LMStudioAsker:
    """The real `ask`: LM Studio's OpenAI-compatible server on this machine.

    The same local server Scope #1's LLMAgent and Scope #3's classifier use.
    The model name is DISCOVERED from /v1/models rather than written here --
    Scope #3's classifier once failed on every call because it sent a
    placeholder model name. Any failure returns None, which the tier reports
    as "LLM unavailable" and leaves the field empty: a dead server must not
    stop a run whose source tier already decided.

    Two choices made for speed, both measured on the user's machine:

      * 127.0.0.1, not "localhost". On Windows "localhost" tries IPv6 (::1)
        first; LM Studio listens on IPv4 only, so every fresh process waited
        ~2 s for that attempt to fail before falling back. 127.0.0.1: 0.01 s.
      * plain urllib, not the openai package. Importing openai alone cost
        2.2 s per run, for two small JSON requests the standard library
        already makes.

    Together those were 6 of the 7.7 s the LLM step took; the two questions
    themselves took 0.2 s each.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:1234/v1", timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._connected = None
        self._model = None
        self.error = ""

    def _request(self, path: str, body: Optional[dict] = None, timeout: Optional[float] = None):
        import json
        import urllib.request
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer lm-studio"})
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _connect(self) -> bool:
        if self._connected is not None:
            return self._connected
        try:
            ids = [m["id"] for m in self._request("/models", timeout=5).get("data", [])]
            chat = [i for i in ids if "embed" not in i.lower()]
            self._model = chat[0] if chat else None
            if not self._model:
                self.error = "no chat model loaded in LM Studio"
        except Exception as exc:  # noqa: BLE001
            self.error = f"LM Studio unreachable ({exc.__class__.__name__})"
            self._model = None
        self._connected = self._model is not None
        return self._connected

    @property
    def model(self) -> Optional[str]:
        return self._model

    def __call__(self, prompt: str) -> Optional[str]:
        if not self._connect():
            return None
        try:
            reply = self._request("/chat/completions", {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 24,
            })
            return reply["choices"][0]["message"].get("content") or ""
        except Exception as exc:  # noqa: BLE001
            self.error = f"LLM call failed ({exc.__class__.__name__})"
            return None


def remaining(items: Sequence, taken: Iterable[str], key) -> list:
    taken = set(taken)
    return [i for i in items if key(i) not in taken]
