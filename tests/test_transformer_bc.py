"""
tests/test_transformer_bc.py
============================
Smoke tests for the Transformer Policy Network (Behavioral Cloning).

Run from the repo root:
    python -m pytest tests/test_transformer_bc.py -v

All tests run on CPU with a small synthetic dataset so no GPU is required.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
import torch

# ── Make sure the repo root is on the path ──────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from components.learning_models.transformer.dataset import (
    TraceDataset,
    encode_state,
    ACTION_CLICK,
    ACTION_KEYBOARD,
    ACTION_NOOP,
    ELEM_FEATURES,
)
from components.learning_models.transformer.model import TransformerPolicyNetwork
from components.learning_models.transformer.train import train
from components.learning_models.transformer.predict import predict


# ── Paths ───────────────────────────────────────────────────────────────────
LIVE_TRACES = ROOT / "data" / "output" / "traces" / "live"


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_synthetic_trace(
    n_elements: int = 20,
    action: str = "click",  # "click" | "keyboard" | "noop"
) -> dict:
    """Build a minimal trace dict that matches the live trace schema."""
    elements = [
        {
            "element_id": f"label_{i}",
            "type": "label",
            "bbox": [i * 10, i * 5, i * 10 + 50, i * 5 + 20],
            "text": f"element_{i}",
            "confidence": 0.85,
            "enabled": True,
            "visible": True,
            "metadata": {"source": "ocr"},
        }
        for i in range(n_elements)
    ]

    trace = {
        "trace_id": "test_trace",
        "type": "gui",
        "state": {
            "application": "TestApp",
            "screen_resolution": [1920, 1200],
            "focused_element_id": None,
            "elements": elements,
        },
        "mouse": {"actions": []},
        "keyboard": {"actions": []},
        "diff": {"added": [], "removed": [], "changed": []},
    }

    if action == "click":
        trace["mouse"]["actions"] = [
            {"id": "mouse_action_0000", "position": [960, 600], "type": "click",
             "timestamp": "2026-03-08T14:57:00.000000"}
        ]
    elif action == "keyboard":
        trace["keyboard"]["actions"] = [
            {"strokes": ["H", "e", "l", "l", "o"]}
        ]
    return trace


def _write_traces(directory: Path, n: int = 8) -> None:
    """Write n synthetic trace JSON files to *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    actions = ["click", "keyboard", "noop"]
    for i in range(n):
        trace = _make_synthetic_trace(
            n_elements=15 + (i % 5),
            action=actions[i % 3],
        )
        trace["trace_id"] = f"test_step_{i:04d}"
        with (directory / f"test_step_{i:04d}.json").open("w") as f:
            json.dump(trace, f)


# ─────────────────────────────────────────────────────────────────────────────
#  Test: encode_state
# ─────────────────────────────────────────────────────────────────────────────

def test_encode_state_shape():
    """encode_state must return a (max_elements, 6) float32 tensor."""
    trace = _make_synthetic_trace(n_elements=30)
    state = trace["state"]
    MAX_ELEM = 64
    tensor = encode_state(state, max_elements=MAX_ELEM)
    assert tensor.shape == (MAX_ELEM, ELEM_FEATURES), (
        f"Expected ({MAX_ELEM}, {ELEM_FEATURES}), got {tuple(tensor.shape)}"
    )
    assert tensor.dtype == torch.float32


def test_encode_state_normalised():
    """All bbox-derived values must be in [0, 1] for standard screen sizes."""
    trace = _make_synthetic_trace(n_elements=10)
    tensor = encode_state(trace["state"], max_elements=16)
    # Columns 0-3 are normalised coordinates
    bbox_part = tensor[:, :4]
    valid = bbox_part[bbox_part != 0.0]  # exclude padding rows
    assert (valid >= 0.0).all() and (valid <= 1.0).all()


def test_encode_state_padding():
    """Fewer elements than max_elements should produce zero-padded rows."""
    trace = _make_synthetic_trace(n_elements=5)
    tensor = encode_state(trace["state"], max_elements=20)
    # Last 15 rows must be all-zero (padding)
    assert tensor[5:].abs().sum().item() == 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  Test: TraceDataset
