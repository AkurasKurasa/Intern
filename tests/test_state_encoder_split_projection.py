"""
Regression test for StateEncoder's structured/embedding split projection.

Investigated 2026-08-07 while chasing the "right neighborhood, wrong exact
field" confusion pattern seen in the corrected 48.1% click-accuracy eval
(e.g. "DL Expiration" -> predicted "DL Issuing State", "Comprehensive
Deductible" -> predicted "Collision Deductible" — near-identical text embeddings,
distinct bbox positions). Per-element input is [11 structured features
(bbox, control type, filled/attempted flags, ...)] + [384-dim text embedding],
and the old StateEncoder ran ALL 395 dims through a single nn.Linear. With the
text embedding outnumbering structured features 35-to-1, gradient descent has
a much easier time finding shortcuts through the huge embedding block than
learning to use the 4 bbox dims for fine-grained disambiguation — a structural
reason a text-similarity shortcut would win over precise positional
targeting, independent of how much training data exists.

Fix: project structured features and the text embedding through separate
linear layers (each gets a fixed share of d_model) before combining, so
position/type/filled signal can't be diluted out by the embedding's sheer
dimensionality. General architecture change, not specific to this form.
"""
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from intelligence.model.transformer import StateEncoder, ELEM_FEATURES, _EMBED_DIM


def _rand_state(batch=2, elems=5):
    x = torch.rand(batch, elems, ELEM_FEATURES)
    x[..., 0] = 1.0  # is_real flag — treat all rows as real elements
    return x


def test_output_shape_and_mask_unchanged():
    enc = StateEncoder(ELEM_FEATURES, d_model=64)
    x = _rand_state()
    out, mask = enc(x)
    assert out.shape == (2, 5, 64)
    assert mask.shape == (2, 5)
    assert mask.all()  # every row flagged is_real


def test_padding_rows_are_zeroed():
    enc = StateEncoder(ELEM_FEATURES, d_model=64)
    x = _rand_state()
    x[:, -1, 0] = 0.0  # last row is padding (is_real=0)
    out, mask = enc(x)
    assert not mask[:, -1].any()
    assert torch.all(out[:, -1, :] == 0.0)


def test_structured_features_alone_change_the_output():
    """Perturbing only bbox/type/flags (not the embedding) must move the output —
    proves the structured pathway isn't being drowned out or ignored."""
    enc = StateEncoder(ELEM_FEATURES, d_model=64)
    enc.eval()
    struct_dim = ELEM_FEATURES - _EMBED_DIM
    x = _rand_state()
    x2 = x.clone()
    x2[..., 1:struct_dim] = torch.rand_like(x2[..., 1:struct_dim])  # keep is_real, change bbox/type/flags
    out1, _ = enc(x)
    out2, _ = enc(x2)
    assert not torch.allclose(out1, out2), "changing only structured features should change the embedding"


def test_embedding_features_alone_change_the_output():
    enc = StateEncoder(ELEM_FEATURES, d_model=64)
    enc.eval()
    struct_dim = ELEM_FEATURES - _EMBED_DIM
    x = _rand_state()
    x2 = x.clone()
    x2[..., struct_dim:] = torch.rand_like(x2[..., struct_dim:])
    out1, _ = enc(x)
    out2, _ = enc(x2)
    assert not torch.allclose(out1, out2), "changing only the text embedding should change the output"


def test_gradients_flow_to_both_projections():
    """A single shared linear layer can silently starve one input block of
    gradient signal if the optimizer favors the higher-dimensional one; the
    split ensures each pathway has its own weights that must receive gradient
    on every backward pass touching that feature block."""
    enc = StateEncoder(ELEM_FEATURES, d_model=64)
    x = _rand_state()
    out, _ = enc(x)
    out.sum().backward()
    # LayerNorm can push either pathway's gradient magnitude very small for a
    # given random draw — checking wiring (grad populated at all), not size,
    # is what actually proves both pathways sit in the backward graph.
    assert enc.struct_proj.weight.grad is not None
    assert enc.embed_proj.weight.grad is not None


def test_structured_dim_matches_elem_features_minus_embed_dim():
    enc = StateEncoder(ELEM_FEATURES, d_model=64)
    assert enc._struct_dim == ELEM_FEATURES - _EMBED_DIM == 11
