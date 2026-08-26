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
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from components.agent.capsule import WorkflowCapsule, CapsuleRegistry


def _agent_capsule(model_path):
    return WorkflowCapsule(
        name="form_filling", description="x", model_path=str(model_path),
        trigger_keywords=["form", "insurance"], trigger_apps=["Car Insurance"],
    )


def _script_capsule(entrypoint, args=None, cwd="", model_path="", checkpoint_flag=""):
    return WorkflowCapsule(
        name="sheet_matcher", description="x", model_path=model_path,
        trigger_keywords=[], trigger_apps=[],
        kind="script", entrypoint=entrypoint, args=args or [], cwd=cwd,
        checkpoint_flag=checkpoint_flag,
    )


def _url_capsule(url):
    return WorkflowCapsule(
        name="inbox_dispatch", description="x", model_path="",
        trigger_keywords=[], trigger_apps=[],
        kind="url", url=url,
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


class TestLaunchCommandScriptKindWithCheckpoint:
    """A script-kind capsule can ALSO have a real, swappable checkpoint
    (e.g. Scope #2's matcher.pt, loaded via automate.py --matcher) --
    checkpoint_flag names the CLI flag, resolved dynamically from
    model_path at launch time so a Deploy in the UI actually changes what
    the next Play run uses, not just cosmetic."""

    def test_checkpoint_flag_appends_flag_and_resolved_path(self, tmp_path):
        script = tmp_path / "automate.py"
        script.write_text("# fake")
        checkpoint = tmp_path / "matcher.pt"
        checkpoint.write_bytes(b"fake")
        capsule = _script_capsule(
            "automate.py", args=["--variant", "v0_base"],
            model_path=str(checkpoint), checkpoint_flag="--matcher",
        )

        argv, cwd = capsule.launch_command(str(tmp_path))

        assert argv == [sys.executable, "-u", str(script),
                         "--variant", "v0_base", "--matcher", str(checkpoint)]

    def test_relative_model_path_resolved_against_repo_root(self, tmp_path):
        (tmp_path / "components" / "scope2" / "data" / "models").mkdir(parents=True)
        script = tmp_path / "components" / "scope2" / "automate.py"
        script.write_text("# fake")
        checkpoint = tmp_path / "components" / "scope2" / "data" / "models" / "matcher.pt"
        checkpoint.write_bytes(b"fake")
        capsule = _script_capsule(
            "components/scope2/automate.py",
            model_path="components/scope2/data/models/matcher.pt",
            checkpoint_flag="--matcher",
        )

        argv, cwd = capsule.launch_command(str(tmp_path))

        assert Path(argv[-1]).resolve() == checkpoint.resolve()

    def test_raises_file_not_found_for_missing_checkpoint(self, tmp_path):
        script = tmp_path / "automate.py"
        script.write_text("# fake")
        capsule = _script_capsule(
            "automate.py", model_path=str(tmp_path / "nope.pt"), checkpoint_flag="--matcher",
        )

        try:
            capsule.launch_command(str(tmp_path))
            assert False, "expected FileNotFoundError"
        except FileNotFoundError as exc:
            assert "not found" in str(exc).lower()

    def test_model_path_ignored_without_checkpoint_flag(self, tmp_path):
        """Backward compatibility: a script-kind capsule that only sets
        model_path (no checkpoint_flag) must behave exactly as before --
        model_path is simply not used, not an error."""
        script = tmp_path / "automate.py"
        script.write_text("# fake")
        capsule = _script_capsule(
            "automate.py", args=["--variant", "v0_base"],
            model_path=str(tmp_path / "nope.pt"), checkpoint_flag="",
        )

        argv, cwd = capsule.launch_command(str(tmp_path))

        assert argv == [sys.executable, "-u", str(script), "--variant", "v0_base"]


class TestLaunchCommandUrlKind:
    """kind="url" (Scope #3's mockup, deliberately built outside the
    Electron app) has no subprocess at all -- main.js's capsule-run
    handler opens self.url via shell.openExternal() and never calls
    launch_command() for this kind. launch_command() itself raises a
    clear, specific error if it's ever reached anyway, rather than
    silently falling through to the agent-kind branch's confusing
    "checkpoint not found" error on an empty model_path."""

    def test_raises_value_error_naming_the_capsule(self, tmp_path):
        capsule = _url_capsule("https://example.com/mockup")

        try:
            capsule.launch_command(str(tmp_path))
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "inbox_dispatch" in str(exc)
            assert "url" in str(exc).lower()

    def test_does_not_raise_file_not_found(self, tmp_path):
        """Specifically NOT FileNotFoundError -- a url-kind capsule has no
        file to be missing; ValueError signals a caller-side logic error
        instead of a misleadingly file-shaped one."""
        capsule = _url_capsule("https://example.com/mockup")

        try:
            capsule.launch_command(str(tmp_path))
        except FileNotFoundError:
            assert False, "should raise ValueError, not FileNotFoundError"
        except ValueError:
            pass


class TestRouteNeverReturnsAUrlCapsule:
    """Same guard as TestRouteNeverReturnsAScriptCapsule, for kind="url" --
    route() only ever picks a .pt checkpoint for LLMAgent, and a url-kind
    capsule has no checkpoint at all."""

    def test_empty_trigger_lists_exclude_url_capsule_from_any_goal(self, tmp_path):
        registry = CapsuleRegistry(registry_path=str(tmp_path / "registry.json"))
        registry.register(_url_capsule("https://example.com/mockup"))
        registry.register(WorkflowCapsule(
            name="form_filling", description="x", model_path="tasks/form_filling/model.pt",
            trigger_keywords=["form", "insurance"], trigger_apps=["Car Insurance"],
        ))

        for goal in ("fill the insurance form", "open the inbox dispatch",
                     "inbox_dispatch", "totally unrelated goal", ""):
            result = registry.route(goal, "some window", fallback="fallback.pt")
            assert result != ""  # never the url capsule's empty model_path

    def test_kind_is_checked_structurally_even_with_real_triggers_set(self, tmp_path):
        registry = CapsuleRegistry(registry_path=str(tmp_path / "registry.json"))
        registry.register(WorkflowCapsule(
            name="inbox_dispatch", description="x", model_path="",
            trigger_keywords=["inbox", "email"], trigger_apps=["Gmail"],
            kind="url", url="https://example.com/mockup",
        ))

        result = registry.route("check my inbox", "Gmail", fallback="fallback.pt")

        assert result == "fallback.pt"


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

    def test_kind_is_checked_structurally_even_with_real_triggers_set(self, tmp_path):
        """route() must skip a script-kind capsule by kind, not merely by
        convention (empty trigger lists) -- proven directly by giving a
        script-kind capsule real triggers and a real model_path (Scope #2's
        matcher.pt is exactly this shape now) and confirming route() still
        never returns it."""
        registry = CapsuleRegistry(registry_path=str(tmp_path / "registry.json"))
        registry.register(WorkflowCapsule(
            name="sheet_matcher", description="x", model_path="components/scope2/data/models/matcher.pt",
            trigger_keywords=["sheet", "matcher"], trigger_apps=["Grade Portal"],
            kind="script", entrypoint="components/scope2/automate.py",
            checkpoint_flag="--matcher",
        ))

        result = registry.route("run the sheet matcher", "Grade Portal", fallback="fallback.pt")

        assert result == "fallback.pt"


class TestLocalServerField:
    def test_defaults_to_empty_string(self):
        capsule = WorkflowCapsule(
            name="x", description="", model_path="", trigger_keywords=[], trigger_apps=[],
        )
        assert capsule.local_server == ""

    def test_round_trips_through_registry_load(self, tmp_path):
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps({"capsules": [{
            "name": "Inbox Dispatch", "description": "", "model_path": "",
            "trigger_keywords": [], "trigger_apps": [], "kind": "url",
            "url": "http://localhost:8765/",
            "local_server": "components/inbox_router/local_server.py",
        }]}), encoding="utf-8")
        registry = CapsuleRegistry(registry_path=str(registry_path))
        capsule = registry.list_capsules()[0]
        assert capsule.local_server == "components/inbox_router/local_server.py"
