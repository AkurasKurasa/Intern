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

    Also checks e["visible"] (default True, so callers/tests that don't set
    it are unaffected) alongside the geometric bbox/viewport_bottom check.
    Found 2026-08-08, live: a run needed zero explicit SCROLL decisions from
    this module at all, yet the user watched the on-screen view visibly
    creep down one field at a time as each field got clicked — the target
    app's own scroll panel auto-scrolling a newly-focused control into view,
    invisible to this module because viewport_bottom (a window-rect
    estimate, necessarily approximate) judged those fields "visible" when
    they weren't really on-screen yet without that per-field auto-scroll.
    UIA exposes the real answer directly (IsOffscreen); ui_observer.py now
    reads it into "visible" instead of hardcoding True. Trusting it here
    replaces a second geometric estimate with the authoritative one.
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
        if not e.get("visible", True):
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
        if not e.get("visible", True):
            continue
        b = e.get("bbox")
        if not b or len(b) != 4:
            continue
        cy = (b[1] + b[3]) / 2
        if b[1] >= 0 and cy <= viewport_bottom:
            lbl = (e.get("label") or e.get("text") or "").strip().lower()
            sig.add((lbl, round(cy / 15) * 15))
    return frozenset(sig)


def _max_points_in_window(cys: List[float], width: float) -> int:
    """
    Given every remaining target's y-position (in CURRENT, unscrolled
    coordinates), the most that could ever be simultaneously on-screen at
    once, if the pane could scroll to any offset >= 0.

    Scrolling down by S moves every element's y up by S; a viewport window
    of [0, width] at scroll-offset S is therefore the same set of points as
    a fixed window of width `width` starting at y=S in CURRENT coordinates.
    So "best achievable simultaneous count" is just the classic max-points-
    in-a-fixed-width-window problem — two pointers over the sorted y's.
    """
    pts = sorted(cys)
    best = 0
    left = 0
    for right in range(len(pts)):
        while pts[right] - pts[left] > width:
            left += 1
        best = max(best, right - left + 1)
    return best


def optimal_view_counts(
    state: Dict[str, Any],
    viewport_bottom: float,
    attempted_keys: Optional[Set[Any]] = None,
    attempt_key_fn: Optional[Callable[[Dict[str, Any], List[Dict[str, Any]]], Any]] = None,
) -> tuple:
    """
    (cur, best) — cur is how many actionable empty targets are visible
    RIGHT NOW; best is the most that could EVER be simultaneously visible
    at some scroll position, counting every remaining target's known
    position (including ones currently off-screen below the viewport —
    ui_observer.py reports their real bbox regardless of current
    visibility).

    Added 2026-08-08 per direct user correction: "nothing's visible ->
    scroll down once" is the wrong question. The right one is "is this
    already the BEST view available, or does scrolling get us to a view
    with MORE actionable targets on it at once." cur >= best is the
    stopping condition for scrolling, not "cur > 0".

    A target counts toward `best` only if its bbox top is >= 0 (i.e. not
    already scrolled past — this module only scrolls DOWN, so a target
    above the current fold can't be brought back by scrolling further
    down). `cur` additionally requires e["visible"] (real IsOffscreen
    signal, not just geometry) since that reflects what's ACTUALLY
    rendered right now, whereas the position used for `best` is a
    hypothetical future scroll offset the visible flag can't speak to.
    """
    elements = state.get("elements", [])
    attempted_keys = attempted_keys or set()
    cys: List[float] = []
    cur = 0
    for e in elements:
        if e.get("window_role") == "background":
            continue
        if (e.get("type") or "").lower() not in _FILLABLE_TYPES:
            continue
        if (e.get("value") or "").strip():
            continue
        if attempt_key_fn is not None and attempt_key_fn(e, elements) in attempted_keys:
            continue
        b = e.get("bbox")
        if not b or len(b) != 4 or b[1] < 0:
            continue
        cy = (b[1] + b[3]) / 2
        cys.append(cy)
        if cy <= viewport_bottom and e.get("visible", True):
            cur += 1
    best = _max_points_in_window(cys, viewport_bottom) if cys else 0
    return cur, best


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
    consecutive scrolls have failed to reveal anything actionable, what
    should happen next?

    WAIT the moment ANYTHING actionable is visible (cur > 0) — do not hold
    out for a denser view. `best` (see optimal_view_counts) is still
    computed and used to decide whether scrolling is worth trying at all
    (best == 0 means nothing is reachable anywhere) and to size the
    ADVANCE_TAB give-up message, but it is deliberately NOT a condition for
    acting.

    Found 2026-08-08, live, immediately after a version of this function
    that DID require cur == best: the very first view of a tab already had
    16 of 22 total targets visible (cur=16, best=22) — genuinely plenty to
    act on — but decide() kept returning SCROLL, chasing the last few
    fields one at a time (16→17→18→...) for 8 consecutive scrolls. Worse,
    the mathematical maximum (22 simultaneously visible) turned out to be
    physically unreachable: scrolling far enough to bring the last field
    into view pushed an earlier one off the top, so cur capped out at 21
    and never equalled best. dead_scroll_count then hit its cap and handed
    off to ADVANCE_TAB — abandoning the ENTIRE tab having filled zero
    fields, despite 16-21 of them sitting on screen the whole time. Direct
    user correction: "There is nothing to improve yet because we haven't
    even filled up anything yet at all, why are we optimizing when the
    initial view is already optimized?" Right call — chasing a global
    maximum is both pointless (a good-enough view is already actionable)
    and fragile (the maximum isn't always achievable at all). `best` is
    still useful context for deciding whether it's worth scrolling to find
    something when the CURRENT view has nothing (cur == 0), just not a bar
    for stopping once something's already there.

    dead_scroll_count is caller-tracked (increments only when a scroll
    didn't bring cur above 0 — resets the moment it does), so a tab with
    real content further down still gets a bounded number of tries to find
    it, while one that's truly exhausted gives up promptly.
    """
    cur, best = optimal_view_counts(state, viewport_bottom, attempted_keys, attempt_key_fn)
    if cur > 0:
        return NavDecision(NavAction.WAIT, f"{cur} actionable target(s) already visible — act now")
    if best == 0:
        return NavDecision(NavAction.ADVANCE_TAB, "no empty, not-yet-attempted targets remain on this tab")
    if dead_scroll_count >= max_dead_scrolls:
        return NavDecision(
            NavAction.ADVANCE_TAB,
            f"{dead_scroll_count} consecutive scrolls revealed nothing actionable "
            f"({best} target(s) exist somewhere below) — giving up, tab exhausted",
        )
    return NavDecision(
        NavAction.SCROLL,
        f"nothing actionable visible yet, but {best} target(s) exist below — scrolling to reach them",
    )
