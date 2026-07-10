"""Offline probe: viewport-jump ping-pong fixes (2026-07-10 evening).

Drives _optimal_viewport_jump on a synthetic element tree — no form, no UIA.
Verifies the three fixes:
  1. DENSITY GATE   — model-anchored jump refuses a window no denser than the
                      current view (the 1-empty-window-while-2-visible bug).
  2. VIEWPORT LOCK  — re-jump to ANY anchor visited since last progress burns
                      it (A→B→A alternation, not just A→A).
  3. FAR-FIELD REVEAL — jumping DOWN focuses the window's BOTTOM-most empty
                      (whole window rides in), jumping UP focuses the anchor.

Run:  python scratch/probe_jump_pingpong.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "components"))

from components.agent.agent import LLMAgent


def _edit(label, y, value=""):
    return {"type": "editcontrol", "label": label, "value": value,
            "bbox": [100, y, 400, y + 24], "window_role": "active"}


def make_agent(elements, vt=200, vb=700):
    a = object.__new__(LLMAgent)                      # skip __init__ (no LLM, no UIA)
    a._dead_fill_keys = set()
    a._attempted_keys = set()
    a.step_delay = 0.0
    a._attempt_key = lambda e, state=None: (e.get("label") or "").lower()
    a._form_viewport_top = lambda s: vt
    a._form_viewport_bottom = lambda s: vb + 8       # code subtracts 8
    a._mark_attempted = lambda e: a._attempted_keys.add((e.get("label") or "").lower())
    a.focused = []                                    # record of _scroll_into_view calls
    a._scroll_into_view = lambda lbl: (a.focused.append(lbl), True)[1]
    a._observe = lambda: {"elements": elements}
    return a


def test_density_gate():
    # 2 empties visible; model's top pick is off-screen in a 1-empty window.
    els = [_edit("Visible A", 300), _edit("Visible B", 400),
           _edit("Lonely", 2000)]
    a = make_agent(els)
    t_pred = {"click_topk": [[2, 0.9, [250, 2012]]]}    # model wants 'Lonely'
    out = a._optimal_viewport_jump({"elements": els}, t_pred)
    # Model window (1) <= visible (2) → gate skips it; geometry fallback also
    # finds nothing denser → jump must NO-OP.
    assert out is None, f"density gate failed: jumped ({a.focused})"
    print("PASS  density gate — 1-empty model window refused while 2 visible")


def test_viewport_lock_alternation():
    # Zero visible; two off-screen windows A (y~2000) and B (y~-1500).
    els = [_edit("A1", 2000), _edit("A2", 2060),
           _edit("B1", -1500), _edit("B2", -1460), _edit("B3", -1420)]
    a = make_agent(els)
    st = {"elements": els}
    out1 = a._optimal_viewport_jump(st)               # → densest = B (3)
    assert out1 is not None
    first_anchor = "b1"
    assert first_anchor in a._jump_anchors_since_progress
    out2 = a._optimal_viewport_jump(st)               # B again → burned → A
    assert "b1" in a._attempted_keys, "lock did not burn re-visited anchor"
    # Each fruitless re-jump burns one anchor; with 5 empties the loop MUST
    # terminate (None) within 5 more calls — bounded, not infinite.
    for i in range(len(els) + 1):
        if a._optimal_viewport_jump(st) is None:
            break
    else:
        raise AssertionError("lock did not terminate the jump loop")
    print(f"PASS  viewport lock — A/B alternation burns anchors, terminates ({i + 1} extra jumps)")


def test_far_field_reveal_down():
    # Nothing visible; dense window below: anchor 'W1' + riders W2..W4.
    els = [_edit("W1", 1000), _edit("W2", 1080), _edit("W3", 1160), _edit("W4", 1240)]
    a = make_agent(els)
    out = a._optimal_viewport_jump({"elements": els})
    assert out is not None
    assert a.focused[-1] == "W4", f"expected far-field 'W4', focused {a.focused[-1]!r}"
    print("PASS  far-field reveal (down) — focused bottom-most 'W4', not anchor 'W1'")


def test_far_field_reveal_up():
    # Window ABOVE the viewport → reveal the anchor itself (top-most).
    els = [_edit("U1", -1200), _edit("U2", -1120), _edit("U3", -1040)]
    a = make_agent(els)
    out = a._optimal_viewport_jump({"elements": els})
    assert out is not None
    assert a.focused[-1] == "U1", f"expected anchor 'U1', focused {a.focused[-1]!r}"
    print("PASS  far-field reveal (up) — focused anchor 'U1' at top edge")


def test_lock_clears_on_progress():
    els = [_edit("A1", 2000), _edit("A2", 2060)]
    a = make_agent(els)
    a._optimal_viewport_jump({"elements": els})
    assert a._jump_anchors_since_progress
    a._jump_anchors_since_progress = set()            # what the ranked picker does on a hit
    out = a._optimal_viewport_jump({"elements": els})
    assert out is not None and "a1" not in a._attempted_keys, \
        "anchor wrongly burned after progress cleared the lock"
    print("PASS  lock release — post-progress re-jump to same window allowed")


if __name__ == "__main__":
    test_density_gate()
    test_viewport_lock_alternation()
    test_far_field_reveal_down()
    test_far_field_reveal_up()
    test_lock_clears_on_progress()
    print("\nALL PASS — density gate + viewport lock + far-field reveal behave.")
