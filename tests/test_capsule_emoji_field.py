"""
Regression tests for WorkflowCapsule's `emoji` field
(components/agent/capsule.py) -- added so the Electron app's Workflows tab
can show a user-set emoji per capsule (falling back to a placeholder when
unset), per direct request ("add emojis and placeholder emojis... make it
that you could add emojis to the workflow capsules").

The Electron side (app_electron/main.js) reads and writes tasks/registry.json
directly as plain JSON, but this Python dataclass is what actually loads
that same file everywhere else (CapsuleRegistry, run_task.py's routing).
The one thing that could break silently: a strict dataclass constructor
rejecting a JSON key it doesn't know, or rejecting an *old* registry entry
that predates this field. Both are covered here.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from components.agent.capsule import WorkflowCapsule, CapsuleRegistry


class TestEmojiFieldDefaults:
    def test_emoji_defaults_to_empty_string(self):
        capsule = WorkflowCapsule(
            name="form_filling", description="x", model_path="tasks/form_filling/model.pt",
            trigger_keywords=[], trigger_apps=[],
        )
        assert capsule.emoji == ""

    def test_emoji_can_be_set_explicitly(self):
        capsule = WorkflowCapsule(
            name="form_filling", description="x", model_path="tasks/form_filling/model.pt",
            trigger_keywords=[], trigger_apps=[], emoji="🚗",
        )
        assert capsule.emoji == "🚗"


class TestRegistryBackwardCompatibility:
    def test_loads_a_legacy_entry_with_no_emoji_key_at_all(self, tmp_path):
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps({
            "capsules": [{
                "name": "form_filling", "description": "x",
                "model_path": "tasks/form_filling/model.pt",
                "trigger_keywords": [], "trigger_apps": [],
            }]
        }))
        registry = CapsuleRegistry(registry_path=str(registry_path))
        capsules = registry.list_capsules()
        assert len(capsules) == 1
        assert capsules[0].emoji == ""

    def test_register_then_reload_preserves_a_set_emoji(self, tmp_path):
        registry_path = tmp_path / "registry.json"
        registry = CapsuleRegistry(registry_path=str(registry_path))
        registry.register(WorkflowCapsule(
            name="form_filling", description="x", model_path="tasks/form_filling/model.pt",
            trigger_keywords=[], trigger_apps=[], emoji="📋",
        ))

        reloaded = CapsuleRegistry(registry_path=str(registry_path))
        assert reloaded.list_capsules()[0].emoji == "📋"

    def test_emoji_survives_a_round_trip_written_by_the_electron_side(self, tmp_path):
        # app_electron/main.js writes plain JSON directly (no Python in the
        # loop) -- simulate that write shape exactly, then load it here.
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps({
            "capsules": [{
                "name": "form_filling", "description": "x",
                "model_path": "tasks/form_filling/model.pt",
                "trigger_keywords": ["form"], "trigger_apps": [],
                "trace_dir": "", "created": "2026-05-17T17:52:45",
                "emoji": "🧩",
            }]
        }))
        registry = CapsuleRegistry(registry_path=str(registry_path))
        assert registry.list_capsules()[0].emoji == "🧩"
