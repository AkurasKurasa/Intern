"""
Regression test: LLMAgent must construct its ActionExecutor with
ghost_cursor=True -- re-enabled 2026-08-11, direct request, after every
known root cause behind the feature's real bugs was actually fixed
rather than avoided:

1. Broke every UIA ControlFromPoint read for as long as it ran -- root
   cause (4 below) fixed; the defensive skip in
   _select_combobox_value_via_keyboard() stays in place regardless of
   whether the overlay is active.
2. Creating it stole OS foreground/activation and kept re-stealing it
   after being reclaimed -- fixed with WS_EX_NOACTIVATE, confirmed live
   (5+ seconds, zero recurrence after reclaim).
3. A real click on the drawn cursor icon got swallowed instead of
   passing through, despite WS_EX_TRANSPARENT being "set" -- turned out
   to share the same root cause as (4).
4. ROOT CAUSE: root.winfo_id() for a plain Tk() root returns a CHILD
   window (class TkChild), not the real top-level window Windows
   composites/hit-tests (class TkTopLevel). Every style bit was landing
   on the wrong window. _make_click_through() now resolves the correct
   top-level ancestor via GetAncestor() first -- see
   ghost_cursor_wrong_hwnd_render_bug in DEVELOPERS.md for the full
   diagnosis, including two other theories tried and disproven with real
   evidence (pixel-sampling via GetPixel turned out to be an unreliable
   way to verify a layered window's actual on-screen content) before
   finding this.

This test exists so ghost_cursor doesn't silently flip back to False in
some future refactor without that being a deliberate decision -- same
role the previous (now inverted) version of this test played for the
opposite state.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def test_llm_agent_constructs_executor_with_ghost_cursor_enabled():
    captured = {}

    def _fake_executor(*args, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    # agent.py imports ActionExecutor locally inside __init__ (from
    # agent.executor import ActionExecutor, ...), not at module level --
    # patch the source module it actually pulls from at call time.
    with patch("agent.executor.ActionExecutor", side_effect=_fake_executor):
        LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    assert captured.get("ghost_cursor") is True
