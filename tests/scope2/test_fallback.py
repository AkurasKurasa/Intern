"""Scope #2's mapping tiers (resolver/fallback.py).

    source lookup  ->  LLM

Direct request: build Scope #2 the way Scope #1 fills its form -- read the
label, find it in the source, ask the LLM only for what the source cannot
name -- with no embedding model and no trained matcher, because Scope #1 has
neither.

The assertions that carry the weight:

  * neither tier ever reads truth_key or column_key
  * both tiers prefer refusing to guessing: ambiguous lookups pass the field
    on, NONE is honoured, loose answers count as NONE, a contested column
    goes to one claimant only when a tie-break names exactly one
  * a dead LLM leaves fields empty and never stops the run
  * a run never loads an embedding model or torch

Run:  python -m pytest tests/scope2/test_fallback.py -q
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent / "components" / "scope2"
sys.path.insert(0, str(REPO))

from descriptors import FieldDescriptor, SourceColumn  # noqa: E402
from resolver.fallback import (  # noqa: E402
    VIA_LLM, VIA_SOURCE, LMStudioAsker, TierDecision, build_prompt,
    build_tiebreak_prompt, candidate_columns, label_words, llm_tier, lookup_tier,
    parse_answer, parse_field_answer,
)


def fld(label, input_type="text", **kw):
    return FieldDescriptor(label=label, label_rule=3, kind="input",
                           input_type=input_type, column_key=kw.pop("column_key", "k"),
                           **kw)


def col(header, samples, inferred="text", index=0):
    return SourceColumn(header=header, index=index, inferred_type=inferred,
                        samples=list(samples))


SHEET = [
    col("No.", [1, 2, 3], "numeric"),
    col("STUDENT NUMBER", ["2021-10001", "2021-10008"], "id"),
    col("NAME OF STUDENT", ["ABAD", "AGUILAR"]),
    col("MIDTERM", [85, 95, 92], "numeric"),
    col("FINAL", [85, 96, 98], "numeric"),
    col("PROGRAM", ["BS Information Systems", "BS Accountancy"]),
    col("YEAR LEVEL", [2, 2, 3], "numeric"),
    col("FINAL GRADE", [85, 96, 95], "numeric"),
]
ALIGN = ("STUDENT NUMBER", "NAME OF STUDENT")


# ------------------------------------------------------------------ lookup


def test_lookup_takes_an_exact_name():
    ds = lookup_tier([fld("Program")], SHEET, exclude=ALIGN)
    assert [(d.field, d.column, d.via) for d in ds] == [("Program", "PROGRAM", VIA_SOURCE)]


def test_the_source_tier_is_called_source_like_scope_1():
    """Both HUDs say 'source' for the same tier."""
    assert VIA_SOURCE == "source"


def test_lookup_takes_a_unique_word_subset():
    """Scope #1's fuzzy test -- every word of the shorter inside the longer --
    but only when exactly one column passes it."""
    ds = lookup_tier([fld("Year")], SHEET, exclude=ALIGN)
    assert [(d.field, d.column) for d in ds] == [("Year", "YEAR LEVEL")]


def test_lookup_passes_an_ambiguous_field_on_instead_of_guessing():
    """Both FINAL and FINAL GRADE are word subsets of 'Final Grade Score'.
    Scope #1 would take whichever came first in dictionary order; here two
    hits mean pass it to the next tier."""
    ds = lookup_tier([fld("Final Grade Score")], SHEET, exclude=ALIGN)
    assert ds == []


def test_a_loose_hit_with_no_rival_is_taken():
    """The other side of the rule: PROGRAM is the only column that shares any
    word with 'Degree Program', so there is nothing to be ambiguous about."""
    ds = lookup_tier([fld("Degree Program")], SHEET, exclude=ALIGN)
    assert [(d.field, d.column) for d in ds] == [("Degree Program", "PROGRAM")]


def test_a_decoy_is_not_taken_just_because_it_is_the_only_subset():
    """Regression, found running V2. 'Final Rating 0-100' loosely matched the
    one-word column FINAL -- its only word is inside the label -- and did NOT
    match the right column, FINAL GRADE, whose 'grade' is not. A subset test
    alone therefore picked the decoy uniquely and confidently, and since lookup
    runs first it overrode the matcher. FINAL GRADE is a rival (it shares
    'final'), so the field must be passed on instead."""
    ds = lookup_tier([fld("Final Rating 0-100", "number", min="0", max="100")],
                     SHEET, exclude=ALIGN)
    assert ds == []

    # and the same shape one step removed: a rival makes any loose hit unsafe
    ds = lookup_tier([fld("Final Score")], SHEET, exclude=ALIGN)
    assert ds == []


def test_format_hints_are_not_part_of_a_name():
    """'Year 1-5' is read as 'Year', the way a person reads it."""
    assert label_words("Year 1-5") == ["year"]
    assert label_words("Grade 1.00-5.00") == ["grade"]
    assert label_words("Recommendations optional") == ["recommendations", "optional"]


def test_the_source_decides_most_of_v0_and_leaves_the_rest_for_the_llm():
    """V0 as the portal now scans. Before hints were ignored, plain lookup
    decided 0 of these; now it decides the four whose names ARE the sheet's,
    and leaves Course (the sheet says PROGRAM) and Recommendations (no column
    at all) for the LLM."""
    v0 = [fld("Course"), fld("Year 1-5", "number", min="1", max="5"),
          fld("Midterm 0-100", "number", min="0", max="100"),
          fld("Final 0-100", "number", min="0", max="100"),
          fld("Grade 0-100", "number", min="0", max="100"),
          fld("Recommendations optional", "textarea")]
    got = {d.field: d.column for d in lookup_tier(v0, SHEET, exclude=ALIGN)}
    assert got == {"Year 1-5": "YEAR LEVEL", "Midterm 0-100": "MIDTERM",
                   "Final 0-100": "FINAL", "Grade 0-100": "FINAL GRADE"}


def test_the_source_never_takes_a_column_whose_values_do_not_fit():
    """V6b grades on 1.00-5.00. FINAL GRADE is called 'grade' too, but its
    values (85, 96...) cannot go in that field; writing them would be wrong."""
    g = fld("Grade 1.00-5.00", "number", min="1", max="5")
    assert lookup_tier([g], SHEET, exclude=ALIGN) == []


def test_two_fields_claiming_one_column_get_neither():
    ds = lookup_tier([fld("Program"), fld("program")], SHEET, exclude=ALIGN)
    assert ds == []


def test_alignment_columns_are_never_candidates():
    """The portal PRINTS the Student ID and name; those columns align rows,
    they never feed a field."""
    ds = lookup_tier([fld("Student Number")], SHEET, exclude=ALIGN)
    assert ds == []


# ----------------------------------------------------- candidate filtering


def test_a_ranged_number_field_only_sees_numeric_columns_inside_its_range():
    year = fld("Year 1-5", "number", min="1", max="5")
    names = [c.header for c in candidate_columns(year, SHEET)]
    assert "YEAR LEVEL" in names and "No." in names
    assert "MIDTERM" not in names          # 85 > 5
    assert "PROGRAM" not in names          # not numeric


def test_a_text_field_sees_every_column():
    assert len(candidate_columns(fld("Course"), SHEET)) == len(SHEET)


def test_a_notes_box_never_sees_a_column_of_bare_numbers():
    """Found on V2 and V6b: asked alone about 'Recommendations optional', the
    4B model answered FINAL GRADE. A multi-line box is for prose."""
    names = [c.header for c in candidate_columns(fld("Notes", "textarea"), SHEET)]
    assert "PROGRAM" in names and "NAME OF STUDENT" in names
    assert not {"No.", "MIDTERM", "FINAL", "YEAR LEVEL", "FINAL GRADE"} & set(names)


# --------------------------------------------------------------- the prompt


def test_the_prompt_never_sees_the_answer():
    """The load-bearing invariant, the same one the matcher's extractor is held
    to: truth_key is the portal's data-key -- the answer -- and must have no
    effect on what the model is asked. column_key is excluded too."""
    truthful = fld("Grade 0-100", "number", min="0", max="100",
                   truth_key="grade", column_key="grade")
    blinded = fld("Grade 0-100", "number", min="0", max="100",
                  truth_key=None, column_key="zzz")
    misleading = fld("Grade 0-100", "number", min="0", max="100",
                     truth_key="recommendations", column_key="year")
    cands = candidate_columns(truthful, SHEET)
    p = build_prompt(truthful, cands)
    assert p == build_prompt(blinded, cands) == build_prompt(misleading, cands)
    assert "truth" not in p.lower()


def test_the_prompt_carries_what_is_on_the_page():
    f = fld("Remarks", "select", options=["Passed", "Failed"], placeholder="pick one")
    p = build_prompt(f, [col("FINAL GRADE", [85, 96], "numeric")])
    assert '"Remarks"' in p and "Passed, Failed" in p and "pick one" in p
    assert "FINAL GRADE: 85, 96" in p
    assert "NONE" in p


# ------------------------------------------------------------ parsing replies


@pytest.mark.parametrize("reply,expected", [
    ("FINAL GRADE", "FINAL GRADE"),
    ("final grade", "FINAL GRADE"),
    ('"FINAL GRADE".', "FINAL GRADE"),
    ("Column: FINAL GRADE", "FINAL GRADE"),
    ("**FINAL GRADE**", "FINAL GRADE"),
    ("NONE", None),
    ("none.", None),
    ("", None),
    (None, None),
    ("I think it is FINAL GRADE", None),       # a sentence is not an answer
    ("FINAL GRADES", None),                     # not an offered column
    ("GRADE", None),                            # partial name
])
def test_only_an_exact_offered_column_counts(reply, expected):
    cands = [col("FINAL", [1]), col("FINAL GRADE", [1]), col("MIDTERM", [1])]
    assert parse_answer(reply, cands) == expected


def test_final_is_never_read_out_of_final_grade():
    """Substring matching would accept 'FINAL' from 'FINAL GRADE' -- and those
    are exactly the look-alikes this tier exists to keep apart."""
    cands = [col("FINAL", [1]), col("FINAL GRADE", [1])]
    assert parse_answer("FINAL GRADE", cands) == "FINAL GRADE"
    assert parse_answer("FINAL", cands) == "FINAL"


# --------------------------------------------------------------- the LLM tier


def scripted(answers, tiebreaks=None):
    """An `ask` that answers per field label -- or, for a tie-break, per
    contested column -- and records every prompt."""
    seen = []
    tiebreaks = tiebreaks or {}

    def ask(prompt):
        seen.append(prompt)
        contested = re.search(r"Spreadsheet column: (.+)", prompt)
        if contested:
            return tiebreaks.get(contested.group(1).strip())
        label = re.search(r'label: "([^"]+)"', prompt).group(1)
        return answers.get(label)
    ask.seen = seen
    return ask


def test_llm_maps_a_field_it_is_confident_about():
    ask = scripted({"Course": "PROGRAM"})
    ds = llm_tier([fld("Course")], SHEET, ask, exclude=ALIGN)
    assert [(d.field, d.column, d.via) for d in ds] == [("Course", "PROGRAM", VIA_LLM)]


def test_llm_none_leaves_the_field_empty():
    ask = scripted({"Recommendations optional": "NONE"})
    ds = llm_tier([fld("Recommendations optional", "textarea")], SHEET, ask, exclude=ALIGN)
    assert ds[0].column is None and "NONE" in ds[0].reason


def test_a_contested_column_with_no_clear_winner_is_refused_for_both():
    """Two fields claimed FINAL and the tie-break named neither: a guess here
    would be wrong half the time."""
    ask = scripted({"Grade 0-100": "FINAL", "Grade (Recomputed)": "FINAL"},
                   tiebreaks={"FINAL": "NONE"})
    fields = [fld("Grade 0-100", "number", min="0", max="100"),
              fld("Grade (Recomputed)", "number", min="0", max="100")]
    ds = llm_tier(fields, SHEET, ask, exclude=ALIGN)
    assert all(d.column is None for d in ds)
    assert all("claimed by 2 fields" in d.reason for d in ds)


def test_a_contested_column_goes_to_the_one_field_the_tiebreak_names():
    """Found on V0: asked alone, both Course and 'Recommendations optional'
    were answered PROGRAM, so both were refused and Course went empty. Asked
    once from the column's side, the model picks Course."""
    ask = scripted({"Course": "PROGRAM", "Remarks text": "PROGRAM"},
                   tiebreaks={"PROGRAM": "Course"})
    ds = llm_tier([fld("Course"), fld("Remarks text")], SHEET, ask, exclude=ALIGN)
    got = {d.field: d.column for d in ds}
    assert got == {"Course": "PROGRAM", "Remarks text": None}
    assert "went to Course" in next(d.reason for d in ds if d.field == "Remarks text")
    assert sum("Spreadsheet column:" in p for p in ask.seen) == 1


