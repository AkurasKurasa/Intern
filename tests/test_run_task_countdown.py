"""
Regression test: run_task.py's pre-run countdown must produce real,
newline-terminated, flushed output -- not the previous `print(..., end="\\r")`
version, which never wrote a newline between its 5 lines at all.

Found live, directly from the user: "I pressed Play and it's not running
at all." recorder_bridge.py's run_capsule() launches this exact script as
a subprocess and reads its stdout line-by-line (`for line in proc.stdout`)
to drive the Electron Play panel's Activity log. Python's text-mode pipe
iteration only yields once a real '\\n' arrives -- with the old `end="\\r"`
version, the entire 5-second countdown produced ZERO newlines, so the
iterator couldn't yield anything until the final line printed afterward.
The whole countdown window looked like total silence to anyone watching
the Play panel, even though the process was legitimately running.

Also covers the second, separate request from the same message ("add a
countdown too"): the lines are structured (COUNTDOWN_BEGIN / COUNTDOWN N /
COUNTDOWN_END) specifically so the Electron renderer can detect and render
an actual countdown UI element instead of raw scrolling log text.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run_task


def _run_countdown(seconds=3):
    lines = []
    sleeps = []
    run_task.print_countdown(
        seconds=seconds,
        sleep_fn=lambda s: sleeps.append(s),
        print_fn=lambda *a, **kw: lines.append(a[0] if a else ""),
    )
    return lines, sleeps


class TestCountdownProducesRealLines:
    def test_every_line_is_a_separate_string_not_one_carriage_returned_blob(self):
        """The exact regression: the old version's 5 counts were a single
        \\r-joined chunk with no '\\n' between them anywhere."""
        lines, _ = _run_countdown(seconds=5)
        # 1 begin marker + 1 hint + 5 counts + 1 end marker
        assert len(lines) == 8
        for line in lines:
            assert "\r" not in line
            assert "\n" not in line

    def test_begins_and_ends_with_parseable_markers(self):
        lines, _ = _run_countdown(seconds=5)
        assert lines[0] == "COUNTDOWN_BEGIN"
        assert lines[-1] == "COUNTDOWN_END"

    def test_counts_down_from_seconds_to_one(self):
        lines, _ = _run_countdown(seconds=5)
        counts = [l for l in lines if l.startswith("COUNTDOWN ") and l != "COUNTDOWN_BEGIN"]
        assert counts == ["COUNTDOWN 5", "COUNTDOWN 4", "COUNTDOWN 3", "COUNTDOWN 2", "COUNTDOWN 1"]

    def test_sleeps_one_second_per_count(self):
        _, sleeps = _run_countdown(seconds=4)
        assert sleeps == [1, 1, 1, 1]

    def test_custom_duration_is_respected(self):
        lines, sleeps = _run_countdown(seconds=2)
        counts = [l for l in lines if l.startswith("COUNTDOWN ") and l != "COUNTDOWN_BEGIN"]
        assert counts == ["COUNTDOWN 2", "COUNTDOWN 1"]
        assert sleeps == [1, 1]

    def test_default_print_and_sleep_are_the_real_builtins_when_not_injected(self, monkeypatch, capsys):
        """Sanity: the injectable params are for testability, not a
        parallel implementation -- with nothing injected it must still
        actually print and actually sleep (using a zero-length sleep here
        so the test doesn't take a real second)."""
        monkeypatch.setattr(run_task.time, "sleep", lambda s: None)
        run_task.print_countdown(seconds=1)
        out = capsys.readouterr().out
        assert "COUNTDOWN_BEGIN" in out
        assert "COUNTDOWN 1" in out


class TestFlushSafePrintSurvivesOSError:
    """Reproduces the exact live crash: recorder_bridge.py is spawned by
    main.js with windowsHide:true (no console window), and an explicit
    sys.stdout.flush() further down that no-console process chain can
    raise OSError: [Errno 22] Invalid argument on Windows even though the
    write itself already succeeded. Found live -- "Run crashed at step 0"
    on the very first countdown line, before a single tick ever displayed
    -- which is exactly what "there isn't even a countdown happening"
    looks like from the Play panel. A fake stream that raises on flush()
    (but not on write()) reproduces the same shape deterministically,
    without depending on the OS-level console/handle conditions that
    triggered it live."""

    def test_flush_oserror_does_not_propagate(self, monkeypatch):
        class _FlushRaisesStream:
            def __init__(self):
                self.written = []
            def write(self, s):
                self.written.append(s)
            def flush(self):
                raise OSError(22, "Invalid argument")

        fake_stream = _FlushRaisesStream()
        monkeypatch.setattr(run_task.sys, "stdout", fake_stream)

        run_task._flush_safe_print("COUNTDOWN_BEGIN")  # must not raise

        assert "".join(fake_stream.written) == "COUNTDOWN_BEGIN\n"

    def test_a_normal_stream_still_gets_flushed(self):
        """Regression check the other direction: the fix must not make
        flush() a no-op unconditionally -- a stream that CAN flush still
        should."""
        class _RecordingStream:
            def __init__(self):
                self.flushed = False
                self.written = []
            def write(self, s):
                self.written.append(s)
            def flush(self):
                self.flushed = True

        import sys as _sys
        real_stdout = _sys.stdout
        fake = _RecordingStream()
        _sys.stdout = fake
        try:
            # print() itself is bound to whichever stdout is current at
            # call time, so this exercises the real builtin, not a mock.
            run_task._flush_safe_print("COUNTDOWN 2")
        finally:
            _sys.stdout = real_stdout

        assert fake.flushed is True
        assert "".join(fake.written) == "COUNTDOWN 2\n"
