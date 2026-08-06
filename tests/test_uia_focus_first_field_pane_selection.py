"""
Regression test for LLMAgent._uia_focus_first_field()'s active-pane detection.

Bug found 2026-08-07, live (a direct consequence of the wrong-tab stuck-loop
fix from earlier the same day): the pane-detection loop iterated
`tab_pane_names` in list order and took the FIRST one that "exists with a
positive-coord child" — not necessarily the ACTUALLY active tab's pane.
Verified live: this locked the search onto an earlier tab's pane and made
the agent skip straight past Policyholder (Policy -> Vehicle -> Coverage,
only 3 of 13 Policy fields ever touched) because Policy's pane satisfied the
loop's check before Policyholder's did.

Fix: use `self._current_tab_idx` (already reliably maintained elsewhere —
it correctly detects "already on this tab") to look up the exact pane name
directly, instead of iterating and guessing from coordinates.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent
from agent.scope import ScopeConfig


_TAB_PANE_NAMES = ["tab_policy", "tab_policyholder", "tab_vehicle"]


def _make_agent(current_tab_idx: int):
    scope = ScopeConfig(tab_pane_names=list(_TAB_PANE_NAMES))
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1, scope=scope)
    agent._current_tab_idx = current_tab_idx
    agent._filled_this_tab = set()
    return agent


def _install_fake_uia(monkeypatch, existing_panes: set[str]):
    """
    Fake `uiautomation` where root.PaneControl(Name=X) returns a pane whose
    .Exists() is True only for names in `existing_panes` (mimicking: only the
    truly active tab's pane exists in the real UIA tree). Every existing pane
    reports one positive-coord child, so the OLD coordinate-guess loop would
    treat it as a valid "active" candidate too — the point of this test is
    that the FIX doesn't even ask about panes it shouldn't.
    """
    calls = []

    def make_pane(name):
        pane = MagicMock()
        pane.Exists.return_value = name in existing_panes
        child = MagicMock()
        child.BoundingRectangle = types.SimpleNamespace(left=10, top=10, width=50, height=20)
        pane.GetChildren.return_value = [child]
        return pane

    root = MagicMock()

    def pane_control(searchDepth=6, Name=""):
        calls.append(Name)
        return make_pane(Name)
    root.PaneControl.side_effect = pane_control
    root.GetChildren.return_value = []  # no fields found — irrelevant to this test

    fake_win32gui = types.SimpleNamespace(GetForegroundWindow=lambda: 111)
    fake_uia = types.SimpleNamespace(ControlFromHandle=lambda hwnd: root)

    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(sys.modules, "uiautomation", fake_uia)
    return calls


def test_goes_straight_to_the_active_tabs_pane_by_index(monkeypatch):
    # Policy's pane ALSO exists (as it did live — verified the old loop
    # locked onto it), but current_tab_idx says Policyholder (index 1) is
    # active. The fix must look up "tab_policyholder" directly, not iterate
    # from "tab_policy" first and stop there.
    calls = _install_fake_uia(monkeypatch, existing_panes={"tab_policy", "tab_policyholder"})
    agent = _make_agent(current_tab_idx=1)

    agent._uia_focus_first_field()

    assert calls[0] == "tab_policyholder", (
        f"expected the direct index-1 lookup first, got call order {calls}"
    )
    assert "tab_policy" not in calls, (
        "fix regressed: fell back to iterating from tab_policy instead of "
        "using the known-active tab index"
    )


def test_falls_back_to_iteration_if_the_indexed_pane_is_missing(monkeypatch):
    # Direct lookup for the active tab's own pane fails (edge case) — must
    # still fall back to the old scan rather than crash or silently give up.
    calls = _install_fake_uia(monkeypatch, existing_panes={"tab_vehicle"})
    agent = _make_agent(current_tab_idx=1)  # "tab_policyholder" won't be found

    agent._uia_focus_first_field()

    assert "tab_policyholder" in calls   # tried the direct lookup first
    assert "tab_vehicle" in calls        # then fell back to scanning the rest
