"""
Regression tests for LLMAgent._resolve_field_value_with_escalation().

Extracted 2026-08-14 from _ask_llm()'s own blank fast path (see
tests/test_ask_llm_fast_path_lookup.py for the original incident: a naive
single lookup miss isn't trustworthy enough to conclude "blank" -- 'Account
Type' got hallucinated by the LLM three times before a fourth attempt,
using this same three-step escalation, finally got it right). Pulled out
into its own method so OPT2 batch fast-fill (a new no-model blank-skip
path, direct request "it needs to be instant" after log evidence showed a
confirmed-blank field still cost two full transformer calls) can reuse the
exact same trust bar instead of a second, independent guess at "is this
genuinely blank" that could disagree with _ask_llm's answer.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _make_agent():
    return LLMAgent(goal="test goal", dry_run=True, max_steps=1)


def test_returns_the_value_on_a_plain_lookup_hit():
    agent = _make_agent()
    agent._lookup_field = MagicMock(return_value="PAI-2026-00441")
    agent._refresh_record_cache = MagicMock()
    agent._peek_notepad = MagicMock()

    result = agent._resolve_field_value_with_escalation({}, "Policy Number", section="")

    assert result == "PAI-2026-00441"
    agent._refresh_record_cache.assert_not_called()
    agent._peek_notepad.assert_not_called()


def test_escalates_to_cache_refresh_on_a_miss_and_returns_the_recovered_value():
    agent = _make_agent()
    agent._lookup_field = MagicMock(side_effect=["", "Recovered Value"])
    agent._refresh_record_cache = MagicMock()
    agent._peek_notepad = MagicMock()

    result = agent._resolve_field_value_with_escalation({}, "Account Number", section="")

    assert result == "Recovered Value"
    agent._refresh_record_cache.assert_called_once()
    agent._peek_notepad.assert_not_called()


def test_escalates_to_notepad_peek_when_refresh_still_misses():
    agent = _make_agent()
    agent._lookup_field = MagicMock(side_effect=["", "", "Peeked Value"])
    agent._refresh_record_cache = MagicMock()
    agent._peek_notepad = MagicMock()

    result = agent._resolve_field_value_with_escalation({}, "Account Number", section="")

    assert result == "Peeked Value"
    agent._refresh_record_cache.assert_called_once()
    agent._peek_notepad.assert_called_once()


def test_returns_empty_only_once_all_three_attempts_agree():
    agent = _make_agent()
    agent._lookup_field = MagicMock(return_value="")   # every attempt comes back empty
    agent._refresh_record_cache = MagicMock()
    agent._peek_notepad = MagicMock()

    result = agent._resolve_field_value_with_escalation({}, "Account Type", section="")

    assert result == ""
    agent._refresh_record_cache.assert_called_once()
    agent._peek_notepad.assert_called_once()


def test_empty_field_name_short_circuits_with_no_lookups_at_all():
    """Mirrors _ask_llm's own '?' guard -- nothing to confidently call
    blank without a real field name, so don't even try."""
    agent = _make_agent()
    agent._lookup_field = MagicMock(return_value="")
    agent._refresh_record_cache = MagicMock()
    agent._peek_notepad = MagicMock()

    result = agent._resolve_field_value_with_escalation({}, "", section="")

    assert result == ""
    agent._lookup_field.assert_not_called()
    agent._refresh_record_cache.assert_not_called()
    agent._peek_notepad.assert_not_called()


def test_section_is_passed_through_to_every_lookup_attempt():
    agent = _make_agent()
    agent._lookup_field = MagicMock(return_value="")
    agent._refresh_record_cache = MagicMock()
    agent._peek_notepad = MagicMock()

    agent._resolve_field_value_with_escalation({}, "First Name", section="Driver 2")

    for call in agent._lookup_field.call_args_list:
        assert call.kwargs.get("section") == "Driver 2"
