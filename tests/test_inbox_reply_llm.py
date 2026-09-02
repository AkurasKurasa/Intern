import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import inbox_reply_llm


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


class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeCompletionsResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletionsAPI:
    def __init__(self, content, captured_calls):
        self._content = content
        self._captured_calls = captured_calls

    def create(self, **kwargs):
        self._captured_calls.append(kwargs)
        return _FakeCompletionsResponse(self._content)


class _FakeChatAPI:
    def __init__(self, content, captured_calls):
        self.completions = _FakeCompletionsAPI(content, captured_calls)


class _FakeOpenAIClient:
    def __init__(self, base_url="", api_key="", model_ids=("qwen2.5-7b-instruct",), content="", captured_calls=None):
        self.models = _FakeModelsAPI(model_ids)
        self.chat = _FakeChatAPI(content, captured_calls if captured_calls is not None else [])


def test_generate_reply_returns_the_real_response_text(monkeypatch):
    # generate_reply() does `from openai import OpenAI` locally, inside
    # the function -- patching the openai module's own OpenAI attribute
    # is what a local import like that actually picks up, same
    # convention as test_cold_email_llm.py.
    captured = []
    fake_client = _FakeOpenAIClient(
        content="Thanks for sending this over -- I'll take a look and get back to you.",
        captured_calls=captured,
    )
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: fake_client)

    reply = inbox_reply_llm.generate_reply("Dana Whitfield", "Quick question", "Can you confirm this?")

    assert "take a look" in reply
    assert captured[0]["model"] == "qwen2.5-7b-instruct"  # read from LM Studio, not hardcoded


def test_generate_reply_returns_empty_when_no_model_is_loaded(monkeypatch):
    fake_client = _FakeOpenAIClient(model_ids=(), content="")
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: fake_client)

    assert inbox_reply_llm.generate_reply("Dana", "Subject", "body") == ""


def test_generate_reply_fails_closed_when_lm_studio_is_unreachable(monkeypatch):
    import openai

    def _boom(**kw):
        raise ConnectionError("LM Studio not running")
    monkeypatch.setattr(openai, "OpenAI", _boom)

    assert inbox_reply_llm.generate_reply("Dana", "Subject", "body") == ""
