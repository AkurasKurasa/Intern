"""
scratch/probe_sweep_confirmed_fill_no_observe.py
==================================================
Real-code-path drill for the sweep speed fix (2026-07-23): a confirmed
fill inside _sweep_tab's per-field loop used to call self._observe() --
a full UIA tree re-scan -- purely to refresh `state`, even though the
write function's own read-back already proved the value landed. Fixed
to patch the just-filled element's value in place instead (since _fx is
a direct reference into state["elements"], not a copy).

This drill proves, on the ACTUAL _sweep_tab method (no live UI):
  1. self._observe() is NOT called on the confirmed-fill path.
  2. state["elements"]'s matching element's "value" IS updated in place,
     so the next _navigation_protocol(state) call sees it filled.

Fakes _navigation_protocol (propose filling one field, then report the
tab clean) and _nav_fill_field (always confirms). No live UI, no LLM.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from components.agent.agent import LLMAgent

agent = LLMAgent(
    goal="Fill the car insurance form using data from the open Notepad file.",
    provider="lmstudio",
    dry_run=True,
    model_path="tasks/form_filling/model_eight_tabs.pt",
)

FIELD_EL = {"type": "editcontrol", "label": "Credit Score", "text": "Credit Score",
            "value": "", "bbox": [100, 200, 300, 220], "window_role": "foreground"}

STATE = {"elements": [FIELD_EL]}

observe_calls = {"count": 0}


def fake_observe():
    observe_calls["count"] += 1
    return {"elements": [FIELD_EL]}


agent._observe = fake_observe

nav_calls = {"count": 0}


def fake_navigation_protocol(state):
    nav_calls["count"] += 1
    if nav_calls["count"] == 1:
        return {"action": "fill", "field": "Credit Score", "value": "690"}
    return {"action": "done"}


agent._navigation_protocol = fake_navigation_protocol


def fake_nav_fill_field(state, field_label, value, prefer_key=None):
    return True   # always confirms, same as a real successful write


agent._nav_fill_field = fake_nav_fill_field

# Isolate the FILL step's behavior specifically -- the finish/submit path
# (triggered once nav proposes "done") legitimately needs its own real
# observe() call, unrelated to this fix. Stub it out so only the fill
# iteration's observe()-count is measured.
agent._confirm_finished = lambda state: True
agent._click_submit = lambda state: True

result_state, finished = agent._sweep_tab(STATE)

print("=== PROBE: sweep confirmed-fill observe() short-circuit ===")
print(f"observe() calls during the confirmed fill: {observe_calls['count']}")
print(f"FIELD_EL['value'] after the call: {FIELD_EL['value']!r}")
print(f"Same object identity (state['elements'][0] is FIELD_EL): "
      f"{result_state['elements'][0] is FIELD_EL}")

ok = observe_calls["count"] == 0 and FIELD_EL["value"] == "690"
if ok:
    print("\nPASS -- confirmed fill patched the element in place, zero observe() calls.")
    sys.exit(0)
else:
    print("\nFAIL -- either observe() fired unnecessarily or the value wasn't patched.")
    sys.exit(1)
