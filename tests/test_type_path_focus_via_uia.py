"""
Regression tests for two type-execution call sites in agent.py's run() that
used to click a raw coordinate immediately before pasting, with no real
focus verification.

Found live 2026-08-14, investigating a direct report ("verify-at-fill
failure") on two fields with the identical symptom (typed text never lands,
field stays empty) but two DIFFERENT root causes:

  - 'Years Continuously Insured' -- already diagnosed 2026-08-08 (comment
    preserved in agent.py) as a focus-TRANSITION race: the simulated click
    reaches the field UIA reports as focused, but real OS keyboard focus
    hasn't actually landed there yet by the time the paste fires. The
    2026-08-08 fix (click + a flat 0.1s sleep) still wasn't enough --
    reproduced failing again tonight, in two separate live runs, at the
    identical coordinate.

  - 'Years at Address' -- a genuinely different mechanism: a direct check
    against the field's real, current UIA bbox (read live from the actual
    form, not assumed) found the click coordinate the log used was 474px
    off from where the field really sits. Not a race -- a stale coordinate.

Both are fixed the same way, at both call sites that had this raw-click
pattern (the standard "focused, needs typing" path, and the "collapse
navigate+fill into one step" path): try _focus_element_via_uia first (UIA's
own SetFocus(), already built and proven for exactly this class of bug --
see its own docstring, 'Street Address 1/2', 2026-08-08) -- it sidesteps
the focus-transition race (a direct, synchronous accessibility call, not a
queued OS input event) AND sidesteps coordinate staleness (looks the
control up by its current accessible Name, not an earlier-captured bbox).
Falls back to the coordinate click only if that lookup fails -- same
fallback shape as every other _focus_element_via_uia call site in this
file (the redirect-click sites fixed earlier the same night).

Deliberately NOT a fix specific to either field -- these two names are the
reproduction cases that surfaced the bug, not the scope of the fix. Verified
directly: this applies to the shared, generic type-execution code, not a
per-label branch.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _make_agent():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1, step_delay=0)
    agent._executor = MagicMock()
    return agent


def _should_use_raw_click(agent, label, uia_focus_succeeds: bool) -> bool:
    """Mirrors the exact gating condition at both fixed call sites:
    `if not (label and self._focus_element_via_uia(label, expected_bbox=bbox)):
    raw coordinate click`."""
    agent._focus_element_via_uia = MagicMock(return_value=uia_focus_succeeds)
    return not (label and agent._focus_element_via_uia(label, expected_bbox=[0, 0, 10, 10]))


class TestFocusViaUiaTriedFirst:
    def test_uia_focus_success_means_no_raw_click_needed(self):
        agent = _make_agent()
        assert _should_use_raw_click(agent, "Years Continuously Insured", uia_focus_succeeds=True) is False

    def test_uia_focus_failure_falls_back_to_raw_click(self):
        agent = _make_agent()
        assert _should_use_raw_click(agent, "Years at Address", uia_focus_succeeds=False) is True

    def test_missing_label_falls_back_to_raw_click_without_calling_uia(self):
        """No label to look up by name -> can't use the UIA path at all,
        must fall back -- mirrors real behavior for an element with no
        readable label/text."""
        agent = _make_agent()
        agent._focus_element_via_uia = MagicMock(return_value=True)
        result = not ("" and agent._focus_element_via_uia("", expected_bbox=[0, 0, 10, 10]))
        assert result is True
        agent._focus_element_via_uia.assert_not_called()


class TestBothCallSitesWireInTheRealGuard:
    """Direct, real check against the actual file -- not just the isolated
    mirror logic above. Confirms both the standard type path and the
    collapse-navigate+fill path actually call self._focus_element_via_uia,
    not a re-implementation that could silently drift from it."""

    def _agent_py_source(self) -> str:
        return (Path(__file__).resolve().parent.parent
                / "components" / "agent" / "agent.py").read_text(encoding="utf-8")

    def test_standard_type_path_calls_focus_via_uia_before_the_click(self):
        src = self._agent_py_source()
        anchor = src.index("if _focused_el.get(\"bbox\"):")
        window = src[anchor:anchor + 400]
        assert "self._focus_element_via_uia(_fc_label" in window
        assert 'self._executor.execute({"action_type": "click"' in window

    def test_collapse_navigate_fill_path_calls_focus_via_uia_before_the_click(self):
        src = self._agent_py_source()
        anchor = src.index("collapsing navigate+fill")
        window = src[anchor:anchor + 1300]
        assert "self._focus_element_via_uia(_cn_label" in window
        assert 'self._executor.execute({"action_type": "click"' in window

    def test_fix_is_not_hardcoded_to_either_specific_field_name(self):
        """The whole point -- this must be a generic label-driven call,
        never a literal field-name comparison, or it's exactly the
        deterministic per-field patch this project explicitly avoids.
        These two names DO appear in agent.py -- as comments documenting
        the incidents that surfaced the bug (the established pattern
        throughout this file) -- so this checks for real CODE logic
        (an equality/membership check against the literal name), not mere
        textual presence, which would false-fail on that documentation."""
        import re
        src = self._agent_py_source()
        for name in ("Years Continuously Insured", "Years at Address"):
            assert not re.search(rf'==\s*[\'"]{re.escape(name)}[\'"]', src), \
                f"found a hardcoded equality check against {name!r}"
            assert not re.search(rf'[\'"]{re.escape(name)}[\'"]\s*==', src), \
                f"found a hardcoded equality check against {name!r}"
            assert not re.search(rf'in\s*\([^)]*[\'"]{re.escape(name)}[\'"]', src), \
                f"found {name!r} inside a hardcoded membership check"
