"""
Regression tests for LLMAgent._detect_section.

This is the safety net for killing the form-specific hardcodes: it locks the
CURRENT behavior of section detection so the upcoming ScopeConfig refactor can be
proven behavior-preserving (`pytest` green = nothing changed).

_detect_section maps a focused element → the Driver/Vehicle section it sits in,
by finding the lowest `section_*` pane whose top edge is at/above the field's
vertical centre, then formatting `section_driver_2` → "Driver 2".
"""
import types
import pytest

from agent.agent import LLMAgent
from agent.scope import INSURANCE_SCOPE


# ── helpers ──────────────────────────────────────────────────────────────────

def _pane(label: str, top: int) -> dict:
    return {"type": "panecontrol", "label": label, "window_role": "active",
            "bbox": [0, top, 500, top + 28]}


def _field(top: int, label: str = "First Name") -> dict:
    return {"type": "editcontrol", "label": label, "window_role": "active",
            "bbox": [0, top, 500, top + 28]}


def _state(*elements) -> dict:
    return {"elements": list(elements)}


def _stub(scope):
    """A stand-in for `self` carrying just the scope -- _detect_section and
    _section_bounds both call self._sorted_section_panes(), so that must be
    bound as a real method too, not just present as a plain attribute."""
    stub = types.SimpleNamespace(_scope=scope)
    stub._sorted_section_panes = types.MethodType(LLMAgent._sorted_section_panes, stub)
    return stub


def detect(state: dict, focused: dict, scope=INSURANCE_SCOPE) -> str:
    """Call _detect_section without building a full LLMAgent — a stub carrying the
    scope is enough (the method only reads self._scope)."""
    return LLMAgent._detect_section(_stub(scope), state, focused)


# ── the contract ─────────────────────────────────────────────────────────────

def test_driver_section():
    fld = _field(140)
    st  = _state(_pane("section_driver_2", 100), fld)
    assert detect(st, fld) == "Driver 2"


def test_vehicle_section():
    fld = _field(140)
    st  = _state(_pane("section_vehicle_1", 100), fld)
    assert detect(st, fld) == "Vehicle 1"


def test_picks_lowest_section_above_field():
    # field sits below BOTH driver_1 and driver_2 → belongs to driver_2
    fld = _field(240)
    st  = _state(_pane("section_driver_1", 100),
                 _pane("section_driver_2", 200),
                 fld)
    assert detect(st, fld) == "Driver 2"


def test_field_above_all_sections_returns_empty():
    fld = _field(100)
    st  = _state(_pane("section_driver_1", 200), fld)
    assert detect(st, fld) == ""


def test_no_section_panes_returns_empty():
    fld = _field(140)
    st  = _state(_field(100, "Policy Number"), fld)   # no section_* panes
    assert detect(st, fld) == ""


def test_non_matching_section_returns_empty():
    # a section pane that isn't driver/vehicle → not a recognized section
    fld = _field(140)
    st  = _state(_pane("section_policy_information", 100), fld)
    assert detect(st, fld) == ""


def test_no_bbox_returns_empty():
    assert detect(_state(), {"type": "editcontrol", "label": "x"}) == ""


# ── genericization: default (empty) scope makes ZERO section assumptions ──────

def test_default_scope_is_a_noop():
    from agent.scope import ScopeConfig
    fld = _field(140)
    # same input that yields "Driver 2" under the insurance scope...
    st  = _state(_pane("section_driver_2", 100), fld)
    # ...returns "" under the generic default scope (no section_pattern)
    assert detect(st, fld, scope=ScopeConfig()) == ""


# ── _section_bounds: the y-range a named section owns ──────────────────────
# Real live bug, direct report ("Driver 2 returns empty... also add a way to
# distinguish similar bare label names"). _resolve_field_control's UIA-level
# disambiguation only had raw nearest-distance to tell same-named repeated
# controls apart (e.g. "First Name" on the Policyholder tab AND Driver 2 AND
# Driver 3) -- distance alone can be thrown off by a stale/drifted bbox.
# _section_bounds gives a stronger, geometry-based signal: which section's
# own on-screen pane a candidate control actually falls inside, reusing
# _detect_section's own pane-scanning logic so the two can never disagree.

def bounds(state: dict, section: str, scope=INSURANCE_SCOPE):
    return LLMAgent._section_bounds(_stub(scope), state, section)


def test_bounds_of_the_last_section_extend_to_infinity():
    st = _state(_pane("section_driver_1", 100), _pane("section_driver_2", 400))
    top, bottom = bounds(st, "Driver 2")
    assert top == 400
    assert bottom == float("inf")


def test_bounds_of_a_middle_section_stop_at_the_next_pane():
    st = _state(_pane("section_driver_1", 100), _pane("section_driver_2", 400),
                _pane("section_driver_3", 700))
    assert bounds(st, "Driver 2") == (400, 700)


def test_bounds_of_the_first_section_start_at_its_own_pane():
    st = _state(_pane("section_driver_1", 100), _pane("section_driver_2", 400))
    assert bounds(st, "Driver 1") == (100, 400)


def test_unknown_section_returns_none():
    st = _state(_pane("section_driver_1", 100))
    assert bounds(st, "Driver 9") is None


def test_no_sections_at_all_returns_none():
    st = _state(_field(100, "Policy Number"))
    assert bounds(st, "Driver 2") is None


def test_default_scope_returns_none_for_any_section():
    from agent.scope import ScopeConfig
    st = _state(_pane("section_driver_2", 100))
    assert bounds(st, "Driver 2", scope=ScopeConfig()) is None
