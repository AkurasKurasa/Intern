"""
Regression test for the `scope1_leave_blank_bug` Task Tree item: a record
value of "(leave blank)" (or "(none)", "n/a", etc.) could get typed into a
field LITERALLY, instead of being recognized as "skip this field."

Root cause, found 2026-08-10 while working through the Task Tree's open
Scope #1 items: SIX separate call sites in agent.py each carried their own
hardcoded set like {"(none)", "none", "(leave blank)", "n/a"} -- but each
one checked membership via `value.lower().strip("()")` (parens stripped off
the VALUE) against a set whose entries still HAD their parens
(`"(leave blank)"`, not `"leave blank"`). `"leave blank" in {"(leave
blank)", ...}` is False -- the stripped value can never match the unstripped
set entry, so the skip-check silently failed and the placeholder text
reached the fill path.

One correct implementation already existed the whole time --
`_lookup_field`'s own local `_is_blank` helper, which uses a prefix match
(`n.startswith("leave blank")`) instead of exact-set-membership, so it was
never vulnerable to this exact bug. Promoted to a module-level function,
`_is_leave_blank_value`, and pointed all 8 call sites (the 6 broken ones,
plus 2 that already worked via a different strip-both-sides pattern, plus
_lookup_field's own copy) at the single shared implementation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import _is_leave_blank_value


class TestIsLeaveBlankValue:
    def test_true_for_parenthesized_leave_blank_the_exact_live_bug_shape(self):
        """The literal value this bug let slip through untouched."""
        assert _is_leave_blank_value("(leave blank)") is True

    def test_true_for_leave_blank_with_a_trailing_note(self):
        assert _is_leave_blank_value("leave blank — liability only") is True
        assert _is_leave_blank_value("(leave blank — owned outright)") is True
        assert _is_leave_blank_value("leave blank - some future reason never seen before") is True

    def test_true_for_none_and_na_variants(self):
        assert _is_leave_blank_value("(none)") is True
        assert _is_leave_blank_value("none") is True
        assert _is_leave_blank_value("n/a") is True
        assert _is_leave_blank_value("NA") is True

    def test_true_for_none_with_a_trailing_note(self):
        assert _is_leave_blank_value("none - not applicable to this policy") is True

    def test_true_for_empty_or_missing(self):
        assert _is_leave_blank_value("") is True
        assert _is_leave_blank_value(None) is True

    def test_false_for_a_real_value(self):
        assert _is_leave_blank_value("James") is False
        assert _is_leave_blank_value("PAI-2026-00441") is False

    def test_false_for_yes_check_not_a_blank_marker(self):
        """'yes (check)' means 'check this checkbox' -- an actionable value,
        not a leave-blank marker. Call sites that skip on this must keep
        checking for it SEPARATELY from _is_leave_blank_value."""
        assert _is_leave_blank_value("yes (check)") is False

    def test_false_for_a_real_value_that_merely_contains_parens(self):
        assert _is_leave_blank_value("Settlement Amount ($5,000)") is False


class TestOldBrokenPatternWouldHaveFailedTheseSameCases:
    """Documents exactly why the old per-call-site sets were broken --
    stripping the value but not the set it's compared against."""

    def test_old_pattern_fails_where_the_new_one_succeeds(self):
        old_broken_set = {"(none)", "none", "(leave blank)", "n/a"}
        value = "(leave blank)"
        old_result = value.lower().strip("()") in old_broken_set
        assert old_result is False, "sanity check: reproducing the actual historical bug"

        new_result = _is_leave_blank_value(value)
        assert new_result is True, "the shared helper correctly catches what the old sets missed"
