"""
Regression test for _option_matches() — the shared combobox dropdown-item
fuzzy matcher used by all three combobox handlers in agent.py.

Found live 2026-08-07: a run looped 25+ times opening the "Body Type"
dropdown, never finding a match for 'Sedan', closing it, and trying again.
The matching logic only checked exact equality and prefix (either
direction) — neither catches 'Sedan' vs a real option like '4-Door Sedan'
(the wanted value sits at the END, not the start). Fixed by adding a third
tier: whole-word-token containment, without ever turning into a raw
substring check (which would incorrectly match 'Active' against
'Inactive' — an explicit constraint from when this matching was first
written, still upheld here).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import _option_matches, _tokenize_option


class TestExactAndPrefixTiersStillWork:
    def test_exact_match(self):
        assert _option_matches("Active", "Active") is True

    def test_case_and_whitespace_insensitive(self):
        assert _option_matches("  active ", "ACTIVE") is True

    def test_prefix_match_option_extends_value(self):
        assert _option_matches("Full Coverage", "Full Coverage (Comprehensive)") is True

    def test_prefix_match_value_extends_option(self):
        assert _option_matches("Full Coverage (Comprehensive)", "Full Coverage") is True

    def test_no_match_for_unrelated_strings(self):
        assert _option_matches("Sport 2.0T", "Base 1.5L") is False


class TestWholeWordTokenContainment:
    def test_matches_the_real_live_failure_case(self):
        assert _option_matches("Sedan", "4-Door Sedan") is True

    def test_matches_when_wanted_value_is_a_middle_word(self):
        assert _option_matches("Door", "4-Door Sedan") is True

    def test_matches_multi_word_value_as_contiguous_subsequence(self):
        assert _option_matches("4 Door", "4-Door Sedan") is True

    def test_does_not_match_out_of_order_words(self):
        assert _option_matches("Sedan Door", "4-Door Sedan") is False

    def test_never_matches_active_against_inactive(self):
        """The explicit constraint this matcher must never violate."""
        assert _option_matches("Active", "Inactive") is False
        assert _option_matches("Inactive", "Active") is False

    def test_never_matches_partial_word_fragments(self):
        # "act" is a substring of "inactive" but not a whole token — must not match.
        assert _option_matches("act", "Inactive") is False

    def test_empty_value_never_matches(self):
        assert _option_matches("", "Sedan") is False

    def test_empty_option_never_matches(self):
        assert _option_matches("Sedan", "") is False


class TestTokenize:
    def test_splits_on_non_alphanumeric(self):
        assert _tokenize_option("4-Door Sedan") == ["4", "door", "sedan"]

    def test_collapses_repeated_separators(self):
        assert _tokenize_option("Full  Coverage (Comprehensive)") == [
            "full", "coverage", "comprehensive",
        ]

    def test_single_word_stays_one_token(self):
        assert _tokenize_option("Inactive") == ["inactive"]
