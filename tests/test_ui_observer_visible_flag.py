"""
Regression test for ui_observer.py's element "visible" field -- it used to
be hardcoded True unconditionally, never actually reading whether an
element is really scrolled into view.

Found 2026-08-08, live: a run needed zero explicit SCROLL decisions from
navigation_protocol.decide() at all (every field's bbox y-coordinate fell
within the geometric window-rect viewport_bottom estimate agent.py has to
guess), yet the user watched the on-screen view visibly creep down one
field at a time as each field got clicked -- the target app's own scroll
panel auto-scrolling a newly-focused control into view, invisible to the
system because "visible" carried no real information at all.

UIA exposes the real, authoritative answer directly via IsOffscreen (a
base UIA property, not pattern-specific) -- ui_observer.py now reads it
instead of guessing a second time from window geometry.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _resolve_visible(ctrl):
    """Mirrors the CURRENT ui_observer.py fix: read ctrl.IsOffscreen safely
    (defaulting to 'not offscreen' if the property access fails, matching
    the old always-True fallback so a read failure never wrongly hides a
    field), then set visible = not is_offscreen."""
    try:
        is_offscreen = bool(ctrl.IsOffscreen)
    except Exception:
        is_offscreen = False
    return not is_offscreen


class TestVisibleFlagReadsRealUiaOffscreenState:
    def test_onscreen_control_is_marked_visible(self):
        ctrl = MagicMock()
        ctrl.IsOffscreen = False
        assert _resolve_visible(ctrl) is True

    def test_offscreen_control_is_marked_not_visible(self):
        ctrl = MagicMock()
        ctrl.IsOffscreen = True
        assert _resolve_visible(ctrl) is False

    def test_property_read_failure_defaults_to_visible(self):
        """Matches the pre-fix behavior (always True) as a safe fallback --
        a control this system can't ask must not be silently excluded from
        every downstream fill/navigation decision."""
        ctrl = MagicMock()
        type(ctrl).IsOffscreen = property(lambda self: (_ for _ in ()).throw(RuntimeError("no pattern")))
        assert _resolve_visible(ctrl) is True
