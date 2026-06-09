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


def detect(state: dict, focused: dict, scope=INSURANCE_SCOPE) -> str:
    """Call _detect_section without building a full LLMAgent — a stub carrying the
    scope is enough (the method only reads self._scope)."""
    stub = types.SimpleNamespace(_scope=scope)
    return LLMAgent._detect_section(stub, state, focused)


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
