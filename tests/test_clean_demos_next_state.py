"""
Regression tests for scripts/clean_demos.py's next_state resolution.

Bug: current-format recordings (post ~May 2026) don't carry a literal
`next_state` field anymore (removed as redundant — it's just the next
step's own `state`). clean_demos.py used to default it to {} whenever the
key was missing, which made elem_at() unable to resolve any click's
target element, so every mouse-driven step got classified as junk and
dropped — only typing survived. See docs/clean_demos_next_state_fix.md.
"""
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN_DEMOS = os.path.join(REPO_ROOT, "scripts", "clean_demos.py")

FORM_STATE = {
    "window_title": "Data Entry - Insurance Form",
    "application": "car_insurance_form_wx.py",
    "elements": [
        {"window_role": "active", "type": "editcontrol", "label": "Policy Number",
         "bbox": [100, 100, 300, 120]},
    ],
}


def _step(mouse_pos=None, keys=None, next_state=None, include_next_state_key=True):
    step = {"state": FORM_STATE}
    if include_next_state_key:
        step["next_state"] = next_state if next_state is not None else FORM_STATE
    if mouse_pos is not None:
        step["mouse"] = {"actions": [{"position": mouse_pos}]}
    if keys is not None:
        step["keyboard"] = {"actions": keys}
    return step


def _write_session(tmp_path, name, steps):
    sess_dir = tmp_path / "src" / name
    sess_dir.mkdir(parents=True)
    for i, step in enumerate(steps):
        (sess_dir / f"live_step_{i:04d}.json").write_text(
            json.dumps(step), encoding="utf-8")
    return sess_dir


def _run_clean_demos(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    result = subprocess.run(
        [sys.executable, CLEAN_DEMOS, str(src), str(dst)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return dst, result.stdout


def test_current_format_click_is_kept_not_dropped(tmp_path):
    """A click with no `next_state` key must still resolve and be kept."""
    steps = [
        _step(mouse_pos=(150, 110), include_next_state_key=False),
    ]
    _write_session(tmp_path, "session_001", steps)
    dst, stdout = _run_clean_demos(tmp_path)

    out_files = sorted((dst / "session_001").glob("live_step_*.json"))
    assert len(out_files) == 1, f"expected the click to survive; stdout: {stdout}"


def test_old_format_next_state_field_still_respected(tmp_path):
    """Old recordings that DO carry a literal next_state must still work."""
    steps = [
        _step(mouse_pos=(150, 110), next_state=FORM_STATE, include_next_state_key=True),
    ]
    _write_session(tmp_path, "session_001", steps)
    dst, stdout = _run_clean_demos(tmp_path)

    out_files = sorted((dst / "session_001").glob("live_step_*.json"))
    assert len(out_files) == 1, f"expected the click to survive; stdout: {stdout}"


def test_current_format_uses_next_files_state(tmp_path):
    """next_state should come from the FOLLOWING file's `state`, not the
    current file's own — proves the fix reads ahead rather than just
    reusing the current step's own state as a fallback. The click target
    can only be resolved from file 1's state, not file 0's (empty)."""
    empty_state = {
        "window_title": "Data Entry - Insurance Form",
        "application": "car_insurance_form_wx.py",
        "elements": [],
    }
    steps = [
        {"state": empty_state, "mouse": {"actions": [{"position": [150, 110]}]}},
        {"state": FORM_STATE, "keyboard": {"actions": [{"key": "tab"}]}},
    ]
    _write_session(tmp_path, "session_001", steps)
    dst, stdout = _run_clean_demos(tmp_path)

    out_files = sorted((dst / "session_001").glob("live_step_*.json"))
    assert len(out_files) == 2, f"click should resolve via the NEXT file's state; stdout: {stdout}"


def test_last_step_in_session_falls_back_to_own_state(tmp_path):
    """The last file in a session has no following file — must fall back to
    its own `state` instead of crashing or defaulting to an empty dict."""
    steps = [
        _step(mouse_pos=(150, 110), include_next_state_key=False),
    ]
    _write_session(tmp_path, "session_001", steps)
    dst, stdout = _run_clean_demos(tmp_path)

    out_files = sorted((dst / "session_001").glob("live_step_*.json"))
    assert len(out_files) == 1, f"last-step click should still resolve; stdout: {stdout}"
