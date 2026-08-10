"""
Regression test: run_task.py must import and arm the emergency-stop
hotkey BEFORE importing agent.agent (LLMAgent) -- not just call
start_emergency_stop_listener() textually "early," since a `from X import
Y` statement fully executes X's own module-level code before the next
line of THIS script ever runs, regardless of what that next line does.

Found live 2026-08-10: the previous version had `from agent.agent import
LLMAgent` listed BEFORE `from agent.emergency_stop import
start_emergency_stop_listener`, with a comment claiming the hotkey was
"armed before anything else, even before the countdown." That was true of
the code's position on the page, not its actual execution order --
agent.agent pulls in torch/transformers, and logs/capsule_activity.log
showed a real 23-second silent gap between the process starting and
anything else happening, with the emergency-stop hotkey genuinely not
armed for any of it. Real safety gap, not just a UX one: the one
unconditional escape hatch this project built specifically after a user
was locked out of their own mouse did not exist yet during that entire
window.

This can't be tested by calling run() (the countdown alone takes 5 real
seconds and this is meant to be fast, deterministic, and never touch a
real screen) -- it inspects source order directly, the same technique
already used successfully in test_listitem_type_mismatch.py for a
similar "constant/ordering must not silently regress" concern.
"""
import re
from pathlib import Path

_RUN_TASK_PY = Path(__file__).resolve().parent.parent / "run_task.py"


def test_emergency_stop_import_precedes_the_heavy_agent_import():
    src = _RUN_TASK_PY.read_text(encoding="utf-8")

    emergency_import = re.search(r"from agent\.emergency_stop import", src)
    agent_import = re.search(r"from agent\.agent import LLMAgent", src)

    assert emergency_import, "run_task.py no longer imports agent.emergency_stop"
    assert agent_import, "run_task.py no longer imports LLMAgent"
    assert emergency_import.start() < agent_import.start(), (
        "agent.emergency_stop must be imported (and armed) BEFORE "
        "agent.agent -- LLMAgent's import pulls in torch/transformers, "
        "and Python fully executes an earlier `from X import Y` before "
        "any later line of this script runs, regardless of source "
        "comments claiming otherwise."
    )


def test_start_emergency_stop_listener_is_called_before_the_heavy_agent_import():
    src = _RUN_TASK_PY.read_text(encoding="utf-8")

    listener_call = re.search(r"start_emergency_stop_listener\(\)", src)
    agent_import = re.search(r"from agent\.agent import LLMAgent", src)

    assert listener_call, "run_task.py no longer calls start_emergency_stop_listener()"
    assert agent_import, "run_task.py no longer imports LLMAgent"
    assert listener_call.start() < agent_import.start(), (
        "start_emergency_stop_listener() must be CALLED before the "
        "LLMAgent import line is even reached, not just imported earlier."
    )


def test_the_heavy_agent_import_is_inside_the_results_saving_try_block():
    """A second, separate gap found live the same day, after fixing the
    import ORDER above: interrupting during the LLMAgent import itself
    (not just the countdown) still hard-killed, because the import
    statement sat textually before the try/except/finally that saves
    partial results -- moving emergency_stop earlier didn't move THIS
    import inside the safety net, it just meant the hotkey existed
    slightly sooner while the same gap remained. Fixed by moving the
    import to be the first statement inside the try block. Checked via
    source position (the `try:` line must precede the LLMAgent import
    line) rather than a live timing-dependent reproduction -- how long
    the import actually takes varies run to run with OS file-cache
    warmth, making a live version of this test inherently flaky; the
    structural fact "is this line inside or outside the try block" isn't."""
    src = _RUN_TASK_PY.read_text(encoding="utf-8")

    # Anchored on "agent = None" immediately followed by "results = []"
    # then "try:" -- the unique, distinctive shape of THIS specific try
    # block (the one whose except/finally know how to handle agent still
    # being None) rather than any of the several OTHER unrelated
    # try/except blocks already in this file (e.g. the CoInitialize guard
    # near the top), which a bare `try:` search would match first.
    try_line = re.search(r"agent = None\s*\n\s*results = \[\]\s*\n\s*try:", src)
    agent_import = re.search(r"from agent\.agent import LLMAgent", src)

    assert try_line, "run_task.py's run()-wrapping try: block is missing or changed shape"
    assert agent_import, "run_task.py no longer imports LLMAgent"
    assert try_line.start() < agent_import.start(), (
        "the LLMAgent import must be INSIDE the try block that saves "
        "partial results, not before it -- an interrupt during this "
        "import (which can take 20-30s on a cold start) would otherwise "
        "hard-kill the process instead of exiting cleanly."
    )
