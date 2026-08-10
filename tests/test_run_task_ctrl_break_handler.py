"""
Regression test: run_task.py must install a SIGBREAK handler that raises
KeyboardInterrupt, or Electron's Stop button silently hard-kills the
process instead of running its own partial-results/metrics-saving cleanup.

Found live, directly from the user's own capsule run: clicking Stop in
the Play panel produced "Run ended (exit code 3221225786)" -- 3221225786
is 0xC000013A, Windows' STATUS_CONTROL_C_EXIT, the OS's default hard-kill
response to an unhandled console control event. recorder_bridge.py's
stop_capsule() sends CTRL_BREAK_EVENT specifically because run_task.py's
own run() loop catches KeyboardInterrupt to save partial results -- but on
Windows, CTRL_BREAK_EVENT maps to SIGBREAK, and unlike SIGINT (Ctrl+C),
Python installs NO default handler for it. Verified the mechanism with a
standalone child-process test before writing this fix (no handler: hard
kill, exit 3221225786; with one: KeyboardInterrupt caught, exit 0) --
same verification repeated here as an actual regression test.
"""
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run_task


class TestSigBreakHandlerInstalled:
    def test_a_sigbreak_handler_is_registered_on_windows(self):
        if not hasattr(signal, "SIGBREAK"):
            import pytest
            pytest.skip("SIGBREAK only exists on Windows")
        assert signal.getsignal(signal.SIGBREAK) is run_task._handle_ctrl_break

    def test_the_handler_raises_keyboard_interrupt(self):
        if not hasattr(signal, "SIGBREAK"):
            import pytest
            pytest.skip("SIGBREAK only exists on Windows")
        import pytest
        with pytest.raises(KeyboardInterrupt):
            run_task._handle_ctrl_break(signal.SIGBREAK, None)
