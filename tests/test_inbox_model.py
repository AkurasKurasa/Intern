import os
import sys

import pytest
import torch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import inbox_model as im


class TestInboxDecisionNet:
    def test_forward_shape(self):
        model = im.InboxDecisionNet(dims=4, num_decisions=3)
        x = torch.zeros(2, 4)
        logits = model(x)
        assert logits.shape == (2, 3)

    def test_probabilities_sum_to_one(self):
        model = im.InboxDecisionNet(dims=4, num_decisions=3)
        x = torch.randn(5, 4)
        probs = model.probabilities(x)
        sums = probs.sum(dim=-1)
        assert torch.allclose(sums, torch.ones(5), atol=1e-5)

    def test_parameter_count_positive(self):
        model = im.InboxDecisionNet(dims=4, num_decisions=3)
        assert model.parameter_count() > 0


class TestSaveLoad:
    def test_round_trip_preserves_predictions(self, tmp_path):
        model = im.InboxDecisionNet(dims=im.DIMS, num_decisions=len(im.DECISIONS_ORDER))
        x = torch.randn(3, im.DIMS)
        before = model.probabilities(x)

        path = str(tmp_path / "ckpt.pt")
        im.save(model, path, centroids={"reply": [0.1, 0.2]})
        loaded, artifact = im.load(path)

        after = loaded.probabilities(x)
        assert torch.allclose(before, after, atol=1e-6)
        assert artifact["centroids"] == {"reply": [0.1, 0.2]}

    def test_features_version_mismatch_raises(self, tmp_path):
        model = im.InboxDecisionNet(dims=im.DIMS, num_decisions=len(im.DECISIONS_ORDER))
        path = str(tmp_path / "ckpt.pt")
        im.save(model, path, centroids={})
        artifact = torch.load(path, weights_only=False)
        artifact["features_version"] = "some-old-version"
        torch.save(artifact, path)
        with pytest.raises(im.FeaturesMismatch):
            im.load(path)

    def test_dims_mismatch_raises(self, tmp_path):
        model = im.InboxDecisionNet(dims=im.DIMS, num_decisions=len(im.DECISIONS_ORDER))
        path = str(tmp_path / "ckpt.pt")
        im.save(model, path, centroids={})
        artifact = torch.load(path, weights_only=False)
        artifact["dims"] = artifact["dims"] + 1
        torch.save(artifact, path)
        with pytest.raises(im.FeaturesMismatch):
            im.load(path)

    def test_allow_mismatch_bypasses_guard(self, tmp_path):
        model = im.InboxDecisionNet(dims=im.DIMS, num_decisions=len(im.DECISIONS_ORDER))
        path = str(tmp_path / "ckpt.pt")
        im.save(model, path, centroids={})
        artifact = torch.load(path, weights_only=False)
        artifact["features_version"] = "some-old-version"
        torch.save(artifact, path)
        loaded, _artifact = im.load(path, allow_mismatch=True)
        assert isinstance(loaded, im.InboxDecisionNet)
