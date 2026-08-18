"""
Experimental "deep reasoning" tier for LLMAgent._ask_llm() (branch
experiment/reasoning-ladder).

Today the agent has exactly two speeds: fast-fill (skips the Transformer
and LLM entirely, for fields whose value is already known for sure -- kept
completely untouched by this change) and the normal pipeline (Transformer
picks WHERE, LLM decides WHAT, every remaining step, always the same
prompt regardless of how confident the Transformer actually was).

_MED_CONF = 0.50 already existed in agent.py's per-step routing block but
was never read anywhere -- a placeholder for exactly this kind of tier
that was never wired up. This adds the wiring: when the Transformer's own
confidence for this step (t_conf) is below _MED_CONF, _ask_llm() is asked
to reason more carefully via a new deep=True argument, which adds one
explicit "take extra care" instruction to the prompt. When the
Transformer is confident (t_conf >= _MED_CONF, the common case), nothing
changes at all -- the prompt built for deep=False must be byte-identical
to what _ask_llm() already sent today.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent

_AGENT_PY = Path(__file__).resolve().parent.parent / "components" / "agent" / "agent.py"
_AGENT_SOURCE = _AGENT_PY.read_text(encoding="utf-8")


def _state_with_unknown_focused_field() -> dict:
    # No label/text on the focused element -- _fn stays "?", so _ask_llm's
    # own direct-lookup fast paths can't apply and it falls all the way
    # through to the real LLM call, same trick test_ask_llm_fast_path_lookup.py
    # already uses to reach that call in a test.
    return {
        "focused_element_id": "e1",
        "elements": [
            {"element_id": "e1", "type": "editcontrol", "label": "", "text": "", "value": ""},
        ],
    }


def _make_agent():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1)
    agent._detect_section = MagicMock(return_value="")
    agent._cached_record = {}
    agent.provider = "lmstudio"
    agent._llm_client = MagicMock()
    return agent


class TestDeepReasoningPromptContent:
    def test_deep_true_adds_a_low_confidence_instruction_to_the_prompt(self):
        agent = _make_agent()
        captured = {}
        agent._call_openai_compat = lambda user_msg: captured.setdefault("user_msg", user_msg) or {"action_type": "wait"}

        agent._ask_llm(_state_with_unknown_focused_field(), deep=True)

        assert "LOW CONFIDENCE" in captured["user_msg"]

    def test_deep_false_prompt_has_no_low_confidence_instruction(self):
        agent = _make_agent()
        captured = {}
        agent._call_openai_compat = lambda user_msg: captured.setdefault("user_msg", user_msg) or {"action_type": "wait"}

        agent._ask_llm(_state_with_unknown_focused_field(), deep=False)

        assert "LOW CONFIDENCE" not in captured["user_msg"]

    def test_deep_defaults_to_false_so_normal_calls_are_unaffected(self):
        """The whole safety guarantee for the common case: calling
        _ask_llm(state) with no deep argument at all -- exactly how every
        existing call site invokes it today -- must produce the exact same
        prompt as calling it with deep=False explicitly."""
        agent = _make_agent()
        captured_default, captured_explicit = {}, {}

        agent._call_openai_compat = lambda user_msg: captured_default.setdefault("user_msg", user_msg) or {"action_type": "wait"}
        agent._ask_llm(_state_with_unknown_focused_field())

        agent._call_openai_compat = lambda user_msg: captured_explicit.setdefault("user_msg", user_msg) or {"action_type": "wait"}
        agent._ask_llm(_state_with_unknown_focused_field(), deep=False)

        assert captured_default["user_msg"] == captured_explicit["user_msg"]


class TestCallSiteRoutesOnTransformerConfidence:
    """Source-level regression test, same pattern as
    TestDisableTransformerDoesNotAffectNormalMode in
    test_ablation_disable_transformer.py -- the real per-step loop is one
    giant method, too costly to drive end-to-end just to check which
    branch fires, so this asserts the wiring directly in the source
    instead of executing the whole loop."""

    def test_llm_branch_computes_deep_from_med_conf_and_passes_it_through(self):
        anchor = "elif self._llm_client and t_conf < _HIGH_CONF:"
        idx = _AGENT_SOURCE.index(anchor)
        window = _AGENT_SOURCE[idx:idx + 700]
        assert "_MED_CONF" in window, "call site must reference the existing _MED_CONF threshold"
        assert "deep=" in window, "call site must pass deep= through to _ask_llm"

    def test_med_conf_constant_is_unchanged_at_0_50(self):
        # Confirms this change reuses the existing threshold rather than
        # inventing a new one -- _MED_CONF already existed, unused, before
        # this branch.
        assert "_MED_CONF    = 0.50" in _AGENT_SOURCE
