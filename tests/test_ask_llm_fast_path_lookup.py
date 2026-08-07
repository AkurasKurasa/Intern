"""
Regression test for LLMAgent._ask_llm()'s direct-lookup fast path.

Found 2026-08-07: _ask_llm() already computed the correct value for the
focused field via a fast, direct, non-network _lookup_field() call (with
refresh/peek fallbacks) — every logged run that night showed 100% value
accuracy, meaning this lookup was already right every single time. But the
code then handed that exact value to the LLM as a prompt hint ("use EXACTLY
this string... do NOT invent") and paid a full ~5s network round-trip just
to have it echoed back, on every single type/fill step.

Fix: when the direct lookup already has a confident answer, return it
immediately — skip the LLM call entirely. Only fields the record genuinely
doesn't answer (blank/derived/ambiguous — the actual reason to consult the
LLM at all) still make the network call.

SECOND FAST PATH, found live 2026-08-08 ("there seems to be a loop of some
kind"): "still empty after three independent lookup attempts" turned out to
be a bad reason to ask the LLM too. Traced a real run: 'Account Type' (a
blank ACH field on a Credit Card record) got asked about 4 times in a row --
the LLM answered 'Full Coverage' (hallucinated, bled over from Policy Type)
three times before finally saying "leave blank" on the fourth try. ~15s and
3 wrong answers for a field the deterministic lookup had already confirmed,
three separate ways, had no data. If cache lookup, refresh+retry, and direct
peek+retry all agree there's nothing there, that's the same confidence level
as the truthy fast path above -- skip the LLM and return leave-blank
directly instead of asking (unreliably) every single visit.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _state_with_focused_field(label: str) -> dict:
    return {
        "focused_element_id": "e1",
        "elements": [
            {"element_id": "e1", "type": "editcontrol", "label": label, "text": label, "value": ""},
        ],
    }


def _make_agent():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1)
    agent._detect_section = MagicMock(return_value="")
    agent._cached_record = {}
    return agent


def test_skips_the_llm_call_when_the_lookup_already_has_the_answer():
    agent = _make_agent()
    agent._lookup_field = MagicMock(return_value="PAI-2026-00441")
    agent._llm_client = MagicMock()   # would fail this test if actually called

    result = agent._ask_llm(_state_with_focused_field("Policy Number"))

    assert result == {"action_type": "type", "text": "PAI-2026-00441", "_fast_path": "lookup"}
    agent._llm_client.chat.completions.create.assert_not_called()


def test_skips_the_llm_call_when_the_lookup_confirms_the_field_is_blank():
    """The actual bug: three independent lookup attempts (cache, refresh,
    peek) all agreeing on '' must be trusted as "genuinely blank", not
    treated as "ambiguous, ask the LLM" -- asking turned out unreliable."""
    agent = _make_agent()
    agent._lookup_field = MagicMock(return_value="")   # every attempt comes back empty
    agent._refresh_record_cache = MagicMock()
    agent._peek_notepad = MagicMock()
    agent._llm_client = MagicMock()   # would fail this test if actually called

    result = agent._ask_llm(_state_with_focused_field("Account Type"))

    assert result == {"action_type": "type", "text": "", "_fast_path": "lookup_blank"}
    agent._llm_client.chat.completions.create.assert_not_called()


def test_blank_fast_path_result_is_recognized_as_leave_blank():
    """Wires directly into the pre-merge leave-blank check
    (execution_payment_tab_oscillation_fix) -- confirms the fast path's
    output is actually usable by that check, not just superficially
    similar in shape."""
    from agent.agent import _is_leave_blank_prediction
    agent = _make_agent()
    agent._lookup_field = MagicMock(return_value="")
    agent._refresh_record_cache = MagicMock()
    agent._peek_notepad = MagicMock()
    agent._llm_client = MagicMock()

    llm_action = agent._ask_llm(_state_with_focused_field("Account Type"))

    assert llm_action.get("action_type") in ("type", "keyboard")
    assert _is_leave_blank_prediction(
        {"action_type": "keyboard", "text": llm_action.get("text", "")}) is True


def test_still_falls_through_to_the_llm_when_the_field_name_is_unknown():
    """If there's no real field name at all (no label/text on the focused
    element), there's nothing to confidently call "blank" -- must still
    ask the LLM, matching the pre-existing '?' guard."""
    agent = _make_agent()
    agent._lookup_field = MagicMock(return_value="")
    agent._refresh_record_cache = MagicMock()
    agent._peek_notepad = MagicMock()
    agent.provider = "lmstudio"
    agent._call_openai_compat = MagicMock(return_value={"action_type": "wait"})
    agent._llm_client = MagicMock()

    state = {
        "focused_element_id": "e1",
        "elements": [{"element_id": "e1", "type": "editcontrol", "label": "", "text": "", "value": ""}],
    }
    result = agent._ask_llm(state)

    agent._call_openai_compat.assert_called_once()
    assert result == {"action_type": "wait"}
