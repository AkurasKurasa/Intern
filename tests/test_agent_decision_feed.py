"""The DECISION line that drives Scope #1's floating decision HUD.

Scope #2 renders its reasoning into the page it automates. Scope #1 has no
page -- it drives a native wxPython window on the real screen -- so the
equivalent is a separate always-on-top window fed by one machine-readable line
per step, on the same stdout the Play panel already reads.

The assertions that matter are not that it looks right, they are that it can
never affect a run:

  * a broken emit is swallowed, never raised
  * the line is parseable by main.js exactly as written
  * the flush guard survives a spawn with no console (the Electron Play
    button uses windowsHide, where an explicit flush can raise OSError even
    though the write already landed)

Run:  python -m pytest tests/test_agent_decision_feed.py -q
"""

import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
AGENT_PY = REPO / "components" / "agent" / "agent.py"


@pytest.fixture(scope="module")
def emit():
    """_emit_decision, lifted out of agent.py without importing it.

    agent.py pulls in torch and the whole observer stack; this test is about
    eight lines of print formatting, so it compiles just that function rather
    than paying for the import.
    """
    import ast

    tree = ast.parse(AGENT_PY.read_text(encoding="utf-8"))
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_emit_decision"), None)
    assert fn is not None, "_emit_decision has gone from agent.py"

    namespace = {"json": json, "sys": sys}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(AGENT_PY), "exec"), namespace)
    return namespace["_emit_decision"]


def capture(emit, *args):
    """Run an emit and return the raw line it printed."""
    buffer = io.StringIO()
    original = sys.stdout
    sys.stdout = buffer
    try:
        emit(*args)
    finally:
        sys.stdout = original
    return buffer.getvalue().strip()


# ------------------------------------------------------------- the line


def test_emits_one_parseable_decision_line(emit):
    line = capture(emit, 14, "transformer", 0.9231, "click")

    assert line.startswith("DECISION ")
    # main.js does exactly this: slice(9), then JSON.parse.
    payload = json.loads(line[9:])
    assert payload == {"step": 14, "by": "transformer", "conf": 0.9231, "action": "click"}


def test_confidence_is_rounded_not_raw(emit):
    """A float32 score prints as 0.9230999946594238 without this, which is
    noise in a panel showing two decimal places."""
    payload = json.loads(capture(emit, 1, "transformer", 0.92309999465942383, "click")[9:])
    assert payload["conf"] == 0.9231


def test_the_llm_path_emits_too(emit):
    payload = json.loads(capture(emit, 7, "llm", 0.0, "keyboard")[9:])
    assert payload["by"] == "llm"
    assert payload["conf"] == 0.0


def test_it_is_a_single_line(emit):
    """main.js matches on a whole line. A payload that wrapped would be
    parsed as several broken lines."""
    line = capture(emit, 3, "transformer", 0.5, "click")
    assert "\n" not in line


# --------------------------------------------- it must never break a run


@pytest.mark.parametrize("step,by,conf,action", [
    (None, None, None, None),
    (5, "transformer", None, "click"),
    (5, None, 0.5, None),
    ("not-a-number", "transformer", "not-a-float", "click"),
])
def test_bad_input_is_swallowed_not_raised(emit, step, by, conf, action):
    """Presentation must never take down a run whose actions all succeeded."""
    capture(emit, step, by, conf, action)


def test_missing_values_still_produce_valid_json(emit):
    payload = json.loads(capture(emit, 5, None, None, None)[9:])
    assert payload["by"] == "unknown"
    assert payload["conf"] == 0.0
    assert payload["action"] == ""


def test_an_oserror_on_flush_is_survived(emit, monkeypatch):
    """Found and fixed twice already in this project: spawned with no console
    (windowsHide), an explicit flush can raise OSError on Windows even though
    the write itself succeeded. run_task.py's print_countdown() and
    automate.py both carry the same guard."""
    class Exploding(io.StringIO):
        def flush(self):
            raise OSError(22, "Invalid argument")

    buffer = Exploding()
    monkeypatch.setattr(sys, "stdout", buffer)
    emit(9, "transformer", 0.8, "click")       # must not raise
    assert "DECISION " in buffer.getvalue()


# ------------------------------------------------- wired into the agent


def test_the_agent_actually_calls_it_after_recording_a_step():
    """The emit has to sit where decision_by and t_conf are both known -- the
    same place the run's own result record is appended."""
    source = AGENT_PY.read_text(encoding="utf-8")
    assert "_emit_decision(step_idx + 1, _decision_maker, t_conf," in source
    appended = source.index('"step_time_sec":     round(_step_time_sec, 4),')
    called = source.index("_emit_decision(step_idx + 1")
    assert called > appended, "the emit must follow the result record, not precede it"
