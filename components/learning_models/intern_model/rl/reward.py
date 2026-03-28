"""
rl/reward.py
============
RewardFunction — scores agent actions during RL training.

Reward structure
----------------
  +1.0  correct value typed into correct field
  +0.2  correct field clicked (focused)
  -0.5  wrong value typed into a field
  -0.3  clicked wrong field (skipped required one)
  -0.1  no_op when work remains
  -2.0  error state detected (dialog, wrong app, crash)
  +5.0  task fully complete (all fields correct)
  +0.1  partial progress (any new field correctly filled)

All weights are configurable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RewardWeights:
    field_correct:    float = 1.0
    field_wrong:      float = -0.5
    click_correct:    float = 0.2
    click_wrong:      float = -0.3
    noop_penalty:     float = -0.1
    error_penalty:    float = -2.0
    task_complete:    float = 5.0
    partial_progress: float = 0.1


class RewardFunction:
    """
    Computes a scalar reward given the transition (prev_state, action, next_state).

    Works with both:
    - TkinterFormEnvironment (mock training)
    - Real UIAutomationObserver states (evaluation)

    Parameters
    ----------
    target_data : Dict mapping field names to correct values.
                  e.g. {"First Name": "James", "Last Name": "Delgado"}
    weights     : RewardWeights instance (use defaults or customise).
    """

    def __init__(
        self,
        target_data: Dict[str, str],
        weights:     Optional[RewardWeights] = None,
    ):
        self.target   = {k.lower(): v.strip().lower() for k, v in target_data.items()}
        self.weights  = weights or RewardWeights()
        self._prev_correct: int = 0   # tracks partial progress

    def compute(
        self,
        prev_state: Dict[str, Any],
        action:     Dict[str, Any],
        next_state: Dict[str, Any],
    ) -> float:
        reward = 0.0
        w      = self.weights

        # ── no_op penalty ─────────────────────────────────────────────────────
        if action.get("action_type") == "no_op":
            reward += w.noop_penalty

        # ── error detection ───────────────────────────────────────────────────
        if self._is_error_state(next_state):
            reward += w.error_penalty
            return reward

        # ── field fill rewards ────────────────────────────────────────────────
        prev_vals = self._extract_field_values(prev_state)
        curr_vals = self._extract_field_values(next_state)

        now_correct = 0
        for field_name, target_val in self.target.items():
            was   = prev_vals.get(field_name, "").strip().lower()
            now   = curr_vals.get(field_name, "").strip().lower()
            if was == target_val:
                now_correct += 1
                continue   # already correct — no change reward
            if now == target_val:
                reward += w.field_correct
                now_correct += 1
            elif now and now != target_val:
                reward += w.field_wrong

        # ── partial progress bonus ────────────────────────────────────────────
        if now_correct > self._prev_correct:
            reward += w.partial_progress * (now_correct - self._prev_correct)
            self._prev_correct = now_correct

        # ── task complete ─────────────────────────────────────────────────────
        if self._is_complete(curr_vals):
            reward += w.task_complete

        return round(reward, 4)

    def reset(self) -> None:
        """Call at the start of each episode."""
        self._prev_correct = 0

    # ── helpers ───────────────────────────────────────────────────────────────

    def _extract_field_values(self, state: Dict[str, Any]) -> Dict[str, str]:
        """Extract input field values from a state dict."""
        result = {}
        for elem in state.get("elements", []):
            if elem.get("type") != "input":
                continue
            label = (elem.get("label") or elem.get("text") or "").strip().lower()
            value = (elem.get("value") or elem.get("text") or "").strip()
            if label:
                result[label] = value
        return result

    def _is_complete(self, field_values: Dict[str, str]) -> bool:
        return all(
            field_values.get(f, "").strip().lower() == v
            for f, v in self.target.items()
        )

    def _is_error_state(self, state: Dict[str, Any]) -> bool:
        """Detect error dialogs or unexpected states."""
        title = state.get("window_title", "").lower()
        error_keywords = {"error", "warning", "exception", "failed", "alert"}
        return any(kw in title for kw in error_keywords)
