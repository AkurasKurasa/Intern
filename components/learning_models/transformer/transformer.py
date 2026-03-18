"""
transformer.py — TransformerAgentNetwork (all-in-one)
=======================================================
Causal Transformer agent trained via Behavioral Cloning on trace JSON files.

Sequence contract
-----------------
    (state_1, action_1, state_2, action_2, ..., state_t) -> next_action

Sections
--------
  1. Dataset   — encode_state, TrajectoryDataset
  2. Model     — StateEncoder, ActionEncoder, TransformerAgentNetwork
  3. Training  — train()
  4. Inference — predict(), predict_folder()
  5. CLI       — __main__

Quick-start
-----------
  # Train
  python -m components.learning_models.transformer.transformer \\
      --mode train --data_dir data/output/traces/live --epochs 20

  # Predict
  python -m components.learning_models.transformer.transformer \\
      --mode predict --trace_path data/output/traces/live/live_step_0006.json

Anti-overfitting tips (small datasets)
---------------------------------------
  - Collect more traces: `python record_trace.py`
  - Shrink the model:    --d_model 64 --num_layers 2
  - Label smoothing:     --label_smoothing 0.1  (built-in, default 0.1)
  - Element dropout:     --aug_drop_prob 0.15   (randomly zeros UI rows)
  - Regularisation:      --dropout 0.3 --weight_decay 1e-3
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset, random_split


# ═══════════════════════════════════════════════════════════
#  SECTION 1 — DATASET
# ═══════════════════════════════════════════════════════════

ACTION_NOOP     = 0
ACTION_CLICK    = 1
ACTION_KEYBOARD = 2

ELEM_FEATURES = 6
VOCAB_SIZE    = 10_000
DEFAULT_W     = 1920
DEFAULT_H     = 1200


def _encode_element(elem: dict, W: float, H: float) -> List[float]:
    x1, y1, x2, y2 = (float(v) for v in elem.get("bbox", [0, 0, 0, 0])[:4])
    conf      = float(elem.get("confidence", 0.0))
    text_hash = (abs(hash(elem.get("text", "") or "")) % VOCAB_SIZE) / VOCAB_SIZE
    return [x1 / W, y1 / H, x2 / W, y2 / H, conf, text_hash]


def encode_state(state: dict, max_elements: int = 128) -> torch.Tensor:
    """State dict -> FloatTensor (max_elements, 6), zero-padded."""
    res  = state.get("screen_resolution", [DEFAULT_W, DEFAULT_H])
    W, H = float(res[0]) or DEFAULT_W, float(res[1]) or DEFAULT_H
    rows = [_encode_element(e, W, H) for e in state.get("elements", [])[:max_elements]]
    while len(rows) < max_elements:
        rows.append([0.0] * ELEM_FEATURES)
    return torch.tensor(rows, dtype=torch.float32)


def _decode_actions(
    mouse: dict, keyboard: dict, W: float, H: float
) -> Tuple[int, float, float, float]:
    """Return (action_type, cx_norm, cy_norm, key_norm)."""
    mouse_actions   = mouse.get("actions", [])
    keyboard_groups = keyboard.get("actions", [])
    key_count = sum(len(g.get("strokes", [])) for g in keyboard_groups)
    clicks = [a for a in mouse_actions if a.get("type") == "click"]
    if clicks:
        pos = clicks[0].get("position", [0, 0])
        return ACTION_CLICK, float(pos[0]) / W, float(pos[1]) / H, min(key_count / 100.0, 1.0)
    if key_count > 0:
        return ACTION_KEYBOARD, 0.0, 0.0, min(key_count / 100.0, 1.0)
    return ACTION_NOOP, 0.0, 0.0, 0.0


def _load_trace(fpath: Path) -> dict | None:
    try:
        with fpath.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"[Dataset] Skipping {fpath.name}: {exc}")
        return None


class TrajectoryDataset(Dataset):
    """
    Sliding-window trajectory samples from consecutive trace JSON files.

    Each sample (window of H traces):
      states         : FloatTensor (H, max_elements, 6)
      past_act_types : LongTensor  (H-1,)
      past_act_cont  : FloatTensor (H-1, 3)  [cx, cy, key_norm]
      target_type    : LongTensor  scalar
      target_click   : FloatTensor (2,)
      target_key     : FloatTensor scalar
    """

    def __init__(
        self,
        data_dir: str | Path,
        max_elements: int = 128,
        hist_len: int = 4,
        glob: str = "*.json",
        aug_drop_prob: float = 0.0,   # probability of zeroing a UI element row
    ):
        self.max_elements  = max_elements
        self.hist_len      = hist_len
        self.aug_drop_prob = aug_drop_prob

        files = sorted(Path(data_dir).glob(glob))
        if not files:
            raise FileNotFoundError(f"No trace JSONs in {data_dir!r}")

        all_states:  List[torch.Tensor]              = []
        all_actions: List[Tuple[int, float, float, float]] = []

        for fpath in files:
            trace = _load_trace(fpath)
            if trace is None:
                continue
            state = trace.get("state", {})
            res   = state.get("screen_resolution", [DEFAULT_W, DEFAULT_H])
            W     = float(res[0]) or DEFAULT_W
            H_px  = float(res[1]) or DEFAULT_H
            all_states.append(encode_state(state, max_elements))
            all_actions.append(_decode_actions(
                trace.get("mouse", {}), trace.get("keyboard", {}), W, H_px
            ))

        N = len(all_states)
        if N < hist_len:
            raise ValueError(
                f"Need >= {hist_len} traces (hist_len={hist_len}), found {N}."
            )

        self._samples = []
        for i in range(N - hist_len + 1):
            ctx = all_actions[i : i + hist_len - 1]
            tgt = all_actions[i + hist_len - 1]
            self._samples.append((
                torch.stack(all_states[i : i + hist_len]),   # (H, T, 6)
                [a[0] for a in ctx],                          # past types
                [[a[1], a[2], a[3]] for a in ctx],            # past cont
                tgt[0], (tgt[1], tgt[2]), tgt[3],            # target
            ))

    def __len__(self) -> int:
        return len(self._samples)

    def __getitem__(self, idx: int):
        states, p_types, p_cont, tgt_type, tgt_click, tgt_key = self._samples[idx]

        # Element dropout augmentation
        if self.aug_drop_prob > 0.0:
            states = states.clone()
            mask = torch.rand(states.shape[:3]) < self.aug_drop_prob  # (H, T)
            states[mask] = 0.0

        H = self.hist_len
        if H > 1:
            past_types = torch.tensor(p_types, dtype=torch.long)
            past_cont  = torch.tensor(p_cont,  dtype=torch.float32)
        else:
            past_types = torch.zeros(0, dtype=torch.long)
            past_cont  = torch.zeros(0, 3,     dtype=torch.float32)

        return (
            states,
            past_types,
            past_cont,
            torch.tensor(tgt_type,        dtype=torch.long),
            torch.tensor(list(tgt_click), dtype=torch.float32),
            torch.tensor(tgt_key,         dtype=torch.float32),
        )

    def class_counts(self) -> dict:
        from collections import Counter
        names = {0: "no_op", 1: "click", 2: "keyboard"}
        return {names[k]: v for k, v in Counter(s[3] for s in self._samples).items()}

    def __repr__(self) -> str:
        return (
            f"TrajectoryDataset(samples={len(self)}, hist_len={self.hist_len}, "
            f"max_elements={self.max_elements}, class_counts={self.class_counts()})"
        )


# ═══════════════════════════════════════════════════════════
#  SECTION 2 — MODEL
# ═══════════════════════════════════════════════════════════

class PolicyOutput(NamedTuple):
    type_logits: torch.Tensor   # (B, num_actions)
    click_xy:    torch.Tensor   # (B, 2)
    key_count:   torch.Tensor   # (B, 1)


class StateEncoder(nn.Module):
    """Mean-pool UI elements (mask padding) -> d_model."""
    def __init__(self, elem_features: int, d_model: int):
        super().__init__()
        self.proj = nn.Linear(elem_features, d_model)
        self.norm = nn.LayerNorm(d_model)

    def forward(self, state: torch.Tensor) -> torch.Tensor:
        mask  = (state.abs().sum(-1) > 0).float().unsqueeze(-1)  # (B, T, 1)
        denom = mask.sum(1).clamp(min=1.0)
        return self.norm((self.proj(state) * mask).sum(1) / denom)


class ActionEncoder(nn.Module):
    """Encode (type_id, cx, cy, key_norm) -> d_model."""
    def __init__(self, num_actions: int, d_model: int):
        super().__init__()
        half = d_model // 2
        self.type_emb  = nn.Embedding(num_actions + 1, half, padding_idx=num_actions)
        self.cont_proj = nn.Linear(3, d_model - half)
        self.out_proj  = nn.Linear(d_model, d_model)
        self.norm      = nn.LayerNorm(d_model)

    def forward(self, types: torch.Tensor, cont: torch.Tensor) -> torch.Tensor:
        return self.norm(self.out_proj(
            torch.cat([self.type_emb(types), self.cont_proj(cont)], dim=-1)
        ))


class TransformerAgentNetwork(nn.Module):
    """
    Causal Transformer Agent.

    Interleaves state and action tokens [s1,a1,s2,a2,...,sH] and uses
    causal self-attention to predict the next action from the last state.
    """

    def __init__(
        self,
        elem_features:   int   = 6,
        max_elements:    int   = 128,
        d_model:         int   = 128,
        nhead:           int   = 4,
        num_layers:      int   = 4,
        dim_feedforward: int   = 256,
        dropout:         float = 0.1,
        num_actions:     int   = 3,
        hist_len:        int   = 4,
    ):
        super().__init__()
        self.d_model     = d_model
        self.max_elements = max_elements
        self.hist_len    = hist_len
        self.num_actions = num_actions

        self.state_enc  = StateEncoder(elem_features, d_model)
        self.action_enc = ActionEncoder(num_actions, d_model)
        self.pos_enc    = nn.Embedding(2 * hist_len, d_model)

        enc_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=dim_feedforward,
            dropout=dropout, batch_first=True, norm_first=True,
        )
        self.encoder  = nn.TransformerEncoder(enc_layer, num_layers=num_layers,
                                               enable_nested_tensor=False)
        self.out_norm  = nn.LayerNorm(d_model)
        self.type_head  = nn.Linear(d_model, num_actions)
        self.click_head = nn.Linear(d_model, 2)
        self.key_head   = nn.Linear(d_model, 1)

        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None: nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Embedding):
                nn.init.normal_(m.weight, std=0.02)

    def forward(
        self,
        states:     torch.Tensor,   # (B, H, T, 6)
        past_types: torch.Tensor,   # (B, H-1)
        past_cont:  torch.Tensor,   # (B, H-1, 3)
    ) -> PolicyOutput:
        B, H = states.shape[:2]

        # Encode states: (B, H, d)
        s = self.state_enc(states.view(B * H, *states.shape[2:])).view(B, H, -1)

        # Build sequence [s1, a1, s2, a2, ..., sH]
        seq_len = 2 * H - 1
        tokens  = torch.zeros(B, seq_len, self.d_model, device=states.device)
        tokens[:, 0::2] = s
        if H > 1:
            tokens[:, 1::2] = self.action_enc(past_types, past_cont)

        # Positional encoding
        pos    = torch.arange(seq_len, device=states.device).unsqueeze(0)
        tokens = tokens + self.pos_enc(pos)

        # Causal attention
        mask = nn.Transformer.generate_square_subsequent_mask(seq_len, device=states.device)
        out  = self.encoder(tokens, mask=mask, is_causal=True)

        last = self.out_norm(out[:, -1])
        return PolicyOutput(
            self.type_head(last),
            torch.sigmoid(self.click_head(last)),
            torch.sigmoid(self.key_head(last)),
        )

    def make_empty_history(self, B: int, device: torch.device):
        H = self.hist_len
        return (
            torch.full((B, H - 1), self.num_actions, dtype=torch.long,  device=device),
            torch.zeros(B, H - 1, 3,                  dtype=torch.float32, device=device),
        )

    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)

    def __repr__(self) -> str:
        return (
            f"TransformerAgentNetwork(d_model={self.d_model}, "
            f"hist_len={self.hist_len}, params={self.count_parameters():,})"
        )


# Backward compat alias
TransformerPolicyNetwork = TransformerAgentNetwork


# ═══════════════════════════════════════════════════════════
#  SECTION 3 — TRAINING
# ═══════════════════════════════════════════════════════════

def _masked_mse(pred, target, mask) -> torch.Tensor:
    if mask.sum() == 0:
        return torch.tensor(0.0, device=pred.device, requires_grad=True)
    return nn.functional.mse_loss(pred[mask], target[mask])


def _run_epoch(model, loader, optimizer, device, lambda_click, lambda_key, label_smoothing):
    is_train = optimizer is not None
    model.train(is_train)
    ce = nn.CrossEntropyLoss(label_smoothing=label_smoothing)
    totals = dict(loss=0.0, type=0.0, click=0.0, key=0.0, correct=0, samples=0, batches=0)

    ctx = torch.enable_grad() if is_train else torch.no_grad()
    with ctx:
        for states, p_types, p_cont, tgt_types, tgt_clicks, tgt_keys in loader:
            states, p_types, p_cont = states.to(device), p_types.to(device), p_cont.to(device)
            tgt_types, tgt_clicks, tgt_keys = (
                tgt_types.to(device), tgt_clicks.to(device), tgt_keys.to(device)
            )
            out    = model(states, p_types, p_cont)
            l_type  = ce(out.type_logits, tgt_types)
            l_click = _masked_mse(out.click_xy, tgt_clicks, tgt_types == ACTION_CLICK)
            l_key   = _masked_mse(out.key_count.squeeze(-1), tgt_keys, tgt_types == ACTION_KEYBOARD)
            loss    = l_type + lambda_click * l_click + lambda_key * l_key

            if is_train:
                optimizer.zero_grad(); loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()

            totals["loss"]   += loss.item()
            totals["type"]   += l_type.item()
            totals["click"]  += l_click.item()
            totals["key"]    += l_key.item()
            totals["correct"] += (out.type_logits.argmax(-1) == tgt_types).sum().item()
            totals["samples"] += tgt_types.size(0)
            totals["batches"] += 1

    n = max(totals["batches"], 1)
    return {
        "loss":     totals["loss"]  / n,
        "l_type":   totals["type"]  / n,
        "l_click":  totals["click"] / n,
        "l_key":    totals["key"]   / n,
        "accuracy": totals["correct"] / max(totals["samples"], 1),
    }


def train(
    data_dir: str = "data/output/traces/live",
    epochs: int = 20,
    batch_size: int = 16,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    max_elements: int = 128,
    hist_len: int = 4,
    val_split: float = 0.2,
    save_path: str = "data/models/transformer_bc.pt",
    lambda_click: float = 1.0,
    lambda_key: float = 0.5,
    label_smoothing: float = 0.1,
    aug_drop_prob: float = 0.0,
    seed: int = 42,
    d_model: int = 128,
    nhead: int = 4,
    num_layers: int = 4,
    dim_feedforward: int = 256,
    dropout: float = 0.1,
    device_str: str = "auto",
    verbose: bool = True,
) -> TransformerAgentNetwork:
    """
    Train TransformerAgentNetwork via Behavioral Cloning.

    Anti-overfitting parameters
    ---------------------------
    label_smoothing : float (default 0.1)  — soften CrossEntropy targets
    aug_drop_prob   : float (default 0.0)  — randomly zero UI element rows
    dropout         : float (default 0.1)  — increase to 0.3+ for small data
    weight_decay    : float (default 1e-4) — increase to 1e-3 for small data
    d_model / num_layers — shrink to 64 / 2 for very small datasets
    """
    torch.manual_seed(seed)
    if device_str == "auto":
        device = (torch.device("cuda") if torch.cuda.is_available()
                  else torch.device("mps") if torch.backends.mps.is_available()
                  else torch.device("cpu"))
    else:
        device = torch.device(device_str)

    if verbose:
        print(f"[train] Device: {device}")

    dataset = TrajectoryDataset(
        data_dir, max_elements=max_elements, hist_len=hist_len,
        aug_drop_prob=aug_drop_prob,
    )
    if verbose:
        print(f"[train] {dataset}")

    n_val   = max(1, int(len(dataset) * val_split))
    n_train = len(dataset) - n_val
    g       = torch.Generator().manual_seed(seed)
    train_ds, val_ds = random_split(dataset, [n_train, n_val], generator=g)

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True,  drop_last=False)
    val_loader   = DataLoader(val_ds,   batch_size=batch_size, shuffle=False, drop_last=False)

    model = TransformerAgentNetwork(
        elem_features=6, max_elements=max_elements, d_model=d_model,
        nhead=nhead, num_layers=num_layers, dim_feedforward=dim_feedforward,
        dropout=dropout, hist_len=hist_len,
    ).to(device)

    if verbose:
        print(f"[train] {model}")

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=lr / 100)

    best_val_loss = math.inf
    save_path_p   = Path(save_path)
    save_path_p.parent.mkdir(parents=True, exist_ok=True)

    for epoch in range(1, epochs + 1):
        train_m = _run_epoch(model, train_loader, optimizer, device, lambda_click, lambda_key, label_smoothing)
        val_m   = _run_epoch(model, val_loader,   None,      device, lambda_click, lambda_key, label_smoothing)
        scheduler.step()

        if verbose:
            print(
                f"Epoch {epoch:>3}/{epochs}  |  "
                f"train_loss={train_m['loss']:.4f}  train_acc={train_m['accuracy']:.3f}  |  "
                f"val_loss={val_m['loss']:.4f}  val_acc={val_m['accuracy']:.3f}"
            )

        if val_m["loss"] < best_val_loss:
            best_val_loss = val_m["loss"]
            torch.save({
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "val_loss": best_val_loss,
                "hyperparams": {
                    "elem_features": 6, "max_elements": max_elements,
                    "d_model": d_model, "nhead": nhead, "num_layers": num_layers,
                    "dim_feedforward": dim_feedforward, "dropout": dropout,
                    "hist_len": hist_len,
                },
            }, save_path_p)
            if verbose:
                print(f"           -> Saved checkpoint (val_loss={best_val_loss:.4f})")

    if verbose:
        print(f"[train] Done.  Best val_loss={best_val_loss:.4f} -> {save_path_p}")

    ckpt = torch.load(save_path_p, map_location=device, weights_only=True)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    return model


# ═══════════════════════════════════════════════════════════
#  SECTION 4 — INFERENCE
# ═══════════════════════════════════════════════════════════

_model_cache: Dict[str, TransformerAgentNetwork] = {}
_ACTION_LABELS = {0: "no_op", 1: "click", 2: "keyboard"}
_ACTION_IDS    = {"no_op": 0, "click": 1, "keyboard": 2}


def _load_model(model_path: str, device: torch.device) -> TransformerAgentNetwork:
    key = f"{model_path}:{device}"
    if key not in _model_cache:
        ckpt = torch.load(model_path, map_location=device, weights_only=True)
        hp   = ckpt.get("hyperparams", {})
        m    = TransformerAgentNetwork(
            elem_features=hp.get("elem_features", 6), max_elements=hp.get("max_elements", 128),
            d_model=hp.get("d_model", 128), nhead=hp.get("nhead", 4),
            num_layers=hp.get("num_layers", 4), dim_feedforward=hp.get("dim_feedforward", 256),
            dropout=hp.get("dropout", 0.1), hist_len=hp.get("hist_len", 4),
        ).to(device)
        m.load_state_dict(ckpt["model_state_dict"])
        m.eval()
        _model_cache[key] = m
    return _model_cache[key]


def predict(
    state: dict,
    history: Optional[List[Dict[str, Any]]] = None,
    model_path: str = "data/models/transformer_bc.pt",
    device_str: str = "auto",
    clear_cache: bool = False,
) -> Dict[str, Any]:
    """
    Predict the next GUI action.

    Parameters
    ----------
    state   : current state dict from a trace JSON.
    history : list of previous step dicts (oldest first), each containing:
              {"state": <dict>, "action_type": <int|str>,
               "click_xy": [x, y], "key_count": <int>}
              Up to hist_len-1 entries used; zero-padded if fewer.
    """
    if device_str == "auto":
        device = (torch.device("cuda") if torch.cuda.is_available()
                  else torch.device("mps") if torch.backends.mps.is_available()
                  else torch.device("cpu"))
    else:
        device = torch.device(device_str)

    if clear_cache:
        _model_cache.pop(f"{model_path}:{device}", None)

    model        = _load_model(model_path, device)
    H            = model.hist_len
    max_elements = model.max_elements
    num_actions  = model.num_actions

    ctx = (history or [])[-(H - 1):]  # last H-1 items

    # Build state tensor list (H-1 context + 1 current)
    ctx_tensors = [encode_state(item["state"], max_elements) for item in ctx]
    while len(ctx_tensors) < H - 1:
        ctx_tensors.insert(0, torch.zeros(max_elements, ELEM_FEATURES))
    all_states = torch.stack(ctx_tensors + [encode_state(state, max_elements)])  # (H, T, 6)

    # Build action tensors
    p_types_list, p_cont_list = [], []
    for item in ctx:
        at  = item.get("action_type", 0)
        at  = _ACTION_IDS.get(at, at) if isinstance(at, str) else at
        cxy = item.get("click_xy", [0.0, 0.0])
        kn  = min(float(item.get("key_count", 0)) / 100.0, 1.0)
        res = item.get("state", {}).get("screen_resolution", [DEFAULT_W, DEFAULT_H])
        W   = float(res[0]) or DEFAULT_W
        H_px = float(res[1]) or DEFAULT_H
        cx   = float(cxy[0]) / W   if cxy[0] > 1 else float(cxy[0])
        cy   = float(cxy[1]) / H_px if cxy[1] > 1 else float(cxy[1])
        p_types_list.append(at)
        p_cont_list.append([cx, cy, kn])
    while len(p_types_list) < H - 1:
        p_types_list.insert(0, num_actions)
        p_cont_list.insert(0, [0.0, 0.0, 0.0])

    p_types = torch.tensor(p_types_list, dtype=torch.long).unsqueeze(0).to(device)
    p_cont  = torch.tensor(p_cont_list,  dtype=torch.float32).unsqueeze(0).to(device)
    s_batch = all_states.unsqueeze(0).to(device)

    with torch.no_grad():
        out = model(s_batch, p_types, p_cont)

    idx = out.type_logits.argmax(-1).item()
    res = state.get("screen_resolution", [DEFAULT_W, DEFAULT_H])
    W   = float(res[0]) or DEFAULT_W
    H_px = float(res[1]) or DEFAULT_H

    result: Dict[str, Any] = {"action_type": _ACTION_LABELS.get(idx, "no_op")}
    if idx == ACTION_CLICK:
        cx, cy = out.click_xy[0].tolist()
        result["click_position"] = [round(cx * W, 1), round(cy * H_px, 1)]
    elif idx == ACTION_KEYBOARD:
        result["key_count"] = max(1, round(out.key_count[0, 0].item() * 100))
    return result


# ═══════════════════════════════════════════════════════════
#  SECTION 5 — CLI
# ═══════════════════════════════════════════════════════════

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="TransformerAgentNetwork — train or predict")
    p.add_argument("--mode",            choices=["train", "predict"], default="train")
    # Shared
    p.add_argument("--model_path",      default="data/models/transformer_bc.pt")
    p.add_argument("--device",          default="auto", dest="device_str")
    # Train args
    p.add_argument("--data_dir",        default="data/output/traces/live")
    p.add_argument("--epochs",          default=20,   type=int)
    p.add_argument("--batch_size",      default=16,   type=int)
    p.add_argument("--lr",              default=1e-3, type=float)
    p.add_argument("--weight_decay",    default=1e-4, type=float)
    p.add_argument("--max_elements",    default=128,  type=int)
    p.add_argument("--hist_len",        default=4,    type=int)
    p.add_argument("--val_split",       default=0.2,  type=float)
    p.add_argument("--label_smoothing", default=0.1,  type=float)
    p.add_argument("--aug_drop_prob",   default=0.0,  type=float)
    p.add_argument("--d_model",         default=128,  type=int)
    p.add_argument("--nhead",           default=4,    type=int)
    p.add_argument("--num_layers",      default=4,    type=int)
    p.add_argument("--dim_feedforward", default=256,  type=int)
    p.add_argument("--dropout",         default=0.1,  type=float)
    # Predict args
    p.add_argument("--trace_path",      default=None)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.mode == "train":
        train(
            data_dir=args.data_dir, epochs=args.epochs, batch_size=args.batch_size,
            lr=args.lr, weight_decay=args.weight_decay, max_elements=args.max_elements,
            hist_len=args.hist_len, val_split=args.val_split, save_path=args.model_path,
            label_smoothing=args.label_smoothing, aug_drop_prob=args.aug_drop_prob,
            d_model=args.d_model, nhead=args.nhead, num_layers=args.num_layers,
            dim_feedforward=args.dim_feedforward, dropout=args.dropout,
            device_str=args.device_str,
        )
    else:
        if not args.trace_path:
            raise SystemExit("--trace_path required for predict mode")
        with open(args.trace_path, encoding="utf-8") as f:
            trace = json.load(f)
        result = predict(trace.get("state", {}), model_path=args.model_path,
                         device_str=args.device_str)
        print(json.dumps(result, indent=2))
