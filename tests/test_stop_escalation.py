"""Stopping a capsule run has to finish in bounded time.

CTRL_BREAK_EVENT is the right first move: it raises KeyboardInterrupt inside
run_task.py, whose run() catches it to save partial results and write metrics.
But a signal only lands when Python next executes bytecode, and a step blocked
in a live LLM request sits inside a socket read for five to seven seconds
first. Direct report: "stopping takes too long". Nothing escalated, so a
wedged run could sit there indefinitely.

These pin the escalation: signal, then terminate, then kill, each with a
deadline, and none of it on the thread that serves commands.

Run:  python -m pytest tests/test_stop_escalation.py -q
"""

import ast
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BRIDGE = REPO / "app" / "recorder_bridge.py"


@pytest.fixture(scope="module")
def escalate():
    """_escalate_stop, compiled out of recorder_bridge.py on its own.

    Importing the bridge starts a DemoRecorder and a whole observer stack;
    this is a thirty-line shutdown helper, so it is lifted rather than paid
    for.
    """
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef)
               and any(isinstance(f, ast.FunctionDef) and f.name == "_escalate_stop"
                       for f in n.body))
    wanted = {"_escalate_stop"}
    body = [n for n in cls.body
            if (isinstance(n, ast.FunctionDef) and n.name in wanted)
            or isinstance(n, ast.Assign)]
    shim = ast.ClassDef(name="Shim", bases=[], keywords=[], body=body, decorator_list=[],
                        type_params=[])
    mod = ast.Module(body=[shim], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {"subprocess": subprocess, "threading": threading,
          "_log_capsule_line": lambda *_a, **_k: None}
    exec(compile(mod, str(BRIDGE), "exec"), ns)
    return ns["Shim"]


class FakeProc:
    """A process that exits after `dies_after` seconds, or never."""

    def __init__(self, dies_after=None):
        self._dies_after = dies_after
        self._started = time.monotonic()
        self.terminated = False
        self.killed = False

    def _alive(self):
        if self.killed or self.terminated:
            return False
        return self._dies_after is None or (time.monotonic() - self._started) < self._dies_after

    def wait(self, timeout=None):
        deadline = time.monotonic() + (timeout or 0)
        while time.monotonic() < deadline:
            if not self._alive():
                return 0
            time.sleep(0.01)
        if self._alive():
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def settle(shim, proc, budget=3.0):
    """Run the escalation with short deadlines and wait for its thread."""
    shim._STOP_GRACE_SEC = 0.2
    shim._STOP_KILL_SEC = 0.2
    before = set(threading.enumerate())
    shim._escalate_stop(shim, proc)
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if not (set(threading.enumerate()) - before):
            return
        time.sleep(0.01)
    pytest.fail("the escalation thread did not finish")


def test_a_clean_exit_is_left_completely_alone(escalate):
    """The whole point of the signal is that run_task.py saves its partial
    results. Terminating a process that is already shutting down cleanly would
    throw that away."""
    proc = FakeProc(dies_after=0.05)
    settle(escalate, proc)
    assert not proc.terminated and not proc.killed


def test_a_process_that_ignores_the_signal_is_terminated(escalate):
    proc = FakeProc(dies_after=None)
    settle(escalate, proc)
    assert proc.terminated


def test_a_process_that_ignores_terminate_is_killed(escalate):
    class Stubborn(FakeProc):
        def terminate(self):
            self.terminated = True      # recorded, but it keeps running

        def _alive(self):
            return not self.killed

    proc = Stubborn(dies_after=None)
    settle(escalate, proc)
    assert proc.terminated and proc.killed


def test_it_does_not_block_the_caller(escalate):
    """The bridge thread has to keep serving commands. If this blocked, the UI
    would be unresponsive for exactly as long as the stop took -- which is the
    complaint this fixes, not a new way to cause it."""
    escalate._STOP_GRACE_SEC = 5.0
    escalate._STOP_KILL_SEC = 5.0
    proc = FakeProc(dies_after=None)

    started = time.monotonic()
    escalate._escalate_stop(escalate, proc)
    assert time.monotonic() - started < 0.2


def test_the_thread_is_a_daemon(escalate):
    """A non-daemon thread waiting on a wedged process would stop the bridge
    itself from exiting."""
    escalate._STOP_GRACE_SEC = 5.0
    escalate._STOP_KILL_SEC = 5.0
    before = set(threading.enumerate())
    escalate._escalate_stop(escalate, FakeProc(dies_after=None))
    new = [t for t in threading.enumerate() if t not in before]
    assert new and all(t.daemon for t in new)


def test_the_deadlines_are_short_enough_to_be_worth_having():
    """Bounded, and bounded at a number a person would call responsive."""
    source = BRIDGE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    consts = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("_STOP_GRACE_SEC", "_STOP_KILL_SEC"):
                consts[name] = ast.literal_eval(node.value)

    assert consts["_STOP_GRACE_SEC"] <= 6.0, "longer than an LLM call is not a grace period"
    assert consts["_STOP_KILL_SEC"] <= 3.0
    total = consts["_STOP_GRACE_SEC"] + consts["_STOP_KILL_SEC"]
    assert total <= 8.0, f"worst-case stop of {total}s is what was complained about"


def test_stop_capsule_actually_calls_the_escalation():
    source = BRIDGE.read_text(encoding="utf-8")
    sent = source.index("send_signal(signal.CTRL_BREAK_EVENT)")
    escalated = source.index("self._escalate_stop(self._capsule_proc)")
    assert escalated > sent, "escalate only after the clean signal has been sent"
