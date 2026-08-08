"""
Regression test for agent.py's repeat-action guard -- it fingerprints the
FINAL `prediction` right before execution and force-Tabs (skipping the
normal ExecutionResult/Validator logging) whenever the last N fingerprints
are identical.

Found 2026-08-09, live, direct user report ("We're in a loop and we're
missing certain steps. I don't even know if we're fucking using the
Transformer."). logs/latest.log, steps 108-113: the low-confidence-pointer
fallback checked a genuinely DIFFERENT checkbox each step via BM_SETCHECK
('Roadside Assistance', 'GAP Insurance', 'Rideshare Coverage', 'New Car
Replacement', ...), then reassigned `prediction` to a boilerplate "tab to
move on" afterward. Three DIFFERENT successful checks in a row all ended
with the IDENTICAL trailing tab prediction, so the guard saw "same action
3x" and force-Tabbed AGAIN at step 111 -- through its own direct
self._executor.execute() call, which (confirmed in the log) skips the
normal "[OK] ExecutionResult" / "Validator" lines every other action gets.
Real, distinguishable progress was happening every single step; the guard
just couldn't see past the leftover bookkeeping Tab to notice.

Fixed by having any code path that performs a genuine state-changing action
(both checkbox-check BM_SETCHECK sites) set a per-step
`_real_progress_this_step` flag; the guard now skips its fingerprint check
entirely (and clears history, since real progress legitimately breaks any
actual stuck streak too) whenever that flag is set, letting the normal
keyboard-tab path execute with its usual logging instead.
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

REPEAT_LIMIT = 3


def _repeat_action_guard(prediction, action_history, real_progress_this_step, pure_transformer=False):
    """Mirrors the CURRENT (2026-08-09) repeat-action guard in agent.py's
    run(): returns the forced-Tab prediction if the guard intervenes, else
    None (meaning the caller's own `prediction` should execute normally)."""
    if pure_transformer:
        action_history.clear()
    if real_progress_this_step:
        action_history.clear()
        return None
    atype = prediction.get("action_type", "no_op")
    if atype == "keyboard":
        fp = ("keyboard", (prediction.get("text") or "".join(prediction.get("keystrokes", [])))[:40])
    elif atype == "click":
        cp = prediction.get("click_position", [0, 0])
        fp = ("click", round(cp[0] / 20) * 20, round(cp[1] / 20) * 20)
    else:
        fp = (atype,)
    action_history.append(fp)
    if len(action_history) >= REPEAT_LIMIT and len(set(action_history)) == 1:
        action_history.clear()
        return {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
    return None


_TRAILING_TAB = {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}


class TestGuardIgnoresBoilerplateTabAfterRealProgress:
    def test_three_different_checkbox_checks_never_trigger_the_guard(self):
        """The actual live regression: three DIFFERENT checkboxes checked
        in a row, each followed by the same trailing tab prediction -- must
        NOT force an extra, unlogged Tab."""
        history = deque(maxlen=REPEAT_LIMIT)
        for _ in range(REPEAT_LIMIT):
            forced = _repeat_action_guard(_TRAILING_TAB, history, real_progress_this_step=True)
            assert forced is None
        assert len(history) == 0

    def test_real_progress_clears_an_in_progress_streak(self):
        """Two genuinely-stuck identical predictions (no progress), then a
        step WITH real progress that happens to share the same fingerprint
        -- the streak must reset, not carry over into the next check."""
        history = deque(maxlen=REPEAT_LIMIT)
        assert _repeat_action_guard(_TRAILING_TAB, history, real_progress_this_step=False) is None
        assert _repeat_action_guard(_TRAILING_TAB, history, real_progress_this_step=False) is None
        assert len(history) == 2
        assert _repeat_action_guard(_TRAILING_TAB, history, real_progress_this_step=True) is None
        assert len(history) == 0
        # One more identical prediction after the reset must not immediately
        # re-trigger -- the streak genuinely restarted.
        assert _repeat_action_guard(_TRAILING_TAB, history, real_progress_this_step=False) is None
        assert len(history) == 1


class TestGuardStillCatchesAGenuineStuckLoop:
    """The guard must still do its real job when nothing legitimate is
    happening -- this fix must not weaken it into a no-op."""

    def test_three_identical_predictions_with_no_progress_still_forces_tab(self):
        history = deque(maxlen=REPEAT_LIMIT)
        forced = None
        for _ in range(REPEAT_LIMIT):
            forced = _repeat_action_guard(_TRAILING_TAB, history, real_progress_this_step=False)
        assert forced == {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
        assert len(history) == 0

    def test_three_different_predictions_with_no_progress_never_fires(self):
        history = deque(maxlen=REPEAT_LIMIT)
        preds = [
            {"action_type": "click", "click_position": [100, 100]},
            {"action_type": "click", "click_position": [500, 500]},
            {"action_type": "click", "click_position": [900, 900]},
        ]
        for p in preds:
            forced = _repeat_action_guard(p, history, real_progress_this_step=False)
        assert forced is None
