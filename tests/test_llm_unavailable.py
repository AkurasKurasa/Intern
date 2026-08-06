"""
Regression test for agent._is_llm_unavailable().

Bug this locks down: _ask_llm() returns {"action_type": "wait",
"reason": "llm unavailable"} when the LLM connection fails (see agent.py's
_ask_llm except block). Downstream, that used to be indistinguishable from
the LLM genuinely deciding a field's value is blank — both collapsed to an
empty prediction["text"], so a dead LLM connection (e.g. LM Studio's local
server not started) made the agent silently Tab-skip every field in the
form instead of surfacing that something was actually broken. Found
2026-08-06 during a live run that "wasn't inputting anything."

_is_llm_unavailable() is the guard that tells the two apart.
"""
from agent.agent import _is_llm_unavailable


def test_recognizes_the_infra_failure_sentinel():
    assert _is_llm_unavailable({"action_type": "wait", "reason": "llm unavailable"}) is True


def test_does_not_flag_a_genuine_wait_for_another_reason():
    # "wait" alone (e.g. the LLM legitimately asking to wait for a dialog)
    # must NOT be treated as an infra failure.
    assert _is_llm_unavailable({"action_type": "wait", "reason": "dialog open"}) is False
    assert _is_llm_unavailable({"action_type": "wait"}) is False


def test_does_not_flag_a_real_leave_blank_decision():
    assert _is_llm_unavailable({"action_type": "type", "target": "SSN", "text": ""}) is False


def test_does_not_flag_a_normal_click_or_done_action():
    assert _is_llm_unavailable({"action_type": "click", "target": "Next"}) is False
    assert _is_llm_unavailable({"action_type": "done"}) is False


def test_handles_missing_keys_gracefully():
    assert _is_llm_unavailable({}) is False
