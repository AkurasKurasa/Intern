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


def test_falls_through_to_the_llm_when_the_lookup_finds_nothing():
    agent = _make_agent()
    agent._lookup_field = MagicMock(return_value="")   # every attempt comes back empty
    agent._refresh_record_cache = MagicMock()
    agent._peek_notepad = MagicMock()
    agent.provider = "lmstudio"
    agent._call_openai_compat = MagicMock(return_value={"action_type": "wait"})
    agent._llm_client = MagicMock()

    result = agent._ask_llm(_state_with_focused_field("Some Unmapped Field"))

    agent._call_openai_compat.assert_called_once()
    assert result == {"action_type": "wait"}
