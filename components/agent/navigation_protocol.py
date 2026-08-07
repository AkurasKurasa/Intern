"""
components/agent/navigation_protocol.py
========================================
Navigation Protocol — decides how to move the on-screen viewport (scroll,
advance to the next tab) so the maximum number of actionable, still-empty
targets are visible, independent of any recorded human scroll behavior.

Why this exists
----------------
Behavioral cloning normally has the model imitate exactly what a human demo
did, including when/how far they scrolled. That doesn't work here: 0 of the
11,062 steps recorded before this project's "eight_Tabs" campaign ever
contained a real scroll action, and the model's scroll-output head has no
learnable target as a result (see DEVELOPERS.md -> adaptability_scroll_gap).
Rather than wait for enough scroll demonstrations to clone the *behavior*,
navigation is treated as a separate SYSTEM responsibility, not something the
Transformer must learn: the system's only job is to keep an actionable,
empty target on screen; the Transformer/Agent only ever decides WHERE to
click and WHAT to type among what's currently visible.

This module is the decision layer only — pure functions of a state snapshot
(plus small, explicit config), no side effects, no pyautogui calls, no
sleeps. The caller (agent.py) is responsible for executing the decision
(actually scrolling, re-observing, advancing the tab) and for the small
amount of live-environment state (viewport bottom, attempted-field keys)
that can't be derived from a plain state dict. This split is what makes the
navigation logic unit-testable without a live GUI.

Previously this same logic (has_visible_empty_target / visible signature /
give-up threshold) was duplicated across ~3 separate call sites in agent.py
under different names and slightly different conditions (a step-count-based
"drought guard" and a visibility-based "scroll-reveal" block). Consolidated
here as one named, tested decision surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set

_FILLABLE_TYPES: Set[str] = {"editcontrol", "input", "comboboxcontrol", "combobox",
                              "checkbox", "checkboxcontrol"}
_SIG_TYPES: Set[str] = {"editcontrol", "comboboxcontrol", "checkbox", "checkboxcontrol"}


class NavAction(Enum):
    """What the Navigation Protocol wants done next."""
    WAIT        = "wait"          # an empty target is already visible — let the agent act
    SCROLL      = "scroll"        # no empty target visible — scroll to try to reveal one
    ADVANCE_TAB = "advance_tab"   # scrolling isn't revealing anything new — move on


@dataclass(frozen=True)
class NavDecision:
    action: NavAction
    reason: str


def find_visible_empty_target(
    state: Dict[str, Any],
    viewport_bottom: float,
    attempted_keys: Optional[Set[Any]] = None,
    attempt_key_fn: Optional[Callable[[Dict[str, Any], List[Dict[str, Any]]], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    The first actionable, empty, not-yet-attempted field currently rendered
    inside the visible viewport, or None if there isn't one.

    Universal mechanic — no field names, coordinates, or app names hardcoded:
    fillable WIDGET TYPE + empty VALUE + not yet attempted + on-screen geometry.
    Off-fold fields still report real bboxes, so callers must pass the live
    viewport bottom (from the actual window rect) rather than raw screen height.

    Added 2026-08-07 alongside has_visible_empty_target (which this now
    powers) so a caller struggling to hit a target via the learned
    transformer's own low-confidence pointer has something deterministic to
    fall back to — the ELEMENT itself, not just a yes/no.
    """
    elements = state.get("elements", [])
    attempted_keys = attempted_keys or set()
    for e in elements:
        if e.get("window_role") == "background":
            continue
        if (e.get("type") or "").lower() not in _FILLABLE_TYPES:
            continue
        if (e.get("value") or "").strip():
            continue
        if attempt_key_fn is not None:
            if attempt_key_fn(e, elements) in attempted_keys:
                continue
        b = e.get("bbox")
        if not b or len(b) != 4:
            continue
        cy = (b[1] + b[3]) / 2
        if b[1] >= 0 and cy <= viewport_bottom:
            return e
    return None


def has_visible_empty_target(
    state: Dict[str, Any],
    viewport_bottom: float,
    attempted_keys: Optional[Set[Any]] = None,
    attempt_key_fn: Optional[Callable[[Dict[str, Any], List[Dict[str, Any]]], Any]] = None,
) -> bool:
    """
    True when at least one actionable, empty, not-yet-attempted field is
    currently rendered inside the visible viewport.
    """
    return find_visible_empty_target(state, viewport_bottom, attempted_keys, attempt_key_fn) is not None


def visible_field_signature(state: Dict[str, Any], viewport_bottom: float) -> FrozenSet[tuple]:
    """
    Coarse fingerprint (label, rounded-y) of every fillable field currently
    inside the viewport. Used to verify a scroll actually moved the view:
    unchanged signature after a scroll means the view didn't move (bottom
    reached), not that nothing is left to fill.
    """
    sig = set()
    for e in state.get("elements", []):
        if e.get("window_role") == "background":
            continue
        if (e.get("type") or "").lower() not in _SIG_TYPES:
            continue
        b = e.get("bbox")
        if not b or len(b) != 4:
            continue
        cy = (b[1] + b[3]) / 2
        if b[1] >= 0 and cy <= viewport_bottom:
            lbl = (e.get("label") or e.get("text") or "").strip().lower()
            sig.add((lbl, round(cy / 15) * 15))
    return frozenset(sig)


def decide(
    state: Dict[str, Any],
    viewport_bottom: float,
    dead_scroll_count: int,
    max_dead_scrolls: int = 2,
    attempted_keys: Optional[Set[Any]] = None,
    attempt_key_fn: Optional[Callable[[Dict[str, Any], List[Dict[str, Any]]], Any]] = None,
) -> NavDecision:
    """
    The single decision point: given the current state and how many
    consecutive scrolls have failed to reveal anything new on this tab,
    what should happen next?

    dead_scroll_count is caller-tracked (increments only on a scroll that
    didn't change visible_field_signature — resets the moment a field is
    visible again), so a long tab can scroll as many times as it genuinely
    has content, while a tab that's truly exhausted gives up promptly.
    """
    if has_visible_empty_target(state, viewport_bottom, attempted_keys, attempt_key_fn):
        return NavDecision(NavAction.WAIT, "actionable empty field visible")
    if dead_scroll_count >= max_dead_scrolls:
        return NavDecision(
            NavAction.ADVANCE_TAB,
            f"{dead_scroll_count} consecutive scrolls revealed nothing new — tab exhausted",
        )
    return NavDecision(NavAction.SCROLL, "no empty target visible — scrolling to reveal more")