# ─────────────────────────────────────────────────────────────────────────────

def test_dataset_from_live_traces():
    """Dataset must load without error from the real live trace folder."""
    if not LIVE_TRACES.exists() or not any(LIVE_TRACES.glob("*.json")):
        pytest.skip("No live trace files found — skipping live-data test.")

    ds = TraceDataset(data_dir=LIVE_TRACES, max_elements=64)
    assert len(ds) > 0, "Dataset should have at least one sample."

    state_t, atype, click_xy, key_count = ds[0]
    assert state_t.shape == (64, ELEM_FEATURES)
    assert atype.dtype == torch.long
    assert click_xy.shape == (2,)
    assert key_count.ndim == 0  # scalar


def test_dataset_from_synthetic(tmp_path):
    """Dataset must handle synthetic traces and yield correct tensor shapes."""
    _write_traces(tmp_path, n=10)
    ds = TraceDataset(data_dir=tmp_path, max_elements=32)
    assert len(ds) == 10

    state_t, atype, click_xy, key_count = ds[0]
    assert state_t.shape == (32, ELEM_FEATURES)
    assert atype.item() in {ACTION_NOOP, ACTION_CLICK, ACTION_KEYBOARD}
    assert click_xy.shape == (2,)
    assert 0.0 <= key_count.item() <= 1.0


def test_dataset_class_counts(tmp_path):
    """class_counts() must return non-empty dict with valid keys."""
    _write_traces(tmp_path, n=9)  # 3 of each type
    ds = TraceDataset(data_dir=tmp_path, max_elements=32)
    counts = ds.class_counts()
    assert isinstance(counts, dict)
    assert all(k in {"no_op", "click", "keyboard"} for k in counts)
    assert sum(counts.values()) == len(ds)


# ─────────────────────────────────────────────────────────────────────────────
#  Test: TransformerPolicyNetwork
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def small_model() -> TransformerPolicyNetwork:
    """A tiny model for fast shape-checking tests."""
    return TransformerPolicyNetwork(
        elem_features=6,
        max_elements=32,
        d_model=32,
        nhead=2,
        num_layers=2,
        dim_feedforward=64,
        dropout=0.0,
    )


def test_model_output_shapes(small_model):
    """Forward pass must produce (B, 3), (B, 2), (B, 1) tensors."""
    B, T = 4, 32
    x   = torch.randn(B, T, 6)
    out = small_model(x)
    assert out.type_logits.shape == (B, 3),  f"type_logits shape mismatch: {out.type_logits.shape}"
    assert out.click_xy.shape    == (B, 2),  f"click_xy shape mismatch: {out.click_xy.shape}"
    assert out.key_count.shape   == (B, 1),  f"key_count shape mismatch: {out.key_count.shape}"


def test_model_click_xy_in_range(small_model):
    """click_xy values must be in [0, 1] (sigmoid output)."""
    x   = torch.randn(4, 32, 6)
    out = small_model(x)
    assert out.click_xy.min().item() >= 0.0
    assert out.click_xy.max().item() <= 1.0


def test_model_padding_mask(small_model):
    """Padding mask must be detected correctly from zero-padded rows."""
    B, T = 2, 32
    x = torch.randn(B, T, 6)
    # Pad the last 10 rows of each sample
    x[:, 22:, :] = 0.0
    mask = small_model.build_padding_mask(x)
    assert mask.shape == (B, T)
    assert mask[:, 22:].all(), "Padded rows should be True in mask"
    assert not mask[:, :22].any(), "Non-padded rows should be False in mask"


def test_model_param_count(small_model):
    """count_parameters() must return a positive integer."""
    n = small_model.count_parameters()
    assert isinstance(n, int) and n > 0


def test_model_no_nan(small_model):
    """No NaN or Inf in any output head for random inputs."""
    x   = torch.randn(8, 32, 6)
    out = small_model(x)
    for name, tensor in [
        ("type_logits", out.type_logits),
        ("click_xy",    out.click_xy),
        ("key_count",   out.key_count),
    ]:
        assert not torch.isnan(tensor).any(), f"NaN in {name}"
        assert not torch.isinf(tensor).any(), f"Inf in {name}"


