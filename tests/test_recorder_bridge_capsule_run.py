"""
Regression tests for app/recorder_bridge.py's "run_capsule"/"stop_capsule"
commands -- this is what "Play" actually means now: running the real,
live process for a registered capsule, not a replay of one recorded
session's raw actions (that earlier version was explicitly reverted --
direct user correction: "I don't want to play per session... capsule
meaning model").

Two capsule kinds are covered:
  - kind="agent" (the original shape): spawns
    `python run_task.py --model <checkpoint>`, the real trained agent
    (transformer decides WHERE, LLM decides WHAT), same as run_task.py
    run from a terminal -- one real implementation, not two that can
    quietly drift apart.
  - kind="script": spawns the capsule's own `entrypoint` with its own
    `args` directly -- added so a structurally different workflow (Scope
    #2's components/scope2/automate.py, no .pt checkpoint at all) can be
    launched from the same Play button, without pretending to have a
    swappable checkpoint it doesn't have.

run_capsule() now takes a capsule NAME (looked up in the bridge's
CapsuleRegistry), not a bare model_path -- WorkflowCapsule.launch_command()
is the one place that turns a capsule into an actual argv/cwd, so both
kinds share the exact same Popen call below.

Only the safe, non-executing parts are covered here -- subprocess.Popen is
monkeypatched out entirely so no test ever actually launches a real
process or touches the real screen. Actually running a live capsule is a
real GUI-automation action and is the user's call to make from the app,
the same as every other live run in this project.
"""
import signal
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import recorder_bridge as rb
from agent.capsule import CapsuleRegistry, WorkflowCapsule


def _events(monkeypatch):
    captured = []
    monkeypatch.setattr(rb, "emit", lambda event, **fields: captured.append({"event": event, **fields}))
    return captured


def _registry(tmp_path, *capsules):
    registry = CapsuleRegistry(registry_path=str(tmp_path / "registry.json"))
    for c in capsules:
        registry.register(c)
    return registry


def _agent_capsule(name, model_path):
    return WorkflowCapsule(
        name=name, description="x", model_path=str(model_path),
        trigger_keywords=[], trigger_apps=[],
    )


def _script_capsule(name, entrypoint, args=None, cwd="", model_path="", checkpoint_flag=""):
    return WorkflowCapsule(
        name=name, description="Sheet matcher", model_path=model_path,
        trigger_keywords=[], trigger_apps=[],
        kind="script", entrypoint=entrypoint, args=args or [], cwd=cwd,
        checkpoint_flag=checkpoint_flag,
    )


class _FakeProc:
    """Stands in for subprocess.Popen -- no real process ever spawned."""
    def __init__(self, lines=None, exit_code=0):
        self.stdout = iter((lines or []))
        self._exit_code = exit_code
        self._polled_running = True
        self.sent_signals = []

    def poll(self):
        return None if self._polled_running else self._exit_code

    def wait(self):
        self._polled_running = False
        return self._exit_code

    def send_signal(self, sig):
        self.sent_signals.append(sig)


