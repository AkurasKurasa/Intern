"""
Regression test: TrajectoryDataset resolves relative data_dir paths to
absolute immediately, so its on-disk pickle cache stores cwd-independent
paths.

Found 2026-08-08: a dataset built once from one working directory, then its
pickle cache loaded by a second process running from a DIFFERENT working
directory, silently resolved every cached relative path against the wrong
cwd. _load_trace() swallows the resulting FileNotFoundError and returns
None, which becomes an empty {} state -- no crash, no error, just an entire
training run silently corrupted into empty-state noise. Concretely hit this
running a from-components/ sanity check and a from-repo-root background
training run back to back -- they shared one cache file (same resolved
_roots_hash) but disagreed on what the relative paths inside it meant.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from intelligence.model.transformer import TrajectoryDataset


def _write_session(directory: Path, n: int = 4) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        step = {
            "state": {
                "screen_resolution": [1920, 1080],
                "focused_element_id": None,
                "elements": [
                    {"element_id": "e0", "type": "editcontrol", "window_role": "active",
                     "label": "Field", "text": "Field", "value": "", "bbox": [100, 100, 300, 130],
                     "confidence": 1.0},
                ],
            },
            "mouse": {"actions": [{"type": "click", "position": [200, 115]}]},
            "keyboard": {"actions": []},
        }
        (directory / f"live_step_{i:04d}.json").write_text(json.dumps(step), encoding="utf-8")


def test_relative_data_dir_is_stored_as_absolute(tmp_path, monkeypatch):
    _write_session(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    ds = TrajectoryDataset(tmp_path.name, max_elements=8, hist_len=4)
    assert ds._grouped_files, "expected at least one file group"
    for group in ds._grouped_files:
        for fpath in group:
            assert fpath.is_absolute(), f"cached path is not absolute: {fpath}"


def test_relative_data_dir_list_is_stored_as_absolute(tmp_path, monkeypatch):
    _write_session(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    ds = TrajectoryDataset([tmp_path.name], max_elements=8, hist_len=4)
    for group in ds._grouped_files:
        for fpath in group:
            assert fpath.is_absolute(), f"cached path is not absolute: {fpath}"


def test_cached_paths_still_resolve_from_a_different_cwd(tmp_path, monkeypatch):
    """The actual end-to-end failure mode: build once from cwd A, reload the
    resulting pickle cache from cwd B, confirm every file still opens."""
    _write_session(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    TrajectoryDataset(tmp_path.name, max_elements=8, hist_len=4)  # builds + caches

    other_cwd = tmp_path.parent.parent
    monkeypatch.chdir(other_cwd)
    ds2 = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4)  # loads cache
    for group in ds2._grouped_files:
        for fpath in group:
            assert fpath.exists(), f"cached path no longer resolves: {fpath}"
