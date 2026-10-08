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


STEP_KEYS = {"synthetic", "trace_id", "timestamp", "duration", "type", "tab", "state", "action", "next_state"}
ELEMENT_KEYS = {"element_id", "type", "control_type", "bbox", "text", "value", "label",
                "enabled", "visible", "focused", "confidence", "source"}


def _ids(state):
    return {e["element_id"] for e in state["elements"]}


def _by_id(state):
    return {e["element_id"]: e for e in state["elements"]}


def test_every_scope_is_a_state_action_step_like_scope_1(tmp_path):
    """2026-10-08: Scope 2 and 3 files were a finished row and a bare email.
    All three are now one state -> action -> next_state step."""
    gen.generate(40, out_root=str(tmp_path))
    for scope in ("scope_1", "scope_2", "scope_3"):
        for step in _load_all(tmp_path / scope):
            assert STEP_KEYS <= step.keys(), scope
            for e in step["state"]["elements"] + step["next_state"]["elements"]:
                assert ELEMENT_KEYS <= e.keys(), scope
            assert step["action"]["target_element_id"] in _ids(step["state"]), scope
            assert _ids(step["state"]) == _ids(step["next_state"]), scope


def test_scope2_copies_each_value_from_the_sheet_cell_it_names(tmp_path):
    gen.generate(40, out_root=str(tmp_path))
    for step in _load_all(tmp_path / "scope_2"):
        a, els = step["action"], _by_id(step["state"])
        assert els[a["target_element_id"]]["window_role"] == "active"
        after = _by_id(step["next_state"])[a["target_element_id"]]
        assert after["value"] == a["value"]
        if a["type"] == "type":
            src = els[a["source_element_id"]]
            assert src["window_role"] == "background" and src["value"] == a["value"]
            assert src["label"] == a["source_label"]
        else:
            # Remarks is derived from the grade, never copied from a column.
            assert a["type"] == "select" and a["source_element_id"] is None
            grade = int(next(e["value"] for e in step["state"]["elements"]
                             if e["label"] == "FINAL GRADE"))
            assert a["value"] == ("Passed" if grade >= gen.S2_PASS_MARK else "Failed")


def test_scope2_steps_build_each_student_row_in_order(tmp_path):
    gen.generate(8, out_root=str(tmp_path))
    steps = _load_all(tmp_path / "scope_2")
    assert [s["action"]["target_label"] for s in steps[:4]] == [f[0] for f in gen.S2_FIELDS]
    assert len({s["student_id"] for s in steps[:4]}) == 1          # one student per four steps
    # A later step sees the earlier fields already filled.
    last = _by_id(steps[3]["state"])
    assert all(last[f"elem_{k}"]["value"] for k in range(3))


def test_scope3_clicks_the_button_for_its_decision(tmp_path):
    gen.generate(60, out_root=str(tmp_path))
    expected = {"invoice": "reply", "Meeting request": "schedule", "maintenance": "schedule",
                "forward": "forward", "newsletter": "leave_alone"}
    for step in _load_all(tmp_path / "scope_3"):
        a = step["action"]
        target = _by_id(step["state"])[a["target_element_id"]]
        assert a["type"] == "click" and target["control_type"] == "ButtonControl"
        assert target["label"] == dict((k, l) for l, k in gen.S3_BUTTONS)[a["decision"]]
        subject = step["message"]["subject"]
        assert any(key in subject and a["decision"] == d for key, d in expected.items()), subject
        status = next(e for e in step["next_state"]["elements"] if e["label"] == "Status")
        assert status["value"] == gen.S3_RESULT[a["decision"]]


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


def test_scope2_and_scope3_states_encode_like_scope1(tmp_path):
    """Integration: the state the Scope 1 model reads (128 elements x 395
    numbers) can be built from every scope's synthetic step unchanged."""
    import pytest
    sys.path.insert(0, os.path.join(REPO, "components"))
    try:
        from intelligence.model.transformer import ELEM_FEATURES, encode_state
    except Exception as exc:  # noqa: BLE001 -- torch / embedding model unavailable
        pytest.skip(f"model code not importable here: {exc}")
    gen.generate(4, out_root=str(tmp_path))
    for scope in ("scope_1", "scope_2", "scope_3"):
        step = _load_all(tmp_path / scope)[0]
        t = encode_state(step["state"], max_elements=128)
        assert tuple(t.shape) == (128, ELEM_FEATURES), scope
        real = int((t[:, 0] > 0.5).sum())
        assert real == len(step["state"]["elements"]), scope
