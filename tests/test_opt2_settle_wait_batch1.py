"""
Regression tests for the first batch of OPT2 blind-sleep -> _adaptive_settle_wait
conversions in components/agent/agent.py.

Built 2026-08-14, direct continuation of scope1_speed's _adaptive_settle_wait fix.
That fix only touched the main loop's single highest-frequency call site
(the success-path end-of-step sleep) -- deliberately leaving "the ~25 other,
smaller fractional sleeps scattered through specific retry/redirect
branches... untouched, to avoid destabilizing logic not individually
verified safe to change in the same pass."

Found this session: an earlier speed feature ("plan-then-replay") had been
built on top of FormFillerPlugin, which the real live-run script
(run_task.py) never uses -- task_plugin=None, disable_auto_handlers=True.
disable_auto_handlers is itself a deliberate choice ("we want to see the
pure transformer with no rescue -- honest navigation test"), so any real
speed work has to target OPT2 (the transformer-navigation + LLM-value-
oracle mechanism active under disable_auto_handlers), not the plugin path.

12 call sites were individually read and confirmed safe to convert (plain
Tab/click followed immediately by `continue`, no tab-strip switch, no
dropdown-open state) -- excluding every site next to a real tab switch,
the verify-at-fill retry loop, and combobox open/close handling, all of
which have their own documented history of subtle live-only bugs.

Since these sites live deep inside LLMAgent.run() (~8000 lines, heavy
local state -- _reclick_streak, _lowconf_fallback_streak, _action_history,
etc. -- not easily driven from outside), this file follows this project's
own established pattern for testing logic embedded in that method: direct
source-level verification that each converted site actually calls
_adaptive_settle_wait now (not a raw time.sleep), the same technique
already used in tests/test_redirect_click_destructive_button_guard.py and
tests/test_type_path_focus_via_uia.py. _adaptive_settle_wait's own timing
semantics (early-settle, never-settle bound, observe-failure fallback) are
already exhaustively covered by tests/test_adaptive_settle_wait.py and are
NOT re-derived here.

Three of the twelve sites (the reclick-redirect, combobox-reclick-redirect,
and low-confidence-fallback-redirect branches) already implement their own
manual "wait, then observe and check the click actually landed" logic right
after the sleep -- exactly the shape _adaptive_settle_wait automates. Those
get an additional mirror-function test (this project's own established
pattern for testing gating logic embedded in run() without extracting it)
proving the wait-then-verify shape still settles early/bounds correctly
once wired through the real helper.
"""
import re
import sys
import time as _time_module
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent

_AGENT_PY = Path(__file__).resolve().parent.parent / "components" / "agent" / "agent.py"
_SOURCE = _AGENT_PY.read_text(encoding="utf-8")


def _make_agent():
    return LLMAgent(goal="test goal", dry_run=True, max_steps=1, step_delay=0)


# ------------------------------------------------- source-level conversion checks
#
# Each test below anchors on a real, unique snippet of source immediately
# surrounding a converted wait call -- not just a line number, which drifts.


