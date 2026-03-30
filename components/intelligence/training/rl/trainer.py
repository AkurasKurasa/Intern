"""
rl/trainer.py
=============
RLTrainer — fine-tunes a BC-initialised policy using PPO.

Why PPO
-------
PPO (Proximal Policy Optimisation) is the standard choice for GUI
automation RL because:
  - Stable training (clipped surrogate loss prevents catastrophic updates)
  - Works well from a BC warm-start (policy doesn't diverge early)
  - Handles discrete action types + continuous click coordinates together

Architecture
------------
  TransformerAgentNetwork  (policy — already trained by BCTrainer)
      + ValueHead (critic — new linear layer added for RL)

  The policy outputs: action_type logits, click_xy, key_count, source_elem
  The critic outputs: V(state) — expected future reward from this state

Training loop (per episode)
---------------------------
  1. Run policy in mock environment (collect rollout)
  2. Compute advantages via GAE (Generalised Advantage Estimation)
  3. Update policy + critic with PPO clipped loss
  4. Decay exploration epsilon
  5. Save checkpoint if best reward seen

Usage
-----
  trainer = RLTrainer(
      policy_path = "data/models/transformer_bc.pt",
      environment = TkinterFormEnvironment(fields, source_data),
      reward_fn   = RewardFunction(target_data),
  )
  trainer.train(episodes=2000)
"""

from __future__ import annotations

import logging
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)

