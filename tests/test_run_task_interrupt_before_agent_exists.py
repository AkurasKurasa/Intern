"""
Regression test: sending CTRL_BREAK_EVENT (what Electron's Stop button
sends) to run_task.py *before* LLMAgent(...) has finished constructing --
e.g. during the 5-second pre-run countdown, or while the checkpoint is
still loading -- must exit cleanly (code 0), not hard-kill.

Reproduced live: stopping during the countdown still exited 3221225786
(Windows' STATUS_CONTROL_C_EXIT) even after installing the SIGBREAK
handler (test_run_task_ctrl_break_handler.py) that makes CTRL_BREAK_EVENT
raise a catchable KeyboardInterrupt. Root cause: the handler makes the
signal catchable, but run_task.py's try/except/finally only ever wrapped
`agent.run()` -- the countdown and the entire LLMAgent(...) construction
sat outside it, so an interrupt during either was still completely
uncaught. Fixed by widening the try to cover everything from the
countdown through agent.run(), with `agent = None` initialized first so
the except/finally blocks can tell "never got that far" apart from "got
partway through a run" without crashing on their own over an agent that
was never constructed (e.g. `agent._heuristic_steps` in the metrics call
used to assume `agent` always existed).

This spawns the real run_task.py and interrupts it during the countdown
-- before LLMAgent(...) is ever reached, so no real automation is
possible at any point (agent construction alone doesn't touch the mouse
or keyboard; only agent.run() does, and this test never lets it get
there). Slower than the rest of the suite (real interpreter + heavy
imports) -- acceptable for the one thing this test exists to prove.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_ctrl_break_during_countdown_exits_cleanly_not_hard_killed():
    proc = subprocess.Popen(
        [sys.executable, "-u", str(_ROOT / "run_task.py"),
         "--model", str(_ROOT / "tasks" / "form_filling" / "model.pt")],
        cwd=str(_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    lines = []
    saw_countdown_tick = False
    deadline = time.time() + 30
    try:
        # Read until we've seen at least one real countdown tick -- proof
        # we're safely inside the countdown window, well before
        # LLMAgent(...) (and therefore agent.run()) could ever start.
        while time.time() < deadline and not saw_countdown_tick:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            lines.append(line.rstrip("\n"))
            if line.startswith("COUNTDOWN ") and line.strip() != "COUNTDOWN_BEGIN":
                saw_countdown_tick = True

        assert saw_countdown_tick, f"never reached a countdown tick; captured: {lines}"

        proc.send_signal(signal.CTRL_BREAK_EVENT)

        remaining, _ = proc.communicate(timeout=20)
        lines.extend(remaining.splitlines())
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)

    full_output = "\n".join(lines)
    assert proc.returncode == 0, (
        f"exit code {proc.returncode} (0xC000013A / 3221225786 means it was "
        f"hard-killed, not caught) -- output:\n{full_output}"
    )
    assert "Run interrupted by user" in full_output, full_output
    assert "Run ended" in full_output, full_output
    # The exact crash class this fix targets: agent is still None at this
    # point, so any code that assumed it always existed would blow up here.
    assert "AttributeError" not in full_output, full_output
    assert "NameError" not in full_output, full_output
