"""
Regression tests for LLMAgent._resolve_control_via_point().

Found live 2026-08-14, direct request ("any more improvements"): after
_resolve_field_control's disambiguation-skip fix shipped, a real run's
total time barely moved. The SAME log showed why -- checkboxes (already
resolving their control via WindowFromPoint, a plain screen-coordinate
lookup, never a UIA accessible-Name SEARCH) landed 3 fields within about
a second; editcontrol/comboboxcontrol fields right next to them in the
SAME batch, still doing a UIA name-search even after the disambiguation
fix, landed roughly 1/second. This extends the checkbox mechanism to
text/combobox fields: resolve the control directly from its own bbox
center via WindowFromPoint (one cheap Win32 call), wrap it via UIA's
ControlFromHandle (cheap -- no search, just wraps a handle already in
hand), and verify its native window class AND accessible Name before
trusting it -- so a stale bbox or an unexpected control under that point
can't silently cause a write into the wrong field. Falls back to the
proven UIA-search path (_find_uia_control_by_name) on any mismatch.

Uses this project's established injectable-dependency pattern (same
shape as executor.py's `user32=None` parameters on _try_semantic_click /
_keyboard_direct / _combobox_direct) rather than faking sys.modules --
ctypes.windll.user32 is a live, C-backed, process-wide object, unsafe to
patch in place in a test.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _make_agent():
    return LLMAgent(goal="test goal", dry_run=True, max_steps=1)


class _FakeControl:
    def __init__(self, name):
        self.Name = name


def _fake_deps(hwnd=12345, class_name="Edit", ctrl_name="Policy Number"):
    fake_uia = MagicMock()
    fake_uia.ControlFromHandle = MagicMock(return_value=_FakeControl(ctrl_name))

    fake_win32gui = MagicMock()
    fake_win32gui.WindowFromPoint = MagicMock(return_value=hwnd)

    fake_user32 = MagicMock()

    def _fake_get_class_name(hwnd_arg, buf, size):
        buf.value = class_name
        return len(class_name)
    fake_user32.GetClassNameW.side_effect = _fake_get_class_name

    return fake_uia, fake_win32gui, fake_user32


def test_resolves_a_matching_edit_control_via_window_from_point():
    agent = _make_agent()
    fake_uia, fake_win32gui, fake_user32 = _fake_deps(class_name="Edit", ctrl_name="Policy Number")

    result = agent._resolve_control_via_point(
        (100, 100, 300, 130), "Policy Number",
        uia_mod=fake_uia, win32gui_mod=fake_win32gui, user32=fake_user32)

    assert result is not None
    assert result.Name == "Policy Number"
    fake_win32gui.WindowFromPoint.assert_called_once_with((200, 115))


def test_resolves_a_matching_combobox_control():
    agent = _make_agent()
    fake_uia, fake_win32gui, fake_user32 = _fake_deps(class_name="ComboBox", ctrl_name="Policy Status")

    result = agent._resolve_control_via_point(
        (0, 0, 100, 30), "Policy Status",
        uia_mod=fake_uia, win32gui_mod=fake_win32gui, user32=fake_user32)

    assert result is not None
    assert result.Name == "Policy Status"


def test_wrong_class_name_returns_none_not_a_wrong_write_target():
    """WindowFromPoint landing on something that isn't a real edit/combo
    control (e.g. a parent panel) must be rejected, not trusted."""
    agent = _make_agent()
    fake_uia, fake_win32gui, fake_user32 = _fake_deps(class_name="Panel", ctrl_name="Policy Number")

    result = agent._resolve_control_via_point(
        (100, 100, 300, 130), "Policy Number",
        uia_mod=fake_uia, win32gui_mod=fake_win32gui, user32=fake_user32)

    assert result is None
    fake_uia.ControlFromHandle.assert_not_called()


def test_name_mismatch_returns_none_not_the_wrong_field():
    """The core safety guarantee -- if the resolved control's own
    accessible Name doesn't match what we asked for (stale bbox, drifted
    layout, wrong point), never silently write into it."""
    agent = _make_agent()
    fake_uia, fake_win32gui, fake_user32 = _fake_deps(class_name="Edit", ctrl_name="Some Other Field")

    result = agent._resolve_control_via_point(
        (100, 100, 300, 130), "Policy Number",
        uia_mod=fake_uia, win32gui_mod=fake_win32gui, user32=fake_user32)

    assert result is None


def test_name_match_is_case_and_whitespace_insensitive():
    agent = _make_agent()
    fake_uia, fake_win32gui, fake_user32 = _fake_deps(class_name="Edit", ctrl_name="  policy number  ")

    result = agent._resolve_control_via_point(
        (100, 100, 300, 130), "Policy Number",
        uia_mod=fake_uia, win32gui_mod=fake_win32gui, user32=fake_user32)

    assert result is not None


def test_no_window_at_that_point_returns_none():
    agent = _make_agent()
    fake_uia, fake_win32gui, fake_user32 = _fake_deps(hwnd=0)

    result = agent._resolve_control_via_point(
        (100, 100, 300, 130), "Policy Number",
        uia_mod=fake_uia, win32gui_mod=fake_win32gui, user32=fake_user32)

    assert result is None


def test_missing_bbox_returns_none_without_any_win32_call():
    agent = _make_agent()
    fake_uia, fake_win32gui, fake_user32 = _fake_deps()

    result = agent._resolve_control_via_point(
        None, "Policy Number",
        uia_mod=fake_uia, win32gui_mod=fake_win32gui, user32=fake_user32)

    assert result is None
    fake_win32gui.WindowFromPoint.assert_not_called()


def test_empty_label_returns_none_without_any_win32_call():
    agent = _make_agent()
    fake_uia, fake_win32gui, fake_user32 = _fake_deps()

    result = agent._resolve_control_via_point(
        (100, 100, 300, 130), "",
        uia_mod=fake_uia, win32gui_mod=fake_win32gui, user32=fake_user32)

    assert result is None
    fake_win32gui.WindowFromPoint.assert_not_called()


def test_exception_from_windowfrompoint_is_swallowed_not_raised():
    agent = _make_agent()
    fake_uia, fake_win32gui, fake_user32 = _fake_deps()
    fake_win32gui.WindowFromPoint.side_effect = OSError("invalid point")

    result = agent._resolve_control_via_point(
        (100, 100, 300, 130), "Policy Number",
        uia_mod=fake_uia, win32gui_mod=fake_win32gui, user32=fake_user32)

    assert result is None


def test_uses_the_bbox_center_not_the_top_left_corner():
    agent = _make_agent()
    fake_uia, fake_win32gui, fake_user32 = _fake_deps()

    agent._resolve_control_via_point(
        (0, 0, 200, 100), "Policy Number",
        uia_mod=fake_uia, win32gui_mod=fake_win32gui, user32=fake_user32)

    fake_win32gui.WindowFromPoint.assert_called_once_with((100, 50))
