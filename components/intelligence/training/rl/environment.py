"""
rl/environment.py
=================
Mock environments for safe RL training — no real apps harmed.

Classes
-------
  MockEnvironment        — abstract base all environments inherit from
  TkinterFormEnvironment — concrete Tkinter form that resets between episodes

The RL trainer interacts exclusively with these environments during training.
The real Windows desktop is never touched until inference time.

Usage
-----
  env = TkinterFormEnvironment(
      fields=["First Name", "Last Name", "Phone", "Email"],
      source_data={"First Name": "James", "Last Name": "Delgado", ...},
  )
  state = env.reset()
  state, reward, done = env.step({"action_type": "click", "click_position": [x, y]})
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


# ══════════════════════════════════════════════════════════════════════════════
#  Abstract base
# ══════════════════════════════════════════════════════════════════════════════

class MockEnvironment(ABC):
    """
    All mock environments must implement this interface so RLTrainer
    can train on any task type without changing its own code.
    """

    @abstractmethod
    def reset(self) -> Dict[str, Any]:
        """Clear all fields and return the initial state dict."""

    @abstractmethod
    def step(self, action: Dict[str, Any]) -> Tuple[Dict[str, Any], float, bool]:
        """
        Apply action.  Returns (next_state, reward, done).
        reward comes from the environment's built-in RewardFunction.
        done is True when all required fields are correctly filled.
        """

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """Return current state dict (same schema as UIAutomationObserver)."""

    @abstractmethod
    def is_complete(self) -> bool:
        """True when the task is fully and correctly done."""

    @abstractmethod
    def close(self) -> None:
        """Clean up the environment (close window, delete temp files, etc.)."""


# ══════════════════════════════════════════════════════════════════════════════
#  Tkinter form environment
# ══════════════════════════════════════════════════════════════════════════════

class TkinterFormEnvironment(MockEnvironment):
    """
    A real Tkinter window with labelled text-entry fields.

    Resets instantly between RL episodes.
    UIAutomationObserver can read it exactly like a real app.
    Safe — closing or resetting it causes no side effects.

    Parameters
    ----------
    fields      : List of field names (e.g. ["First Name", "Last Name"]).
    source_data : The correct values the agent should type
                  (e.g. {"First Name": "James", "Last Name": "Delgado"}).
    title       : Window title shown to UIAutomationObserver.
    """

    def __init__(
        self,
        fields:      List[str],
        source_data: Dict[str, str],
        title:       str = "Intern — Training Form",
    ):
        self.fields      = fields
        self.source_data = source_data
        self.title       = title

        self._root    = None
        self._entries: Dict[str, Any] = {}
        self._ready   = threading.Event()
        self._thread  = threading.Thread(target=self._run_tk, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    # ── MockEnvironment interface ─────────────────────────────────────────────

    def reset(self) -> Dict[str, Any]:
        """Clear all fields back to empty."""
        if self._root:
            self._root.after(0, self._clear_fields)
        time.sleep(0.1)
        return self.get_state()

    def step(self, action: Dict[str, Any]) -> Tuple[Dict[str, Any], float, bool]:
        prev = self._read_fields()
        self._apply_action(action)
        time.sleep(0.05)
        next_state = self.get_state()
        curr = self._read_fields()

        reward = self._compute_reward(prev, curr, action)
        done   = self.is_complete()
        if done:
            reward += 5.0   # big bonus for completing the task

        return next_state, reward, done

    def get_state(self) -> Dict[str, Any]:
        """Return a UIAutomationObserver-compatible state dict."""
        elements = []
        for i, field in enumerate(self.fields):
            value = self._read_field(field)
            # Label element
            elements.append({
                "element_id":  f"label_{i}",
                "type":        "label",
                "control_type": "Text",
                "bbox":        [10, 40 + i * 60, 150, 60 + i * 60],
                "text":        field,
                "value":       "",
                "label":       field,
                "enabled":     True,
                "visible":     True,
                "focused":     False,
                "confidence":  1.0,
                "source":      "mock",
                "window_role": "active",
                "app":         "mock_form",
            })
            # Input element
            elements.append({
                "element_id":  f"input_{i}",
                "type":        "input",
                "control_type": "Edit",
                "bbox":        [160, 40 + i * 60, 460, 60 + i * 60],
                "text":        value,
                "value":       value,
                "label":       field,
                "enabled":     True,
                "visible":     True,
                "focused":     False,
                "confidence":  1.0,
                "source":      "mock",
                "window_role": "active",
                "app":         "mock_form",
            })

        # Source data elements (simulates Notepad in background)
        for j, (k, v) in enumerate(self.source_data.items()):
            elements.append({
                "element_id":  f"src_{j}",
                "type":        "label",
                "control_type": "Text",
                "bbox":        [600, 40 + j * 30, 900, 60 + j * 30],
                "text":        f"{k}: {v}",
                "value":       v,
                "label":       k,
                "enabled":     True,
                "visible":     True,
                "focused":     False,
                "confidence":  1.0,
                "source":      "mock",
                "window_role": "background",
                "app":         "mock_notepad",
            })

        return {
            "application":        "mock_form",
            "window_title":       self.title,
            "process_id":         None,
            "screen_resolution":  [1024, 768],
            "focused_element_id": None,
            "elements":           elements,
            "source":             "mock",
        }

    def is_complete(self) -> bool:
        vals = self._read_fields()
        return all(
            vals.get(f, "").strip().lower() == self.source_data.get(f, "").strip().lower()
            for f in self.fields
            if f in self.source_data
        )

    def close(self) -> None:
        if self._root:
            self._root.after(0, self._root.destroy)

    # ── internal ─────────────────────────────────────────────────────────────

    def _run_tk(self) -> None:
        import tkinter as tk
        self._root = tk.Tk()
        self._root.title(self.title)
        self._root.geometry("500x" + str(80 + len(self.fields) * 60))
        self._root.resizable(False, False)

        for i, field in enumerate(self.fields):
            tk.Label(self._root, text=field, width=20, anchor="w").grid(
                row=i, column=0, padx=10, pady=8)
            entry = tk.Entry(self._root, width=30)
            entry.grid(row=i, column=1, padx=10, pady=8)
            self._entries[field] = entry

        self._ready.set()
        self._root.mainloop()

    def _clear_fields(self) -> None:
        for entry in self._entries.values():
            entry.delete(0, "end")

    def _read_fields(self) -> Dict[str, str]:
        return {f: (e.get() if e.winfo_exists() else "") for f, e in self._entries.items()}

    def _read_field(self, field: str) -> str:
        entry = self._entries.get(field)
        if entry and entry.winfo_exists():
            return entry.get()
        return ""

    def _apply_action(self, action: Dict[str, Any]) -> None:
        """Apply an action directly to the mock form (no pyautogui needed)."""
        atype = action.get("action_type", "no_op")
        if atype == "click":
            pos = action.get("click_position", [0, 0])
            field = self._field_at(pos[0], pos[1])
            if field and self._root:
                self._root.after(0, lambda f=field: self._entries[f].focus_set())
        elif atype == "keyboard":
            text = action.get("text", "")
            focused = self._focused_field()
            if text and focused and self._root:
                def _type(f=focused, t=text):
                    self._entries[f].delete(0, "end")
                    self._entries[f].insert(0, t)
                self._root.after(0, _type)
                time.sleep(0.05)

    def _field_at(self, x: float, y: float) -> Optional[str]:
        """Return which field the click landed on based on mock bbox."""
        for i, field in enumerate(self.fields):
            y1 = 40 + i * 60
            y2 = 60 + i * 60
            if 160 <= x <= 460 and y1 <= y <= y2:
                return field
        return None

    def _focused_field(self) -> Optional[str]:
        if not self._root:
            return None
        try:
            focused = self._root.focus_get()
            for f, e in self._entries.items():
                if e is focused:
                    return f
        except Exception:
            pass
        return None

    def _compute_reward(
        self,
        prev: Dict[str, str],
        curr: Dict[str, str],
        action: Dict[str, Any],
    ) -> float:
        reward = 0.0
        for field in self.fields:
            target  = self.source_data.get(field, "").strip().lower()
            was     = prev.get(field, "").strip().lower()
            now     = curr.get(field, "").strip().lower()
            if not was and now == target:
                reward += 1.0    # correct value typed
            elif not was and now and now != target:
                reward -= 0.5    # wrong value typed
        if action.get("action_type") == "no_op":
            reward -= 0.1        # penalise doing nothing
        return reward