def test_a_tiebreak_answer_must_be_exactly_one_claimant():
    a, b = fld("Course"), fld("Section")
    assert parse_field_answer('"Course".', [a, b]) == "Course"
    assert parse_field_answer("course", [a, b]) == "Course"
    assert parse_field_answer("Adviser", [a, b]) is None       # not a claimant
    assert parse_field_answer("Course or Section", [a, b]) is None
    assert parse_field_answer(None, [a, b]) is None


def test_the_tiebreak_prompt_never_sees_the_answer():
    truthful = fld("Course", truth_key="course", column_key="course")
    blinded = fld("Course", truth_key=None, column_key="zzz")
    program = SHEET[5]
    assert build_tiebreak_prompt(program, [truthful]) == build_tiebreak_prompt(program, [blinded])


def test_an_unreachable_llm_leaves_fields_empty_and_does_not_raise():
    ds = llm_tier([fld("Course")], SHEET, lambda p: None, exclude=ALIGN)
    assert ds[0].column is None and ds[0].reason == "LLM unavailable"


def test_no_compatible_column_means_the_model_is_never_asked():
    ask = scripted({})
    year = fld("Year 1-5", "number", min="1", max="5")
    ds = llm_tier([year], [col("MIDTERM", [85, 95], "numeric")], ask)
    assert ds[0].column is None and "no compatible column" in ds[0].reason
    assert ask.seen == []


