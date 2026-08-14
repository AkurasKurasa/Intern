"""
components/agent/field_planner.py
==================================
Field Planner — scans the currently visible, fillable elements on a form
tab ONCE and produces an ordered plan of what to do with each one, instead
of re-deciding field-by-field, live, every step.

Why this exists
----------------
FormFillerPlugin.handle_step() already resolves most fields with zero
transformer/LLM calls (WHERE via UIA SetFocus-by-Name, WHAT via a
deterministic lookup-cache hit) -- but it re-derives that decision through
a long guard cascade (focus-check, tab-switch, stuck-guard, pane-escape,
button-escape, tab-complete-verify, auto-skip, auto-fill+peek+dup-label,
auto-check, combobox-fix, drought-guard) from scratch on every single step,
one field at a time. This module is the decision layer only: given one
observed state, it returns the full list of what's fillable on screen right
now and what each one resolves to, so the caller (FormFillerPlugin, in its
optional plan_replay mode) can execute the easy ones mechanically instead
of re-deriving them every step.

Pure functions of a state snapshot plus injected callables (lookup/peek/
section-detection are all owned by FormFillerPlugin and passed in, not
duplicated here) -- no side effects, no pyautogui calls, no sleeps. Same
split navigation_protocol.py already uses, for the same reason: makes this
logic unit-testable without a live GUI.

v1 scope: only "editcontrol" fields are marked fast-replayable. Comboboxes
and checkboxes are still enumerated (so a plan-replay caller can track
progress against them and know WHAT they'd need) but always routed back to
the existing, heavily bug-fixed reactive branches -- _combobox_needs_fix's
dropdown state machine alone has been through ten separate documented fix
rounds (execution_filled_combobox_reclick_guard); re-deriving that logic
here would risk regressing hard-won correctness for an unproven speed gain.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Union

from .navigation_protocol import FILLABLE_TYPES

# Mirrors _lookup_field / _auto_fill's exact "this means leave it blank,
# don't type anything" sentinel set (form_filler_plugin.py).
_SKIP_VALUES: Set[str] = {
    "(none)", "none", "(leave blank)", "n/a", "yes (check)",
    "leave blank — liability only", "leave blank — owned outright",
}

# Checkbox "should this be checked" parsing -- mirrors _auto_check exactly.
_CHECK_WORDS: Set[str] = {"check", "true", "1", "checked"}


class Resolution(Enum):
    """What a planned field's WHAT-value resolves to."""
    LOOKUP_HIT   = "lookup_hit"     # cache (or post-peek cache) has a real value
    LOOKUP_BLANK = "lookup_blank"   # cache says "leave this blank" -- Tab past, don't type
    NEEDS_LLM    = "needs_llm"      # both lookup attempts missed -- genuinely ambiguous


class DivergenceStatus(Enum):
    """Whether a planned field still matches what the live screen shows."""
    PENDING   = "pending"     # still empty, unchanged -- execute as planned
    SATISFIED = "satisfied"   # already holds the expected value -- just Tab past
    DIVERGED  = "diverged"    # vanished, or holds an unexpected non-empty value


@dataclass(frozen=True)
class PlannedField:
    stable_key:     Union[str, tuple]
    label:          str
    element_type:   str
    bbox:           Optional[List[float]]   # plan-time hint only, never the click target
    section:        str
    expected_value: str                     # "" for LOOKUP_BLANK / NEEDS_LLM
    needs_clear:    bool
    resolution:     Resolution


def stable_field_key(
    elem: Dict[str, Any],
    elements: Optional[List[Dict[str, Any]]] = None,
) -> Union[str, tuple]:
    """
    (label, rank-among-same-labeled-elements) -- mirrors
    LLMAgent._attempt_key (components/agent/agent.py) exactly, since
    element_id is position-based and documented unstable across separate
    observations (state_validator.py). Two independent copies of this
    scheme already exist (agent.py and transformer.py); this is a third,
    cross-checked by a drift-guard test against the agent.py copy.

    Returns a bare lowercased label when it's unique in `elements`, a
    (label, rank) tuple when it collides with another same-labeled element
    (repeated-section forms: Driver 1/2/3, Vehicle 1/2), or a coarse
    geometric fallback key when the element has no label at all.
    """
    label = (elem.get("label") or elem.get("text") or "").strip().lower()
    if not label:
        b = elem.get("bbox") or [0, 0, 0, 0]
        return ("@", round((b[0] + b[2]) / 2 / 20) * 20, round((b[1] + b[3]) / 2 / 20) * 20)
    if elements:
        same_label_ids = [id(e) for e in elements
                           if (e.get("label") or e.get("text") or "").strip().lower() == label]
        if len(same_label_ids) > 1:
            try:
                return (label, same_label_ids.index(id(elem)))
            except ValueError:
                pass
    return label


