"""
tests/test_executor.py
========================
Smoke-tests for the Executor component.

All tests run with dry_run=True — no real mouse/keyboard events fired.
No trained model checkpoint is required for most tests.

Run from the repo root:
    python -m pytest tests/test_executor.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from components.executor.executor import ActionExecutor, ExecutorAgent, ExecutionResult


# ══════════════════════════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def executor() -> ActionExecutor:
    return ActionExecutor(dry_run=True)


def _make_state(n_elems: int = 5) -> dict:
    return {
        "application": "TestApp",
        "screen_resolution": [1920, 1080],
        "focused_element_id": None,
        "elements": [
            {
                "element_id": f"el_{i}",
                "type": "textbox",
                "bbox": [i * 50, i * 30, i * 50 + 40, i * 30 + 20],
                "text": f"text_{i}",
                "confidence": 0.9,
                "enabled": True,
            }
            for i in range(n_elems)
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
#  ActionExecutor tests
# ══════════════════════════════════════════════════════════════════════════════

class TestActionExecutor:

    def test_execute_click_returns_result(self, executor):
        pred   = {"action_type": "click", "click_position": [960, 540]}
        result = executor.execute(pred)
        assert isinstance(result, ExecutionResult)
        assert result.action_type == "click"
        assert result.position    == (960, 540)
        assert result.success     is True
        assert result.dry_run     is True
        assert result.error       == ""

    def test_execute_click_rounds_position(self, executor):
        pred   = {"action_type": "click", "click_position": [100.7, 200.3]}
        result = executor.execute(pred)
        assert result.position == (101, 200)

    def test_execute_keyboard_with_keystrokes(self, executor):
        pred   = {"action_type": "keyboard", "key_count": 3,
                  "keystrokes": ["H", "i", "Key.enter"]}
        result = executor.execute(pred)
        assert result.action_type == "keyboard"
        assert result.keystrokes  == ["H", "i", "Key.enter"]
        assert result.key_count   == 3
        assert result.success     is True

    def test_execute_keyboard_no_keystrokes(self, executor):
        """Without explicit keystrokes the executor skips and warns (not an error)."""
        pred   = {"action_type": "keyboard", "key_count": 5}
        result = executor.execute(pred)
        assert result.action_type == "keyboard"
        assert result.keystrokes  == []
        assert result.key_count   == 0
        assert result.success     is True   # graceful degradation, not a failure

    def test_execute_noop(self, executor):
        result = executor.execute({"action_type": "no_op"})
        assert result.action_type == "no_op"
        assert result.position    is None
        assert result.key_count   == 0
        assert result.success     is True

    def test_execute_unknown_type_falls_back_to_noop(self, executor):
        result = executor.execute({"action_type": "unsupported_xyz"})
        assert result.action_type == "no_op"
        assert result.success     is True

    def test_result_str_click(self, executor):
        result = executor.execute({"action_type": "click", "click_position": [10, 20]})
        s = str(result)
        assert "click" in s
        assert "[DRY-RUN]" in s

    def test_result_str_keyboard(self, executor):
        result = executor.execute({"action_type": "keyboard", "key_count": 2,
                                   "keystrokes": ["a", "b"]})
        s = str(result)
        assert "keyboard" in s

    def test_result_str_noop(self, executor):
        result = executor.execute({"action_type": "no_op"})
        assert "no_op" in str(result)

    def test_dry_run_true_in_result(self, executor):
        result = executor.execute({"action_type": "click", "click_position": [0, 0]})
        assert result.dry_run is True

    def test_live_executor_raises_without_pyautogui(self, monkeypatch):
        """If pyautogui is missing, live ActionExecutor must raise ImportError."""
        import components.executor.executor as mod
        monkeypatch.setattr(mod, "_PYAUTOGUI_AVAILABLE", False)
        with pytest.raises(ImportError, match="pyautogui"):
            ActionExecutor(dry_run=False)


# ══════════════════════════════════════════════════════════════════════════════
#  ExecutorAgent tests  (no model needed — prediction is mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestExecutorAgent:

    def _make_agent(self, mock_pred: dict) -> ExecutorAgent:
        """Return an agent whose predict() is monkey-patched to return mock_pred."""
        agent = ExecutorAgent(dry_run=True, max_steps=5, step_delay=0.0)
        # Patch the predict import inside ExecutorAgent.step
        import components.executor.executor as mod
        original_step = agent.step

        def patched_step(state):
            result = agent._executor.execute(mock_pred)
            res    = state.get("screen_resolution", [1920, 1080])
            W, H   = float(res[0]), float(res[1])
            pos    = mock_pred.get("click_position", [0.0, 0.0])
            agent._history.append({
                "state":       state,
                "action_type": mock_pred.get("action_type", "no_op"),
                "click_xy":    [pos[0] / W, pos[1] / H] if pos else [0.0, 0.0],
                "key_count":   mock_pred.get("key_count", 0),
            })
            agent._results.append(result)
            return result

        agent.step = patched_step
        return agent

    def test_agent_step_click(self):
        agent  = self._make_agent({"action_type": "click", "click_position": [100, 200]})
        result = agent.step(_make_state())
        assert result.action_type == "click"
        assert result.position    == (100, 200)

    def test_agent_step_keyboard(self):
        agent  = self._make_agent({"action_type": "keyboard", "key_count": 3,
                                   "keystrokes": ["a", "b", "c"]})
        result = agent.step(_make_state())
        assert result.action_type == "keyboard"
        assert result.key_count   == 3

    def test_agent_step_noop(self):
        agent  = self._make_agent({"action_type": "no_op"})
        result = agent.step(_make_state())
        assert result.action_type == "no_op"

    def test_agent_run_max_steps(self):
        agent   = self._make_agent({"action_type": "no_op"})
        results = agent.run(_make_state(), max_steps=3, step_delay=0.0)
        assert len(results) == 3

    def test_agent_stop_flag(self):
        """Calling stop() before run() should produce zero steps."""
        agent = self._make_agent({"action_type": "no_op"})
        agent.stop()
        results = agent.run(_make_state(), max_steps=10, step_delay=0.0)
        assert len(results) == 0

    def test_agent_history_grows(self):
        agent = self._make_agent({"action_type": "click", "click_position": [50, 50]})
        agent.run(_make_state(), max_steps=4, step_delay=0.0)
        assert len(agent.history) == 4

    def test_agent_reset_history(self):
        agent = self._make_agent({"action_type": "no_op"})
        agent.run(_make_state(), max_steps=3, step_delay=0.0)
        agent.reset_history()
        assert agent.history == []

    def test_agent_results_property(self):
        agent = self._make_agent({"action_type": "no_op"})
        agent.run(_make_state(), max_steps=2, step_delay=0.0)
        assert len(agent.results) == 2
        assert all(isinstance(r, ExecutionResult) for r in agent.results)

    def test_agent_halts_on_failure(self):
        """If a step returns success=False, the loop should stop early."""
        agent  = self._make_agent({"action_type": "no_op"})
        # Force first step to fail
        def bad_step(state):
            r = ExecutionResult("no_op", None, 0, [], "ts", True, False, "forced error")
            agent._results.append(r)
            return r
        agent.step = bad_step
        results = agent.run(_make_state(), max_steps=10, step_delay=0.0)
        assert len(results) == 1
        assert results[0].success is False