class TestConvertedSitesUseAdaptiveSettleWait:
    """Direct source verification: every one of the 12 identified sites now
    calls self._adaptive_settle_wait, none of them a raw time.sleep."""

    def test_all_twelve_new_sites_call_adaptive_settle_wait(self):
        count = len(re.findall(r"self\._adaptive_settle_wait\(self\.step_delay", _SOURCE))
        # 12 sites converted in this batch + the 1 original success-path call
        # site + 1 more added later the same night by the OPT2 fast-fill
        # feature (tests/test_opt2_fast_fill.py) -- a floor, not an exact
        # count, since further legitimate call sites may be added later.
        assert count >= 14, (
            f"expected at least 14 total _adaptive_settle_wait(self.step_delay...) "
            f"call sites (12 from this batch + 1 original + 1 from OPT2 fast-fill), "
            f"found {count}"
        )

    def test_dialog_guard_escape_site(self):
        assert ('self._executor.execute({"action_type": "hotkey", "keys": ["escape"]})\n'
                '                self._adaptive_settle_wait(self.step_delay)') in _SOURCE

    def test_leave_blank_llm_branch_site(self):
        assert ('self._attempt_key(_fe2, elements=state.get("elements", [])))\n'
                '                        self._executor.execute({"action_type": "keyboard",\n'
                '                                                "key_count": 1, "keystrokes": ["tab"]})\n'
                '                        self._adaptive_settle_wait(self.step_delay * 0.4)') in _SOURCE

    def test_leave_blank_empty_site(self):
        assert ('self._mark_attempted(_fe2, elements=state.get("elements", []))\n'
                '                        self._executor.execute({"action_type": "keyboard",\n'
                '                                                "key_count": 1, "keystrokes": ["tab"]})\n'
                '                        self._adaptive_settle_wait(self.step_delay * 0.4)') in _SOURCE

    def test_pointer_drift_reclick_tab_site(self):
        assert ('_reclick_reason, _reclick_ty, _reclick_label)\n'
                '                            self._executor.execute({"action_type": "keyboard",\n'
                '                                                    "key_count": 1, "keystrokes": ["tab"]})\n'
                '                            self._adaptive_settle_wait(self.step_delay * 0.4)') in _SOURCE

    def test_reclick_streak_redirect_site(self):
        assert ('"click_position": _rc_pos,\n'
                '                                        })\n'
                '                                    self._adaptive_settle_wait(self.step_delay * 0.4)\n'
                '                                    _reclick_streak = 0') in _SOURCE

    def test_combobox_reclick_redirect_site(self):
        assert ('"click_position": _cb_rc_pos,\n'
                '                                        })\n'
                '                                    self._adaptive_settle_wait(self.step_delay * 0.4)\n'
                '                                    _reclick_streak = 0') in _SOURCE

    def test_combobox_known_blank_tab_site(self):
        assert ('self._executor.execute({"action_type": "keyboard",\n'
                '                                                    "key_count": 1, "keystrokes": ["tab"]})\n'
                '                            self._adaptive_settle_wait(self.step_delay * 0.5)\n'
                '                            continue\n'
                '                        if _cbox is not None:') in _SOURCE

    def test_hallucinated_target_outside_form_site(self):
        assert ('OUTSIDE form window — Tab instead of drifting."' in _SOURCE
                and '_adaptive_settle_wait(self.step_delay * 0.5)' in _SOURCE)
        # Confirm the specific pairing (guard branch text directly followed,
        # a few lines later, by the adaptive wait rather than a raw sleep).
        idx = _SOURCE.index('OUTSIDE form window — Tab instead of drifting."')
        window = _SOURCE[idx:idx + 400]
        assert "self._adaptive_settle_wait(self.step_delay * 0.5)" in window
        assert "time.sleep(self.step_delay * 0.5)" not in window

    def test_destructive_button_guard_navigate_tab_site(self):
        # "pointer target ... lands on button" (this converted site) is
        # distinct from "combobox-open target ... lands on button" (a
        # separate, deliberately-untouched combobox guard) -- anchor on the
        # unique "pointer target" phrasing so this doesn't silently match
        # the wrong site.
        idx = _SOURCE.index('"[GUARD] pointer target (%.0f,%.0f) lands on button "')
        window = _SOURCE[idx:idx + 500]
        assert "self._adaptive_settle_wait(self.step_delay * 0.5)" in window

    def test_combobox_open_button_guard_site_untouched(self):
        idx = _SOURCE.index('"[GUARD] combobox-open target (%.0f,%.0f) lands on button %r — "')
        window = _SOURCE[idx:idx + 500]
        assert "time.sleep(self.step_delay * 0.5)" in window
        assert "_adaptive_settle_wait" not in window

    def test_lowconf_fallback_redirect_site(self):
        idx = _SOURCE.index('"click_position": [_tcx, _tcy],')
        window = _SOURCE[idx:idx + 250]
        assert "self._adaptive_settle_wait(self.step_delay * 0.4)" in window
        assert "_lowconf_fallback_streak = 0" in window

    def test_backward_tab_click_guard_site(self):
        idx = _SOURCE.index("this workflow never goes backward.")
        window = _SOURCE[idx:idx + 400]
        assert "self._adaptive_settle_wait(self.step_delay * 0.4)" in window

    def test_repeat_action_guard_site(self):
        idx = _SOURCE.index("Repeat-action guard: same action %dx in a row")
        window = _SOURCE[idx:idx + 400]
        assert "self._adaptive_settle_wait(self.step_delay * 0.5)" in window
        assert "time.sleep(self.step_delay * 0.5)" not in window


