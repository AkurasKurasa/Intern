"""
Regression tests for agent.py's _adaptive_settle_wait / _observed_state_
fingerprint (components/agent/agent.py).

Found live 2026-08-14, direct request ("fix Speed without sacrificing
reliability"): STEP_DELAY (1.0s, fixed, unconditional on every single step)
measured as roughly HALF of a real live run's total per-step wall-clock time.
Most steps don't need the full second -- the observed state has already
stopped changing well before it elapses -- but a real minority (tab
switches, scrolls) genuinely do need it, per this project's own prior
incident ("a bug came from checking the screen too soon after a tab
switch"). A blind constant can't tell the difference between these two
cases; _adaptive_settle_wait can, by polling the observed state and
returning as soon as two consecutive reads agree nothing is still changing
-- the same verify-don't-assume principle already used elsewhere in this
file (verify-at-fill, redirect-then-confirm), applied to the end-of-step
delay itself. Wired into ONLY the main loop's single highest-frequency
call site (the success-path end-of-step sleep) -- the many other, smaller
fractional sleeps scattered through specific retry/redirect branches were
deliberately left untouched, to avoid destabilizing logic not individually
verified safe to change in the same pass.

Uses a fake, deterministic clock (time.sleep advances a fake "now" instead
of actually blocking; time.time() reads it) so these tests run instantly
while still exercising the real iteration/call-count behavior -- not just
no-op-ing time.sleep entirely, which would make it impossible to
distinguish "returned quickly" from "returned slowly."
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


class _FakeClock:
    """time.time() reads self.now; time.sleep(d) advances it without
    blocking -- deterministic, instant, but behaviorally faithful."""
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


def _make_agent():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1, step_delay=0)
    return agent


def _state(n_elements=2, focused_id="e1", values=None):
    values = values or ["a"] * n_elements
    return {
        "elements": [
            {"element_id": f"e{i}", "value": values[i]} for i in range(n_elements)
        ],
        "focused_element_id": focused_id,
    }


class TestFingerprint:
    def test_identical_states_produce_equal_fingerprints(self):
        agent = _make_agent()
        s1 = _state(values=["a", "b"])
        s2 = _state(values=["a", "b"])
        assert (agent._observed_state_fingerprint(s1)
                == agent._observed_state_fingerprint(s2))

    def test_a_changed_value_produces_a_different_fingerprint(self):
        agent = _make_agent()
        s1 = _state(values=["a", "b"])
        s2 = _state(values=["a", "c"])
        assert (agent._observed_state_fingerprint(s1)
                != agent._observed_state_fingerprint(s2))

    def test_a_changed_focus_produces_a_different_fingerprint(self):
        agent = _make_agent()
        s1 = _state(focused_id="e1")
        s2 = _state(focused_id="e2")
        assert (agent._observed_state_fingerprint(s1)
                != agent._observed_state_fingerprint(s2))

    def test_a_changed_element_count_produces_a_different_fingerprint(self):
        agent = _make_agent()
        s1 = _state(n_elements=2)
        s2 = _state(n_elements=3, values=["a", "a", "a"])
        assert (agent._observed_state_fingerprint(s1)
                != agent._observed_state_fingerprint(s2))


class TestAdaptiveSettleWait:
    def _wire_fake_clock(self, agent, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr("agent.agent.time.time", clock.time)
        monkeypatch.setattr("agent.agent.time.sleep", clock.sleep)
        return clock

    def test_zero_or_negative_max_wait_returns_immediately_no_sleep(self, monkeypatch):
        agent = _make_agent()
        clock = self._wire_fake_clock(agent, monkeypatch)
        agent._observe = MagicMock(return_value=_state())
        agent._adaptive_settle_wait(0.0)
        assert clock.now == 0.0
        agent._observe.assert_not_called()

    def test_already_settled_state_returns_after_one_poll_not_the_full_budget(self, monkeypatch):
        """The main case this exists for: an already-stable UI shouldn't
        pay the full 1.0s just because that's the worst-case budget."""
        agent = _make_agent()
        clock = self._wire_fake_clock(agent, monkeypatch)
        agent._observe = MagicMock(return_value=_state(values=["a", "b"]))
        agent._adaptive_settle_wait(1.0, poll=0.15)
        assert clock.now == pytest.approx(0.15)
        assert clock.now < 1.0

    def test_a_state_that_keeps_changing_then_settles_returns_once_stable(self, monkeypatch):
        agent = _make_agent()
        clock = self._wire_fake_clock(agent, monkeypatch)
        # First two reads differ (still changing), third and fourth agree.
        reads = [
            _state(values=["a", "b"]),
            _state(values=["a", "c"]),
            _state(values=["a", "d"]),
            _state(values=["a", "d"]),
        ]
        agent._observe = MagicMock(side_effect=reads)
        agent._adaptive_settle_wait(1.0, poll=0.15)
        # 3 polls to see the 4th (matching) read confirm settling.
        assert clock.now == pytest.approx(0.45)
        assert clock.now < 1.0

    def test_a_state_that_never_settles_is_bounded_by_max_wait(self, monkeypatch):
        """Worst case: never stabilizes. Must still be bounded -- not an
        infinite loop, not wildly longer than the budget it replaces."""
        agent = _make_agent()
        clock = self._wire_fake_clock(agent, monkeypatch)
        counter = {"i": 0}

        def _always_different(*_a, **_kw):
            counter["i"] += 1
            return _state(values=["a", str(counter["i"])])

        agent._observe = MagicMock(side_effect=_always_different)
        agent._adaptive_settle_wait(1.0, poll=0.15)
        # Bounded: never overshoots by more than one poll interval.
        assert clock.now >= 1.0
        assert clock.now <= 1.0 + 0.15

    def test_observe_failure_on_first_call_falls_back_to_a_blind_sleep(self, monkeypatch):
        """Preserves the original safety behavior if observation itself is
        broken -- don't loop forever or crash, just wait the full budget
        exactly like the code this replaces did unconditionally."""
        agent = _make_agent()
        clock = self._wire_fake_clock(agent, monkeypatch)
        agent._observe = MagicMock(side_effect=RuntimeError("observer down"))
        agent._adaptive_settle_wait(1.0)
        assert clock.now == pytest.approx(1.0)

    def test_observe_failure_mid_poll_returns_early_rather_than_crash(self, monkeypatch):
        agent = _make_agent()
        clock = self._wire_fake_clock(agent, monkeypatch)
        agent._observe = MagicMock(side_effect=[_state(values=["a", "b"]),
                                                  RuntimeError("observer down mid-run")])
        # Must not raise.
        agent._adaptive_settle_wait(1.0, poll=0.15)
        assert clock.now == pytest.approx(0.15)

    def test_never_waits_longer_than_the_blind_sleep_it_replaces_when_settled_early(self, monkeypatch):
        """Direct comparison to the behavior being replaced: a stable state
        must finish well under the old fixed 1.0s cost."""
        agent = _make_agent()
        clock = self._wire_fake_clock(agent, monkeypatch)
        agent._observe = MagicMock(return_value=_state())
        agent._adaptive_settle_wait(1.0)
        assert clock.now < 1.0
