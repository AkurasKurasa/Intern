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
        window = _AGENT_SOURCE[idx:idx + 1700]
        assert "_MED_CONF" in window, "call site must reference the existing _MED_CONF threshold"
        assert "deep=" in window, "call site must pass deep= through to _ask_llm"

    def test_llm_branch_also_considers_existing_streak_state(self):
        """Option C, direct request: a single step's raw t_conf isn't the
        only trigger -- real log evidence showed the general LLM branch is
        rarely reached at all (OPT2's own specialized handlers resolve
        most low-confidence clicks directly, never reaching this branch).
        For the steps that DO reach here, deep reasoning should also fire
        when the agent has already been struggling on nearby steps
        (_lowconf_fallback_streak / _reclick_streak, both pre-existing
        escalation counters), not just on this exact step's isolated
        number -- reusing existing state instead of adding a new signal."""
        anchor = "elif self._llm_client and t_conf < _HIGH_CONF:"
        idx = _AGENT_SOURCE.index(anchor)
        window = _AGENT_SOURCE[idx:idx + 1700]
        assert "_lowconf_fallback_streak" in window or "_reclick_streak" in window, (
            "call site must also consider existing streak state, not just this step's raw t_conf"
        )

    def test_med_conf_constant_is_unchanged_at_0_50(self):
        # Confirms this change reuses the existing threshold rather than
        # inventing a new one -- _MED_CONF already existed, unused, before
        # this branch.
        assert "_MED_CONF    = 0.50" in _AGENT_SOURCE


def _state_with_focused_field(label: str) -> dict:
    return {
        "focused_element_id": "e1",
        "elements": [
            {"element_id": "e1", "type": "editcontrol", "label": label, "text": label, "value": ""},
        ],
    }


class TestDeepReasoningReachesConfirmedBlankFields:
    """Live-run finding, direct request ('Find a way to make it fire'):
    _ask_llm()'s own 'confirmed blank' fast path (three independent lookup
    attempts all agreeing a field has no value) returns BEFORE deep is
    ever read -- for any field with a real label (nearly every real
    field), this fires ahead of the deep-reasoning code entirely,
    regardless of confidence or streak state. That fast path exists for a
    real reason (asking the LLM repeatedly about a blank field used to
    make it hallucinate), so deep=True skips ONLY that one shortcut, not
    the 'value found' shortcut above it -- a confidently-known value still
    never needs reasoning, deep or not."""

    def test_deep_true_skips_the_confirmed_blank_shortcut_and_asks_the_llm(self):
        agent = _make_agent()
        agent._lookup_field = MagicMock(return_value="")   # every attempt comes back empty
        agent._refresh_record_cache = MagicMock()
        agent._peek_notepad = MagicMock()
        captured = {}
        agent._call_openai_compat = lambda user_msg: captured.setdefault("user_msg", user_msg) or {"action_type": "wait"}

        result = agent._ask_llm(_state_with_focused_field("Account Type"), deep=True)

        assert result != {"action_type": "type", "text": "", "_fast_path": "lookup_blank"}
        assert "user_msg" in captured, "deep=True must reach the real LLM call, not the blank fast path"
        assert "LOW CONFIDENCE" in captured["user_msg"]

    def test_deep_false_still_uses_the_confirmed_blank_shortcut_unchanged(self):
        """The safety guarantee: this exact scenario (the one that caused
        the original hallucination bug) must be byte-for-byte unaffected
        when deep is left at its default False."""
        agent = _make_agent()
        agent._lookup_field = MagicMock(return_value="")
        agent._refresh_record_cache = MagicMock()
        agent._peek_notepad = MagicMock()
        agent._llm_client = MagicMock()   # would fail this test if actually called

        result = agent._ask_llm(_state_with_focused_field("Account Type"))

        assert result == {"action_type": "type", "text": "", "_fast_path": "lookup_blank"}
        agent._llm_client.chat.completions.create.assert_not_called()

    def test_deep_true_does_not_affect_the_confident_value_shortcut(self):
        """The other fast path (a value WAS found) stays untouched by deep
        -- a confidently-known value never needs reasoning, regardless of
        how the agent got here."""
        agent = _make_agent()
        agent._lookup_field = MagicMock(return_value="PAI-2026-00441")
        agent._llm_client = MagicMock()

        result = agent._ask_llm(_state_with_focused_field("Policy Number"), deep=True)

        assert result == {"action_type": "type", "text": "PAI-2026-00441", "_fast_path": "lookup"}
        agent._llm_client.chat.completions.create.assert_not_called()


class TestComboboxClickFillDefersLowConfidenceToTheLLM:
    """The last known gap, direct request ('let's try that'): OPT2's own
    empty-combobox click-to-fill shortcut (found live -- 'Policy Type' at
    conf=0.37) resolves the VALUE via a plain self._lookup_field() call
    with no escalation and no confidence awareness at all, fully
    bypassing _ask_llm() (and therefore deep) regardless of how confident
    the Transformer was about the click. Surgical fix: only the VALUE
    SOURCE changes when confidence/streak state says this step deserves
    care -- the click, dropdown-open, mark-attempted, leave-blank, and
    repeat-guard fingerprinting mechanics around it (each with real,
    hard-won fix history of its own) stay completely untouched."""

    def test_combobox_fill_source_references_ask_llm_deep_when_low_confidence(self):
        anchor = "logger.info(\"[OPT2] CLICK on empty combobox %r"
        idx = _AGENT_SOURCE.index(anchor)
        window = _AGENT_SOURCE[idx:idx + 2900]
        assert "_ask_llm" in window, "must consult the LLM instead of only the plain lookup"
        assert "deep=True" in window, "must ask the LLM to reason carefully, not a normal call"
        assert "_MED_CONF" in window or "_lowconf_fallback_streak" in window or "_reclick_streak" in window, (
            "must gate the deferral on the same confidence/streak signal Option C already uses, "
            "not defer on every combobox fill"
        )

    def test_combobox_fill_overrides_focused_element_id_to_the_combobox_itself(self):
        """Real bug found live, direct report ("Policy Type" got filled
        with a completely different field's value, 'YES (check)', which
        wasn't even a valid option): _cbox is found by matching the
        Transformer's click COORDINATES against elements, independent of
        state["focused_element_id"] -- the OS-reported focus can (and did)
        point at a different field entirely at the moment this branch
        runs, since the click on _cbox hasn't happened yet. _ask_llm()
        derives "the focused field" purely from state["focused_element_id"],
        so calling it with the original state asks about the wrong field.
        The deferred call must override focused_element_id to _cbox's own
        element_id before calling _ask_llm, not pass state through as-is."""
        anchor = "logger.info(\"[OPT2] CLICK on empty combobox %r"
        idx = _AGENT_SOURCE.index(anchor)
        window = _AGENT_SOURCE[idx:idx + 2900]
        assert "_cbox.get(\"element_id\")" in window or "_cbox.get('element_id')" in window, (
            "must override focused_element_id to _cbox's own id before calling _ask_llm, "
            "not let it fall back to whatever state already reports as focused"
        )
        # The override must happen on a COPY, never mutate the real state
        # dict other code in this same step still reads afterward.
        assert "dict(state)" in window, "must build a copy of state, not mutate the shared state dict in place"
