"""
Tests for the Observer base (generalized perception adapters).

Locks: (1) snapshot() runs capture→normalize→validate; (2) UIA stays IDENTITY
(model is trained on its exact shape — must not change); (3) a dialect observer
(TYPE_MAP/KEY_MAP) is normalized to canonical and passes the schema.
"""
import logging
from observers.base import Observer
from observers.schema import validate_state
from observers.ui_observer import UIAutomationObserver


_CANON = {
    "elements": [
        {"type": "editcontrol", "label": "Policy Number", "value": "",
         "window_role": "active", "bbox": [0, 100, 500, 128]},
    ],
    "screen_resolution": [1920, 1080],
    "source": "uia",
}


# ── UIA must be identity ─────────────────────────────────────────────────────

def test_uia_normalize_is_identity():
    obs = UIAutomationObserver()
    import copy
    state = copy.deepcopy(_CANON)
    assert obs.normalize(state) == _CANON          # unchanged


# ── base template: capture → normalize → validate ────────────────────────────

class _FakeExcel(Observer):
    TYPE_MAP = {"cell": "editcontrol", "header_cell": "textcontrol"}
    KEY_MAP  = {"text": "value"}
    source_name = "excel"

    def __init__(self, raw):
        self._raw = raw

    def _raw_snapshot(self):
        return self._raw


def test_dialect_normalized_to_canonical():
    raw = {
        "elements": [
            {"type": "cell", "text": "Acme", "window_role": "active",
             "bbox": [0, 0, 80, 20]},
        ],
        "screen_resolution": [1920, 1080],
    }
    obs = _FakeExcel(raw)
    state = obs.snapshot()
    e = state["elements"][0]
    assert e["type"] == "editcontrol"              # cell → editcontrol
    assert e["value"] == "Acme"                    # text → value (added)
    assert state["source"] == "excel"              # source filled
    assert [i for i in validate_state(state) if i.startswith("ERROR")] == []


def test_template_calls_raw_then_validates(caplog):
    # raw with a foreign type that ISN'T mapped → should log an ERROR (loud)
    raw = {"elements": [{"type": "widget", "bbox": [0, 0, 1, 1]}],
           "screen_resolution": [1, 1]}
    class _Bad(Observer):
        source_name = "bad"
        def _raw_snapshot(self): return raw
    with caplog.at_level(logging.ERROR):
        _Bad().snapshot()
    assert any("widget" in r.message or "widget" in str(r.args) for r in caplog.records)


def test_check_runs_only_once():
    raw = {"elements": [{"type": "editcontrol", "label": "x", "value": "",
                         "window_role": "active", "bbox": [0, 0, 1, 1]}],
           "screen_resolution": [1, 1], "source": "x"}
    class _Ok(Observer):
        source_name = "x"
        def _raw_snapshot(self): return raw
    o = _Ok()
    o.snapshot()
    assert o._schema_checked is True
