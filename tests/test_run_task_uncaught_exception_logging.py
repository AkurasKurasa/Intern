"""
Regression test: run_task.py logs ANY uncaught exception via sys.excepthook,
not just ones caught inside agent.run()'s own try/except.

Found 2026-08-08: a live run's log cut off right after "Perception: UIA
(accessibility tree)" with nothing explaining why -- the crash happened
during LLMAgent(...) construction, which sits entirely outside the
try/except around agent.run(). The log file, which this whole project's
diagnose-from-logs workflow depends on, had no record of what killed the
process; the traceback only ever reached the console.

Fixed via sys.excepthook, which Python calls for ANY exception that reaches
the top of the script uncaught, regardless of where it happens -- catches
this class of gap structurally instead of adding another narrow try/except.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run_task


def test_excepthook_is_installed():
    assert sys.excepthook is run_task._log_uncaught_exception


def test_uncaught_exception_is_logged_with_full_traceback(caplog):
    try:
        raise ValueError("synthetic crash for test")
    except ValueError:
        exc_type, exc_value, exc_tb = sys.exc_info()

    with caplog.at_level(logging.ERROR, logger="run_task"):
        run_task._log_uncaught_exception(exc_type, exc_value, exc_tb)

    assert any("Unhandled exception" in r.message for r in caplog.records)
    error_record = next(r for r in caplog.records if "Unhandled exception" in r.message)
    assert error_record.exc_info is not None
    assert error_record.exc_info[0] is ValueError
