"""
components/agent/capsule.py
===========================
WorkflowCapsule — a named, runnable workflow: either a BC model checkpoint
tied to a specific task type (kind="agent", the original/default shape), or
a standalone script with its own CLI args (kind="script") -- added for the
Electron app's Workflow section to run Scope #2 (components/scope2/) the
same way it runs Scope #1, without forcing a second, structurally different
system to pretend it has a swappable .pt checkpoint.
CapsuleRegistry — loads/saves capsule metadata, routes task → model_path.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional


REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "tasks", "registry.json"
)


@dataclass
class WorkflowCapsule:
    name:             str         # unique id: "form_filling", "excel", "email"
    description:      str         # human-readable purpose
    model_path:       str         # path to .pt checkpoint (kind="agent" only)
    trigger_keywords: List[str]   # match against goal string (lowercase)
    trigger_apps:     List[str]   # match against window_title (case-insensitive)
    trace_dir:        str = ""    # where training traces live
    created:          str = ""    # ISO timestamp
    emoji:            str = ""    # user-set display emoji; "" = show a placeholder
    # kind="agent" (default): run_task.py --model <model_path>, the original
    # Transformer+LLM capsule shape. kind="script": run entrypoint directly
    # with args -- for a workflow that isn't an LLMAgent run at all.
    kind:             str = "agent"
    entrypoint:       str = ""              # relative path to a .py script (kind="script")
    args:             List[str] = field(default_factory=list)
    cwd:              str = ""              # relative to repo root; "" = repo root
    # A kind="script" capsule can ALSO have a real, swappable checkpoint
    # (e.g. Scope #2's matcher.pt) -- checkpoint_flag names the CLI flag
    # the entrypoint uses to accept it (e.g. "--matcher"). Left "" for a
    # script capsule with no such checkpoint. Resolved dynamically from
    # model_path at launch time (not baked into `args`) so that deploying
    # a different checkpoint via the UI actually changes what the next
    # Play run uses.
    checkpoint_flag:  str = ""
    # kind="url": not a subprocess at all -- Play just opens `url` in the
    # user's default browser (app_electron/main.js's capsule-run handler
    # short-circuits before ever reaching launch_command()/Popen for this
    # kind). Added for Scope #3's standalone mockup, which was deliberately
    # built OUTSIDE the Electron app rather than as an embedded page.
    url:              str = ""

    def launch_command(self, repo_root: str) -> tuple[list[str], str]:
        """Return (argv, cwd) to Popen for this capsule.

        Raises FileNotFoundError if the target script/checkpoint doesn't
        exist, so callers can turn that into a clean UI-facing error instead
        of Popen failing deep inside subprocess machinery.
        """
        import sys

        if self.kind == "url":
            # Never actually reached in normal operation -- main.js's
            # capsule-run handler short-circuits to shell.openExternal()
            # before this is ever called. Raising a clear, specific error
            # here (rather than silently falling through to the "agent"
            # branch below and failing confusingly on an empty model_path)
            # is defense-in-depth for a future call site that forgets the
            # kind check.
            raise ValueError(
                f"launch_command() called on a kind=\"url\" capsule ({self.name!r}) -- "
                "this kind has no subprocess to launch; the caller should have opened "
                "self.url directly instead."
            )

        if self.kind == "script":
            entrypoint_abs = os.path.join(repo_root, self.entrypoint)
            if not os.path.isfile(entrypoint_abs):
                raise FileNotFoundError(f"Entry point not found: {entrypoint_abs}")
            argv = [sys.executable, "-u", entrypoint_abs] + list(self.args)
            if self.checkpoint_flag and self.model_path:
                abs_model = self.model_path if os.path.isabs(self.model_path) \
                    else os.path.join(repo_root, self.model_path)
                if not os.path.isfile(abs_model):
                    raise FileNotFoundError(f"Checkpoint not found: {abs_model}")
                argv += [self.checkpoint_flag, abs_model]
            cwd = os.path.join(repo_root, self.cwd) if self.cwd else repo_root
            return argv, cwd

        # kind == "agent" (default) -- same shape run_capsule() has always used.
        abs_model = self.model_path if os.path.isabs(self.model_path) \
            else os.path.join(repo_root, self.model_path)
        if not os.path.isfile(abs_model):
            raise FileNotFoundError(f"Checkpoint not found: {abs_model}")
        run_task_script = os.path.join(repo_root, "run_task.py")
        argv = [sys.executable, "-u", run_task_script, "--model", abs_model]
        return argv, repo_root


class CapsuleRegistry:
    def __init__(self, registry_path: str = REGISTRY_PATH):
        self._path = os.path.abspath(registry_path)
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._capsules: List[WorkflowCapsule] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            self._capsules = []
            return
        with open(self._path) as f:
            data = json.load(f)
        self._capsules = [WorkflowCapsule(**c) for c in data.get("capsules", [])]

    def _save(self) -> None:
        with open(self._path, "w") as f:
            json.dump({"capsules": [asdict(c) for c in self._capsules]}, f, indent=2)

    def register(self, capsule: WorkflowCapsule) -> None:
        self._capsules = [c for c in self._capsules if c.name != capsule.name]
        self._capsules.append(capsule)
        self._save()

    def route(self, goal: str, window_title: str,
              fallback: str = "tasks/form_filling/model.pt") -> str:
        """Return model_path for best matching capsule, or fallback.

        Only ever considers kind="agent" capsules -- a script-kind capsule's
        model_path (if any, e.g. Scope #2's matcher.pt) is a checkpoint for
        a completely different model shape, never a BC transformer
        checkpoint LLMAgent could load. Enforced here structurally, not
        just by convention (empty trigger_keywords/trigger_apps), so a
        future script capsule that happens to declare real triggers still
        can't be routed into LLMAgent by mistake.
        """
        goal_lower  = goal.lower()
        title_lower = window_title.lower()
        for capsule in self._capsules:
            if capsule.kind != "agent":
                continue
            if any(k in goal_lower  for k in capsule.trigger_keywords):
                return capsule.model_path
            if any(a.lower() in title_lower for a in capsule.trigger_apps):
                return capsule.model_path
        return fallback

    def list_capsules(self) -> List[WorkflowCapsule]:
        return list(self._capsules)
