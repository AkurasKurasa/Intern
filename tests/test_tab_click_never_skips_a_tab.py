"""
Regression test for agent.py's tab-click routing -- a click landing on the
tab strip must never skip over an unvisited tab, forward OR backward.

Found 2026-08-09, live, direct user report ("Coverage was skipped, you're
making me cry, you know that?"). Log evidence: while on Vehicle (idx 2),
the transformer's own raw pointer clicked directly on Drivers (idx 4) --
"Tab-click -> navigating to 'Drivers' (idx 4)" -- and Coverage (idx 3) was
never visited at all; not one of its fields (Bodily Injury, MedPay Limit,
UM/UIM Limit, ...) appears anywhere in the log before that jump.

An existing guard already blocked BACKWARD tab clicks (idx < current), added
2026-08-08 for a different live incident ("it jumped to VIN damn"). But
nothing blocked a click landing MORE THAN ONE TAB AHEAD either -- the code
deliberately routes to whichever tab the model actually clicked (not a
blind current+1) to avoid a real, different bug where repeated clicks
landing on one tab raced through every tab in between. That fix had no
upper bound, so a raw pointer click that happens to land past the
immediately-next tab skips everything in between with nothing noticing.

Fixed by adding a matching forward guard: a click landing more than one tab
ahead of the current one redirects to the immediately NEXT tab instead,
preserving the model's forward intent without letting it skip anything.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _tab(name, bbox):
    return {"type": "tabitemcontrol", "text": name, "label": name, "bbox": list(bbox),
            "window_role": "active"}


def _route_tab_click(all_tabs, click_pos, current_tab_idx, executor):
    """Mirrors the CURRENT (2026-08-09) tab-click routing in agent.py's
    run(): blocks backward clicks (existing), now also blocks forward
    clicks that skip an unvisited tab, redirecting to current+1 instead."""
    tab_hit = next(
        (e for e in all_tabs
         if e["bbox"][0] <= click_pos[0] <= e["bbox"][2]
         and e["bbox"][1] <= click_pos[1] <= e["bbox"][3]),
        None)
    if tab_hit is None:
        return None, current_tab_idx

    sorted_tabs = sorted(all_tabs, key=lambda e: e["bbox"][0])
    hit_idx = sorted_tabs.index(tab_hit)
    hit_name = tab_hit["text"]

    if hit_idx < current_tab_idx:
        executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
        return "blocked_backward", current_tab_idx

    if hit_idx > current_tab_idx + 1:
        next_tab = sorted_tabs[current_tab_idx + 1]
        nx1, ny1, nx2, ny2 = next_tab["bbox"]
        executor.execute({"action_type": "click",
                           "click_position": [(nx1 + nx2) / 2, (ny1 + ny2) / 2]})
        return "redirected_to_next", current_tab_idx + 1

    if hit_idx != current_tab_idx:
        x1, y1, x2, y2 = tab_hit["bbox"]
        executor.execute({"action_type": "click", "click_position": [(x1 + x2) / 2, (y1 + y2) / 2]})
        return "navigated", hit_idx

    return "stayed", current_tab_idx


_TABS = [
    _tab("Policy", (100, 100, 200, 130)),
    _tab("Policyholder", (210, 100, 310, 130)),
    _tab("Vehicle", (320, 100, 420, 130)),
    _tab("Coverage", (430, 100, 530, 130)),
    _tab("Drivers", (540, 100, 640, 130)),
]


class TestForwardTabSkipIsBlocked:
    def test_reproduces_the_live_regression_vehicle_to_drivers_skips_coverage(self):
        """The exact live incident: on Vehicle (idx 2), pointer clicks
        Drivers (idx 4) directly -- must redirect to Coverage (idx 3)
        instead of trusting the skip."""
        executor = MagicMock()
        outcome, new_idx = _route_tab_click(
            _TABS, click_pos=[590, 115], current_tab_idx=2, executor=executor)

        assert outcome == "redirected_to_next"
        assert new_idx == 3  # Coverage, not Drivers
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert len(calls) == 1
        assert calls[0]["action_type"] == "click"
        # Coverage tab's own bbox center
        assert calls[0]["click_position"] == [480.0, 115.0]

    def test_clicking_the_immediately_next_tab_still_works_normally(self):
        """The legitimate, common case (advancing exactly one tab) must be
        completely unaffected."""
        executor = MagicMock()
        outcome, new_idx = _route_tab_click(
            _TABS, click_pos=[480, 115], current_tab_idx=2, executor=executor)

        assert outcome == "navigated"
        assert new_idx == 3

    def test_reclicking_the_current_tab_does_not_move_at_all(self):
        """The scenario the original 'route to whichever tab was actually
        clicked' fix was built to protect: repeated clicks on the SAME tab
        must not advance anywhere, forward guard included."""
        executor = MagicMock()
        outcome, new_idx = _route_tab_click(
            _TABS, click_pos=[370, 115], current_tab_idx=2, executor=executor)

        assert outcome == "stayed"
        assert new_idx == 2
        executor.execute.assert_not_called()


class TestBackwardTabClickStillBlocked:
    def test_backward_click_is_still_blocked_unaffected_by_the_new_guard(self):
        executor = MagicMock()
        outcome, new_idx = _route_tab_click(
            _TABS, click_pos=[150, 115], current_tab_idx=3, executor=executor)

        assert outcome == "blocked_backward"
        assert new_idx == 3
