"""
ScopeConfig — per-application configuration injected into LLMAgent.

Everything app-specific (which form, which tabs, how sections/records are named)
lives here instead of being hardcoded in agent.py. The DEFAULT is fully generic:
no tabs, no sections, no record delimiter — so a brand-new GUI gets an agent that
makes ZERO assumptions. Each scope (insurance form, Excel, triage, …) passes its
own ScopeConfig; the agent code stays application-blind.

This is the seam that turns "an insurance-form agent" into "scope #1 of a
scope-agnostic engine."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set


def _default_section_format(kind: str, num: str) -> str:
    return f"{kind.title()} {num}"


@dataclass
class ScopeConfig:
    """App-specific knobs. All default to generic / none."""

    # Tab navigation (forms with tab strips). Empty → tab logic never fires.
    tab_names:      Set[str]  = field(default_factory=set)   # was _KNOWN_TABS
    tab_pane_names: List[str] = field(default_factory=list)  # was _TAB_PANE_NAMES

    # Repeated sections (e.g. Driver 1..N, Vehicle 1..N). section_pattern=None →
    # _detect_section is a no-op (returns ""), which is correct for non-sectioned
    # apps. The pattern is a regex with two groups (kind, number).
    section_prefix:  str             = "section_"
    section_pattern: Optional[str]   = None   # was r"section_(driver|vehicle)_(\d+)$"
    section_format:  Callable[[str, str], str] = _default_section_format

    # Multi-record source delimiter (moves to the DataSource, kept here for ref).
    record_delimiter: Optional[str] = None    # was "RECORD N OF M"


# ── Prebuilt scope: the car-insurance data-entry form (dev fixture) ───────────
INSURANCE_SCOPE = ScopeConfig(
    tab_names={"policy", "policyholder", "vehicle", "coverage",
               "drivers", "history", "claims", "payment"},
    tab_pane_names=["tab_policy", "tab_policyholder", "tab_vehicle", "tab_coverage",
                    "tab_drivers", "tab_history", "tab_claims", "tab_payment"],
    section_prefix="section_",
    section_pattern=r"section_(driver|vehicle)_(\d+)$",
    section_format=_default_section_format,
    record_delimiter="RECORD N OF M",
)