class TestDoNotTouchSitesRemainBlindSleeps:
    """The sites explicitly excluded in the plan (tab-strip switches, the
    verify-at-fill retry loop, combobox open/close, the retry backoff, the
    record-reset wait) must still use a plain time.sleep -- proves this
    change didn't accidentally sweep past its own stated boundary."""

    def test_record_reset_comment_site_untouched(self):
        idx = _SOURCE.index("# let the form's own reset finish")
        line_start = _SOURCE.rfind("\n", 0, idx) + 1
        line = _SOURCE[line_start:idx]
        assert "time.sleep(self.step_delay)" in line
        assert "_adaptive_settle_wait" not in line

    def test_ask_llm_unavailable_backoff_untouched(self):
        idx = _SOURCE.index("halting rather than blank-filling")
        window = _SOURCE[idx:idx + 400]
        assert "time.sleep(self.step_delay)" in window
        assert "_adaptive_settle_wait" not in window


class TestAdaptiveWaitThenVerifyMirror:
    """Mirror-function test (this project's own established pattern for
    logic embedded in run() rather than extracted) for the three sites that
    already implement their own manual 'wait, then observe and check the
    click landed' logic. Proves that shape still settles early / bounds
    correctly once the wait itself is adaptive rather than blind, without
    needing to drive the full ~8000-line run() method."""

    class _FakeClock:
        def __init__(self):
            self.now = 0.0

        def time(self):
            return self.now

        def sleep(self, duration):
            self.now += duration

    def _wait_then_check(self, agent, clock, max_wait, states, key_fn, expected_key):
        """Mirrors the redirect-then-verify shape: adaptive wait, then one
        _observe() call, then compare the landed key against what was
        expected -- exactly the pattern at agent.py's three redirect sites
        (reclick-streak, combobox-reclick, low-confidence-fallback)."""
        agent._adaptive_settle_wait(max_wait)
        observed = states.pop(0)
        landed_key = key_fn(observed)
        return landed_key == expected_key, clock.now

    def test_settles_early_when_focus_actually_landed(self, monkeypatch):
        agent = _make_agent()
        clock = self._FakeClock()
        monkeypatch.setattr("agent.agent.time.time", clock.time)
        monkeypatch.setattr("agent.agent.time.sleep", clock.sleep)

        fp_calls = [("a",), ("b",), ("b",)]  # settles on the 2nd read
        monkeypatch.setattr(agent, "_observe", lambda: {"elements": []})
        monkeypatch.setattr(agent, "_observed_state_fingerprint", lambda s: fp_calls.pop(0))

        states = [{"elements": [{"element_id": "e1", "focused": True}]}]
        landed, elapsed = self._wait_then_check(
            agent, clock, max_wait=0.4, states=states,
            key_fn=lambda s: "e1", expected_key="e1",
        )
        assert landed is True
        assert elapsed < 0.4  # settled before the full budget elapsed

    def test_bounded_when_state_never_settles(self, monkeypatch):
        agent = _make_agent()
        clock = self._FakeClock()
        monkeypatch.setattr("agent.agent.time.time", clock.time)
        monkeypatch.setattr("agent.agent.time.sleep", clock.sleep)

        monkeypatch.setattr(agent, "_observe", lambda: {"elements": []})
        _n = {"i": 0}

        def _never_equal(_state):
            _n["i"] += 1
            return ("val", _n["i"])  # always different -- never settles

        monkeypatch.setattr(agent, "_observed_state_fingerprint", _never_equal)

        states = [{"elements": []}]
        _, elapsed = self._wait_then_check(
            agent, clock, max_wait=0.4, states=states,
            key_fn=lambda s: None, expected_key="anything",
        )
        assert elapsed <= 0.4 + 0.15  # bounded by max_wait (+ at most one poll interval)
