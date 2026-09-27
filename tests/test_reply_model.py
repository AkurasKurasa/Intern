import os
import sys

import pytest
import torch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import reply_model as rm


class TestReplyMatchNet:
    def test_forward_shape(self):
        model = rm.ReplyMatchNet(dims=3)
        x = torch.zeros(5, 3)
        logits = model(x)
        assert logits.shape == (5,)

    def test_match_probability_in_range(self):
        model = rm.ReplyMatchNet(dims=3)
        x = torch.randn(4, 3)
        probs = model.match_probability(x)
        assert torch.all(probs >= 0.0) and torch.all(probs <= 1.0)

    def test_parameter_count_positive(self):
        model = rm.ReplyMatchNet(dims=3)
        assert model.parameter_count() > 0


class TestSaveLoad:
    def test_round_trip_preserves_predictions(self, tmp_path):
        model = rm.ReplyMatchNet(dims=rm.DIMS)
        x = torch.randn(3, rm.DIMS)
        before = model.match_probability(x)

        path = str(tmp_path / "ckpt.pt")
        rm.save(model, path, metadata={"trained_on": 6})
        loaded, artifact = rm.load(path)

        after = loaded.match_probability(x)
        assert torch.allclose(before, after, atol=1e-6)
        assert artifact["metadata"] == {"trained_on": 6}

    def test_features_version_mismatch_raises(self, tmp_path):
        model = rm.ReplyMatchNet(dims=rm.DIMS)
        path = str(tmp_path / "ckpt.pt")
        rm.save(model, path)
        artifact = torch.load(path, weights_only=False)
        artifact["features_version"] = "some-old-version"
        torch.save(artifact, path)
        with pytest.raises(rm.FeaturesMismatch):
            rm.load(path)

    def test_dims_mismatch_raises(self, tmp_path):
        model = rm.ReplyMatchNet(dims=rm.DIMS)
        path = str(tmp_path / "ckpt.pt")
        rm.save(model, path)
        artifact = torch.load(path, weights_only=False)
        artifact["dims"] = artifact["dims"] + 1
        torch.save(artifact, path)
        with pytest.raises(rm.FeaturesMismatch):
            rm.load(path)

    def test_allow_mismatch_bypasses_guard(self, tmp_path):
        model = rm.ReplyMatchNet(dims=rm.DIMS)
        path = str(tmp_path / "ckpt.pt")
        rm.save(model, path)
        artifact = torch.load(path, weights_only=False)
        artifact["features_version"] = "some-old-version"
        torch.save(artifact, path)
        loaded, _artifact = rm.load(path, allow_mismatch=True)
        assert isinstance(loaded, rm.ReplyMatchNet)