def plan_visible_fields(
    state: Dict[str, Any],
    viewport_bottom: float,
    lookup_fn: Callable[[str, str], str],
    peek_fn: Optional[Callable[[Dict[str, Any], str], None]] = None,
    section_fn: Optional[Callable[[Dict[str, Any], Dict[str, Any]], str]] = None,
    attempted_keys: Optional[Set[Any]] = None,
    attempt_key_fn: Optional[Callable[[Dict[str, Any], List[Dict[str, Any]]], Any]] = None,
) -> List[PlannedField]:
    """
    Every actionable, empty, not-yet-attempted fillable field currently
    rendered inside the visible viewport, each resolved to a value (or
    marked needs_llm) up front.

    WHAT-resolution mirrors the existing two-step miss protocol already in
    FormFillerPlugin.handle_step()'s auto-fill branch exactly: a direct
    lookup, and on a miss, one peek-and-retry -- only NEEDS_LLM if both
    attempts come back empty. Getting this looser (marking NEEDS_LLM after
    only one attempt) would silently regress fields the current reactive
    code already resolves for free.
    """
    elements = state.get("elements", [])
    attempted_keys = attempted_keys or set()
    plan: List[PlannedField] = []

    for e in elements:
        if e.get("window_role") == "background":
            continue
        etype = (e.get("type") or "").lower()
        if etype not in FILLABLE_TYPES:
            continue
        if (e.get("value") or "").strip():
            continue  # already has a value -- nothing to plan
        if not e.get("visible", True):
            continue
        if not e.get("enabled", True):
            continue
        b = e.get("bbox")
        if not b or len(b) != 4:
            continue
        cy = (b[1] + b[3]) / 2
        if not (b[1] >= 0 and cy <= viewport_bottom):
            continue

        label = (e.get("label") or e.get("text") or "").strip()
        if not label:
            continue
        if attempt_key_fn is not None and attempt_key_fn(e, elements) in attempted_keys:
            continue

        section = section_fn(state, e) if section_fn else ""
        key = stable_field_key(e, elements)

        if etype == "checkboxcontrol":
            expected = lookup_fn(label, "")
            if not expected:
                plan.append(PlannedField(key, label, etype, list(b), section,
                                          "", False, Resolution.NEEDS_LLM))
                continue
            ev = expected.lower().strip()
            should_check = ev.startswith("yes") or ev in _CHECK_WORDS
            resolution = Resolution.LOOKUP_HIT if should_check else Resolution.LOOKUP_BLANK
            plan.append(PlannedField(key, label, etype, list(b), section,
                                      expected, False, resolution))
            continue

        # editcontrol / comboboxcontrol -- direct lookup, then peek-and-retry once
        expected = lookup_fn(label, section)
        if not expected and peek_fn is not None:
            peek_fn(state, label)
            expected = lookup_fn(label, section)

        if not expected:
            plan.append(PlannedField(key, label, etype, list(b), section,
                                      "", False, Resolution.NEEDS_LLM))
            continue

        if expected.lower().strip("()") in _SKIP_VALUES:
            plan.append(PlannedField(key, label, etype, list(b), section,
                                      "", False, Resolution.LOOKUP_BLANK))
            continue

        plan.append(PlannedField(key, label, etype, list(b), section,
                                  expected, False, Resolution.LOOKUP_HIT))

    return plan


def is_fast_replayable(planned: PlannedField) -> bool:
    """
    v1 scope gate: only plain text fields get the mechanical replay fast
    path. Comboboxes/checkboxes always fall through to the existing,
    heavily bug-fixed reactive branches (see module docstring) even though
    they're enumerated in the plan.
    """
    return planned.element_type == "editcontrol"


def check_divergence(planned: PlannedField, state: Dict[str, Any]) -> DivergenceStatus:
    """
    Re-resolve `planned`'s stable key against the CURRENT observation --
    never trust the plan-time snapshot blindly. `DIVERGED` covers both "the
    field vanished" (scrolled away, tab changed, conditionally hidden) and
    "the field now holds something other than what the plan expected" (a
    human or the reactive path already touched it) -- in both cases the
    caller should fall back to the existing, unmodified reactive path for
    that field rather than executing a stale plan step.
    """
    elements = state.get("elements", [])
    match = None
    for e in elements:
        if e.get("window_role") == "background":
            continue
        if (e.get("type") or "").lower() != planned.element_type:
            continue
        if stable_field_key(e, elements) == planned.stable_key:
            match = e
            break

    if match is None:
        return DivergenceStatus.DIVERGED

    current = (match.get("value") or "").strip()
    if not current:
        return DivergenceStatus.PENDING

    if planned.resolution == Resolution.LOOKUP_HIT and current.lower() == planned.expected_value.lower():
        return DivergenceStatus.SATISFIED

    return DivergenceStatus.DIVERGED