def test_alignment_columns_never_reach_the_model():
    ask = scripted({"Course": "NONE"})
    llm_tier([fld("Course")], SHEET, ask, exclude=ALIGN)
    assert "STUDENT NUMBER" not in ask.seen[0]
    assert "NAME OF STUDENT" not in ask.seen[0]


def test_a_decision_becomes_the_shape_the_executor_reads():
    a = TierDecision("Course", "PROGRAM", VIA_LLM).to_assignment()
    assert a["source_header"] == "PROGRAM" and a["target_label"] == "Course"
    assert a["via"] == VIA_LLM and a["status"] == "auto"


# -------------------------------------------------------- the real client


def test_the_real_client_degrades_to_none_when_nothing_is_listening():
    """A dead server must cost the run nothing but the LLM tier. Port 9 is the
    discard port; nothing answers an HTTP request there."""
    asker = LMStudioAsker(base_url="http://127.0.0.1:9/v1", timeout=2)
    assert asker("anything") is None
    assert asker.model is None
    assert "unreachable" in asker.error


def test_the_model_name_is_discovered_not_written_down():
    """Scope #3's classifier once failed every call on a placeholder model
    name. Nothing in this module may name a model."""
    src = (REPO / "resolver" / "fallback.py").read_text(encoding="utf-8")
    assert '"/models"' in src
    assert "gemma" not in src.lower() and "local-model" not in src


