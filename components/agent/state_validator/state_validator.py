"""
recorder/state_validator/state_validator.py
===================================================
Post-action validator.

After the executor fires an action, StateValidator compares the screen
state before and after to decide whether the action had the expected
effect.

Possible outcomes
-----------------
  ok          — state changed as expected; continue
  no_change   — state looks identical; the action may have missed
  unexpected  — a new dialog, error window, or unknown element appeared
  done        — heuristic suggests the task goal is complete
  error       — an error dialog or crash is visible

Usage
-----
    validator = StateValidator()

    state_before = observer.snapshot()
    executor.execute(action)
    state_after  = observer.snapshot()

    result = validator.validate(state_before, state_after, action)
    print(result.status, result.reason)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ── keywords that suggest an error dialog ─────────────────────────────────────
_ERROR_KEYWORDS: Set[str] = {
    "error", "exception", "failed", "failure", "cannot", "unable",
    "invalid", "not found", "access denied", "crash", "fatal",
}

# ── keywords that suggest a task-complete dialog ───────────────────────────────
_DONE_KEYWORDS: Set[str] = {
    "success", "complete", "completed", "submitted", "saved", "done",
    "confirmation", "thank you", "receipt",
}

# ── control types that count as interactive ────────────────────────────────────
_INTERACTIVE: Set[str] = {
    "editcontrol", "comboboxcontrol", "checkboxcontrol",
    "buttoncontrol", "listitemcontrol",
}


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    status:       str   = "ok"        # ok | no_change | unexpected | done | error
    reason:       str   = ""
    confidence:   float = 1.0
    new_elements: List[dict] = field(default_factory=list)
    lost_elements: List[dict] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.status in ("ok", "done")

    def __repr__(self) -> str:
        return f"ValidationResult(status={self.status!r}, reason={self.reason!r})"


# ── Validator ─────────────────────────────────────────────────────────────────

class StateValidator:
    """
    Compares screen states before and after an action.

    Parameters
    ----------
    focus_matters   : Treat focus change as a positive signal for clicks.
    value_matters   : Treat field value change as a positive signal for keyboard.
    dialog_timeout  : Number of consecutive identical states before declaring
                      no_change (not yet implemented — placeholder for future).
    """

    def __init__(
        self,
        focus_matters:  bool = True,
        value_matters:  bool = True,
    ):
        self.focus_matters = focus_matters
        self.value_matters = value_matters

    # ── Main API ───────────────────────────────────────────────────────────────

    def validate(
        self,
        state_before: dict,
        state_after:  dict,
        action:       dict,
    ) -> ValidationResult:
        """
        Compare two screen snapshots around an action.

        Parameters
        ----------
        state_before : Snapshot taken immediately before the action.
        state_after  : Snapshot taken immediately after the action.
        action       : The action dict that was executed.

        Returns
        -------
        ValidationResult
        """
        elems_before = {
            e.get("element_id"): e
            for e in state_before.get("elements", [])
            if e.get("window_role") != "background"
        }
        elems_after = {
            e.get("element_id"): e
            for e in state_after.get("elements", [])
            if e.get("window_role") != "background"
        }

        new_ids  = set(elems_after) - set(elems_before)
        lost_ids = set(elems_before) - set(elems_after)

        new_elems  = [elems_after[i]  for i in new_ids]
        lost_elems = [elems_before[i] for i in lost_ids]

        # ── Check for error / done dialogs ────────────────────────────────────
        for elem in new_elems:
            text = (elem.get("text") or "").lower()
            if any(kw in text for kw in _ERROR_KEYWORDS):
                return ValidationResult(
                    status="error",
                    reason=f"Error dialog appeared: {elem.get('text','')!r}",
                    confidence=0.9,
                    new_elements=new_elems,
                    lost_elements=lost_elems,
                )
            if any(kw in text for kw in _DONE_KEYWORDS):
                return ValidationResult(
                    status="done",
                    reason=f"Completion indicator: {elem.get('text','')!r}",
                    confidence=0.8,
                    new_elements=new_elems,
                    lost_elements=lost_elems,
                )

        action_type = (action.get("action_type") or "").lower()

        # ── Click: check if focus moved ───────────────────────────────────────
        if action_type == "click" and self.focus_matters:
            fid_before = state_before.get("focused_element_id")
            fid_after  = state_after.get("focused_element_id")
            if fid_before != fid_after:
                return ValidationResult(
                    status="ok",
                    reason="Focus moved after click",
                    new_elements=new_elems,
                    lost_elements=lost_elems,
                )

        # ── Keyboard: check if any value changed ──────────────────────────────
        if action_type == "keyboard" and self.value_matters:
            for eid, elem_after in elems_after.items():
                elem_before = elems_before.get(eid)
                if elem_before is None:
                    continue
                val_before = (elem_before.get("value") or "").strip()
                val_after  = (elem_after.get("value")  or "").strip()
                if val_before != val_after:
                    return ValidationResult(
                        status="ok",
                        reason=f"Field value changed: {val_before!r} → {val_after!r}",
                        new_elements=new_elems,
                        lost_elements=lost_elems,
                    )

        # ── New interactive elements appeared (e.g. dropdown opened) ──────────
        new_interactive = [
            e for e in new_elems
            if (e.get("type") or "").lower() in _INTERACTIVE
        ]
        if new_interactive:
            return ValidationResult(
                status="ok",
                reason=f"{len(new_interactive)} new interactive element(s) appeared",
                new_elements=new_elems,
                lost_elements=lost_elems,
            )

        # ── Unexpected new window / dialog (not interactive, not error/done) ──
        if new_elems and len(new_elems) > 3:
            return ValidationResult(
                status="unexpected",
                reason=f"{len(new_elems)} unexpected new elements appeared",
                confidence=0.6,
                new_elements=new_elems,
                lost_elements=lost_elems,
            )

        # ── Nothing changed ───────────────────────────────────────────────────
        if not new_elems and not lost_elems:
            return ValidationResult(
                status="no_change",
                reason="Screen state unchanged after action",
                confidence=0.7,
                new_elements=[],
                lost_elements=[],
            )

        # ── Something changed but we can't classify it — assume ok ────────────
        return ValidationResult(
            status="ok",
            reason=f"State changed: +{len(new_elems)} / -{len(lost_elems)} elements",
            new_elements=new_elems,
            lost_elements=lost_elems,
        )

    # ── Convenience: all-fields-filled heuristic ──────────────────────────────

    @staticmethod
    def all_fields_filled(state: dict) -> bool:
        """
        Return True if every visible interactive input field has a non-empty value.
        Useful as a task-done heuristic for form-like tasks.
        """
        fields = [
            e for e in state.get("elements", [])
            if e.get("window_role") != "background"
            and (e.get("type") or "").lower() in {"editcontrol", "comboboxcontrol"}
        ]
        if not fields:
            return False
        return all(
            bool((e.get("value") or "").strip())
            for e in fields
        )
