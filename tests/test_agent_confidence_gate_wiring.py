"""
Piece 5: agent.py's two escalate-trigger call sites (_cb_defer at ~L3907,
_deep_reason at ~L4369-4373) both currently write out the identical
three-condition OR expression by hand. This test first PROVES the shared
should_escalate() function is behaviorally identical to that expression for
every possible input (a truth table over the 3 boolean conditions), then
confirms both call sites in the real agent.py source were rewired to use it
-- source-level, matching this project's established pattern for verifying
agent.py changes without needing to construct a full LLMAgent instance.
"""
import itertools
import re
from pathlib import Path

from shared.confidence_gate import should_escalate

_AGENT_PY = Path(__file__).resolve().parent.parent / "components" / "agent" / "agent.py"
_SOURCE = _AGENT_PY.read_text(encoding="utf-8")


def _old_expression(t_conf_low: bool, streak1: bool, streak2: bool) -> bool:
    """The exact expression both call sites used before this task, as a
    function of its three boolean conditions (t_conf < _MED_CONF,
    _lowconf_fallback_streak > 0, _reclick_streak > 0)."""
    return t_conf_low or streak1 or streak2


def test_should_escalate_matches_old_expression_for_every_combination():
    for t_conf_low, streak1, streak2 in itertools.product([False, True], repeat=3):
        expected = _old_expression(t_conf_low, streak1, streak2)
        actual = should_escalate(t_conf_low, streak1, streak2)
        assert actual == expected, (t_conf_low, streak1, streak2)


def test_cb_defer_call_site_uses_should_escalate():
    idx = _SOURCE.index("_cb_defer = ")
    window = _SOURCE[idx:idx + 200]
    assert "should_escalate(" in window
    assert "t_conf < _MED_CONF" in window
    assert "_lowconf_fallback_streak > 0" in window
    assert "_reclick_streak > 0" in window


def test_deep_reason_call_site_uses_should_escalate():
    idx = _SOURCE.index("_deep_reason = ")
    window = _SOURCE[idx:idx + 200]
    assert "should_escalate(" in window
    assert "t_conf < _MED_CONF" in window
    assert "_lowconf_fallback_streak > 0" in window
    assert "_reclick_streak > 0" in window


def test_shared_confidence_gate_import_present():
    assert "from shared.confidence_gate import should_escalate" in _SOURCE
