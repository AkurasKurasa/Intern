"""
components/agent/task_plugins/base_plugin.py
============================================
Abstract base class for task-specific automation plugins.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Tuple


class TaskPlugin(ABC):
    """
    A TaskPlugin encapsulates all task-specific automation logic.
    The core LLMAgent loop calls handle_step() each iteration;
    if the plugin handles the step it returns (True, True) to continue
    the loop, or (True, False) to break. If unhandled, returns (False, False)
    and the agent falls through to transformer/LLM.
    """

    @abstractmethod
    def handle_step(self, state: Dict[str, Any], step_idx: int) -> Tuple[bool, bool]:
        """
        Process one agent step.
        Returns: (handled: bool, should_continue: bool)
          - (True, True)  -> plugin handled it, loop should continue
          - (True, False) -> plugin handled it, loop should break (task done)
          - (False, False) -> not handled, fall through to transformer/LLM
        """
        ...

    def on_tab_switch(self, state: Dict[str, Any]) -> None:
        """Called when the agent detects a tab switch. Optional."""

    def on_record_start(self, record_num: int, state: Dict[str, Any]) -> None:
        """Called at the start of each record. Optional."""
