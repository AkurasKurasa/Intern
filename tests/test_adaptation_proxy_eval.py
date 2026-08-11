"""
tests/test_adaptation_proxy_eval.py
=====================================
Unit tests for scripts/adaptation_proxy_eval.py's pure perturbation/signature
helpers (objective 6 proxy). Does NOT exercise eval_session/predict() -- that
needs a real model.pt and GPU/CPU inference, out of scope for a fast unit
test; covered instead by manually running the script against real data.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import adaptation_proxy_eval as ape

_ELEMENTS = [
    {"element_id": "e0", "type": "editcontrol", "label": "First Name", "text": "First Name",
     "bbox": [100, 100, 300, 130]},
    {"element_id": "e1", "type": "editcontrol", "label": "Last Name", "text": "Last Name",
     "bbox": [100, 200, 300, 230]},
    {"element_id": "e2", "type": "buttoncontrol", "label": "Submit", "text": "Submit",
     "bbox": [100, 300, 300, 330]},
]

_STATE = {"screen_resolution": [1920, 1080], "elements": _ELEMENTS}


class TestSignature:
    def test_signature_is_type_and_label(self):
        assert ape._signature(_ELEMENTS[0]) == ("editcontrol", "first name")

    def test_signature_falls_back_to_text_when_label_missing(self):
        e = {"type": "editcontrol", "text": "Fallback"}
        assert ape._signature(e) == ("editcontrol", "fallback")


class TestPerturbShuffle:
    def test_shuffle_preserves_the_same_set_of_elements(self):
        rng = random.Random(1)
        out = ape._perturb_shuffle(_STATE, rng)
        assert {id(e["element_id"]) for e in out["elements"]} == \
               {id(e["element_id"]) for e in _ELEMENTS}
        assert set(e["element_id"] for e in out["elements"]) == \
               set(e["element_id"] for e in _ELEMENTS)

    def test_shuffle_does_not_mutate_original_list(self):
        original_order = [e["element_id"] for e in _STATE["elements"]]
        ape._perturb_shuffle(_STATE, random.Random(2))
        assert [e["element_id"] for e in _STATE["elements"]] == original_order

    def test_shuffle_preserves_other_state_keys(self):
        out = ape._perturb_shuffle(_STATE, random.Random(3))
        assert out["screen_resolution"] == _STATE["screen_resolution"]


class TestPerturbTranslate:
    def test_translate_shifts_every_bbox_by_the_same_offset(self):
        rng = random.Random(5)
        out = ape._perturb_translate(_STATE, rng, max_translate=100)
        deltas = set()
        for orig, moved in zip(_ELEMENTS, out["elements"]):
            dx = moved["bbox"][0] - orig["bbox"][0]
            dy = moved["bbox"][1] - orig["bbox"][1]
            deltas.add((dx, dy))
        assert len(deltas) == 1  # same (dx, dy) applied to every element

    def test_translate_clamps_at_zero_not_negative(self):
        state = {"elements": [{"type": "editcontrol", "label": "X", "bbox": [5, 5, 50, 50]}]}
        # Force a large negative offset by seeding until we get one, or just
        # verify the clamp logic directly via a fixed rng draw.
        class _FixedRng:
            def randint(self, a, b):
                return a  # always the most negative allowed offset
        out = ape._perturb_translate(state, _FixedRng(), max_translate=1000)
        b = out["elements"][0]["bbox"]
        assert b[0] >= 0 and b[1] >= 0

    def test_translate_does_not_mutate_original_elements(self):
        original_bbox = list(_ELEMENTS[0]["bbox"])
        ape._perturb_translate(_STATE, random.Random(9), max_translate=100)
        assert _ELEMENTS[0]["bbox"] == original_bbox


class TestPerturbBoth:
    def test_both_reorders_and_shifts(self):
        rng = random.Random(11)
        out = ape.PERTURBATIONS["both"](_STATE, rng, 100)
        assert set(e["element_id"] for e in out["elements"]) == \
               set(e["element_id"] for e in _ELEMENTS)
        # at least one bbox should differ from the original at the same id
        by_id_orig = {e["element_id"]: e["bbox"] for e in _ELEMENTS}
        assert any(by_id_orig[e["element_id"]] != e["bbox"] for e in out["elements"])
