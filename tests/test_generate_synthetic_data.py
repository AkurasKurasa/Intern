"""Tests for scripts/generate_synthetic_data.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import generate_synthetic_data as gen  # noqa: E402


def _load_all(folder):
    return [json.load(open(os.path.join(folder, f), encoding="utf8")) for f in sorted(os.listdir(folder))]


def test_writes_requested_count_per_scope(tmp_path):
    written = gen.generate(25, out_root=str(tmp_path))
    assert written == {"scope_1": 25, "scope_2": 25, "scope_3": 25}
    for scope in ("scope_1", "scope_2", "scope_3"):
        assert len(os.listdir(tmp_path / scope)) == 25


def test_every_file_is_marked_synthetic(tmp_path):
    gen.generate(20, out_root=str(tmp_path))
    for scope in ("scope_1", "scope_2", "scope_3"):
        assert all(d["synthetic"] is True for d in _load_all(tmp_path / scope))


def test_output_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    gen.generate(10, seed=3, out_root=str(a))
    gen.generate(10, seed=3, out_root=str(b))
    for scope in ("scope_1", "scope_2", "scope_3"):
        assert _load_all(a / scope) == _load_all(b / scope)


def test_files_match_the_real_record_shapes(tmp_path):
    gen.generate(5, out_root=str(tmp_path))
    step = _load_all(tmp_path / "scope_1")[0]
    assert {"trace_id", "state", "next_state", "action"} <= step.keys()
    assert step["action"]["target_element_id"] in {e["element_id"] for e in step["state"]["elements"]}

    row = _load_all(tmp_path / "scope_2")[0]
    assert {"row", "student_id", "status", "filled", "verified", "escalations"} <= row.keys()

    msg = _load_all(tmp_path / "scope_3")[0]
    assert {"id", "sender", "sender_email", "subject", "body_text", "received_at", "labels"} <= msg.keys()


def test_synthetic_folder_is_not_a_thesis_metric_source():
    """Synthetic files must never be read by the Chapter 4 metric pipeline."""
    sys.path.insert(0, os.path.join(REPO, "scripts", "thesis_figures"))
    import objective_metrics as om

    sources = [v for k, v in vars(om).items() if k.startswith("P_") and isinstance(v, str)]
    assert sources
    assert not any("synthetic" in s.replace("\\", "/") for s in sources)


def test_synthetic_folder_is_gitignored():
    out = subprocess.run(["git", "check-ignore", "data/synthetic/scope_1/step_00000.json"],
                         cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0, "data/synthetic/ must be gitignored"
