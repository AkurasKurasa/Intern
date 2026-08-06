"""
Regression test for LLMAgent._call_openai_compat()'s request parameters.

Found 2026-08-07 while diagnosing why live runs felt slow: every LM Studio
call requested max_tokens=2048 for what's a short field-value decision, not
open-ended generation, and the CoT instruction only asked for "brief"
reasoning with no length bound — an unbounded ceiling that only mattered if
the model rambled. Tightened the instruction to "1-2 short sentences" and
capped max_tokens at 512 to match, so the cap can't truncate a verbose
<think> block before the JSON line is ever emitted.

Locks in both values so they don't silently drift back to the slower
defaults.

Also locks in a second fix from the same investigation: the system prompt
used to get a random [sid:...] tag appended on every call specifically to
defeat LM Studio's prompt-cache reuse. That line was bundled into an
unrelated commit (OCR-cache scoping) with no diagnosed bug behind it, while
measurably forcing the ~750-token system prompt to be fully reprocessed on
every single call (~5s/call regardless of max_tokens — the real bottleneck,
not output length). Removed so the identical prefix can actually be cached
across calls within a run.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _make_agent_with_mock_client():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1)
    agent._system_prompt = "SYSTEM PROMPT TEXT"
    agent._llm_model = "test-model"

    fake_response = MagicMock()
    fake_response.choices[0].message.content = json.dumps({"action_type": "wait"})
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response
    agent._llm_client = fake_client
    return agent, fake_client


def test_max_tokens_is_capped_at_512_not_2048():
    agent, fake_client = _make_agent_with_mock_client()

    agent._call_openai_compat("user message")

    _, kwargs = fake_client.chat.completions.create.call_args
    assert kwargs["max_tokens"] == 512


def test_reasoning_instruction_bounds_the_think_block_length():
    agent, fake_client = _make_agent_with_mock_client()

    agent._call_openai_compat("user message")

    _, kwargs = fake_client.chat.completions.create.call_args
    system_msg = kwargs["messages"][0]["content"]
    assert "1-2 short sentences" in system_msg


def test_system_prompt_is_identical_across_calls_so_it_can_be_cached():
    # The whole point of removing the [sid:...] tag: repeated calls within a
    # run must send byte-identical system prompts, or the server has nothing
    # stable to cache against.
    agent, fake_client = _make_agent_with_mock_client()

    agent._call_openai_compat("first user message")
    first_system_msg = fake_client.chat.completions.create.call_args[1]["messages"][0]["content"]

    agent._call_openai_compat("second, different user message")
    second_system_msg = fake_client.chat.completions.create.call_args[1]["messages"][0]["content"]

    assert first_system_msg == second_system_msg
    assert "[sid:" not in first_system_msg
