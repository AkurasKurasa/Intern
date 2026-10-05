import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cold_email_llm


def test_parses_a_well_formed_response():
    subject, body = cold_email_llm._parse_subject_and_body(
        "Subject: Reaching out about a partnership\n\n"
        "Hi Dana,\n\nI wanted to reach out about a potential collaboration.\n\nBest,\nIntern"
    )
    assert subject == "Reaching out about a partnership"
    assert "Hi Dana" in body


def test_response_missing_a_subject_line_fails_closed():
    subject, body = cold_email_llm._parse_subject_and_body("")
    assert (subject, body) == ("", "")


def test_response_with_only_a_subject_and_no_body_fails_closed():
    subject, body = cold_email_llm._parse_subject_and_body("Subject: Hello there")
    assert (subject, body) == ("", "")


def test_subject_prefix_is_stripped_case_insensitively():
    subject, body = cold_email_llm._parse_subject_and_body("SUBJECT: Quick question\nHi there, real body text.")
    assert subject == "Quick question"


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


def test_generate_cold_email_returns_the_real_parsed_response(monkeypatch):
    # generate_cold_email() does `from openai import OpenAI` locally, inside
    # the function, re-resolving it fresh on every call -- patching the
    # openai module's own OpenAI attribute is what a local import like that
    # actually picks up, not a module-level name on cold_email_llm itself.
    captured = []
    fake_client = _FakeOpenAIClient(
        content="Subject: Q3 outreach\n\nHi Dana, reaching out about a partnership.",
        captured_calls=captured,
    )
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: fake_client)

    subject, body = cold_email_llm.generate_cold_email("Dana Whitfield", "Q3 outreach")

    assert subject == "Q3 outreach"
    assert "Hi Dana" in body
    assert captured[0]["model"] == "qwen2.5-7b-instruct"  # read from LM Studio, not hardcoded


def test_generate_cold_email_returns_empty_when_no_model_is_loaded(monkeypatch):
    fake_client = _FakeOpenAIClient(model_ids=(), content="")
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: fake_client)

    assert cold_email_llm.generate_cold_email("Dana Whitfield", "Q3 outreach") == ("", "")


def test_generate_cold_email_fails_closed_when_lm_studio_is_unreachable(monkeypatch):
    import openai

    def _boom(**kw):
        raise ConnectionError("LM Studio not running")
    monkeypatch.setattr(openai, "OpenAI", _boom)

    assert cold_email_llm.generate_cold_email("Dana Whitfield", "Q3 outreach") == ("", "")


# ── who is writing (2026-10-05: "Put [My Name] as Kevin") ────────────────

import json  # noqa: E402


def _profile(tmp_path, name="Kevin"):
    p = tmp_path / "sender_profile.json"
    p.write_text(json.dumps({"name": name}), encoding="utf-8")
    return str(p)


def test_the_prompt_names_the_sender_and_forbids_placeholders(tmp_path):
    seen = {}

    def ask(system, user):
        seen["system"] = system
        return "Subject: Hi\nHello Ann,\nShort note.\nBest,\nKevin"

    cold_email_llm.generate_cold_email("Ann", "Intro", profile_path=_profile(tmp_path), ask=ask)
    assert "on behalf of Kevin" in seen["system"]
    assert "[Your Name]" in seen["system"]          # named as forbidden
    assert "never invent" in seen["system"]


def test_name_placeholders_are_filled_with_the_real_name(tmp_path):
    reply = "Subject: Hello from [Your Name]\nHi Ann,\nI'm [My Name].\nBest,\n[Your Full Name]"
    subject, body = cold_email_llm.generate_cold_email(
        "Ann", "Intro", profile_path=_profile(tmp_path), ask=lambda s, u: reply)
    assert subject == "Hello from Kevin"
    assert body == "Hi Ann,\nI'm Kevin.\nBest,\nKevin"


def test_other_blanks_get_one_retry_then_the_draft_is_refused(tmp_path):
    calls = []

    def ask(system, user):
        calls.append(user)
        return "Subject: Hi\nI work at [Your Company].\nKevin"

    assert cold_email_llm.generate_cold_email(
        "Ann", "Intro", profile_path=_profile(tmp_path), ask=ask) == ("", "")
    assert len(calls) == 2 and "square-bracket" in calls[1]


def test_a_clean_retry_is_accepted(tmp_path):
    replies = iter(["Subject: Hi\nI work at [Your Company].", "Subject: Hi\nA short note.\nKevin"])
    assert cold_email_llm.generate_cold_email(
        "Ann", "Intro", profile_path=_profile(tmp_path), ask=lambda s, u: next(replies)) \
        == ("Hi", "A short note.\nKevin")


def test_real_profile_says_kevin():
    assert cold_email_llm.sender_name() == "Kevin"
