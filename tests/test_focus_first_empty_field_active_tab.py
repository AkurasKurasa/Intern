"""
Regression test for agent.LLMAgent._focus_first_empty_field().

Bug: its own state-dict candidate scan trusted element bboxes to tell
active-tab fields from inactive-tab ones — verified live (2026-08-07) that a
hidden tab's field can report the exact same positive on-screen bbox as when
visible, so the scan happily focused a field on a tab that wasn't even
showing, forever (a real stuck-loop caught in a live run: 65+ steps
re-focusing "Policy Number" while the Policyholder tab was active).

Fix: try the already-correct, pane-scoped `_uia_focus_first_field()` first
(verified live that an inactive tab's pane genuinely stops existing in the
UIA tree, unlike its bbox) — only fall back to the state-dict scan if that
finds nothing, or if a caller asks for the min_y floor it doesn't support.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

from agent.agent import LLMAgent


def _fake_self(uia_result: bool):
    """A minimal stand-in for `self` — only the attributes this method touches."""
    fake = MagicMock()
    fake._uia_focus_first_field.return_value = uia_result
    fake._filled_this_tab = set()
    fake._detect_section.return_value = ""
    return fake


def test_prefers_the_pane_scoped_uia_search_and_short_circuits():
    fake = _fake_self(uia_result=True)
    state = {"elements": [{"type": "editcontrol", "label": "Should Not Be Used",
                            "bbox": [10, 10, 50, 30], "enabled": True}]}

    result = LLMAgent._focus_first_empty_field(fake, state)

    assert result is True
    fake._uia_focus_first_field.assert_called_once()
    # Short-circuited — never touched the unreliable state-dict candidate scan.
    fake._executor.execute.assert_not_called()


def test_falls_back_to_state_scan_when_uia_search_finds_nothing():
    fake = _fake_self(uia_result=False)
    state = {"elements": [{"type": "editcontrol", "label": "Fallback Field", "text": "",
                            "bbox": [10, 10, 50, 30], "enabled": True}]}

    result = LLMAgent._focus_first_empty_field(fake, state)

    assert result is True
    fake._uia_focus_first_field.assert_called_once()
    fake._executor.execute.assert_called_once()


def test_min_y_floor_skips_the_uia_search_entirely():
    # min_y>0 is the pane-escape case _uia_focus_first_field doesn't support —
    # must go straight to the state-dict scan, not call the UIA path at all.
    fake = _fake_self(uia_result=True)
    state = {"elements": [{"type": "editcontrol", "label": "Below Floor", "text": "",
                            "bbox": [10, 500, 50, 520], "enabled": True}]}

    LLMAgent._focus_first_empty_field(fake, state, min_y=100)

    fake._uia_focus_first_field.assert_not_called()
