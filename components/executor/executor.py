"""
components/executor/executor.py
================================
Executor — translates Transformer model predictions into real OS-level actions.

Pipeline position
-----------------
  ScreenObserver  →  TraceTranslator  →  TransformerAgentNetwork.predict()
                                                          ↓
                                                    ActionExecutor   ← you are here
                                                          ↓
                                                  OS mouse / keyboard

Classes
-------
  ExecutionResult  — immutable record of what was executed
  ActionExecutor   — converts a single prediction dict into OS input
  ExecutorAgent    — stateful loop: observe → predict → execute → repeat

Quick-start (dry-run, no model needed)
---------------------------------------
  from components.executor.executor import ActionExecutor

  result = ActionExecutor(dry_run=True).execute(
      {"action_type": "click", "click_position": [960, 540]}
  )
  print(result)

Full agentic loop (requires a trained checkpoint)
--------------------------------------------------
  from components.executor.executor import ExecutorAgent

  agent = ExecutorAgent(model_path="data/models/transformer_bc.pt")
  agent.run(initial_state=current_state_dict, max_steps=20)

CLI
---
  python -m components.executor.executor \\
      --trace_path data/output/traces/live/live_step_0000.json \\
      --dry_run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import threading
from datetime import datetime
from typing import Any, Dict, List, NamedTuple, Optional

# ── path setup ────────────────────────────────────────────────────────────────
_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))   # components/executor/
_COMP_DIR   = os.path.dirname(_THIS_DIR)                   # components/
_ROOT       = os.path.dirname(_COMP_DIR)                   # Intern/
for _p in (_ROOT, _COMP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── optional dependency: pyautogui ────────────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE  = True   # move mouse to corner to abort
    pyautogui.PAUSE     = 0.05  # small delay between every pyautogui call
    _PYAUTOGUI_AVAILABLE = True
except ImportError:
    _PYAUTOGUI_AVAILABLE = False

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)


# ══════════════════════════════════════════════════════════════════════════════
#  ExecutionResult
# ══════════════════════════════════════════════════════════════════════════════

class ExecutionResult(NamedTuple):
    """
    Immutable record of a single executed action.

    Fields
    ------
    action_type  : "click" | "keyboard" | "no_op"
    position     : (x, y) pixel coordinates for click actions, else None
    key_count    : number of keystrokes issued (keyboard actions), else 0
    keystrokes   : actual key strings sent (if any), else []
    timestamp    : ISO-8601 string of when the action fired
    dry_run      : True if no real input was produced
    success      : False if an exception was caught during execution
    error        : Exception message if success is False, else ""
    """
    action_type : str
    position    : Optional[tuple]
    key_count   : int
    keystrokes  : List[str]
    timestamp   : str
    dry_run     : bool
    success     : bool
    error       : str

    def __str__(self) -> str:
        tag = "[DRY-RUN] " if self.dry_run else ""
        ok  = "✓" if self.success else "✗"
        if self.action_type == "click":
            detail = f"@ {self.position}"
        elif self.action_type == "keyboard":
            detail = f"keys={self.key_count}"
            if self.keystrokes:
                detail += f" ({', '.join(self.keystrokes[:6])}{'…' if len(self.keystrokes) > 6 else ''})"
        else:
            detail = ""
        return (
            f"{tag}{ok} ExecutionResult("
            f"type={self.action_type!r}, {detail}, ts={self.timestamp})"
            + (f" ERROR: {self.error}" if self.error else "")
        )


# ══════════════════════════════════════════════════════════════════════════════
#  ActionExecutor
# ══════════════════════════════════════════════════════════════════════════════

class ActionExecutor:
    """
    Converts a single model prediction dict into a real (or simulated) OS action.

    Prediction dict format (as returned by ``TransformerAgentNetwork.predict()``)
    ----------------------------------------------------------------------------
    {
        "action_type": "click" | "keyboard" | "no_op",
        "click_position": [x_px, y_px],   # present for "click"
        "key_count": <int>,                # present for "keyboard"
        "keystrokes": ["a", "b", ...],     # optional — explicit keys to type
    }

    Parameters
    ----------
    dry_run          : If True, log actions but do not produce real input.
    pre_click_delay  : Seconds to wait before a click (lets UI settle).
    post_click_delay : Seconds to wait after a click.
    keyboard_delay   : Seconds between individual keystrokes.
    """

    def __init__(
        self,
        dry_run:           bool  = False,
        pre_click_delay:   float = 0.05,
        post_click_delay:  float = 0.1,
        keyboard_delay:    float = 0.05,
    ):
        self.dry_run           = dry_run
        self.pre_click_delay   = pre_click_delay
        self.post_click_delay  = post_click_delay
        self.keyboard_delay    = keyboard_delay

        if not dry_run and not _PYAUTOGUI_AVAILABLE:
            raise ImportError(
                "pyautogui is required for live execution.\n"
                "Install with:  pip install pyautogui\n"
                "Or use dry_run=True to simulate actions without real input."
            )

    # ── public API ────────────────────────────────────────────────────────────

    def execute(self, prediction: Dict[str, Any]) -> ExecutionResult:
        """
        Dispatch a model prediction dict to the appropriate action method.

        Returns
        -------
        ExecutionResult with success=True on success, False on any exception.
        """
        action_type  = prediction.get("action_type", "no_op")
        ts           = datetime.now().isoformat()

        try:
            if action_type == "click":
                pos = prediction.get("click_position", [0, 0])
                x, y = int(round(pos[0])), int(round(pos[1]))
                self._click(x, y)
                return ExecutionResult(
                    action_type="click", position=(x, y), key_count=0,
                    keystrokes=[], timestamp=ts, dry_run=self.dry_run,
                    success=True, error="",
                )

            elif action_type == "keyboard":
                key_count  = max(1, int(prediction.get("key_count", 1)))
                keystrokes = list(prediction.get("keystrokes", []))
                issued     = self._keyboard(key_count, keystrokes)
                return ExecutionResult(
                    action_type="keyboard", position=None,
                    key_count=len(issued), keystrokes=issued,
                    timestamp=ts, dry_run=self.dry_run, success=True, error="",
                )

            else:  # no_op
                self._no_op()
                return ExecutionResult(
                    action_type="no_op", position=None, key_count=0,
                    keystrokes=[], timestamp=ts, dry_run=self.dry_run,
                    success=True, error="",
                )

        except Exception as exc:
            logger.error("execute() raised: %s", exc)
            return ExecutionResult(
                action_type=action_type, position=None, key_count=0,
                keystrokes=[], timestamp=ts, dry_run=self.dry_run,
                success=False, error=str(exc),
            )

    # ── action primitives ─────────────────────────────────────────────────────

    def _click(self, x: int, y: int) -> None:
        """Move to (x, y) and left-click."""
        logger.info("CLICK  @ (%d, %d)%s", x, y, "  [DRY-RUN]" if self.dry_run else "")
        if self.dry_run:
            return
        time.sleep(self.pre_click_delay)
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.click(x, y)
        time.sleep(self.post_click_delay)

    def _keyboard(self, key_count: int, keystrokes: List[str]) -> List[str]:
        """
        Type keystrokes if provided; otherwise log a stub for the predicted count.

        The current Transformer model predicts *how many* keys were pressed
        but not *which* keys. If the calling code has resolved the actual keys
        (e.g. by looking at the next-state diff), it passes them in ``keystrokes``.
        Otherwise this method logs a warning and issues no real keystrokes.

        Returns the list of keys that were actually issued.
        """
        if keystrokes:
            issued = keystrokes[:key_count]
            logger.info(
                "KEYBOARD  keys=%d %s%s", len(issued), issued,
                "  [DRY-RUN]" if self.dry_run else "",
            )
            if not self.dry_run:
                for key in issued:
                    self._press_key(key)
                    time.sleep(self.keyboard_delay)
            return issued
        else:
            # Model predicted keystrokes but no explicit keys were resolved.
            logger.warning(
                "KEYBOARD  key_count=%d — no keystrokes resolved, action skipped. "
                "Pass 'keystrokes' in the prediction dict to type real characters.",
                key_count,
            )
            return []

    def _press_key(self, key: str) -> None:
        """Press a single key string (handles special keys like 'Key.enter')."""
        if key.startswith("Key."):
            special = key.split("Key.", 1)[1]
            pyautogui.press(special)
        elif len(key) == 1:
            pyautogui.typewrite(key, interval=0.0)
        else:
            pyautogui.press(key)

    def _no_op(self) -> None:
        logger.info("NO_OP%s", "  [DRY-RUN]" if self.dry_run else "")


# ══════════════════════════════════════════════════════════════════════════════
#  ExecutorAgent  — stateful observation→prediction→execution loop
# ══════════════════════════════════════════════════════════════════════════════

class ExecutorAgent:
    """
    Stateful agent loop: reads screen state, predicts next action, executes it.

    Uses ``TransformerAgentNetwork.predict()`` to choose actions and
    ``ActionExecutor`` to issue them.  Maintains a rolling history of past
    (state, action) pairs so the Transformer has context for its next prediction.

    Parameters
    ----------
    model_path      : Path to a trained ``.pt`` checkpoint.
    dry_run         : Pass-through to ActionExecutor — no real input if True.
    max_steps       : Hard cap on loop iterations (safety).
    step_delay      : Seconds to sleep between steps (let UI react).
    min_step_delay  : Floor on step_delay (prevents runaway loops).
    device_str      : "auto" | "cpu" | "cuda" | "mps"
    executor_kwargs : Extra kwargs forwarded to ActionExecutor.__init__.
    """

    def __init__(
        self,
        model_path:      str   = "data/models/transformer_bc.pt",
        dry_run:         bool  = False,
        max_steps:       int   = 50,
        step_delay:      float = 1.0,
        min_step_delay:  float = 0.2,
        device_str:      str   = "auto",
        **executor_kwargs,
    ):
        self.model_path     = model_path
        self.dry_run        = dry_run
        self.max_steps      = max_steps
        self.step_delay     = max(step_delay, min_step_delay)
        self.device_str     = device_str

        self._executor      = ActionExecutor(dry_run=dry_run, **executor_kwargs)
        self._history: List[Dict[str, Any]] = []  # rolling context for predict()
        self._stop_event    = threading.Event()
        self._results: List[ExecutionResult] = []

    # ── public API ────────────────────────────────────────────────────────────

    def step(self, state: Dict[str, Any]) -> ExecutionResult:
        """
        Execute one predict → execute cycle.

        Parameters
        ----------
        state : Current UI state dict (same schema as trace JSON ``state`` field).

        Returns
        -------
        ExecutionResult for the action that was taken.
        """
        from components.learning_models.transformer.transformer import predict

        prediction = predict(
            state=state,
            history=self._history,
            model_path=self.model_path,
            device_str=self.device_str,
        )
        logger.info("Prediction → %s", prediction)

        result = self._executor.execute(prediction)
        logger.info("%s", result)

        # Advance rolling history
        res   = state.get("screen_resolution", [1920, 1080])
        W, H  = float(res[0]) or 1920.0, float(res[1]) or 1080.0
        pos   = prediction.get("click_position", [0.0, 0.0])

        self._history.append({
            "state":       state,
            "action_type": prediction.get("action_type", "no_op"),
            "click_xy":    [pos[0] / W, pos[1] / H] if pos else [0.0, 0.0],
            "key_count":   prediction.get("key_count", 0),
        })

        self._results.append(result)
        return result

    def run(
        self,
        initial_state: Dict[str, Any],
        max_steps: Optional[int] = None,
        step_delay: Optional[float] = None,
    ) -> List[ExecutionResult]:
        """
        Run the agentic loop starting from ``initial_state``.

        The loop runs for ``max_steps`` iterations (or until ``stop()`` is
        called). After each step it sleeps ``step_delay`` seconds to allow
        the UI to react before the next observation.

        .. note::
            In a full live deployment, ``initial_state`` would be refreshed
            each step from a ``ScreenObserver`` snapshot.  This method accepts
            a single state for simplicity; extend by passing a callable that
            returns the latest state if you want continuous observation.

        Parameters
        ----------
        initial_state : Starting UI state dict.
        max_steps     : Override instance max_steps for this run.
        step_delay    : Override instance step_delay for this run.

        Returns
        -------
        List of ExecutionResult — one per step taken.
        """
        n_steps  = max_steps  if max_steps  is not None else self.max_steps
        delay    = step_delay if step_delay is not None else self.step_delay
        self._stop_event.clear()
        self._results.clear()

        logger.info(
            "ExecutorAgent.run() — max_steps=%d  step_delay=%.2fs  dry_run=%s",
            n_steps, delay, self.dry_run,
        )

        current_state = initial_state
        for step_idx in range(n_steps):
            if self._stop_event.is_set():
                logger.info("Stop requested — halting at step %d.", step_idx)
                break

            logger.info("── Step %d / %d ──", step_idx + 1, n_steps)
            result = self.step(current_state)

            if not result.success:
                logger.error("Step %d failed: %s — halting.", step_idx + 1, result.error)
                break

            if step_idx < n_steps - 1:
                time.sleep(delay)

        logger.info(
            "ExecutorAgent finished — %d step(s) executed.", len(self._results)
        )
        return list(self._results)

    def stop(self) -> None:
        """Signal the run loop to halt after the current step completes."""
        self._stop_event.set()

    def reset_history(self) -> None:
        """Clear the rolling action history (start fresh context)."""
        self._history.clear()

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    @property
    def results(self) -> List[ExecutionResult]:
        return list(self._results)


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Executor — run a single predict→execute step from a trace JSON."
    )
    p.add_argument(
        "--trace_path", required=True,
        help="Path to a trace JSON; the 'state' field is used as the current state.",
    )
    p.add_argument(
        "--model_path", default="data/models/transformer_bc.pt",
        help="Path to trained TransformerAgentNetwork checkpoint.",
    )
    p.add_argument(
        "--device", default="auto", dest="device_str",
        help="Torch device: 'auto' | 'cpu' | 'cuda' | 'mps'.",
    )
    p.add_argument(
        "--dry_run", action="store_true",
        help="Log the predicted action without firing real input.",
    )
    p.add_argument(
        "--max_steps", type=int, default=1,
        help="Number of predict→execute steps to run (default: 1).",
    )
    p.add_argument(
        "--step_delay", type=float, default=1.0,
        help="Seconds to wait between steps.",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    with open(args.trace_path, encoding="utf-8") as f:
        trace = json.load(f)

    state = trace.get("state", {})
    agent = ExecutorAgent(
        model_path=args.model_path,
        dry_run=args.dry_run,
        max_steps=args.max_steps,
        step_delay=args.step_delay,
        device_str=args.device_str,
    )

    results = agent.run(initial_state=state)

    print("\n── Execution Summary ────────────────────────────")
    for i, r in enumerate(results, 1):
        print(f"  Step {i:>2}: {r}")
    print(f"{'':─<49}")


if __name__ == "__main__":
    main()
