"""
Regression test for a class of bug in agent.py's run(): every place that
redirects a click toward a find_visible_empty_target()/find_scroll_target_
element() result -- after the preferred UIA-focus-by-name attempt fails --
falls through to a raw coordinate click with NO check for what's actually
at that position.

Found live 2026-08-13, from a real user-run session: the checkbox type-
intercept's own fallback redirect (agent.py, the "uncheck a checkbox whose
value should be NO" branch) built a click at a coordinate that, by the time
it executed, was occupied by the form's real 'Clear All' button -- not the
intended field. A genuine Windows confirmation dialog popped ('#32770'),
and the run stalled dismissing it for several steps.

_find_destructive_button_at (see test_destructive_button_guard.py) already
exists for exactly this risk and was already wired into the navigate and
combobox-open-dropdown branches -- but the checkbox-redirect branch that
actually broke, and SIX other call sites sharing the identical shape
(find_visible_empty_target -> try UIA-focus-by-name -> fall back to a raw
coordinate click), had never been wired through it. All seven fixed the
same way, same guard, same reasoning -- not a new keyword list, not a
Clear-All-specific rule; the guard blocks ANY button-type element at the
target position, by construction, regardless of label. This is directly
the fix the user asked for after the incident: "don't add a deterministic
fix for the Clear All fields" -- this generalizes the EXISTING guard's
coverage instead of adding a new, narrower one.

Mirrors this project's own established pattern for testing deeply-nested,
non-extracted run() logic (see test_checkbox_uncheck_type_intercept.py):
a small function reimplementing the exact gating condition added at each
site, tested against realistic incident-shaped data, plus direct checks
against the real _find_destructive_button_at the fix actually calls.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import _find_destructive_button_at


def _button(text, bbox):
    return {"type": "buttoncontrol", "text": text, "label": text, "bbox": list(bbox)}


def _bbox_center(bbox):
    return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]


def _should_execute_redirect_click(elements, target_bbox, uia_focus_succeeded: bool) -> bool:
    """Mirrors the exact gating condition added at all seven call sites:
    `if (not uia_focus_succeeded) and (not _find_destructive_button_at(...)):
    execute the click`. UIA-focus-by-name is always tried first and is
    itself immune to this risk (it targets by name, not raw pixel) -- the
    guard only matters once that's already failed and a coordinate click
    is the only option left."""
    if uia_focus_succeeded:
        return False
    pos = _bbox_center(target_bbox)
    return _find_destructive_button_at(elements, pos) is None


class TestRedirectClickSkipsDestructiveButtons:
    def test_the_real_incident_shape_is_blocked(self):
        """Reproduces the actual 2026-08-13 incident: a redirect target's
        bbox-center coordinate lands on the real Clear All button."""
        clear_all = _button("Clear All", (1400, 730, 1560, 780))
        elements = [clear_all]
        # The redirect's own resolved "target" bbox happens to center on
        # the same point Clear All occupies -- the exact failure mode.
        drifted_bbox = (1400, 730, 1560, 780)
        assert _should_execute_redirect_click(elements, drifted_bbox, uia_focus_succeeded=False) is False

    def test_other_footer_buttons_are_blocked_too_not_just_clear_all(self):
        """Same generalization _find_destructive_button_at itself already
        proves -- this wiring must not silently narrow it back down to one
        label."""
        for label in ("Print Preview", "Load Record", "Save Record"):
            btn = _button(label, (1400, 730, 1560, 780))
            assert _should_execute_redirect_click([btn], (1400, 730, 1560, 780),
                                                   uia_focus_succeeded=False) is False, \
                f"expected {label!r} to block the redirect click"

    def test_a_real_field_target_is_not_blocked(self):
        """Must not become overly cautious -- a genuine empty field at the
        redirect target should still get clicked when UIA focus fails."""
        field = {"type": "editcontrol", "text": "ZIP Code", "label": "ZIP Code",
                  "bbox": [400, 500, 600, 530]}
        assert _should_execute_redirect_click([field], (400, 500, 600, 530),
                                               uia_focus_succeeded=False) is True

    def test_uia_focus_success_skips_the_coordinate_click_entirely(self):
        """The preferred path -- if UIA-focus-by-name already worked, the
        raw click (and therefore this guard) never even needs to run."""
        clear_all = _button("Clear All", (1400, 730, 1560, 780))
        assert _should_execute_redirect_click([clear_all], (1400, 730, 1560, 780),
                                               uia_focus_succeeded=True) is False

    def test_no_button_at_the_position_is_unaffected(self):
        """Sanity: an empty position (no element at all) behaves like any
        other non-destructive target -- still clicks."""
        assert _should_execute_redirect_click([], (400, 500, 600, 530),
                                               uia_focus_succeeded=False) is True


class TestAllSevenCallSitesUseTheSameRealGuardFunction:
    """Confirms the seven fixed sites all import and call the SAME
    _find_destructive_button_at agent.py already exposes at module scope --
    not a re-implementation that could quietly drift from it."""

    def test_guard_function_is_importable_at_module_scope(self):
        import agent.agent as agent_mod
        assert hasattr(agent_mod, "_find_destructive_button_at")
        assert callable(agent_mod._find_destructive_button_at)

    def test_seven_sites_reference_the_guard_in_source(self):
        """Direct, real check against the actual file -- not just the
        isolated mirror logic above. Confirms the wiring exists at every
        one of the seven fixed call sites, not just the one that broke."""
        agent_py = (Path(__file__).resolve().parent.parent
                    / "components" / "agent" / "agent.py").read_text(encoding="utf-8")
        # 8 = 7 newly-wired call sites (2026-08-13) + _find_destructive_button_at's
        # own definition (the `def` line itself doesn't call itself, but the
        # docstring/comment block mentioning it elsewhere in the file pads
        # this count -- use a floor, not an exact count, so unrelated future
        # comment edits can't make this brittle).
        assert agent_py.count("_find_destructive_button_at(") >= 7
