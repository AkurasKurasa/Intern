"""
Regression tests for LLMAgent._resolve_field_control().

Found live 2026-08-14, direct request ("still too slow, at least <60s"):
timed OPT2 batch fast-fill's own log -- 13 fields filled in 7 seconds with
NO observe() between them, yet still ~0.5s/field. Traced into
_find_uia_control_by_name: every caller always passed expected_bbox,
forcing its expensive position-based disambiguation search (a
foundIndex=1..10 loop, each "does a second match exist" check costing a
real UIA timeout when it doesn't) even for the overwhelming majority of
fields whose label is completely unique on screen -- disambiguation only
matters for the small minority that genuinely repeat (e.g. "First Name"
across Driver 1/2/3 sections, the actual reason expected_bbox exists,
added 2026-08-09).

_resolve_field_control wraps _find_uia_control_by_name and decides, from
the elements already in the current observed state (no new UIA call),
whether the label is actually ambiguous. Only ambiguous labels still pay
for expected_bbox's slow disambiguation path; a unique label gets
expected_bbox=None, which takes _find_uia_control_by_name's own fast path
instead. _find_uia_control_by_name itself is untouched -- this is purely
about which argument gets passed to it, never a change to its own
disambiguation guarantee.

EXTENDED 2026-08-14 ("any more improvements"): now tries
_resolve_control_via_point FIRST -- a WindowFromPoint-based resolution
(see tests/test_resolve_control_via_point.py for that mechanism's own
tests) that skips the UIA name-search entirely -- before ever falling
back to _find_uia_control_by_name. Every test below explicitly mocks
_resolve_control_via_point (rather than letting the real one run against
whatever's actually on screen in the test environment) so these tests
stay deterministic and test ONLY the routing logic between the two
mechanisms, not real Win32 state.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _make_agent():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1)
    agent._resolve_control_via_point = MagicMock(return_value=None)
    return agent


def _field(label, bbox=(100, 100, 300, 130)):
    return {"element_id": label, "type": "editcontrol", "label": label,
            "text": label, "value": "", "bbox": list(bbox)}


def test_point_based_hit_short_circuits_before_any_uia_search():
    """The whole point of the extension -- when the fast point-based
    resolution succeeds, the slow name-search path must never run at
    all, not even the disambiguation-aware version."""
    agent = _make_agent()
    agent._resolve_control_via_point = MagicMock(return_value="point_hit_control")
    agent._find_uia_control_by_name = MagicMock()
    state = {"elements": [_field("Policy Number")]}

    result = agent._resolve_field_control(state, "Policy Number", (100, 100, 300, 130))

    assert result == "point_hit_control"
    agent._find_uia_control_by_name.assert_not_called()


def test_point_based_miss_falls_back_to_unique_label_fast_path():
    agent = _make_agent()
    agent._find_uia_control_by_name = MagicMock(return_value="the_control")
    state = {"elements": [_field("Policy Number")]}

    result = agent._resolve_field_control(state, "Policy Number", (100, 100, 300, 130))

    assert result == "the_control"
    agent._find_uia_control_by_name.assert_called_once_with("Policy Number", expected_bbox=None)


def test_duplicate_label_still_uses_the_real_bbox_disambiguation():
    """The whole safety guarantee -- a genuinely-repeated label (Driver
    1/2/3 sections) must still disambiguate by position, unchanged."""
    agent = _make_agent()
    agent._find_uia_control_by_name = MagicMock(return_value="the_correct_one")
    state = {"elements": [
        _field("First Name", bbox=(100, 100, 300, 130)),
        _field("First Name", bbox=(100, 400, 300, 430)),
        _field("First Name", bbox=(100, 700, 300, 730)),
    ]}

    result = agent._resolve_field_control(state, "First Name", (100, 400, 300, 430))

    assert result == "the_correct_one"
    agent._find_uia_control_by_name.assert_called_once_with(
        "First Name", expected_bbox=(100, 400, 300, 430))


def test_empty_label_returns_none_without_calling_uia_at_all():
    agent = _make_agent()
    agent._find_uia_control_by_name = MagicMock()
    state = {"elements": []}

    result = agent._resolve_field_control(state, "", (100, 100, 300, 130))

    assert result is None
    agent._find_uia_control_by_name.assert_not_called()


def test_label_absent_from_state_still_treated_as_unique():
    """A field the caller already has (e.g. from a stale/partial state)
    that doesn't literally appear in state['elements'] counts as 0
    occurrences -- not ambiguous, take the fast path."""
    agent = _make_agent()
    agent._find_uia_control_by_name = MagicMock(return_value="ctrl")
    state = {"elements": [_field("Some Other Field")]}

    agent._resolve_field_control(state, "Policy Number", (100, 100, 300, 130))

    agent._find_uia_control_by_name.assert_called_once_with("Policy Number", expected_bbox=None)


def test_matches_on_label_or_text_same_as_the_rest_of_the_file():
    """Elements that only carry 'text' (no separate 'label') must still
    count toward the duplicate check -- matches every other label-lookup
    site in this file (_bf_label pattern: label or text)."""
    agent = _make_agent()
    agent._find_uia_control_by_name = MagicMock(return_value="ctrl")
    state = {"elements": [
        {"element_id": "e1", "type": "editcontrol", "text": "Suffix", "value": "", "bbox": [0, 0, 10, 10]},
        {"element_id": "e2", "type": "editcontrol", "text": "Suffix", "value": "", "bbox": [0, 50, 10, 60]},
    ]}

    agent._resolve_field_control(state, "Suffix", (0, 50, 10, 60))

    agent._find_uia_control_by_name.assert_called_once_with("Suffix", expected_bbox=(0, 50, 10, 60))


def test_real_find_uia_control_by_name_is_not_reimplemented():
    """Must actually reuse the real, already-tested lookup mechanism, not
    a parallel UIA-calling implementation."""
    import inspect
    from agent.agent import LLMAgent as _LLMAgent
    src = inspect.getsource(_LLMAgent._resolve_field_control)
    assert "self._find_uia_control_by_name(" in src


def test_real_resolve_control_via_point_is_tried_not_reimplemented():
    import inspect
    from agent.agent import LLMAgent as _LLMAgent
    src = inspect.getsource(_LLMAgent._resolve_field_control)
    assert "self._resolve_control_via_point(" in src
