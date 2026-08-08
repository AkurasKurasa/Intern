"""
Regression test for agent.py's OPT2 reclick guard -- once the transformer's
own pointer drifts back onto the SAME already-handled field several
consecutive times, redirect to a known-good target instead of another blind
Tab that just gambles on OS focus-traversal order.

Found 2026-08-08, live, direct user report ("still not finding the right
view"): the pointer aimed at the exact same already-filled field's screen
position on 3 consecutive steps in a row before finally moving on -- each
one caught safely by the reclick guard (no wasted click, no data
corruption), but the guard's only response was a blind Tab every time, with
no tracking of how many times this had just happened and no attempt to
steer toward a field that's actually still empty. This repeated dozens of
times across one run, which is what the "not sweeping cleanly, feels
scattered" complaint was actually about once the earlier scroll/reveal fix
was confirmed working.

Mirrors the SAME deterministic-redirect mechanism already used by the
low-confidence-fallback streak escalation a few lines below it in agent.py
-- find_visible_empty_target() gives a known, currently-empty, currently-
visible target to click instead of hoping Tab's default order helps.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import find_visible_empty_target

VIEWPORT_BOTTOM = 1000.0


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol"):
    return {"element_id": label, "type": ftype, "label": label, "value": value,
            "bbox": list(bbox), "window_role": "active"}


def _run_reclick_guard(state, reclick_streak, redirect_limit, executor):
    """Mirrors the CURRENT reclick-guard block in agent.py's run(): once
    reclick_streak reaches redirect_limit, redirect to a known target
    instead of a blind Tab."""
    reclick_streak += 1
    if reclick_streak >= redirect_limit:
        target = find_visible_empty_target(state, VIEWPORT_BOTTOM)
        if target and target.get("bbox"):
            b = target["bbox"]
            executor.execute({"action_type": "click",
                               "click_position": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]})
            return 0   # streak resets
    executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
    return reclick_streak


class TestReclickStreakRedirectsInsteadOfBlindTabbing:
    def test_first_drift_still_gets_a_plain_tab(self):
        """A single drift-back is normal model uncertainty -- don't
        over-react to one instance."""
        executor = MagicMock()
        state = {"elements": [
            _field("Years Continuously Insured", value="9", bbox=(100, 100, 300, 130)),
            _field("Cell Phone", value="", bbox=(100, 200, 300, 230)),
        ]}
        streak = _run_reclick_guard(state, reclick_streak=0, redirect_limit=2, executor=executor)
        assert streak == 1
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}]

    def test_second_consecutive_drift_redirects_to_a_known_empty_target(self):
        """The actual live bug: the SAME stuck position recurring means a
        blind Tab keeps failing to escape it -- redirect deterministically."""
        executor = MagicMock()
        state = {"elements": [
            _field("Years Continuously Insured", value="9", bbox=(100, 100, 300, 130)),
            _field("Cell Phone", value="", bbox=(100, 200, 300, 230)),
        ]}
        streak = _run_reclick_guard(state, reclick_streak=1, redirect_limit=2, executor=executor)
        assert streak == 0
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "click", "click_position": [200.0, 215.0]}]

    def test_redirect_falls_back_to_tab_when_nothing_else_is_visible(self):
        """No genuinely empty target exists yet (e.g. mid-transition) --
        still safe to fall back to a plain Tab rather than clicking nothing."""
        executor = MagicMock()
        state = {"elements": [
            _field("Years Continuously Insured", value="9", bbox=(100, 100, 300, 130)),
        ]}
        streak = _run_reclick_guard(state, reclick_streak=1, redirect_limit=2, executor=executor)
        # Streak stays incremented (not reset) since no target was found to
        # redirect to -- matches agent.py: the counter only resets on an
        # actual successful redirect, so the very next step retries
        # immediately instead of waiting through another full Tab-and-hope
        # cycle before trying again.
        assert streak == 2
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}]
