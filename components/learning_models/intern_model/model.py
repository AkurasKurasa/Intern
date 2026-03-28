"""
intern_model/model.py
=====================
InternModel — the single entry point for everything Intern learns and does.

This class owns all three learning phases and the LLM provider:

  Phase 1 — Behavioral Cloning  (BCTrainer)
    Learn from human demonstrations. The human records a task once;
    the transformer imitates it.

  Phase 2 — Reinforcement Learning  (RLTrainer)
    Fine-tune beyond what the human showed. Runs in a mock environment
    overnight with no human involvement.

  Phase 3 — Continual Learning  (ContinualLearner)
    Never stop improving. Every new recording session automatically
    updates the model in the background.

  LLM Supervision  (LLMProvider)
    Goal inference, task evaluation, stop condition, error detection.
    Pluggable: Anthropic, Groq, Gemini, LM Studio, or none.

Usage
-----
  # Create once
  intern = InternModel(
      model_path = "data/models/transformer_bc.pt",
      provider   = "groq",
      api_key    = "gsk_...",
  )

  # Phase 1: learn from your recordings
  intern.learn_from_demonstrations(trace_dir="data/output/traces/live")

  # Phase 2: fine-tune with RL (needs a mock environment)
  from intern_model.rl import TkinterFormEnvironment, RewardFunction
  env = TkinterFormEnvironment(fields, source_data)
  intern.fine_tune_rl(environment=env, target_data=source_data)

  # Phase 3: start background continual learning
  intern.start_continual_learning()

  # Predict the next action from a live screen state
  action = intern.predict(state)

  # Infer goal from screen automatically
  goal = intern.infer_goal(state)
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── path setup ────────────────────────────────────────────────────────────────
_HERE   = os.path.dirname(os.path.abspath(__file__))
_LM_DIR = os.path.dirname(_HERE)
_COMP   = os.path.dirname(_LM_DIR)
_ROOT   = os.path.dirname(_COMP)
for _p in (_ROOT, _COMP, os.path.join(_LM_DIR, "transformer")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class InternModel:
    """
    Single entry point for all of Intern's learning and inference.

    Parameters
    ----------
    model_path      : Path to read/write the transformer checkpoint.
    provider        : LLM provider — "anthropic"|"groq"|"gemini"|"lmstudio"|"none"
    api_key         : API key for the LLM provider.
    lmstudio_url    : Base URL for LM Studio (default: http://localhost:1234/v1).
    device          : Torch device — "auto"|"cpu"|"cuda"|"mps"
    trace_dir       : Default trace directory for BC and continual learning.
    continual       : If True, start continual learning background thread on init.
    retrain_every   : New traces needed to trigger a continual retraining run.
    """

    def __init__(
        self,
        model_path:     str           = "data/models/transformer_bc.pt",
        provider:       str           = "none",
        api_key:        Optional[str] = None,
        lmstudio_url:   str           = "http://localhost:1234/v1",
        device:         str           = "auto",
        trace_dir:      str           = "data/output/traces/live",
        continual:      bool          = False,
        retrain_every:  int           = 20,
    ):
        self.model_path = model_path
        self.trace_dir  = trace_dir
        self.device     = device

        # ── LLM provider ──────────────────────────────────────────────────────
        from llm.providers import LLMProvider
        self.llm = LLMProvider(
            provider     = provider,
            api_key      = api_key,
            lmstudio_url = lmstudio_url,
        )

        # ── BC trainer ────────────────────────────────────────────────────────
        from bc.behavioral_cloning import BCTrainer
        self.bc = BCTrainer(
            trace_dir  = trace_dir,
            save_path  = model_path,
            device     = device,
        )

        # ── Continual learner (lazy start) ────────────────────────────────────
        from continual.learner import ContinualLearner
        self._continual = ContinualLearner(
            model_path    = model_path,
            trace_dir     = trace_dir,
            retrain_every = retrain_every,
            device        = device,
        )
        if continual:
            self._continual.start()

        logger.info(
            "InternModel ready — provider=%s  model=%s  continual=%s",
            provider, model_path, continual,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  Phase 1 — Behavioral Cloning
    # ══════════════════════════════════════════════════════════════════════════

    def learn_from_demonstrations(
        self,
        trace_dir: Optional[str] = None,
        epochs:    int           = 50,
    ):
        """
        Train the transformer on human-recorded trace files.
        This is the primary way Intern learns — watch the human, imitate them.
        """
        logger.info("Phase 1: Behavioral Cloning — learning from human demonstrations.")
        model = self.bc.train(
            trace_dir = trace_dir or self.trace_dir,
            epochs    = epochs,
        )
        logger.info("Phase 1 complete. Checkpoint: %s", self.model_path)
        return model

    # ══════════════════════════════════════════════════════════════════════════
    #  Phase 2 — Reinforcement Learning
    # ══════════════════════════════════════════════════════════════════════════

    def fine_tune_rl(
        self,
        environment,                           # MockEnvironment instance
        target_data:    Dict[str, str],        # correct field values
        episodes:       int   = 2000,
        rl_save_path:   Optional[str] = None,
        reward_weights  = None,
    ):
        """
        Fine-tune the BC policy with PPO in a mock environment.
        Starts from the BC checkpoint so RL has a warm start.

        Parameters
        ----------
        environment  : A MockEnvironment (e.g. TkinterFormEnvironment).
        target_data  : The correct values the agent should produce.
                       e.g. {"First Name": "James", "Last Name": "Delgado"}
        episodes     : How many RL episodes to run.
        rl_save_path : Where to save the RL checkpoint (defaults to model_path).
        """
        from rl.reward import RewardFunction
        from rl.trainer import RLTrainer

        save = rl_save_path or self.model_path.replace(".pt", "_rl.pt")
        reward_fn = RewardFunction(target_data, weights=reward_weights)

        logger.info("Phase 2: Reinforcement Learning — %d episodes.", episodes)
        trainer = RLTrainer(
            policy_path  = self.model_path,
            environment  = environment,
            reward_fn    = reward_fn,
            save_path    = save,
            episodes     = episodes,
            device_str   = self.device,
        )
        trainer.train()
        logger.info("Phase 2 complete. RL checkpoint: %s", save)

    # ══════════════════════════════════════════════════════════════════════════
    #  Phase 3 — Continual Learning
    # ══════════════════════════════════════════════════════════════════════════

    def start_continual_learning(self) -> None:
        """
        Start the background thread that watches for new traces and retrains
        automatically. Call once; runs until stop_continual_learning().
        """
        self._continual.start()
        logger.info("Phase 3: Continual Learning started.")

    def stop_continual_learning(self) -> None:
        self._continual.stop()

    def notify_new_trace(self, trace_path: str) -> None:
        """
        Call this after ScreenObserver saves a new trace so ContinualLearner
        knows about it immediately (rather than waiting for the next poll).
        """
        self._continual.add_trace(trace_path)

    def force_retrain(self) -> None:
        """Manually trigger a continual retraining run right now."""
        self._continual.force_retrain()

    @property
    def continual_stats(self) -> Dict[str, Any]:
        return self._continual.stats

    # ══════════════════════════════════════════════════════════════════════════
    #  Inference
    # ══════════════════════════════════════════════════════════════════════════

    def predict(
        self,
        state:   Dict[str, Any],
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Predict the next action from a live screen state.
        Uses the transformer (BC or RL checkpoint, whichever is newer).
        """
        return self.bc.predict(
            state      = state,
            history    = history or [],
            model_path = self.model_path,
        )

    def infer_goal(self, state: Dict[str, Any]) -> str:
        """
        Ask the LLM to infer what task the user is trying to accomplish
        from what's currently visible on screen.

        Returns a natural-language goal string, or empty string if LLM
        is unavailable.
        """
        from _state_helpers import state_to_text
        try:
            from _state_helpers import state_to_text as _s2t
        except ImportError:
            _s2t = _default_state_to_text

        return self.llm.infer_goal(_s2t(state))

    def evaluate(
        self,
        goal:         str,
        state:        Dict[str, Any],
        history:      Optional[List[Dict[str, Any]]] = None,
    ):
        """
        Ask the LLM to evaluate whether the task goal has been met.
        Returns an LLMDecision(status, reason, guidance).
        """
        from _state_helpers import state_to_text as _s2t
        try:
            from _state_helpers import state_to_text as _s2t
        except ImportError:
            _s2t = _default_state_to_text

        history_text = _default_history_to_text(history or [])
        return self.llm.evaluate(goal, _s2t(state), history_text)


# ── minimal state→text fallback (avoids circular import with agent.py) ────────

def _default_state_to_text(state: Dict[str, Any]) -> str:
    lines = [f"Active: {state.get('application','')} — {state.get('window_title','')}"]
    active = [e for e in state.get("elements", []) if e.get("window_role") in ("active", None)]
    bg     = [e for e in state.get("elements", []) if e.get("window_role") == "background"]
    for e in active[:30]:
        text = (e.get("text") or e.get("value") or "").strip()
        if text:
            lines.append(f"  [active] {e.get('type','?')} {text!r}")
    for e in bg[:30]:
        text = (e.get("text") or e.get("value") or "").strip()
        if text:
            lines.append(f"  [bg] {e.get('app','')} {text!r}")
    return "\n".join(lines)


def _default_history_to_text(history: List[Dict[str, Any]]) -> str:
    if not history:
        return "No actions yet."
    return "\n".join(
        f"  {i+1}. {h.get('action_type','?')}"
        for i, h in enumerate(history[-5:])
    )
