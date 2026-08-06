"""
Regression test for agent.LLMAgent._focus_first_empty_field().

Bug: its own state-dict candidate scan trusted element bboxes to tell
active-tab fields from inactive-tab ones — verified live (2026-08-07) that a
hidden tab's field can report the exact same positive on-screen bbox as when
visible, so the scan happily focused a field on a tab that wasn't even
showing, forever (a real stuck-loop caught in a live run: 65+ steps
re-focusing "Policy Number" while the Policyholder tab was active).

First fix attempt: try the already-correct, pane-scoped
`_uia_focus_first_field()` first, fall back to the state-dict scan if that
returned False. Still broken — caught live, again — because "the active
pane has nothing left" and "the pane-scoped search failed" both look like
`False`, and falling back on either let the unsafe scan grab a wrong-tab
field right back, masking the "nothing left, advance tab" signal every
caller already handles correctly via `_try_advance_tab`.

Actual fix: for the common case (min_y<=0, true for every current caller),
the pane-scoped result is authoritative, full stop — no fallback. The
state-dict scan only survives for the min_y>0 pane-escape case, which
`_uia_focus_first_field` doesn't support and nothing currently calls.
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
    fake._executor.execute.assert_not_called()


def test_uia_false_is_authoritative_no_unsafe_fallback():
    # This is the actual bug: min_y<=0 and the pane-scoped search says
    # "nothing left on this tab" must return False outright, NOT fall
    # through to a scan that can't tell one tab's fields from another's.
    fake = _fake_self(uia_result=False)
    state = {"elements": [{"type": "editcontrol", "label": "Wrong-Tab Field", "text": "",
                            "bbox": [10, 10, 50, 30], "enabled": True}]}

    result = LLMAgent._focus_first_empty_field(fake, state)

    assert result is False
    fake._uia_focus_first_field.assert_called_once()
    fake._executor.execute.assert_not_called()


def test_min_y_floor_skips_the_uia_search_entirely():
    # min_y>0 is the pane-escape case _uia_focus_first_field doesn't support —
    # must go straight to the state-dict scan, not call the UIA path at all.
    fake = _fake_self(uia_result=True)
    state = {"elements": [{"type": "editcontrol", "label": "Below Floor", "text": "",
                            "bbox": [10, 500, 50, 520], "enabled": True}]}

    result = LLMAgent._focus_first_empty_field(fake, state, min_y=100)

    fake._uia_focus_first_field.assert_not_called()
    assert result is True
    fake._executor.execute.assert_called_once()
