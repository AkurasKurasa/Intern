"""
The perception contract — ONE language every observer must speak.

The agent is blind; it acts only on the dict an observer's `snapshot()` returns.
For an observer (UIA / Excel / web / …) to be swappable, its output must conform
to this schema. When it doesn't, `validate_state` says so LOUD — instead of the
agent silently seeing an empty screen because, e.g., Excel emitted type="cell"
and value-under-"text" while the agent filters on type="editcontrol"/"value".

This is the seam: define the canonical element shape here, validate every
adapter against it, and the agent reads any source identically.

Canonical element (keys the agent relies on):
    type         str    REQUIRED  — normalized control type, one of CONTROL_TYPES
    bbox         [int]*4 REQUIRED — screen pixel rect [x1,y1,x2,y2] (for clicking)
    label        str    recommended — field identity / name
    value        str    recommended — current content ("" = empty); drives is_filled
    window_role  str    recommended — "active" | "background"
    focused      bool   optional
    element_id, text, confidence, app, window_title   optional

Canonical state:
    elements           list  REQUIRED
    screen_resolution  [w,h] REQUIRED (agent fallback geometry)
    focused_element_id str   recommended
    source             str   recommended — which adapter produced this (debugging)
"""
from __future__ import annotations

from typing import Any, Dict, List

# Normalized control-type vocabulary the agent understands. An adapter that emits
# a type outside this set (e.g. Excel's "cell") must map it to one of these, or
# the agent's element filters will silently drop it.
CONTROL_TYPES = {
    "editcontrol", "comboboxcontrol", "checkboxcontrol", "radiobuttoncontrol",
    "buttoncontrol", "tabitemcontrol", "panecontrol", "listcontrol",
    "listitemcontrol", "hyperlinkcontrol", "menubarcontrol", "menuitemcontrol",
    "textcontrol", "documentcontrol", "customcontrol",
}

_STATE_REQUIRED   = ("elements", "screen_resolution")
_ELEM_REQUIRED    = ("type", "bbox")
_ELEM_RECOMMENDED = ("label", "value", "window_role")


def validate_state(state: Dict[str, Any], sample: int = 50) -> List[str]:
    """Check a snapshot against the perception contract.

    Returns a list of issue strings (empty = conforms). Each is prefixed
    ERROR (agent will malfunction) or WARN (works but degraded/suspicious).
    Checks up to `sample` elements (snapshots can be large).
    """
    issues: List[str] = []

    if not isinstance(state, dict):
        return [f"ERROR: state is {type(state).__name__}, expected dict"]

    for k in _STATE_REQUIRED:
        if k not in state:
            issues.append(f"ERROR: state missing required key '{k}'")

    elems = state.get("elements")
    if not isinstance(elems, list):
        issues.append(f"ERROR: state['elements'] is {type(elems).__name__}, expected list")
        return issues

    if not elems:
        issues.append("WARN: state['elements'] is empty — agent sees a blank screen")
        return issues

    if not state.get("source"):
        issues.append("WARN: state missing 'source' (which adapter produced this)")

    bad_types: set = set()
    missing_req: set = set()
    missing_rec: set = set()
    for e in elems[:sample]:
        if not isinstance(e, dict):
            issues.append(f"ERROR: element is {type(e).__name__}, expected dict")
            continue
        for k in _ELEM_REQUIRED:
            if k not in e:
                missing_req.add(k)
        t = e.get("type")
        if t is not None and t not in CONTROL_TYPES:
            bad_types.add(t)
        bb = e.get("bbox")
        if bb is not None and (not isinstance(bb, (list, tuple)) or len(bb) != 4):
            issues.append(f"ERROR: element bbox {bb!r} not a 4-tuple [x1,y1,x2,y2]")
        for k in _ELEM_RECOMMENDED:
            if k not in e:
                missing_rec.add(k)

    if missing_req:
        issues.append(f"ERROR: elements missing required key(s) {sorted(missing_req)} "
                      f"— agent cannot locate/act on them")
    if bad_types:
        issues.append(f"ERROR: element type(s) {sorted(bad_types)} not in CONTROL_TYPES "
                      f"— agent filters will drop these (map them to a known type)")
    if missing_rec:
        issues.append(f"WARN: elements missing recommended key(s) {sorted(missing_rec)} "
                      f"— degraded (e.g. no 'value' → is_filled always false)")
    return issues
