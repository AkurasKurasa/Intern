"""
Regression tests for WorkflowCapsule.launch_command() (components/agent/
capsule.py) -- the one place that turns a registered capsule into an
actual (argv, cwd) to Popen, shared by both capsule kinds:

  - kind="agent" (default): the original Transformer+LLM shape --
    `python -u run_task.py --model <checkpoint>`.
  - kind="script": a standalone entry point with its own args -- added
    for the Electron Workflow section to run Scope #2
    (components/scope2/automate.py) the same way it runs Scope #1,
    without pretending Scope #2 has a swappable .pt checkpoint.

Also guards CapsuleRegistry.route() -- used to pick a checkpoint for
LLMAgent -- never accidentally returning a script-kind capsule's (empty)
model_path for any goal/window_title. That's structural (empty
trigger_keywords/trigger_apps can never match), not a special case, but
it's cheap to prove directly rather than just assumed from the code shape.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from components.agent.capsule import WorkflowCapsule, CapsuleRegistry


def _agent_capsule(model_path):
    return WorkflowCapsule(
        name="form_filling", description="x", model_path=str(model_path),
        trigger_keywords=["form", "insurance"], trigger_apps=["Car Insurance"],
    )


def _script_capsule(entrypoint, args=None, cwd=""):
    return WorkflowCapsule(
        name="sheet_matcher", description="x", model_path="",
        trigger_keywords=[], trigger_apps=[],
        kind="script", entrypoint=entrypoint, args=args or [], cwd=cwd,
    )


class TestLaunchCommandAgentKind:
    def test_returns_run_task_argv_and_repo_root_cwd(self, tmp_path):
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        capsule = _agent_capsule(checkpoint)

        argv, cwd = capsule.launch_command(str(tmp_path))

        assert argv[0] == sys.executable
        assert argv[1] == "-u"
        assert argv[2] == str(tmp_path / "run_task.py")
        assert argv[3] == "--model"
        assert argv[4] == str(checkpoint)
        assert cwd == str(tmp_path)

    def test_raises_file_not_found_for_missing_checkpoint(self, tmp_path):
        capsule = _agent_capsule(tmp_path / "nope.pt")

        try:
            capsule.launch_command(str(tmp_path))
            assert False, "expected FileNotFoundError"
        except FileNotFoundError as exc:
            assert "not found" in str(exc).lower()

    def test_relative_model_path_resolved_against_repo_root(self, tmp_path):
        (tmp_path / "tasks" / "form_filling").mkdir(parents=True)
        checkpoint = tmp_path / "tasks" / "form_filling" / "model.pt"
        checkpoint.write_bytes(b"fake")
        capsule = WorkflowCapsule(
            name="form_filling", description="x",
            model_path="tasks/form_filling/model.pt",
            trigger_keywords=[], trigger_apps=[],
        )

        argv, cwd = capsule.launch_command(str(tmp_path))

        # os.path.join doesn't normalize interior "/" in a relative
        # model_path on Windows -- normpath both sides before comparing.
        assert Path(argv[4]).resolve() == checkpoint.resolve()


class TestLaunchCommandScriptKind:
    def test_returns_entrypoint_argv_with_args(self, tmp_path):
        script = tmp_path / "automate.py"
        script.write_text("# fake")
        capsule = _script_capsule("automate.py", args=["--variant", "v0_base", "--commit"])

        argv, cwd = capsule.launch_command(str(tmp_path))

        assert argv == [sys.executable, "-u", str(script), "--variant", "v0_base", "--commit"]
        assert cwd == str(tmp_path)

    def test_cwd_override_relative_to_repo_root(self, tmp_path):
        (tmp_path / "components" / "scope2").mkdir(parents=True)
        script = tmp_path / "components" / "scope2" / "automate.py"
        script.write_text("# fake")
        capsule = _script_capsule("components/scope2/automate.py", cwd="components/scope2")

        argv, cwd = capsule.launch_command(str(tmp_path))

        assert Path(cwd).resolve() == (tmp_path / "components" / "scope2").resolve()

    def test_raises_file_not_found_for_missing_entrypoint(self, tmp_path):
        capsule = _script_capsule("nonexistent_script.py")

        try:
            capsule.launch_command(str(tmp_path))
            assert False, "expected FileNotFoundError"
        except FileNotFoundError as exc:
            assert "not found" in str(exc).lower()


class TestRouteNeverReturnsAScriptCapsule:
    """route() is only ever meant to pick a .pt checkpoint for LLMAgent --
    a script-kind capsule has no such checkpoint, so it must never be
    returned regardless of what goal/window_title is routed."""

    def test_empty_trigger_lists_exclude_script_capsule_from_any_goal(self, tmp_path):
        registry = CapsuleRegistry(registry_path=str(tmp_path / "registry.json"))
        registry.register(_script_capsule("components/scope2/automate.py"))
        registry.register(WorkflowCapsule(
            name="form_filling", description="x", model_path="tasks/form_filling/model.pt",
            trigger_keywords=["form", "insurance"], trigger_apps=["Car Insurance"],
        ))

        for goal in ("fill the insurance form", "match spreadsheet to portal",
                     "sheet matcher", "totally unrelated goal", ""):
            result = registry.route(goal, "some window", fallback="fallback.pt")
            assert result != ""  # never the script capsule's empty model_path

    def test_route_still_returns_the_agent_capsule_normally(self, tmp_path):
        registry = CapsuleRegistry(registry_path=str(tmp_path / "registry.json"))
        registry.register(_script_capsule("components/scope2/automate.py"))
        registry.register(WorkflowCapsule(
            name="form_filling", description="x", model_path="tasks/form_filling/model.pt",
            trigger_keywords=["form", "insurance"], trigger_apps=["Car Insurance"],
        ))

        result = registry.route("fill the insurance form", "some window", fallback="fallback.pt")

        assert result == "tasks/form_filling/model.pt"