# ─────────────────────────────────────────────────────────────────────────────
#  Test: train()
# ─────────────────────────────────────────────────────────────────────────────

def test_train_creates_checkpoint(tmp_path):
    """train() must create a checkpoint file and the returned model must infer."""
    data_dir  = tmp_path / "traces"
    save_path = str(tmp_path / "model.pt")
    _write_traces(data_dir, n=8)

    model = train(
        data_dir   = str(data_dir),
        epochs     = 2,
        batch_size = 4,
        max_elements = 32,
        d_model    = 32,
        nhead      = 2,
        num_layers = 2,
        dim_feedforward = 64,
        save_path  = save_path,
        verbose    = False,
    )

    assert Path(save_path).exists(), "Checkpoint file was not created."
    assert isinstance(model, TransformerPolicyNetwork)


def test_train_loss_is_finite(tmp_path):
    """Training loss must be a finite number (no NaN / Inf)."""
    import math
    from components.learning_models.transformer.train import _run_epoch
    from torch.utils.data import DataLoader

    data_dir = tmp_path / "traces"
    _write_traces(data_dir, n=6)

    ds    = TraceDataset(str(data_dir), max_elements=32)
    model = TransformerPolicyNetwork(
        max_elements=32, d_model=32, nhead=2, num_layers=2, dim_feedforward=64
    )
    loader = DataLoader(ds, batch_size=3, shuffle=False)
    opt    = torch.optim.AdamW(model.parameters(), lr=1e-3)
    device = torch.device("cpu")

    metrics = _run_epoch(model, loader, opt, device, lambda_click=1.0, lambda_key=0.5)
    assert math.isfinite(metrics["loss"]), f"Loss not finite: {metrics['loss']}"


# ─────────────────────────────────────────────────────────────────────────────
#  Test: predict()
# ─────────────────────────────────────────────────────────────────────────────

def test_predict_returns_valid_dict(tmp_path):
    """predict() must return a dict with 'action_type' key and correct extras."""
    data_dir  = tmp_path / "traces"
    save_path = str(tmp_path / "model.pt")
    _write_traces(data_dir, n=8)

    # Train quickly
    train(
        data_dir      = str(data_dir),
        epochs        = 1,
        batch_size    = 4,
        max_elements  = 32,
        d_model       = 32,
        nhead         = 2,
        num_layers    = 2,
        dim_feedforward = 64,
        save_path     = save_path,
        verbose       = False,
    )

    # Predict on a synthetic state
    state  = _make_synthetic_trace(action="click")["state"]
    result = predict(state, model_path=save_path, max_elements=32, clear_cache=True)

    assert "action_type" in result
    assert result["action_type"] in {"no_op", "click", "keyboard"}

    if result["action_type"] == "click":
        assert "click_position" in result
        pos = result["click_position"]
        assert len(pos) == 2
        # Pixel values should be positive and not wildly out of range
        assert 0 <= pos[0] <= 1920
        assert 0 <= pos[1] <= 1200

    elif result["action_type"] == "keyboard":
        assert "key_count" in result
        assert isinstance(result["key_count"], int) and result["key_count"] >= 1


def test_predict_on_live_trace(tmp_path):
    """End-to-end: train on live data; predict on first live trace."""
    if not LIVE_TRACES.exists() or not any(LIVE_TRACES.glob("*.json")):
        pytest.skip("No live trace files — skipping live end-to-end test.")

    save_path = str(tmp_path / "model.pt")
    train(
        data_dir      = str(LIVE_TRACES),
        epochs        = 1,
        batch_size    = 4,
        max_elements  = 64,
        d_model       = 32,
        nhead         = 2,
        num_layers    = 2,
        dim_feedforward = 64,
        save_path     = save_path,
        verbose       = False,
    )

    first = sorted(LIVE_TRACES.glob("*.json"))[0]
    with first.open(encoding="utf-8") as f:
        trace = json.load(f)

    result = predict(trace["state"], model_path=save_path, max_elements=64, clear_cache=True)
    assert "action_type" in result
    assert result["action_type"] in {"no_op", "click", "keyboard"}
