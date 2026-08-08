"""
Regression test for TrajectoryDataset's rare-action loss weighting being
by individual FIELD identity, not just control TYPE.

Found 2026-08-07 after two other accuracy levers (a bigger data-volume
retrain, and a StateEncoder architecture change) both failed to move
click-target accuracy off ~45-58%, while every per-tab confusion breakdown
all session kept showing the same pattern: the model predicting a common,
same-type neighbor field instead of a rarer one right next to it ("DL
Expiration" -> "DL Issuing State", "Comprehensive Deductible" -> "Collision
Deductible"). The existing rare-action weighting only balanced by control
TYPE (edit box vs button vs tab) — two edit-box fields of wildly different
real-world frequency (a name field appearing on every record vs. an
underwriter field appearing rarely) got the SAME loss weight, so the model
had no loss-level incentive to learn the rare one over its common neighbor.

Fix: weight by the clicked element's specific identity (_attempt_key) instead
of its control type. Subsumes type-level weighting (a rare type's instances
are also individually rare) while additionally fixing the same-type,
different-frequency case type-only weighting couldn't see.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from intelligence.model.transformer import TrajectoryDataset

_COMMON_BBOX = [100, 100, 300, 130]
_RARE_BBOX   = [100, 200, 300, 230]


def _make_step(target: str) -> dict:
    """One synthetic trace step clicking either 'common' or 'rare', both the
    same control type — the exact scenario type-only weighting can't fix."""
    bbox = _COMMON_BBOX if target == "common" else _RARE_BBOX
    cx, cy = (bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2
    return {
        "state": {
            "screen_resolution": [1920, 1080],
            "focused_element_id": None,
            "elements": [
                {"element_id": "e_common", "type": "editcontrol", "window_role": "active",
                 "label": "CommonField", "text": "CommonField", "value": "", "bbox": _COMMON_BBOX,
                 "confidence": 1.0},
                {"element_id": "e_rare", "type": "editcontrol", "window_role": "active",
                 "label": "RareField", "text": "RareField", "value": "", "bbox": _RARE_BBOX,
                 "confidence": 1.0},
            ],
        },
        "mouse": {"actions": [{"type": "click", "position": [cx, cy]}]},
        "keyboard": {"actions": []},
    }


def _write_session(directory: Path) -> None:
    """12 steps: 1 click on the rare field, 11 clicks on the common field —
    same control type both times, wildly different individual frequency."""
    directory.mkdir(parents=True, exist_ok=True)
    targets = ["common", "common", "common", "rare"] + ["common"] * 8
    for i, target in enumerate(targets):
        step = _make_step(target)
        (directory / f"live_step_{i:04d}.json").write_text(json.dumps(step), encoding="utf-8")


def test_rare_field_gets_higher_weight_than_common_field_of_same_type(tmp_path):
    _write_session(tmp_path)
    # rare_weight_basis="field" is explicit here: "type" (the default since the
    # basis became configurable) can't distinguish these two samples at all —
    # both targets share the same control type, which is exactly the gap this
    # test exists to cover. See TrajectoryDataset's docstring for why "type" is
    # the default and "field" has to be opted into.
    ds = TrajectoryDataset(str(tmp_path), max_elements=8, hist_len=4, rare_weight_basis="field")

    assert ds._sample_weights is not None
    common_weights, rare_weights = [], []
    for sample, weight in zip(ds._samples, ds._sample_weights):
        tgt_click_idx = sample[5]
        if tgt_click_idx < 0:
            continue
        # element order in encode_state matches state["elements"] order —
        # index 0 = CommonField, index 1 = RareField
        (rare_weights if tgt_click_idx == 1 else common_weights).append(weight)

    assert rare_weights, "expected at least one sample targeting the rare field"
    assert common_weights, "expected at least one sample targeting the common field"
    assert min(rare_weights) > max(common_weights), (
        "a field clicked once should be weighted strictly higher than one "
        "clicked repeatedly, even though both share the same control type"
    )
