"""
continual/learner.py
====================
ContinualLearner — keeps the model improving after every session.

The problem it solves
---------------------
Standard BC trains once and freezes. Every new recording session
is wasted unless you manually retrain. ContinualLearner watches the
trace directory in the background and automatically retrains when
enough new data has accumulated — so every time you use Intern,
it quietly gets better.

Catastrophic forgetting prevention
-----------------------------------
When neural networks learn new data they tend to forget old data.
We prevent this with Experience Replay:
  - A replay buffer keeps a random sample of OLD traces
  - Every retraining mixes old + new traces
  - The model never forgets what it learned before

Flow
----
  New traces arrive in trace_dir (from ScreenObserver)
        ↓
  ContinualLearner detects N new traces
        ↓
  Background thread triggers retraining
        ↓
  New traces + replay buffer (old traces) combined
        ↓
  BCTrainer.train() runs on combined data
        ↓
  Checkpoint updated silently
        ↓
  Next time Intern runs, it uses the improved model

Usage
-----
  learner = ContinualLearner(
      model_path = "data/models/transformer_bc.pt",
      trace_dir  = "data/output/traces/live",
  )
  learner.start()   # begins watching in background
  # ... Intern runs, new traces accumulate ...
  learner.stop()    # graceful shutdown
"""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── path setup ─────────────────────────────────────────────────────────────────
_HERE   = os.path.dirname(os.path.abspath(__file__))
_IM_DIR = os.path.dirname(_HERE)
_LM_DIR = os.path.dirname(_IM_DIR)
_COMP   = os.path.dirname(_LM_DIR)
_ROOT   = os.path.dirname(_COMP)
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class ContinualLearner:
    """
    Background service that retrains the model whenever new traces arrive.

    Parameters
    ----------
    model_path       : BC/RL checkpoint to update.
    trace_dir        : Directory watched for new trace JSON files.
    retrain_every    : Trigger retraining after this many new traces.
    replay_buffer_size: Max old traces kept in replay buffer.
    min_traces       : Don't retrain unless at least this many total traces exist.
    epochs           : Epochs per retraining run (keep low — this is incremental).
    check_interval   : Seconds between checks for new traces.
    device           : Torch device.
    """

    def __init__(
        self,
        model_path:         str   = "data/models/transformer_bc.pt",
        trace_dir:          str   = "data/output/traces/live",
        retrain_every:      int   = 20,
        replay_buffer_size: int   = 200,
        min_traces:         int   = 10,
        epochs:             int   = 10,
        check_interval:     float = 30.0,
        device:             str   = "auto",
    ):
        self.model_path          = model_path
        self.trace_dir           = trace_dir
        self.retrain_every       = retrain_every
        self.replay_buffer_size  = replay_buffer_size
        self.min_traces          = min_traces
        self.epochs              = epochs
        self.check_interval      = check_interval
        self.device              = device

        self._known_traces:  set         = set()
        self._replay_buffer: List[str]   = []   # paths of old traces kept for replay
        self._new_queue:     List[str]   = []   # paths of new traces not yet trained on
        self._stop_event     = threading.Event()
        self._thread:        Optional[threading.Thread] = None
        self._lock           = threading.Lock()
        self._retrain_count  = 0

    # ── public ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background monitoring thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("ContinualLearner started — watching %s", self.trace_dir)

    def stop(self) -> None:
        """Gracefully stop the background thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("ContinualLearner stopped.")

    def add_trace(self, trace_path: str) -> None:
        """
        Manually register a new trace (call this from ScreenObserver after saving).
        Triggers retraining if enough new traces have accumulated.
        """
        with self._lock:
            if trace_path not in self._known_traces:
                self._known_traces.add(trace_path)
                self._new_queue.append(trace_path)

        if len(self._new_queue) >= self.retrain_every:
            threading.Thread(target=self._retrain, daemon=True).start()

    def force_retrain(self) -> None:
        """Manually trigger a retraining run right now."""
        threading.Thread(target=self._retrain, daemon=True).start()

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "known_traces":   len(self._known_traces),
            "new_queued":     len(self._new_queue),
            "replay_buffer":  len(self._replay_buffer),
            "retrain_count":  self._retrain_count,
        }

    # ── monitoring loop ───────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            self._scan_for_new()
            if len(self._new_queue) >= self.retrain_every:
                self._retrain()
            self._stop_event.wait(self.check_interval)

    def _scan_for_new(self) -> None:
        root = Path(self.trace_dir)
        if not root.exists():
            return
        all_files = list(root.glob("*.json"))
        for sd in root.glob("session_*"):
            if sd.is_dir():
                all_files += list(sd.glob("*.json"))

        with self._lock:
            for f in all_files:
                path = str(f)
                if path not in self._known_traces:
                    self._known_traces.add(path)
                    self._new_queue.append(path)

    # ── retraining ────────────────────────────────────────────────────────────

    def _retrain(self) -> None:
        with self._lock:
            new_traces = list(self._new_queue)
            self._new_queue.clear()

        all_traces = self._replay_buffer + new_traces

        if len(all_traces) < self.min_traces:
            logger.info(
                "ContinualLearner: only %d traces — need %d to retrain.",
                len(all_traces), self.min_traces,
            )
            with self._lock:
                self._new_queue = new_traces + self._new_queue
            return

        logger.info(
            "ContinualLearner: retraining on %d traces (%d new + %d replay).",
            len(all_traces), len(new_traces), len(self._replay_buffer),
        )

        # Write combined trace set to a temp dir for BCTrainer
        tmp_dir = tempfile.mkdtemp(prefix="intern_continual_")
        try:
            self._write_traces_to(all_traces, tmp_dir)
            self._run_bc(tmp_dir)
            self._update_replay_buffer(new_traces)
            self._retrain_count += 1
            logger.info(
                "ContinualLearner: retraining #%d complete.", self._retrain_count
            )
        except Exception as exc:
            logger.error("ContinualLearner: retraining failed — %s", exc)
            with self._lock:
                self._new_queue = new_traces + self._new_queue
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _write_traces_to(self, trace_paths: List[str], dest_dir: str) -> None:
        """Copy valid traces (with elements) into dest_dir for training."""
        written = 0
        for i, src in enumerate(trace_paths):
            try:
                with open(src, encoding="utf-8") as f:
                    data = json.load(f)
                # Skip empty-state traces
                if not data.get("state", {}).get("elements"):
                    continue
                dst = os.path.join(dest_dir, f"trace_{i:05d}.json")
                with open(dst, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                written += 1
            except Exception:
                pass
        logger.info("ContinualLearner: wrote %d valid traces to temp dir.", written)

    def _run_bc(self, trace_dir: str) -> None:
        """Run BCTrainer on the combined trace set."""
        try:
            from bc.behavioral_cloning import BCTrainer
        except ImportError:
            from components.learning_models.intern_model.bc.behavioral_cloning import BCTrainer

        trainer = BCTrainer(
            trace_dir  = trace_dir,
            save_path  = self.model_path,
            epochs     = self.epochs,
            device     = self.device,
        )
        trainer.train()

    def _update_replay_buffer(self, new_traces: List[str]) -> None:
        """
        Add new traces to replay buffer, evicting old ones if over capacity.
        Uses reservoir sampling so all traces have equal probability of being kept.
        """
        with self._lock:
            self._replay_buffer.extend(new_traces)
            if len(self._replay_buffer) > self.replay_buffer_size:
                self._replay_buffer = random.sample(
                    self._replay_buffer, self.replay_buffer_size
                )
