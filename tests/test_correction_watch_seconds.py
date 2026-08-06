"""
Regression test for LLMAgent's correction_watch_seconds wiring.

Bug this addresses (a real speed problem, not correctness): every failed
step (no_change/unexpected/error) blocked for a fixed 4.0s "watch for a
human correction" window — DAgger data-collection machinery that's only
useful when someone is actively standing by to correct in real time. In an
unattended/verification run on 2026-08-06, 5 consecutive failures cost 20s
of pure dead wait (62% of that stretch) for zero benefit.

Fix: correction_watch_seconds is now a constructor parameter (default 4.0,
preserving existing behavior for task_manager.py/run_agent.py/
workflow_builder.py, none of which needed to change) that run_task.py
overrides to 0.5s. 0 disables the watch entirely. This test locks down the
wiring itself — the default staying 4.0 unless overridden is what protects
every other caller from a silent behavior change.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def test_default_preserves_original_behavior():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1)
    assert agent._correction_watch_seconds == 4.0


def test_can_be_overridden_to_disable():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1, correction_watch_seconds=0)
    assert agent._correction_watch_seconds == 0


def test_can_be_overridden_to_a_custom_value():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1, correction_watch_seconds=0.5)
    assert agent._correction_watch_seconds == 0.5
