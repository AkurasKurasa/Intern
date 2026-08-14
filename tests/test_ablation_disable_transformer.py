"""
Regression tests for the disable_transformer ablation flag.

Built 2026-08-14, direct request ("I have to verify that if we don't
have the model we're using right now the performance would drop") after
tonight's log evidence showed 0 real transformer calls across two full,
successfully-completed 176-field records -- meaning the model currently
contributes nothing on this form, but that was an inference from reading
logs, not a real test. This flag makes it a real test: with
disable_transformer=True, LLMAgent._predict() never calls the real model
at all -- it returns a synthetic zero-confidence result instead.

Every key in that synthetic result already has a `.get(key, default)`
on the reading side elsewhere in agent.py (confirmed by reading every
t_pred access before writing this), so a zero-confidence result routes
through the SAME low-confidence Tab-fallback path the real model already
triggers whenever its OWN confidence is low
(_gate_low_confidence_click, "[OPT2] pointer low-confidence ... Tab
fallback") -- not a new fallback invented for this test, just the
existing one forced to fire every time instead of rarely.

The actual comparison (does a live run still complete correctly with
the model off, and how much slower/faster) is something only a real
run can answer -- per this project's standing rule, that's the user's
call to make, not something these tests attempt.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent

_AGENT_PY = Path(__file__).resolve().parent.parent / "components" / "agent" / "agent.py"
_AGENT_SOURCE = _AGENT_PY.read_text(encoding="utf-8")
_RUN_TASK_PY = Path(__file__).resolve().parent.parent / "run_task.py"
_RUN_TASK_SOURCE = _RUN_TASK_PY.read_text(encoding="utf-8")


def _make_agent(disable_transformer=True):
    return LLMAgent(goal="test goal", dry_run=True, max_steps=1,
                     disable_transformer=disable_transformer)


class TestDisableTransformerDefaultsOff:
    def test_default_construction_leaves_the_real_model_active(self):
        agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1)
        assert agent._disable_transformer is False

    def test_skip_counter_starts_at_zero(self):
        agent = _make_agent()
        assert agent._ablation_transformer_calls_skipped == 0


class TestDisableTransformerSkipsTheRealModel:
    def test_predict_returns_without_ever_touching_the_real_checkpoint(self):
        """The real transformer.predict() would try to load a model
        checkpoint from disk and run torch inference -- if this raised
        or hung, the real model was being reached. Succeeding instantly
        against an empty state proves the real path was never entered."""
        agent = _make_agent(disable_transformer=True)
        result = agent._predict({"elements": []})
        assert isinstance(result, dict)

    def test_returns_a_genuine_zero_confidence_result(self):
        agent = _make_agent(disable_transformer=True)
        result = agent._predict({"elements": []})
        assert result["action_type"] == "no_op"
        assert result["confidence"] == 0.0
        assert result["_click_conf"] == 0.0
        assert result["click_position"] is None
        assert result["_scores"] == {}

    def test_every_key_downstream_code_reads_is_present(self):
        """Matches every t_pred.get(...)/t_pred[...] key actually read
        elsewhere in this file -- confirmed by grep before writing this
        test, not assumed. Missing one would silently fall through to a
        caller's OWN default, which is fine for .get() calls but this
        confirms the stub is a complete, honest stand-in either way."""
        agent = _make_agent(disable_transformer=True)
        result = agent._predict({"elements": []})
        for key in ("action_type", "confidence", "_scores", "click_position", "_click_conf"):
            assert key in result

    def test_skip_counter_increments_once_per_call(self):
        agent = _make_agent(disable_transformer=True)
        agent._predict({"elements": []})
        agent._predict({"elements": []})
        agent._predict({"elements": []})
        assert agent._ablation_transformer_calls_skipped == 3


class TestDisableTransformerDoesNotAffectNormalMode:
    def test_source_gates_the_skip_before_the_real_import(self):
        """The whole safety guarantee -- normal runs (disable_transformer
        left at its default False) must reach the exact same unmodified
        real-model code path as before this flag existed."""
        idx = _AGENT_SOURCE.index("def _predict(self, state: Dict[str, Any]) -> Dict[str, Any]:")
        window = _AGENT_SOURCE[idx:idx + 2600]
        skip_idx = window.index("if self._disable_transformer:")
        import_idx = window.index("from components.intelligence.model.transformer import predict")
        assert skip_idx < import_idx

    def test_real_predict_call_is_untouched(self):
        idx = _AGENT_SOURCE.index("def _predict(self, state: Dict[str, Any]) -> Dict[str, Any]:")
        window = _AGENT_SOURCE[idx:idx + 2600]
        assert "return predict(" in window
        assert "state=self._slim_for_model(state)" in window
        assert "history=self._history[-3:]" in window


class TestRunTaskCliWiring:
    def test_disable_transformer_flag_is_defined(self):
        assert '"--disable_transformer"' in _RUN_TASK_SOURCE
        assert 'action="store_true"' in _RUN_TASK_SOURCE.split('"--disable_transformer"')[1][:200]

    def test_flag_is_passed_through_to_the_agent_construction(self):
        assert "disable_transformer = _args.disable_transformer" in _RUN_TASK_SOURCE

    def test_flag_is_defined_before_llmagent_is_constructed(self):
        flag_idx = _RUN_TASK_SOURCE.index('"--disable_transformer"')
        agent_idx = _RUN_TASK_SOURCE.index("disable_transformer = _args.disable_transformer")
        assert flag_idx < agent_idx
