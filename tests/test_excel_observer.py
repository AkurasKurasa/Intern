"""
Tests for ExcelObserver.normalize — the dialect→canonical map (the swap proof,
hermetic). Excel's raw cells (type=cell, value/label under `semantic`, no
window_role) must come out conforming to the perception contract, so the agent
reads cells exactly like UIA fields.
"""
from observers.excel_observer.excel_observer import ExcelObserver
from observers.schema import validate_state


def _raw():
    # shape of ExcelObserver._read_state() output (pre-normalize)
    return {
        "application": "Microsoft Excel",
        "window_title": "Roster.xlsx",
        "screen_resolution": [1920, 1080],
        "focused_element_id": "cell_B5",
        "elements": [
            {"element_id": "cell_B3", "type": "cell", "bbox": [0, 40, 80, 60],
             "text": "Last Name: Acme",
             "semantic": {"raw_value": "Acme", "column_header": "Last Name",
                          "is_header": False, "is_active": False}},
            {"element_id": "cell_B1", "type": "header_cell", "bbox": [0, 0, 80, 20],
             "text": "Last Name",
             "semantic": {"raw_value": "Last Name", "column_header": "Last Name",
                          "is_header": True}},
            {"element_id": "cell_B5", "type": "active_cell", "bbox": [0, 80, 80, 100],
             "text": "Last Name:",
             "semantic": {"raw_value": "", "column_header": "Last Name",
                          "is_active": True}},
        ],
    }


def test_types_mapped_to_canonical():
    s = ExcelObserver().normalize(_raw())
    t = [e["type"] for e in s["elements"]]
    assert t == ["editcontrol", "textcontrol", "editcontrol"]   # cell/header/active


def test_value_and_label_from_semantic():
    s = ExcelObserver().normalize(_raw())
    data = s["elements"][0]
    assert data["value"] == "Acme"            # raw_value → value
    assert data["label"] == "Last Name"       # column_header → label
    empty = s["elements"][2]
    assert empty["value"] == ""               # empty cell → empty value (is_filled false)


def test_window_role_and_source_added():
    s = ExcelObserver().normalize(_raw())
    assert all(e["window_role"] == "active" for e in s["elements"])
    assert s["source"] == "excel"


def test_normalized_excel_passes_schema():
    s = ExcelObserver().normalize(_raw())
    errors = [i for i in validate_state(s) if i.startswith("ERROR")]
    assert errors == [], errors          # the whole point: Excel now conforms


def test_raw_excel_would_fail_schema():
    # sanity: BEFORE normalize, raw Excel dialect is flagged (type=cell)
    errors = [i for i in validate_state(_raw()) if i.startswith("ERROR")]
    assert any("cell" in e for e in errors)
