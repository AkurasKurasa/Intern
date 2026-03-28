"""
rl/explorer.py
==============
SafeExplorer — epsilon-greedy exploration constrained to known UI elements.

Instead of clicking random screen pixels (dangerous), the explorer only
ever clicks elements that UIAutomationObserver has already identified.
This means even fully random exploration is safe — it can only interact
with real, visible UI elements.

Exploration schedule
--------------------
  Early episodes  : high epsilon → mostly random (discover the environment)
  Middle episodes : decaying epsilon → mix of random and policy
  Late episodes   : low epsilon → mostly policy (exploit learned behaviour)

  epsilon = max(min_epsilon, start_epsilon * decay_rate ^ episode)
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional


class SafeExplorer:
    """
    Epsilon-greedy explorer constrained to visible UI elements.

    Parameters
    ----------
    start_epsilon : Initial exploration rate (default 0.8 = 80% random).
    min_epsilon   : Floor — never go below this (default 0.05 = 5% random).
    decay_rate    : Multiplied by epsilon each episode (default 0.995).
    seed          : Random seed for reproducibility.
    """

    def __init__(
        self,
        start_epsilon: float = 0.8,
        min_epsilon:   float = 0.05,
        decay_rate:    float = 0.995,
        seed:          Optional[int] = None,
    ):
        self.epsilon      = start_epsilon
        self.min_epsilon  = min_epsilon
        self.decay_rate   = decay_rate
        self._rng         = random.Random(seed)
        self._episode     = 0

    # ── public ────────────────────────────────────────────────────────────────

    def select_action(
        self,
        state:         Dict[str, Any],
        policy_action: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Return either the policy's action or a random safe action,
        depending on current epsilon.

        Parameters
        ----------
        state         : Current UIAutomationObserver state.
        policy_action : Action predicted by the transformer policy.
        """
        if self._rng.random() < self.epsilon:
            return self._random_action(state)
        return policy_action

    def step_episode(self) -> None:
        """Call once per episode to decay epsilon."""
        self._episode += 1
        self.epsilon   = max(self.min_epsilon, self.epsilon * self.decay_rate)

    @property
    def episode(self) -> int:
        return self._episode

    # ── random action generation ──────────────────────────────────────────────

    def _random_action(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a random action that only targets known UI elements.
        Action distribution mirrors real task distribution:
          60% click, 30% keyboard, 10% no_op
        """
        roll = self._rng.random()

        if roll < 0.60:
            return self._random_click(state)
        elif roll < 0.90:
            return self._random_keyboard(state)
        else:
            return {"action_type": "no_op"}

    def _random_click(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Click the centre of a random visible element."""
        clickable_types = {"button", "input", "checkbox", "radio",
                           "combobox", "listitem", "tabitem", "link"}
        candidates = [
            e for e in state.get("elements", [])
            if e.get("type") in clickable_types and e.get("enabled", True)
        ]
        if not candidates:
            candidates = state.get("elements", [])
        if not candidates:
            return {"action_type": "no_op"}

        elem = self._rng.choice(candidates)
        bbox = elem.get("bbox", [0, 0, 100, 30])
        cx   = (bbox[0] + bbox[2]) / 2
        cy   = (bbox[1] + bbox[3]) / 2
        return {"action_type": "click", "click_position": [cx, cy]}

    def _random_keyboard(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Type a random value from the background (source) window elements.
        This simulates the agent randomly picking a source value to type —
        safe, and teaches the agent which values belong to which fields.
        """
        bg_elems = [
            e for e in state.get("elements", [])
            if e.get("window_role") == "background"
        ]
        if bg_elems:
            src = self._rng.choice(bg_elems)
            text = (src.get("value") or src.get("text") or "").strip()
            if ":" in text:
                text = text.split(":", 1)[1].strip()
            if text:
                return {"action_type": "keyboard", "text": text, "key_count": len(text)}

        return {"action_type": "keyboard", "key_count": 1}
