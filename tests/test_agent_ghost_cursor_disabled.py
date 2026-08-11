"""
Regression test: LLMAgent must construct its ActionExecutor with
ghost_cursor=False -- reverted 2026-08-11, direct request ("Same old
problem, fuck the cursor and the caret, revert to when the play was
working properly") after re-enabling it a second time (see git history)
still cost a live session its working state.

ghost_cursor=True DID pass a real live checkpoint after every one of its
four known root causes was fixed (broken UIA reads, foreground theft,
click-swallowing, and the root-cause wrong-HWND bug -- see
ghost_cursor_wrong_hwnd_render_bug in DEVELOPERS.md for that whole
investigation). This isn't a claim that those fixes were wrong or that
the overlay is newly broken again -- it's the user deciding the visual
overlay itself isn't worth the recurring risk, after it cost two
separate live sessions their working state. Cursor-free clicking is not
lost by this revert: _try_semantic_click() (BM_CLICK on real Button-
class win32 controls, agent/executor.py) still applies on every click
regardless of this flag.

This test exists so ghost_cursor doesn't silently flip back to True in
some future refactor without that being a deliberate decision -- same
role the previous (now inverted) version of this test played for the
opposite state.
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
