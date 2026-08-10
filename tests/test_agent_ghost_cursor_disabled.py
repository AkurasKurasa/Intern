"""
Regression test: LLMAgent must construct its ActionExecutor with
ghost_cursor=False -- disabled 2026-08-10 after three separate, real,
live-confirmed bugs traced to this one feature in a single session:

1. Breaks every UIA ControlFromPoint read for as long as it runs (the
   overlay covers the whole screen; see execution_ghost_overlay_breaks_
   uia_reads in DEVELOPERS.md).
2. Creating it steals OS foreground/activation on creation and, before a
   same-day WS_EX_NOACTIVATE fix, kept re-stealing it after being
   reclaimed -- reported live as "clicked the form, literally nothing
   happened."
3. Found live, directly, the same day as the NOACTIVATE fix: a real
   pyautogui.click() aimed exactly at the drawn ghost-cursor icon was
   swallowed by the overlay instead of reaching a real target window
   (Notepad) underneath, despite WS_EX_TRANSPARENT being set correctly --
   this feature's own stated "MUST get right" requirement had never
   actually been verified against a real click on real drawn content.

Purely cosmetic (avoids moving the real OS cursor during a live run) in
exchange for three confirmed incidents in one session is not a trade
worth keeping active. This test exists so ghost_cursor doesn't silently
flip back to True in some future refactor without that being a deliberate
decision.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def test_llm_agent_constructs_executor_with_ghost_cursor_disabled():
    captured = {}

    def _fake_executor(*args, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    # agent.py imports ActionExecutor locally inside __init__ (from
    # agent.executor import ActionExecutor, ...), not at module level --
    # patch the source module it actually pulls from at call time.
    with patch("agent.executor.ActionExecutor", side_effect=_fake_executor):
        LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    assert captured.get("ghost_cursor") is False
