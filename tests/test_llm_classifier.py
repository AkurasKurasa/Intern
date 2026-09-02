import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import llm_classifier
from llm_classifier import LLMClassifier


class _FakeModel:
    def __init__(self, model_id):
        self.id = model_id


class _FakeModelsList:
    def __init__(self, ids):
        self.data = [_FakeModel(i) for i in ids]


class _FakeModelsAPI:
    def __init__(self, ids):
        self._ids = ids

    def list(self):
        return _FakeModelsList(self._ids)


class _FakeOpenAIClient:
    def __init__(self, base_url="", api_key="", model_ids=("qwen2.5-7b-instruct",)):
        self.models = _FakeModelsAPI(model_ids)
        self.chat = None


def test_lmstudio_resolves_the_real_loaded_model_id_not_the_hardcoded_placeholder(monkeypatch):
    # Regression test: LLMClassifier used to always send LM Studio the
    # hardcoded placeholder "local-model" (_DEFAULT_MODELS["lmstudio"]),
    # which matches no real LM Studio model -- every classify() call
    # failed with "No models loaded" and fell back to "flag" even with a
    # real model genuinely loaded. Found live running the real classifier
    # against a real, loaded model.
    monkeypatch.setattr(
        llm_classifier, "_OpenAI",
        lambda **kw: _FakeOpenAIClient(model_ids=("qwen2.5-7b-instruct",)),
    )
    classifier = LLMClassifier(provider="lmstudio")
    assert classifier._llm_model == "qwen2.5-7b-instruct"


def test_lmstudio_falls_back_to_placeholder_when_no_model_is_loaded(monkeypatch):
    monkeypatch.setattr(
        llm_classifier, "_OpenAI",
        lambda **kw: _FakeOpenAIClient(model_ids=()),
    )
    classifier = LLMClassifier(provider="lmstudio")
    assert classifier._llm_model == "local-model"


def test_lmstudio_falls_back_to_placeholder_when_unreachable(monkeypatch):
    def _boom(**kw):
        raise ConnectionError("LM Studio not running")
    monkeypatch.setattr(llm_classifier, "_OpenAI", _boom)
    # Constructing the classifier must not raise even if LM Studio is down --
    # classify() itself fails closed to "leave_alone" later.
    classifier = LLMClassifier(provider="lmstudio")
    assert classifier._llm_model == "local-model"
    assert not classifier.available


def test_lmstudio_respects_an_explicitly_passed_model_id(monkeypatch):
    # A caller who deliberately wants a specific one of several
    # simultaneously-loaded models must still get exactly that one --
    # dynamic resolution only kicks in when no model_id was requested.
    monkeypatch.setattr(
        llm_classifier, "_OpenAI",
        lambda **kw: _FakeOpenAIClient(model_ids=("qwen2.5-7b-instruct", "llama-3-8b")),
    )
    classifier = LLMClassifier(provider="lmstudio", model_id="llama-3-8b")
    assert classifier._llm_model == "llama-3-8b"
