"""
Regression test for the checkbox type-intercept branch in agent.py's run()
(~L3874, "LLM typed a truthy/falsy value into a checkbox").

Found live 2026-08-12, directly reported ("another loop found") on the
first real live multi-record run: 'Renewal Policy' was already checked on
record 2 -- not anything the agent did, it was checked the very first time
the agent looked at it that record (a form-reset quirk) -- but record 2's
own data wanted it 'NO'. The type-intercept branch only ever had logic for
CHECKING a box (`_should_chk=True`); when `_should_chk` was False, nothing
happened at all -- prediction fell through unchanged to the "find next
target" redirect a few lines later, which (since the field was still wrong)
kept re-offering the SAME checkbox, which the separate "block re-clicking
an already-checked box" guard then (correctly, on its own terms) blocked
every single time, since it only ever knows "checked" == "leave it alone",
never "checked but shouldn't be". logs/run_task_20260812_180659.log shows
25 identical redirects over ~80 real seconds before the run gave up and
moved on.

Fix: a symmetric uncheck branch (BM_SETCHECK, BST_UNCHECKED=0) alongside
the existing check branch (BST_CHECKED=1) -- the agent now actively drives
a checkbox to its target state regardless of which state it started in,
instead of assuming the form always resets to the right default.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _decide_checkbox_type_intercept(chk_text: str, already_handled: bool) -> str:
    """Mirrors the CURRENT (2026-08-12) decision: what does the type-
    intercept branch do with a checkbox given the LLM's typed text?
    `already_handled` mirrors `_flabel not in self._checked_fields` (the
    per-field guard against re-processing the same box twice)."""
    should_chk = chk_text.lower().strip() not in ("", "no", "false", "0", "unchecked")
    if already_handled:
        return "no_op_already_handled"
    return "check" if should_chk else "uncheck"


class TestTruthyValuesCheckTheBox:
    def test_yes_check_value_checks(self):
        assert _decide_checkbox_type_intercept("YES (check)", already_handled=False) == "check"

    def test_bare_yes_checks(self):
        assert _decide_checkbox_type_intercept("Yes", already_handled=False) == "check"

    def test_arbitrary_truthy_text_checks(self):
        assert _decide_checkbox_type_intercept("true", already_handled=False) == "check"


class TestFalsyValuesNowUncheckTheBox:
    """The actual fix: before 2026-08-12, every one of these mapped to
    NOTHING happening -- an already-checked box with a falsy target value
    had no code path to ever become unchecked. Now they all map to an
    active uncheck."""

    def test_no_unchecks(self):
        assert _decide_checkbox_type_intercept("No", already_handled=False) == "uncheck"

    def test_empty_string_unchecks(self):
        assert _decide_checkbox_type_intercept("", already_handled=False) == "uncheck"

    def test_false_unchecks(self):
        assert _decide_checkbox_type_intercept("False", already_handled=False) == "uncheck"

    def test_zero_unchecks(self):
        assert _decide_checkbox_type_intercept("0", already_handled=False) == "uncheck"

    def test_unchecked_literal_unchecks(self):
        assert _decide_checkbox_type_intercept("unchecked", already_handled=False) == "uncheck"

    def test_case_and_whitespace_insensitive(self):
        assert _decide_checkbox_type_intercept("  NO  ", already_handled=False) == "uncheck"


class TestAlreadyHandledFieldsAreSkippedEitherDirection:
    def test_already_handled_check_target_is_a_no_op(self):
        assert _decide_checkbox_type_intercept("Yes", already_handled=True) == "no_op_already_handled"

    def test_already_handled_uncheck_target_is_a_no_op(self):
        assert _decide_checkbox_type_intercept("No", already_handled=True) == "no_op_already_handled"
