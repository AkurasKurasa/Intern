"""
Tests for the CV vision-perception component.

Proves the pipeline works WITHOUT a live GUI by rendering a synthetic form
image and running the full stack on it:

    cv_detector       — detects boxes + reads text
    CVVisionObserver  — assembles a schema-conforming state, feeds the model
    perception_eval   — scores a candidate state against a reference

Run:  py -3.14 -m pytest tests/test_cv_perception.py -v
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMP = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

cv2 = pytest.importorskip("cv2")
PIL = pytest.importorskip("PIL")

from PIL import Image, ImageDraw, ImageFont

from observers.vlm.vision_observer.cv_detector import detect_elements, deps_status
from observers.vlm.vision_observer.cv_vision_observer import CVVisionObserver
from observers.schema import validate_state


# ── fixtures ────────────────────────────────────────────────────────────────────
def _font(size: int):
    """A TrueType font big enough for reliable OCR; fall back to bitmap."""
    for name in ("arial.ttf", "DejaVuSans.ttf", "calibri.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


@pytest.fixture(scope="module")
def synth_form():
    """A simple form: two labeled inputs (one filled), a button, a checkbox."""
    W, H = 900, 500
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f = _font(22)

    # labeled input 1 (filled)
    d.text((50, 70), "Policy Number", fill="black", font=f)
    d.rectangle([300, 64, 640, 104], outline=(120, 120, 120), width=2)
    d.text((312, 72), "POL99001", fill="black", font=f)

    # labeled input 2 (empty)
    d.text((50, 150), "Full Name", fill="black", font=f)
    d.rectangle([300, 144, 640, 184], outline=(120, 120, 120), width=2)

    # button
    d.rectangle([300, 240, 460, 290], outline=(0, 90, 200), width=2, fill=(210, 228, 255))
    d.text((345, 254), "Submit", fill="black", font=f)

    # checkbox + caption
    d.rectangle([300, 340, 326, 366], outline="black", width=2)
    d.text((340, 342), "Renewal", fill="black", font=f)

    return img


# ── tests ───────────────────────────────────────────────────────────────────────
def test_backends_available():
    """OpenCV must be present; Tesseract should be (warn-only if not)."""
    status = deps_status()
    assert status["opencv"] is True, "OpenCV not importable — perception cannot run"
    if not status["tesseract"]:
        pytest.skip("Tesseract OCR not installed — text tests need it")


def test_detector_finds_interactive_elements(synth_form):
    els = detect_elements(synth_form)
    assert els, "detector returned no elements"
    types = {e["type"] for e in els}
    # the three interactive kinds should all be recognized
    assert "editcontrol" in types
    assert "buttoncontrol" in types
    assert "checkboxcontrol" in types


def test_every_element_is_schema_shaped(synth_form):
    """Each element must carry the keys the encoder reads."""
    els = detect_elements(synth_form)
    for e in els:
        assert isinstance(e["bbox"], list) and len(e["bbox"]) == 4
        x1, y1, x2, y2 = e["bbox"]
        assert x2 > x1 and y2 > y1, f"degenerate bbox {e['bbox']}"
        for k in ("type", "label", "value", "text", "confidence"):
            assert k in e


def test_ocr_reads_a_known_label(synth_form):
    if not deps_status()["tesseract"]:
        pytest.skip("no Tesseract")
    els = detect_elements(synth_form)
    blob = " ".join((e.get("label") or "") + " " + (e.get("value") or "")
                     for e in els).lower()
    # at least one of the rendered strings should survive OCR
    assert any(tok in blob for tok in ("policy", "name", "submit", "renewal")), (
        f"OCR recovered no known text; got: {blob!r}"
    )


def test_observer_state_conforms_to_schema(synth_form):
    state = CVVisionObserver(image=synth_form).snapshot()
    assert state["source"] == "cv_vision"
    assert state["screen_resolution"] == [900, 500]
    errors = [i for i in validate_state(state) if i.startswith("ERROR")]
    assert not errors, f"schema ERRORS: {errors}"


def test_observer_output_feeds_the_model(synth_form):
    """The whole point: vision output must encode for the BC transformer."""
    enc = pytest.importorskip("intelligence.model.transformer")
    state = CVVisionObserver(image=synth_form).snapshot()
    tensor = enc.encode_state(state)
    assert tuple(tensor.shape) == (128, enc.ELEM_FEATURES)


def test_observer_injected_image_beats_capture(synth_form):
    """Injected image path must not touch screen capture (so tests are headless)."""
    obs = CVVisionObserver(image=synth_form)
    img, w, h = obs._load_image()
    assert (w, h) == (900, 500)


def test_scorer_perfect_on_self(synth_form):
    """A state scored against itself = perfect recall + IoU."""
    from perception_eval import score_states
    state = CVVisionObserver(image=synth_form).snapshot()
    rep = score_states(state, state)
    assert rep["recall"] == 1.0
    assert rep["precision"] == 1.0
    assert rep["mean_iou"] == 1.0
    assert rep["counts"]["false_neg"] == 0


def test_scorer_penalizes_missing_and_invented(synth_form):
    """Dropping a reference element lowers recall; adding junk lowers precision."""
    from perception_eval import score_states
    full = CVVisionObserver(image=synth_form).snapshot()
    inter = [e for e in full["elements"]
             if e["type"] in {"editcontrol", "buttoncontrol", "checkboxcontrol"}]
    assert len(inter) >= 2, "need a couple interactive elements for this test"

    # candidate missing one interactive element → recall < 1
    candidate = dict(full, elements=full["elements"][1:])
    rep = score_states(candidate, full)
    assert rep["recall"] < 1.0

    # candidate with an invented box far away → precision < 1
    junk = dict(inter[0], bbox=[5, 5, 40, 40], element_id="junk")
    candidate2 = dict(full, elements=full["elements"] + [junk])
    rep2 = score_states(candidate2, full)
    assert rep2["precision"] < 1.0


def test_detector_empty_on_blank_image():
    """A blank canvas yields no interactive elements (no hallucinated boxes)."""
    blank = Image.new("RGB", (400, 300), "white")
    els = detect_elements(blank)
    interactive = [e for e in els
                   if e["type"] in {"editcontrol", "buttoncontrol", "checkboxcontrol"}]
    assert interactive == []
