"""
Observer — the base every perception adapter inherits.

Generalizes all observers: snapshot() is a SHARED template (capture → normalize →
validate). Each observer implements `_raw_snapshot()` (its app-specific I/O) and
declares how its raw dialect maps to the canonical element schema (TYPE_MAP /
KEY_MAP, or override `normalize`). The contract (observers/schema.py) is then
enforced AT THE SOURCE — a foreign dialect is caught here, not discovered late in
the agent.

UIA is already canonical → empty maps → normalize is identity. Excel/web declare
their maps (cell→editcontrol, text→value, …).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict

try:
    from observers.schema import validate_state
except ImportError:  # pragma: no cover
    from components.observers.schema import validate_state

logger = logging.getLogger(__name__)


class Observer(ABC):
    # raw element "type" → canonical CONTROL_TYPES (empty = identity)
    TYPE_MAP: Dict[str, str] = {}
    # raw element key → canonical key, e.g. {"text": "value"} (empty = identity)
    KEY_MAP:  Dict[str, str] = {}
    # short adapter name for log lines / state["source"]
    source_name: str = ""

    def snapshot(self) -> Dict[str, Any]:
        """Capture → normalize → validate. Shared; do not override."""
        state = self._raw_snapshot()
        state = self.normalize(state)
        self._check(state)
        return state

    @abstractmethod
    def _raw_snapshot(self) -> Dict[str, Any]:
        """Adapter-specific capture (UIA tree / Excel COM / web DOM)."""
        ...

    # ── normalization (dialect → canonical) ──────────────────────────────────

    def normalize(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Remap element types via TYPE_MAP and keys via KEY_MAP. Identity when
        both are empty (so UIA, already canonical, is untouched). Observers with
        richer needs override this."""
        if not self.TYPE_MAP and not self.KEY_MAP:
            return state
        for e in state.get("elements", []):
            for raw_k, canon_k in self.KEY_MAP.items():
                if raw_k in e and canon_k not in e:
                    e[canon_k] = e[raw_k]
            t = e.get("type")
            if t in self.TYPE_MAP:
                e["type"] = self.TYPE_MAP[t]
        if self.source_name and not state.get("source"):
            state["source"] = self.source_name
        return state

    # ── contract enforcement ─────────────────────────────────────────────────

    def _check(self, state: Dict[str, Any]) -> None:
        """Validate against the schema ONCE per instance; log issues loud."""
        if getattr(self, "_schema_checked", False):
            return
        self._schema_checked = True
        who = self.source_name or type(self).__name__
        for issue in validate_state(state):
            lvl = logger.error if issue.startswith("ERROR") else logger.warning
            lvl("Perception schema [%s]: %s", who, issue)