def test_the_client_is_cheap_to_start():
    """Measured: 'localhost' cost ~2 s per fresh process on Windows (IPv6 tried
    first) and importing openai cost 2.2 s -- 6 of the LLM step's 7.7 s.
    The two questions themselves took 0.2 s each."""
    assert LMStudioAsker().base_url.startswith("http://127.0.0.1:")
    src = (REPO / "resolver" / "fallback.py").read_text(encoding="utf-8")
    assert "import openai" not in src and "from openai" not in src


# -------------------------------------------------------------- end to end


def _automate(*args, variant="v0_base"):
    out = subprocess.run(
        [sys.executable, "-u", str(REPO / "automate.py"), "--variant", variant,
         "--limit", "2", *args],
        capture_output=True, text=True, cwd=str(REPO), timeout=900)
    return re.sub(r"\x1b\[[0-9;]*m", "", out.stdout + out.stderr), out.returncode


def test_a_run_never_loads_an_embedding_model_or_torch():
    """Scope #1 loads no embedding model, so neither may Scope #2. The model
    load was ~9 s of every Play before this change. (features.encoders may be
    imported as a module -- it only loads the model when called -- and
    rules/options.py no longer calls it.)"""
    probe = ("import sys; sys.path.insert(0, r'%s'); import automate; "
             "bad = [m for m in ('sentence_transformers', 'torch', 'transformers', "
             "'model.matcher', 'openai') if m in sys.modules]; "
             "print('LOADED', bad)" % REPO)
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, cwd=str(REPO), timeout=300)
    assert "LOADED []" in out.stdout, out.stdout + out.stderr


