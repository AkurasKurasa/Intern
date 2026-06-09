"""
Tests for the perception contract (observers/schema.py).

Guarantees the seam fails LOUD: a conforming adapter (UIA) passes clean, and a
non-conforming one (Excel's raw dialect) is flagged with actionable ERRORs
instead of the agent silently seeing a blank screen.
"""
from observers.schema import validate_state, CONTROL_TYPES


def _errors(issues):
    return [i for i in issues if i.startswith("ERROR")]


def _warns(issues):
    return [i for i in issues if i.startswith("WARN")]


# ── conforming (UIA-shaped) passes ───────────────────────────────────────────

def test_uia_shaped_state_is_clean():
    state = {
        "elements": [
            {"type": "editcontrol", "label": "Policy Number", "value": "",
             "window_role": "active", "bbox": [0, 100, 500, 128], "focused": True},
            {"type": "comboboxcontrol", "label": "Policy Term", "value": "6 Month",
             "window_role": "active", "bbox": [0, 140, 500, 168]},
        ],
        "screen_resolution": [1920, 1080],
        "source": "uia",
    }
    assert _errors(validate_state(state)) == []


# ── Excel's raw dialect is caught LOUD ───────────────────────────────────────

def test_excel_dialect_is_flagged():
    state = {
        "elements": [
            # type=cell (not in vocab), value-under-"text", no label/window_role
            {"type": "cell", "text": "Acme", "bbox": [0, 100, 80, 120],
             "element_id": "cell_B3"},
        ],
        "screen_resolution": [1920, 1080],
        "source": "excel",
    }
    issues = validate_state(state)
    errs = _errors(issues)
    # bad type 'cell' must be an ERROR (agent filters would drop it)
    assert any("CONTROL_TYPES" in e and "cell" in e for e in errs), errs
    # missing label/value/window_role must surface (degraded)
    assert any("recommended" in w for w in _warns(issues))


# ── structural breakage is ERROR ─────────────────────────────────────────────

def test_missing_screen_resolution_is_error():
    state = {"elements": []}
    assert any("screen_resolution" in e for e in _errors(validate_state(state)))


def test_elements_not_a_list_is_error():
    assert any("elements" in e for e in _errors(validate_state(
        {"elements": "nope", "screen_resolution": [1, 1]})))


def test_bad_bbox_is_error():
    state = {"elements": [{"type": "editcontrol", "bbox": [0, 1, 2]}],
             "screen_resolution": [1, 1], "source": "x"}
    assert any("bbox" in e for e in _errors(validate_state(state)))


def test_empty_elements_warns_blank_screen():
    state = {"elements": [], "screen_resolution": [1, 1], "source": "x"}
    assert any("blank screen" in w for w in _warns(validate_state(state)))


def test_missing_required_type_is_error():
    state = {"elements": [{"label": "X", "bbox": [0, 0, 1, 1]}],
             "screen_resolution": [1, 1], "source": "x"}
    assert any("required key" in e and "type" in e for e in _errors(validate_state(state)))


def test_uia_container_types_dont_false_alarm():
    # windowcontrol et al. are legit UIA containers the agent ignores — they must
    # NOT raise ERROR (regression: live run flagged 'windowcontrol' 2026-06-09).
    for t in ("windowcontrol", "titlebarcontrol", "groupcontrol", "imagecontrol"):
        state = {"elements": [{"type": t, "label": "", "value": "",
                               "window_role": "active", "bbox": [0, 0, 10, 10]}],
                 "screen_resolution": [1, 1], "source": "uia"}
        assert _errors(validate_state(state)) == [], f"{t} false-alarmed"


def test_vocab_covers_agent_filter_types():
    # the types agent.py filters on must all be in the contract
    for t in ("editcontrol", "comboboxcontrol", "checkboxcontrol",
              "tabitemcontrol", "panecontrol", "listitemcontrol"):
        assert t in CONTROL_TYPES