class TestRunCapsuleGuards:
    def test_refuses_while_recording(self, monkeypatch):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        bridge._running = True

        bridge.run_capsule("form_filling")

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "recording" in errors[0]["message"].lower()

    def test_refuses_when_a_capsule_is_already_running(self, monkeypatch):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        bridge._capsule_proc = _FakeProc()  # poll() -> None -> still running

        bridge.run_capsule("form_filling")

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "already running" in errors[0]["message"].lower()

    def test_refuses_unknown_capsule_name(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        registry = _registry(tmp_path)  # empty
        bridge = rb.Bridge(registry=registry)

        bridge.run_capsule("nonexistent_capsule")

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "not found" in errors[0]["message"].lower()

    def test_refuses_missing_checkpoint(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        registry = _registry(tmp_path, _agent_capsule("form_filling", tmp_path / "nope.pt"))
        bridge = rb.Bridge(registry=registry)

        bridge.run_capsule("form_filling")

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "not found" in errors[0]["message"].lower()


class TestRunCapsuleSpawnsRunTaskCorrectly:
    def test_spawns_run_task_with_model_flag(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        registry = _registry(tmp_path, _agent_capsule("form_filling", checkpoint))
        bridge = rb.Bridge(registry=registry)

        popen_calls = []

        def _fake_popen(args, **kwargs):
            popen_calls.append((args, kwargs))
            return _FakeProc(lines=[])

        monkeypatch.setattr(rb.subprocess, "Popen", _fake_popen)

        bridge.run_capsule("form_filling")

        assert len(popen_calls) == 1
        args, kwargs = popen_calls[0]
        assert args[0] == sys.executable
        # "-u" -- unbuffered stdout, same fix already used elsewhere in this
        # project for GUI-launched subprocesses. Without it, a non-tty
        # stdout is block-buffered by default and _pump()'s line-by-line
        # read can sit dead for a while with real output already produced
        # but not yet flushed to the pipe -- found live as part of the same
        # "Play does nothing" report the countdown fix above addresses.
        assert args[1] == "-u"
        assert args[2] == rb.os.path.join(rb._ROOT, "run_task.py")
        assert args[3] == "--model"
        assert args[4] == str(checkpoint)
        assert kwargs["cwd"] == rb._ROOT

    def test_stdin_is_explicitly_devnull_not_inherited(self, monkeypatch, tmp_path):
        """Found live 2026-08-10: this Popen call never set stdin at all, so
        run_task.py inherited the bridge's OWN stdin handle -- a pipe
        Electron's main.js writes JSON commands into. Two run_task.py
        processes launched this way through the real Electron bridge sat
        Responding=True with near-zero CPU for minutes and never created a
        log file (meaning they hung before logging.basicConfig(), the very
        first real statement in run_task.py). Confirmed fixed live, twice,
        through the real bridge after setting stdin=DEVNULL: the log file
        now appears within ~1s and the run proceeds normally. Not inheriting
        an unused pipe handle two processes up is also just correct
        subprocess hygiene independent of this specific bug."""
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        registry = _registry(tmp_path, _agent_capsule("form_filling", checkpoint))
        bridge = rb.Bridge(registry=registry)
        popen_calls = []
        monkeypatch.setattr(
            rb.subprocess, "Popen",
            lambda *a, **k: (popen_calls.append((a, k)), _FakeProc(lines=[]))[1],
        )
        monkeypatch.setattr(rb, "emit", lambda *a, **k: None)

        bridge.run_capsule("form_filling")

        _, kwargs = popen_calls[0]
        assert kwargs["stdin"] == rb.subprocess.DEVNULL

    def test_creationflags_is_new_process_group_not_new_console(self, monkeypatch, tmp_path):
        """CREATE_NEW_CONSOLE was tried as a fix for the same hang (giving
        the child a real console instead of inheriting the console-less
        windowsHide ancestry) and, in one live trial, the hang was gone --
        but it also pops a real, visible console window on screen for every
        Play click, which the user explicitly does not want for a GUI app.
        Reverted to CREATE_NEW_PROCESS_GROUP (invisible, still supports
        CTRL_BREAK_EVENT for Stop -- see TestStopCapsule) after confirming
        live, twice, that stdin=DEVNULL alone -- without CREATE_NEW_CONSOLE
        -- is sufficient to keep the hang fixed."""
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        registry = _registry(tmp_path, _agent_capsule("form_filling", checkpoint))
        bridge = rb.Bridge(registry=registry)
        popen_calls = []
        monkeypatch.setattr(
            rb.subprocess, "Popen",
            lambda *a, **k: (popen_calls.append((a, k)), _FakeProc(lines=[]))[1],
        )
        monkeypatch.setattr(rb, "emit", lambda *a, **k: None)

        bridge.run_capsule("form_filling")

        _, kwargs = popen_calls[0]
        assert kwargs["creationflags"] == rb.subprocess.CREATE_NEW_PROCESS_GROUP

    def test_capsule_started_emitted_synchronously(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        registry = _registry(tmp_path, _agent_capsule("form_filling", checkpoint))
        bridge = rb.Bridge(registry=registry)
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: _FakeProc(lines=[]))

        bridge.run_capsule("form_filling")

        started = [e for e in events if e["event"] == "capsule_started"]
        assert len(started) == 1
        assert "form_filling" in started[0]["label"]
        assert checkpoint.name in started[0]["label"]

    def test_stdout_lines_stream_as_capsule_progress(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        registry = _registry(tmp_path, _agent_capsule("form_filling", checkpoint))
        bridge = rb.Bridge(registry=registry)
        fake_proc = _FakeProc(lines=["step 1: click\n", "step 2: keyboard\n", ""])
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: fake_proc)

        bridge.run_capsule("form_filling")

        for _ in range(50):
            progress = [e for e in events if e["event"] == "capsule_progress"]
            if len(progress) >= 2:
                break
            time.sleep(0.05)

        progress = [e for e in events if e["event"] == "capsule_progress"]
        assert [p["line"] for p in progress] == ["step 1: click", "step 2: keyboard"]

    def test_capsule_done_emitted_with_exit_code(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        registry = _registry(tmp_path, _agent_capsule("form_filling", checkpoint))
        bridge = rb.Bridge(registry=registry)
        fake_proc = _FakeProc(lines=[], exit_code=0)
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: fake_proc)

        bridge.run_capsule("form_filling")

        done = []
        for _ in range(50):
            done = [e for e in events if e["event"] == "capsule_done"]
            if done:
                break
            time.sleep(0.05)

        assert len(done) == 1
        assert done[0]["code"] == 0

    def test_bridge_capsule_proc_cleared_after_done(self, monkeypatch, tmp_path):
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        registry = _registry(tmp_path, _agent_capsule("form_filling", checkpoint))
        bridge = rb.Bridge(registry=registry)
        fake_proc = _FakeProc(lines=[])
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: fake_proc)
        monkeypatch.setattr(rb, "emit", lambda *a, **k: None)

        bridge.run_capsule("form_filling")

        for _ in range(50):
            if bridge._capsule_proc is None:
                break
            time.sleep(0.05)

        assert bridge._capsule_proc is None


class TestRunScriptCapsule:
    """kind="script" -- a capsule that isn't a Transformer+LLM agent run at
    all (e.g. Scope #2's "Sheet-to-Portal Matcher" launching
    components/scope2/automate.py). Uses the real repo's own
    components/scope2/automate.py as the entrypoint, since
    launch_command() genuinely checks the file exists on disk -- same
    trust in rb._ROOT being the real project root that the agent-kind
    tests above already rely on for run_task.py."""

    ENTRYPOINT = "components/scope2/automate.py"

    def test_spawns_entrypoint_with_args(self, monkeypatch, tmp_path):
        args = ["--variant", "v0_base", "--commit", "--show", "--limit", "5"]
        registry = _registry(tmp_path, _script_capsule("sheet_matcher", self.ENTRYPOINT, args=args))
        bridge = rb.Bridge(registry=registry)
        popen_calls = []
        monkeypatch.setattr(rb.subprocess, "Popen", lambda a, **k: (popen_calls.append((a, k)), _FakeProc(lines=[]))[1])
        monkeypatch.setattr(rb, "emit", lambda *a, **k: None)

        bridge.run_capsule("sheet_matcher")

        assert len(popen_calls) == 1
        argv, kwargs = popen_calls[0]
        assert argv[0] == sys.executable
        assert argv[1] == "-u"
        assert argv[2] == rb.os.path.join(rb._ROOT, self.ENTRYPOINT)
        assert argv[3:] == args
        assert kwargs["cwd"] == rb._ROOT

    def test_cwd_override_is_relative_to_repo_root(self, monkeypatch, tmp_path):
        registry = _registry(tmp_path, _script_capsule("sheet_matcher", self.ENTRYPOINT, cwd="components/scope2"))
        bridge = rb.Bridge(registry=registry)
        popen_calls = []
        monkeypatch.setattr(rb.subprocess, "Popen", lambda a, **k: (popen_calls.append((a, k)), _FakeProc(lines=[]))[1])
        monkeypatch.setattr(rb, "emit", lambda *a, **k: None)

        bridge.run_capsule("sheet_matcher")

        _, kwargs = popen_calls[0]
        assert kwargs["cwd"] == rb.os.path.join(rb._ROOT, "components/scope2")

    def test_refuses_missing_entrypoint(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        registry = _registry(tmp_path, _script_capsule("sheet_matcher", "components/scope2/nonexistent_script.py"))
        bridge = rb.Bridge(registry=registry)

        bridge.run_capsule("sheet_matcher")

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "not found" in errors[0]["message"].lower()

    def test_capsule_started_label_uses_entrypoint_basename(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        registry = _registry(tmp_path, _script_capsule("sheet_matcher", self.ENTRYPOINT))
        bridge = rb.Bridge(registry=registry)
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: _FakeProc(lines=[]))

        bridge.run_capsule("sheet_matcher")

        started = [e for e in events if e["event"] == "capsule_started"]
        assert len(started) == 1
        assert "sheet_matcher" in started[0]["label"]
        assert "automate.py" in started[0]["label"]

    def test_checkpoint_flag_flows_through_to_the_real_popen_argv(self, monkeypatch, tmp_path):
        """End-to-end proof (not just launch_command() in isolation) that a
        script capsule's checkpoint_flag + model_path -- Scope #2's actual
        registry shape -- reaches the real Popen call via the bridge. Uses
        the real, already-trained components/scope2/data/models/matcher.pt
        (same trust in rb._ROOT as every other test in this class)."""
        checkpoint_rel = "components/scope2/data/models/matcher.pt"
        registry = _registry(tmp_path, _script_capsule(
            "sheet_matcher", self.ENTRYPOINT, args=["--variant", "v0_base"],
            model_path=checkpoint_rel, checkpoint_flag="--matcher",
        ))
        bridge = rb.Bridge(registry=registry)
        popen_calls = []
        monkeypatch.setattr(rb.subprocess, "Popen", lambda a, **k: (popen_calls.append((a, k)), _FakeProc(lines=[]))[1])
        monkeypatch.setattr(rb, "emit", lambda *a, **k: None)

        bridge.run_capsule("sheet_matcher")

        assert len(popen_calls) == 1
        argv, kwargs = popen_calls[0]
        assert argv[-2] == "--matcher"
        assert argv[-1] == rb.os.path.join(rb._ROOT, checkpoint_rel)


class TestStopCapsule:
    def test_refuses_when_nothing_running(self, monkeypatch):
        events = _events(monkeypatch)
        bridge = rb.Bridge()

        bridge.stop_capsule()

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "no capsule" in errors[0]["message"].lower()

    def test_sends_ctrl_break_not_a_hard_terminate(self, monkeypatch):
        """run_task.py's own run() catches KeyboardInterrupt to save partial
        results and write metrics cleanly -- a hard terminate() would skip
        all of that, so stop must use CTRL_BREAK_EVENT, not .terminate()."""
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        fake_proc = _FakeProc()
        bridge._capsule_proc = fake_proc

        bridge.stop_capsule()

        assert fake_proc.sent_signals == [signal.CTRL_BREAK_EVENT]
        stopped = [e for e in events if e["event"] == "capsule_stopped"]
        assert len(stopped) == 1
