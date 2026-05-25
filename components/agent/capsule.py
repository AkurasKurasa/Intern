"""
components/agent/capsule.py
===========================
WorkflowCapsule — a named BC model checkpoint tied to a specific task type.
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
    model_path:       str         # path to .pt checkpoint
    trigger_keywords: List[str]   # match against goal string (lowercase)
    trigger_apps:     List[str]   # match against window_title (case-insensitive)
    trace_dir:        str = ""    # where training traces live
    created:          str = ""    # ISO timestamp


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
        """Return model_path for best matching capsule, or fallback."""
        goal_lower  = goal.lower()
        title_lower = window_title.lower()
        for capsule in self._capsules:
            if any(k in goal_lower  for k in capsule.trigger_keywords):
                return capsule.model_path
            if any(a.lower() in title_lower for a in capsule.trigger_apps):
                return capsule.model_path
        return fallback

    def list_capsules(self) -> List[WorkflowCapsule]:
        return list(self._capsules)
