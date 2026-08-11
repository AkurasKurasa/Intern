"""
tests/test_section_disambiguation.py
======================================
Regression tests for the new disambiguate_attempted="section" mode
(components/intelligence/model/transformer.py's _detect_section and the
"section" branch of _attempt_key / TrajectoryDataset._make_key_fn).

Context: Objective 2's ambiguity_rate metric (scripts/encoding_ambiguity.py)
is unaffected by this -- it only measures raw (type,label) collisions in a
captured state, which this mechanism doesn't touch. What THIS fixes is a
related-but-different problem: the 'attempted' input feature previously
collapsed same-labeled repeated-section fields (Driver 1/2/3's "First Name")
into one shared key, so filling Driver 1's field silently marked Driver 2's
still-empty field as already-attempted too. "rank" (list-order based) was
already tried and regressed val_click_acc (46.9% vs 68.9% baseline) --
"section" uses real panecontrol bbox geometry instead, mirroring agent.py's
own _detect_section, which is a genuinely more stable signal.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from intelligence.model.transformer import TrajectoryDataset, _attempt_key, _detect_section

_SECTION_PATTERN = r"section_(driver|vehicle)_(\d+)$"


def _pane(label: str, y: int) -> dict:
    return {"element_id": f"pane_{label}", "type": "panecontrol", "window_role": "active",
            "label": label, "text": label, "value": "", "bbox": [0, y, 800, y + 200],
            "confidence": 1.0}


def _field(label: str, y: int, element_id: str) -> dict:
    return {"element_id": element_id, "type": "editcontrol", "window_role": "active",
            "label": label, "text": label, "value": "", "bbox": [100, y, 300, y + 30],
            "confidence": 1.0}


_ELEMENTS = [
    _pane("section_driver_1", 0),
    _field("First Name", 20, "d1_first"),
    _pane("section_driver_2", 250),
    _field("First Name", 270, "d2_first"),
    _pane("section_driver_3", 500),
    _field("First Name", 520, "d3_first"),
]


def _by_id(eid: str) -> dict:
    return next(e for e in _ELEMENTS if e["element_id"] == eid)


class TestDetectSectionOfflineMirrorsAgentGeometry:
    def test_field_in_driver_1_block_detected(self):
        assert _detect_section(_by_id("d1_first"), _ELEMENTS, _SECTION_PATTERN) == "Driver 1"

    def test_field_in_driver_2_block_detected(self):
        assert _detect_section(_by_id("d2_first"), _ELEMENTS, _SECTION_PATTERN) == "Driver 2"

    def test_field_in_driver_3_block_detected(self):
        assert _detect_section(_by_id("d3_first"), _ELEMENTS, _SECTION_PATTERN) == "Driver 3"

    def test_no_pattern_is_a_noop(self):
        assert _detect_section(_by_id("d2_first"), _ELEMENTS, None) == ""

    def test_no_bbox_is_a_noop(self):
        elem = {"element_id": "x", "type": "editcontrol", "label": "First Name"}
        assert _detect_section(elem, _ELEMENTS, _SECTION_PATTERN) == ""

    def test_no_elements_is_a_noop(self):
        assert _detect_section(_by_id("d2_first"), [], _SECTION_PATTERN) == ""

    def test_non_matching_pane_label_ignored(self):
        elements = [_pane("section_other_1", 0), _field("First Name", 20, "e0")]
        assert _detect_section(elements[1], elements, _SECTION_PATTERN) == ""


class TestAttemptKeyWithSection:
    def test_same_label_different_section_gives_distinct_keys(self):
        k1 = _attempt_key(_by_id("d1_first"), section="Driver 1")
        k2 = _attempt_key(_by_id("d2_first"), section="Driver 2")
        assert k1 != k2

    def test_section_key_ignores_elements_param(self):
        """section, when given, takes priority over rank -- no accidental
        double-disambiguation."""
        key = _attempt_key(_by_id("d2_first"), elements=_ELEMENTS, section="Driver 2")
        assert key == ("Driver 2", "first name")

    def test_empty_section_falls_back_to_label_only(self):
        assert _attempt_key(_by_id("d1_first"), section="") == "first name"


def _write_session(directory: Path, click_order: list) -> list:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, eid in enumerate(click_order):
        el = _by_id(eid)
        bbox = el["bbox"]
        cx, cy = (bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2
        step = {
            "state": {"screen_resolution": [1920, 1080], "focused_element_id": None,
                       "elements": _ELEMENTS},
            "mouse": {"actions": [{"type": "click", "position": [cx, cy]}]},
            "keyboard": {"actions": []},
        }
        p = directory / f"live_step_{i:04d}.json"
        p.write_text(json.dumps(step), encoding="utf-8")
        paths.append(p)
    return paths


class TestSectionModeEndToEnd:
    def test_filling_driver_1_does_not_mark_driver_2_as_attempted(self, tmp_path):
        # 4 steps to satisfy hist_len=4: click Driver 1's First Name, then
        # Driver 2's three times.
        paths = _write_session(tmp_path, ["d1_first", "d2_first", "d2_first", "d2_first"])
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4,
                                disambiguate_attempted="section",
                                section_pattern=_SECTION_PATTERN)
        attempted_before_step1 = ds._attempted_by_file[str(paths[1])]
        assert attempted_before_step1 == {("Driver 1", "first name")}
        assert ("Driver 2", "first name") not in attempted_before_step1

    def test_invalid_disambiguate_attempted_raises(self, tmp_path):
        _write_session(tmp_path, ["d1_first", "d2_first", "d2_first", "d2_first"])
        with pytest.raises(ValueError):
            TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4,
                               disambiguate_attempted="bogus")

    def test_section_mode_without_pattern_behaves_like_none(self, tmp_path):
        """No section_pattern given -> _detect_section is a no-op for every
        element -> falls back to plain label-only keys, same as 'none'."""
        paths = _write_session(tmp_path, ["d1_first", "d2_first", "d2_first", "d2_first"])
        ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4,
                                disambiguate_attempted="section")
        attempted_before_step1 = ds._attempted_by_file[str(paths[1])]
        assert attempted_before_step1 == {"first name"}