def test_nothing_on_the_run_path_calls_the_embedding_model():
    """Static check on every module a run imports from this scope."""
    for rel in ("automate.py", "resolver/fallback.py", "rules/options.py",
                "rules/rebind.py", "executor/runner.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "encoders." not in src, rel


def test_there_is_no_countdown():
    """The 5 s countdown was a fifth of the wait before the portal appeared and
    bought nothing: this script drives its own browser, not the user's."""
    src = (REPO / "automate.py").read_text(encoding="utf-8")
    assert "COUNTDOWN" not in src


@pytest.mark.slow
def test_v0_is_mostly_source_without_the_llm():
    if not (REPO / "data" / "sheets" / "grade_sheet.xlsx").exists():
        pytest.skip("run data/sheets/make_sheets.py first")
    text, code = _automate("--no_llm")
    assert code == 0, text[-2000:]
    assert "decided by: source 4, LLM 0" in text
    for pair in (r"YEAR LEVEL\s+-> Year 1-5", r"MIDTERM\s+-> Midterm 0-100",
                 r"FINAL\s+-> Final 0-100", r"FINAL GRADE\s+-> Grade 0-100"):
        assert re.search(pair, text), pair
    assert "filled by rule: Remarks (from Grade 0-100)" in text
    assert "2 rows filled and verified, 0 failed" in text


@pytest.mark.slow
@pytest.mark.parametrize("variant", ["v2_relabeled", "v4_unassociated", "v6b_scale"])
def test_every_variant_fills_and_verifies(variant):
    """The three that crashed before, each for its own reason: V2 renames
    Student ID and Remarks (rule and row alignment named V0's labels), V4 has
    no accessible names (the column fallback was one cell to the right), V6b's
    grade takes a different scale (the rule's input fills no field)."""
    if not (REPO / "data" / "sheets" / "grade_sheet.xlsx").exists():
        pytest.skip("run data/sheets/make_sheets.py first")
    text, code = _automate("--no_llm", variant=variant)
    assert code == 0, text[-2000:]
    assert "2 rows filled and verified, 0 failed" in text


@pytest.mark.slow
def test_the_llm_runs_end_to_end_when_lm_studio_is_up():
    """Only meaningful with LM Studio serving a model; skipped otherwise. On
    V0 the source leaves Course and 'Recommendations optional'. The right
    answers: PROGRAM -> Course, and nothing for Recommendations."""
    if LMStudioAsker(timeout=3)("ping") is None:
        pytest.skip("LM Studio is not serving a chat model")
    text, code = _automate()
    assert code == 0, text[-2000:]
    assert "asking the LLM about 2 field(s)" in text
    assert re.search(r"PROGRAM\s+-> Course\s+LLM", text)
    assert "decided by: source 4, LLM 1" in text
    assert "2 rows filled and verified, 0 failed" in text
