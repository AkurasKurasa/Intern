"""
Regression test for agent.py's tab-click routing -- a click landing on the
NEXT tab (one ahead of the current one, already allowed through by the
forward-skip guard) must still be blocked if the CURRENT tab genuinely has
unfilled content remaining.

Found 2026-08-09, live, direct user report ("that was a good Policyholder
-> Payment, problem is we skipped so much fucking input fields"). Log
evidence: on Coverage, only 8 fields got filled (Bodily Injury, Property
Damage, MedPay Limit, Collision/Comprehensive Deductible, Rental Limit,
UM/UIM Limit, one checkbox) before the transformer's own raw pointer
happened to click the Drivers tab header directly. Since Drivers (idx 4)
is exactly one tab ahead of Coverage (idx 3), the forward-skip guard
(added for the PREVIOUS report -- "Coverage was skipped" -- which blocked
jumps of MORE than one tab) correctly did not fire. But nothing else
verified Coverage was actually done: Roadside Assistance, GAP Insurance,
Accident Forgiveness, Diminishing Deductible, Total Premium, and others
were still sitting there, unfilled, when the tab switched anyway.

The ONLY existing path that verifies "is this tab actually done" is the
separate stuck-guard / Navigation-Protocol advance flow -- which worked
correctly for every OTHER tab transition in this same run. This raw
tab-strip-click path bypassed that check entirely.

Fixed by checking, before allowing ANY tab-strip click through (even to
the immediately-next tab), whether the current tab still has a real,
unattempted, empty target anywhere (unbounded search, on- or off-screen).
If so, redirect there instead of letting the tab switch happen.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import find_visible_empty_target


def _tab(name, bbox):
    return {"type": "tabitemcontrol", "text": name, "label": name, "bbox": list(bbox),
            "window_role": "active"}


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol"):
    return {"element_id": label, "type": ftype, "label": label, "value": value,
            "bbox": list(bbox), "window_role": "active"}


def _route_tab_click_verified(all_tabs, click_pos, current_tab_idx, state, executor, focus_via_uia_fn):
    """Mirrors the CURRENT (2026-08-09) tab-click routing in agent.py's
    run(), the "allowed forward" branch: before trusting a click on the
    immediately-next tab, verifies the CURRENT tab is actually exhausted."""
    tab_hit = next(
        (e for e in all_tabs
         if e["bbox"][0] <= click_pos[0] <= e["bbox"][2]
         and e["bbox"][1] <= click_pos[1] <= e["bbox"][3]),
        None)
    if tab_hit is None:
        return None

    sorted_tabs = sorted(all_tabs, key=lambda e: e["bbox"][0])
    hit_idx = sorted_tabs.index(tab_hit)
    hit_name = tab_hit["text"]

    if hit_idx < current_tab_idx:
        return "blocked_backward"
    if hit_idx > current_tab_idx + 1:
        return "redirected_to_next"
    if hit_idx != current_tab_idx:
        remaining = find_visible_empty_target(state, 1e9)
        if remaining and remaining.get("bbox"):
            rem_label = (remaining.get("label") or remaining.get("text") or "").strip()
            if not (rem_label and focus_via_uia_fn(rem_label)):
                executor.execute({"action_type": "click", "click_position": remaining["bbox"]})
            return "redirected_to_remaining_field"
        x1, y1, x2, y2 = tab_hit["bbox"]
        executor.execute({"action_type": "click", "click_position": [(x1 + x2) / 2, (y1 + y2) / 2]})
        return "navigated"
    return "stayed"


_TABS = [
    _tab("Coverage", (430, 100, 530, 130)),
    _tab("Drivers", (540, 100, 640, 130)),
]


class TestPrematureTabExitIsBlocked:
    def test_reproduces_the_live_regression_coverage_still_has_unfilled_fields(self):
        """The exact live incident: Drivers is only one tab ahead
        (allowed by the forward-skip guard), but Coverage still has real,
        unattempted, empty fields -- must redirect there, not switch."""
        state = {"elements": [
            _field("Bodily Injury (k$/k$)", value="100/300", bbox=(100, 100, 300, 130), ftype="comboboxcontrol"),
            _field("Roadside Assistance", value="", bbox=(100, 200, 300, 230), ftype="checkboxcontrol"),
            _field("Total Premium ($)", value="", bbox=(100, 240, 300, 270)),
        ]}
        executor = MagicMock()
        focus_via_uia = MagicMock(return_value=True)

        outcome = _route_tab_click_verified(
            _TABS, click_pos=[590, 115], current_tab_idx=0,
            state=state, executor=executor, focus_via_uia_fn=focus_via_uia)

        assert outcome == "redirected_to_remaining_field"
        focus_via_uia.assert_called_once_with("Roadside Assistance")

    def test_genuinely_exhausted_tab_still_allows_the_switch(self):
        """The common, correct case: nothing left to fill -- the switch to
        the next tab must proceed exactly as before."""
        state = {"elements": [
            _field("Bodily Injury (k$/k$)", value="100/300", bbox=(100, 100, 300, 130), ftype="comboboxcontrol"),
            _field("Total Premium ($)", value="187.42", bbox=(100, 240, 300, 270)),
        ]}
        executor = MagicMock()
        focus_via_uia = MagicMock(return_value=True)

        outcome = _route_tab_click_verified(
            _TABS, click_pos=[590, 115], current_tab_idx=0,
            state=state, executor=executor, focus_via_uia_fn=focus_via_uia)

        assert outcome == "navigated"
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "click", "click_position": [590.0, 115.0]}]
