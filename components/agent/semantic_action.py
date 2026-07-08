"""
agent/semantic_action.py
========================
Universal Semantic Action Space — the shared action vocabulary between the
transformer (WHERE + WHICH VERB), the LLM (WHAT value), the recorder/trace
translator (labels demos), and the executor (HOW).

Verbs are widget/app-agnostic intents, not raw input events. A verb pairs
with a `target` (pointer to an element in the current observed state) and,
for value-carrying verbs, a `value` supplied by the LLM. Concrete mechanics
(TogglePattern vs click, SelectionItemPattern vs click+type, etc.) are the
executor's job, dispatched by the target element's control-type — never
baked into the verb itself.

This module is additive: it does not change any existing behavior. It is a
typed, shared vocabulary that other components adopt incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class Verb(str, Enum):
    FOCUS         = "focus"          # move keyboard focus to target, no value change
    SET_VALUE     = "set_value"      # write `value` into target (edit/spin/text field)
    SELECT_OPTION = "select_option"  # choose `value` from target (combobox/list/radio)
    TOGGLE        = "toggle"         # flip target's boolean state (checkbox)
    INVOKE        = "invoke"         # press/activate target (button/tab/link)
    SCROLL_TO     = "scroll_to"      # bring target (or a direction) into view
    HOTKEY        = "hotkey"         # window-level key combo, no specific target
    WAIT          = "wait"           # no-op, let the UI settle
    VERIFY        = "verify"         # re-observe and compare target against expected value
    DONE          = "done"           # task/record complete, stop the loop


# Verbs that carry an LLM-supplied value.
VALUE_VERBS = {Verb.SET_VALUE, Verb.SELECT_OPTION}

# Verbs that require a target element to act on.
TARGETED_VERBS = {
    Verb.FOCUS, Verb.SET_VALUE, Verb.SELECT_OPTION,
    Verb.TOGGLE, Verb.INVOKE, Verb.SCROLL_TO, Verb.VERIFY,
}


@dataclass
class SemanticAction:
    """
    One step of intent. `target_idx` is a pointer into the *current* observed
    state's element list (the same pointer-head contract the transformer
    already uses for click_elem/source_elem) — it is the durable, resolution-
    independent identity of the target. `position` is a resolved screen/UIA
    coordinate, filled in by whoever has the state in hand (agent merge
    step), and is what the executor actually acts on today.
    """
    verb:        Verb
    target_idx:  Optional[int]           = None   # pointer into state["elements"]
    source_idx:  Optional[int]           = None   # pointer into background elements (value source)
    position:    Optional[Tuple[float, float]] = None
    value:       str                     = ""
    keystrokes:  List[str]               = field(default_factory=list)
    key_count:   int                     = 1
    direction:   str                     = "down"
    clicks:      int                     = 3
    reason:      str                     = ""

    # ── legacy interop ──────────────────────────────────────────────────────
    # The executor and most of agent.py still speak the old
    # {"action_type": "click"/"keyboard"/"scroll"/"no_op", ...} dict shape.
    # These two conversions let new code adopt SemanticAction without
    # requiring a simultaneous rewrite of every call-site.

    _TO_LEGACY_TYPE = {
        Verb.FOCUS:         "click",
        Verb.INVOKE:        "click",
        Verb.TOGGLE:        "click",
        Verb.SELECT_OPTION: "click",
        Verb.SET_VALUE:     "keyboard",
        Verb.HOTKEY:        "keyboard",
        Verb.SCROLL_TO:     "scroll",
        Verb.WAIT:          "no_op",
        Verb.VERIFY:        "no_op",
        Verb.DONE:          "no_op",
    }

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Convert to the dict shape ActionExecutor.execute() already accepts."""
        action_type = self._TO_LEGACY_TYPE[self.verb]
        d: Dict[str, Any] = {"action_type": action_type}
        if action_type == "click":
            d["click_position"] = list(self.position or [0.0, 0.0])
        elif action_type == "keyboard":
            if self.verb is Verb.HOTKEY:
                d["keystrokes"] = self.keystrokes
                d["key_count"]  = max(1, len(self.keystrokes))
            else:
                d["text"]      = self.value
                d["key_count"] = max(1, len(self.value))
        elif action_type == "scroll":
            d["click_position"] = list(self.position or [0.0, 0.0])
            d["direction"]      = self.direction
            d["clicks"]         = self.clicks
        return d

    @classmethod
    def from_legacy_dict(cls, d: Dict[str, Any]) -> "SemanticAction":
        """
        Best-effort reverse mapping, for callers migrating gradually. This is
        necessarily lossy — a legacy "click" cannot tell FOCUS from TOGGLE from
        INVOKE without the target element's control-type, which the caller
        should supply by overwriting `.verb` after construction once known.
        Defaults to INVOKE for clicks (the least presumptive reading).
        """
        action_type = d.get("action_type", "no_op")
        if action_type == "click":
            pos = d.get("click_position", [0.0, 0.0])
            return cls(verb=Verb.INVOKE, position=tuple(pos))
        if action_type == "keyboard":
            text = d.get("text", "")
            if text:
                return cls(verb=Verb.SET_VALUE, value=text)
            return cls(verb=Verb.HOTKEY, keystrokes=list(d.get("keystrokes", [])))
        if action_type == "scroll":
            pos = d.get("click_position", [0.0, 0.0])
            return cls(verb=Verb.SCROLL_TO, position=tuple(pos),
                       direction=d.get("direction", "down"), clicks=d.get("clicks", 3))
        return cls(verb=Verb.WAIT)
