"""
Regression tests for agent.py's _combobox_dropdown_arrow_pos and its two
call sites (the click-fill branch and the type-into-combobox branch).

Found live 2026-08-14, investigating a real emergency-stop incident: the
run was healthy for 85 clean steps, then hit 'Primary Use' -- a combobox
whose dropdown "never renders" per this file's own long-documented history
(see the 2026-08-09 comment on this exact field: "the REAL cause of the
dropdown never rendering is still open... not something more patience
fixes"). The user killed the run rather than watch it repeat the same
multi-second stall.

Direct UIA inspection of the real, live practice form (not assumed) found:
every combobox exposes its actual dropdown toggle as a SEPARATE
ButtonControl with a real InvokePattern, flush against the combobox's own
right edge (~19px wide, measured directly) -- the combobox control itself
only exposes ValuePattern, no ExpandCollapsePattern. The click-open
mechanism has always clicked dead center of the combobox's own bbox,
gambling on the whole control responding like a button. Center-clicking
happens to work for most comboboxes (confirmed: 'Fuel Type', structurally
IDENTICAL to 'Primary Use' in every UIA property checked, opens fine) --
but it isn't the semantically correct target.

This is NOT presented as a confirmed fix for why 'Primary Use' specifically
fails while structurally-identical comboboxes don't -- that root cause
remains genuinely open, ruled out after checking UIA patterns, bbox
structure, choices-list length, and the widget-creation source code, all
identical to a working combobox. This is a real, generalizing hardening
(the same "use semantic, not precise" principle already shipped for
buttons via executor.py's BM_CLICK tier) found along the way: target the
control UIA says actually toggles the dropdown, for every combobox, not a
guess about one field's specific failure.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _make_agent():
    return LLMAgent(goal="test goal", dry_run=True, max_steps=1, step_delay=0)


class TestArrowPosGeometry:
    def test_targets_the_right_edge_not_the_center(self):
        agent = _make_agent()
        bbox = [1039, 744, 1863, 773]   # real 'Primary Use' bbox, measured live
        pos = agent._combobox_dropdown_arrow_pos(bbox)
        center_x = (bbox[0] + bbox[2]) / 2
        assert pos[0] > center_x, "must be biased toward the right edge, not the center"

    def test_lands_inside_the_real_measured_arrow_button_region(self):
        """Direct check against the real, live-measured button bbox
        (1843, 745, 1862, 771) found via UIA inspection of the actual form
        -- not a guess at the offset."""
        agent = _make_agent()
        bbox = [1039, 744, 1863, 773]
        pos = agent._combobox_dropdown_arrow_pos(bbox)
        assert 1843 <= pos[0] <= 1862

    def test_y_stays_vertically_centered(self):
        agent = _make_agent()
        bbox = [1039, 744, 1863, 773]
        pos = agent._combobox_dropdown_arrow_pos(bbox)
        assert pos[1] == pytest.approx((744 + 773) / 2)

    def test_never_produces_a_position_left_of_the_bbox(self):
        """A very narrow combobox shouldn't push the target outside its
        own bounds -- clamped to the bbox's own left edge as a floor."""
        agent = _make_agent()
        bbox = [100, 100, 108, 120]   # 8px wide, narrower than the 15px offset
        pos = agent._combobox_dropdown_arrow_pos(bbox)
        assert pos[0] >= 100

    def test_generic_across_different_bbox_sizes_not_one_hardcoded_field(self):
        """Same geometry rule regardless of which field's bbox is passed --
        confirms this is bbox-driven, not name-driven."""
        agent = _make_agent()
        bbox_a = [500, 200, 900, 230]    # some other field's plausible bbox
        bbox_b = [1039, 744, 1863, 773]  # 'Primary Use'
        pos_a = agent._combobox_dropdown_arrow_pos(bbox_a)
        pos_b = agent._combobox_dropdown_arrow_pos(bbox_b)
        assert pos_a[0] == pytest.approx(bbox_a[2] - 15)
        assert pos_b[0] == pytest.approx(bbox_b[2] - 15)


class TestBothCallSitesUseTheArrowPosition:
    """Direct, real check against the actual file -- confirms both the
    click-fill branch and the type-into-combobox branch actually call
    _combobox_dropdown_arrow_pos before their open-click, not a
    reimplementation that could drift, and not reverted back to a bare
    center-click by a future edit without this test catching it."""

    def _agent_py_source(self) -> str:
        return (Path(__file__).resolve().parent.parent
                / "components" / "agent" / "agent.py").read_text(encoding="utf-8")

    def test_click_fill_branch_uses_arrow_position(self):
        src = self._agent_py_source()
        anchor = src.index("if not _open_dropdown_items(state.get(\"elements\", [])):")
        window = src[anchor:anchor + 400]
        assert "_combobox_dropdown_arrow_pos(_cbox[\"bbox\"])" in window

    def test_type_into_combobox_branch_uses_arrow_position(self):
        src = self._agent_py_source()
        anchor = src.index("Combobox: clicking to open dropdown for")
        window = src[anchor:anchor + 400]
        assert "_combobox_dropdown_arrow_pos(_fel[\"bbox\"])" in window

    def test_not_hardcoded_to_a_specific_field_name(self):
        import re
        src = self._agent_py_source()
        # The helper's own definition/call sites must be bbox-driven only --
        # no literal field-name branch anywhere near the new code.
        anchor = src.index("_combobox_dropdown_arrow_pos(bbox)")
        fn_window = src[anchor:anchor + 1400]
        assert not re.search(r'==\s*[\'"]Primary Use[\'"]', fn_window)
        assert not re.search(r'if\s+\w+\s*==\s*[\'"][^\'"]+[\'"]', fn_window), \
            "found a field-name equality check inside the arrow-pos helper"
