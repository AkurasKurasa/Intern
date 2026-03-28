"""
recorder/task_manager/task_manager.py
============================================
Central registry for named automation tasks.

A Task bundles everything Intern needs to learn and replay a job:
  - a human-readable name and goal description
  - the trace directory where recordings are stored
  - the model checkpoint path
  - run history (count, last run timestamp)

TaskManager persists tasks as a JSON registry on disk so they survive
across sessions.  It is the single entry-point for recording, training,
and executing any named task.

Usage
-----
    manager = TaskManager()

    # Register a new task
    task = manager.register("fill_insurance", "Fill the car insurance form")

    # Point the recorder at the task's trace directory, record, then train
    manager.train("fill_insurance", epochs=80)

    # Run the task (provider="none" uses transformer only; "lmstudio" adds LLM)
    manager.run("fill_insurance", provider="lmstudio", max_steps=40)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── path setup ─────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))   # task_manager/
_SO   = os.path.dirname(_HERE)                        # recorder/
_COMP = os.path.dirname(_SO)                          # components/
_ROOT = os.path.dirname(_COMP)                        # Intern/
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DEFAULT_REGISTRY   = os.path.join(_ROOT, "data", "tasks", "registry.json")
_DEFAULT_TRACE_BASE = os.path.join(_ROOT, "data", "output", "traces")
_DEFAULT_MODEL_BASE = os.path.join(_ROOT, "data", "models")


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class Task:
    name:        str
    description: str            = ""
    trace_dir:   str            = ""
    model_path:  str            = ""
    created_at:  str            = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_run:    Optional[str]  = None
    run_count:   int            = 0
    metadata:    dict           = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        known = {k for k in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def __repr__(self) -> str:
        return (
            f"Task(name={self.name!r}, runs={self.run_count}, "
            f"last_run={self.last_run!r})"
        )


# ── Manager ────────────────────────────────────────────────────────────────────

class TaskManager:
    """
    Registers, trains, and executes named automation tasks.

    Parameters
    ----------
    registry_path : JSON file where task metadata is persisted.
    """

    def __init__(self, registry_path: str = _DEFAULT_REGISTRY):
        self.registry_path = registry_path
        self._tasks: Dict[str, Task] = {}
        self._load()

    # ── CRUD ───────────────────────────────────────────────────────────────────

    def register(
        self,
        name:        str,
        description: str            = "",
        trace_dir:   Optional[str]  = None,
        model_path:  Optional[str]  = None,
    ) -> Task:
        """
        Register a new task (or return the existing one).

        Automatically creates the trace directory and models directory
        if they don't exist.
        """
        if name in self._tasks:
            logger.info("TaskManager: task '%s' already registered", name)
            return self._tasks[name]

        td = trace_dir  or os.path.join(_DEFAULT_TRACE_BASE, name)
        mp = model_path or os.path.join(_DEFAULT_MODEL_BASE, f"{name}.pt")

        os.makedirs(td, exist_ok=True)
        os.makedirs(_DEFAULT_MODEL_BASE, exist_ok=True)

        task = Task(name=name, description=description,
                    trace_dir=td, model_path=mp)
        self._tasks[name] = task
        self._save()
        logger.info("TaskManager: registered task '%s'  trace_dir=%s", name, td)
        return task

    def get(self, name: str) -> Task:
        if name not in self._tasks:
            raise KeyError(
                f"Task '{name}' not found.  "
                f"Available: {list(self._tasks)}"
            )
        return self._tasks[name]

    def list(self) -> List[Task]:
        return list(self._tasks.values())

    def delete(self, name: str):
        self._tasks.pop(name, None)
        self._save()
        logger.info("TaskManager: deleted task '%s'", name)

    def update(self, task: Task):
        """Persist any in-place changes to a Task object."""
        self._tasks[task.name] = task
        self._save()

    # ── Training ───────────────────────────────────────────────────────────────

    def train(self, name: str, epochs: int = 50, **trainer_kwargs):
        """
        Train the BC model for a task on its trace directory.

        Extra keyword arguments are forwarded to BCTrainer.__init__
        (e.g. batch_size, lr, d_model, …).
        """
        task = self.get(name)

        from learning_models.intern_model.bc.behavioral_cloning import BCTrainer

        trainer = BCTrainer(
            trace_dir  = task.trace_dir,
            save_path  = task.model_path,
            epochs     = epochs,
            **trainer_kwargs,
        )
        model = trainer.train()
        logger.info("TaskManager: training done for '%s' → %s",
                    name, task.model_path)
        return model

    # ── Execution ──────────────────────────────────────────────────────────────

    def run(
        self,
        name:      str,
        goal:      Optional[str] = None,
        provider:  str           = "none",
        max_steps: int           = 30,
        dry_run:   bool          = False,
        **agent_kwargs,
    ):
        """
        Execute a trained task via LLMAgent.

        Parameters
        ----------
        name      : Registered task name.
        goal      : Natural-language goal (defaults to task.description).
        provider  : LLM provider — "none" | "lmstudio" | "anthropic" |
                    "groq" | "gemini".
        max_steps : Maximum agent steps before stopping.
        dry_run   : Print actions without firing real OS input.
        """
        task  = self.get(name)
        _goal = goal or task.description or f"Complete the task: {name}"

        from agent.agent import LLMAgent

        agent = LLMAgent(
            goal       = _goal,
            provider   = provider,
            model_path = task.model_path,
            max_steps  = max_steps,
            dry_run    = dry_run,
            **agent_kwargs,
        )

        logger.info("TaskManager: running '%s'  provider=%s  max_steps=%d",
                    name, provider, max_steps)
        results = agent.run(max_steps=max_steps)

        task.last_run   = datetime.now(timezone.utc).isoformat()
        task.run_count += 1
        self.update(task)

        return results

    # ── Persistence ────────────────────────────────────────────────────────────

    def _save(self):
        os.makedirs(os.path.dirname(self.registry_path), exist_ok=True)
        with open(self.registry_path, "w") as fh:
            json.dump(
                {n: t.to_dict() for n, t in self._tasks.items()},
                fh, indent=2,
            )

    def _load(self):
        if not os.path.exists(self.registry_path):
            return
        try:
            with open(self.registry_path) as fh:
                raw = json.load(fh)
            self._tasks = {n: Task.from_dict(d) for n, d in raw.items()}
            logger.info("TaskManager: loaded %d task(s) from registry", len(self._tasks))
        except Exception as exc:
            logger.warning("TaskManager: could not load registry — %s", exc)