# ── path setup ─────────────────────────────────────────────────────────────────
_HERE   = os.path.dirname(os.path.abspath(__file__))
_IM_DIR = os.path.dirname(_HERE)
_LM_DIR = os.path.dirname(_IM_DIR)
_COMP   = os.path.dirname(_LM_DIR)
_ROOT   = os.path.dirname(_COMP)
for _p in (_ROOT, _COMP, os.path.join(_LM_DIR, "transformer")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ══════════════════════════════════════════════════════════════════════════════
#  Value head (critic)
# ══════════════════════════════════════════════════════════════════════════════

class ValueHead(nn.Module):
    """Thin critic head attached to the transformer's last hidden state."""
    def __init__(self, d_model: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.ReLU(),
            nn.Linear(d_model // 2, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


# ══════════════════════════════════════════════════════════════════════════════
#  Rollout buffer
# ══════════════════════════════════════════════════════════════════════════════

class RolloutBuffer:
    """Stores one episode's worth of (state, action, reward, value, log_prob)."""

    def __init__(self):
        self.states:    List[Dict] = []
        self.actions:   List[Dict] = []
        self.rewards:   List[float] = []
        self.values:    List[float] = []
        self.log_probs: List[float] = []
        self.dones:     List[bool]  = []

    def add(self, state, action, reward, value, log_prob, done):
        self.states.append(state)
        self.actions.append(action)
        self.rewards.append(reward)
        self.values.append(value)
        self.log_probs.append(log_prob)
        self.dones.append(done)

    def clear(self):
        self.__init__()

    def __len__(self):
        return len(self.rewards)


# ══════════════════════════════════════════════════════════════════════════════
#  RLTrainer
# ══════════════════════════════════════════════════════════════════════════════

class RLTrainer:
    """
    PPO trainer that fine-tunes a BC policy in a mock environment.

    Parameters
    ----------
    policy_path    : Path to BC-trained transformer checkpoint (warm start).
    environment    : MockEnvironment instance to train in.
    reward_fn      : RewardFunction instance.
    save_path      : Where to save the RL-fine-tuned checkpoint.
    episodes       : Total training episodes.
    max_steps      : Max steps per episode before forced reset.
    lr             : Learning rate for PPO updates.
    gamma          : Discount factor for future rewards.
    gae_lambda     : GAE smoothing parameter.
    clip_eps       : PPO clipping epsilon (0.2 is standard).
    ppo_epochs     : Number of PPO update passes per rollout.
    device_str     : "auto" | "cpu" | "cuda"
    explorer       : SafeExplorer instance (created automatically if None).
    """

    def __init__(
        self,
        policy_path:  str,
        environment,               # MockEnvironment
        reward_fn,                 # RewardFunction
        save_path:    str   = "data/models/transformer_rl.pt",
        episodes:     int   = 2000,
        max_steps:    int   = 50,
        lr:           float = 3e-5,
        gamma:        float = 0.99,
        gae_lambda:   float = 0.95,
        clip_eps:     float = 0.2,
        ppo_epochs:   int   = 4,
        device_str:   str   = "auto",
        explorer             = None,
    ):
        self.env         = environment
        self.reward_fn   = reward_fn
        self.save_path   = save_path
        self.episodes    = episodes
        self.max_steps   = max_steps
        self.gamma       = gamma
        self.gae_lambda  = gae_lambda
        self.clip_eps    = clip_eps
        self.ppo_epochs  = ppo_epochs

        # Device
        if device_str == "auto":
            self.device = (torch.device("cuda") if torch.cuda.is_available()
                           else torch.device("cpu"))
        else:
            self.device = torch.device(device_str)

        # Load BC policy
        from transformer import _load_model, ELEM_FEATURES
        self.policy     = _load_model(policy_path, self.device)
        self.policy.train()
        self.value_head = ValueHead(self.policy.d_model).to(self.device)

        self.optimizer  = torch.optim.Adam(
            list(self.policy.parameters()) + list(self.value_head.parameters()),
            lr=lr,
        )
        self.buffer     = RolloutBuffer()

        from .explorer import SafeExplorer
        self.explorer   = explorer or SafeExplorer()

    # ── public ────────────────────────────────────────────────────────────────

    def train(self, episodes: Optional[int] = None) -> None:
        n = episodes or self.episodes
        best_reward = -math.inf
        Path(self.save_path).parent.mkdir(parents=True, exist_ok=True)

        for ep in range(1, n + 1):
            self.reward_fn.reset()
            state    = self.env.reset()
            ep_reward = 0.0
            self.buffer.clear()

            for _ in range(self.max_steps):
                # Get policy action + value estimate
                policy_action, log_prob, value = self._policy_step(state)

                # Safe exploration
                action = self.explorer.select_action(state, policy_action)

                # Environment step
                next_state, reward, done = self.env.step(action)
                ep_reward += reward

                self.buffer.add(state, action, reward, value, log_prob, done)
                state = next_state

                if done:
                    break

            # PPO update
            self._ppo_update()
            self.explorer.step_episode()

            if ep % 50 == 0:
                logger.info(
                    "Episode %d/%d  reward=%.2f  epsilon=%.3f",
                    ep, n, ep_reward, self.explorer.epsilon,
                )

            if ep_reward > best_reward:
                best_reward = ep_reward
                self._save_checkpoint(ep, best_reward)

        logger.info("RLTrainer done.  Best reward=%.2f  → %s", best_reward, self.save_path)

    # ── internal ─────────────────────────────────────────────────────────────

    def _policy_step(
        self,
        state: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], float, float]:
        """Run one forward pass → (action_dict, log_prob, value)."""
        from transformer import encode_state, ELEM_FEATURES, _ACTION_LABELS

        s_tensor = encode_state(state).unsqueeze(0).unsqueeze(0).to(self.device)
        H        = self.policy.hist_len
        p_types  = torch.full((1, H - 1), self.policy.num_actions,
                               dtype=torch.long, device=self.device)
        p_cont   = torch.zeros(1, H - 1, 3, device=self.device)

        with torch.no_grad():
            out  = self.policy(s_tensor, p_types, p_cont)
            # Value estimate from last hidden state (re-run to get hidden)
            val  = self._get_value(s_tensor, p_types, p_cont)

        # Sample action type
        probs    = torch.softmax(out.type_logits[0], dim=-1)
        dist     = torch.distributions.Categorical(probs)
        act_idx  = dist.sample()
        log_prob = dist.log_prob(act_idx).item()
        value    = val.item()

        action: Dict[str, Any] = {"action_type": _ACTION_LABELS.get(act_idx.item(), "no_op")}
        if act_idx.item() == 1:   # click
            cx, cy = out.click_xy[0].tolist()
            res    = state.get("screen_resolution", [1920, 1080])
            action["click_position"] = [cx * res[0], cy * res[1]]
        elif act_idx.item() == 2:  # keyboard
            action["key_count"]       = max(1, round(out.key_count[0, 0].item() * 100))
            action["source_elem_idx"] = int(out.source_elem[0].argmax().item())

        return action, log_prob, value

    def _get_value(self, s, p_types, p_cont) -> torch.Tensor:
        """Extract the last token hidden state and pass through value head."""
        H       = self.policy.hist_len
        s_flat  = self.policy.state_enc(s.view(1 * H, *s.shape[2:])).view(1, H, -1)
        seq_len = 2 * H - 1
        tokens  = torch.zeros(1, seq_len, self.policy.d_model, device=self.device)
        tokens[:, 0::2] = s_flat
        if H > 1:
            tokens[:, 1::2] = self.policy.action_enc(p_types, p_cont)
        pos    = torch.arange(seq_len, device=self.device).unsqueeze(0)
        tokens = tokens + self.policy.pos_enc(pos)
        mask   = nn.Transformer.generate_square_subsequent_mask(seq_len, device=self.device)
        out    = self.policy.encoder(tokens, mask=mask, is_causal=True)
        last   = self.policy.out_norm(out[:, -1])
        return self.value_head(last)

    def _ppo_update(self) -> None:
        """Run PPO_EPOCHS passes of the clipped surrogate loss."""
        if len(self.buffer) < 2:
            return

        advantages = self._compute_gae()
        returns    = [a + v for a, v in zip(advantages, self.buffer.values)]
        adv_tensor = torch.tensor(advantages, dtype=torch.float32, device=self.device)
        ret_tensor = torch.tensor(returns,    dtype=torch.float32, device=self.device)
        old_lps    = torch.tensor(self.buffer.log_probs, dtype=torch.float32, device=self.device)

        # Normalise advantages
        if adv_tensor.std() > 1e-8:
            adv_tensor = (adv_tensor - adv_tensor.mean()) / (adv_tensor.std() + 1e-8)

        from transformer import encode_state, _ACTION_IDS
        for _ in range(self.ppo_epochs):
            for i, (state, action) in enumerate(zip(self.buffer.states, self.buffer.actions)):
                s_tensor = encode_state(state).unsqueeze(0).unsqueeze(0).to(self.device)
                H        = self.policy.hist_len
                p_types  = torch.full((1, H - 1), self.policy.num_actions,
                                       dtype=torch.long, device=self.device)
                p_cont   = torch.zeros(1, H - 1, 3, device=self.device)

                out  = self.policy(s_tensor, p_types, p_cont)
                val  = self._get_value(s_tensor, p_types, p_cont)

                at   = _ACTION_IDS.get(action.get("action_type", "no_op"), 0)
                at_t = torch.tensor([at], device=self.device)
                probs = torch.softmax(out.type_logits[0], dim=-1)
                dist  = torch.distributions.Categorical(probs)
                new_lp = dist.log_prob(at_t)

                ratio   = (new_lp - old_lps[i]).exp()
                adv_i   = adv_tensor[i]
                l_clip  = torch.min(
                    ratio * adv_i,
                    torch.clamp(ratio, 1 - self.clip_eps, 1 + self.clip_eps) * adv_i,
                )
                l_value = nn.functional.mse_loss(val.squeeze(), ret_tensor[i])
                loss    = -l_clip + 0.5 * l_value

                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.policy.parameters(), 0.5)
                self.optimizer.step()

    def _compute_gae(self) -> List[float]:
        """Generalised Advantage Estimation."""
        advantages = [0.0] * len(self.buffer)
        last_adv   = 0.0
        last_val   = 0.0
        for t in reversed(range(len(self.buffer))):
            if self.buffer.dones[t]:
                last_adv = 0.0
                last_val = 0.0
            delta      = (self.buffer.rewards[t]
                          + self.gamma * last_val * (1 - self.buffer.dones[t])
                          - self.buffer.values[t])
            last_adv   = delta + self.gamma * self.gae_lambda * last_adv
            last_val   = self.buffer.values[t]
            advantages[t] = last_adv
        return advantages

    def _save_checkpoint(self, episode: int, reward: float) -> None:
        torch.save({
            "episode":           episode,
            "best_reward":       reward,
            "model_state_dict":  self.policy.state_dict(),
            "value_head_state":  self.value_head.state_dict(),
            "hyperparams": {
                "d_model":       self.policy.d_model,
                "hist_len":      self.policy.hist_len,
                "num_actions":   self.policy.num_actions,
                "max_elements":  self.policy.max_elements,
            },
        }, self.save_path)
