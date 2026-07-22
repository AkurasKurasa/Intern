"""
scratch/probe_navigation_protocol_repick_fix.py
=================================================
Real-code-path drill for the GAP-path current-tab-repick fix
(2026-07-22): calls the ACTUAL LLMAgent._ask_llm_next_gap method (not a
logic replay), reproducing the exact bug precondition -- the agent is
sitting on 'Coverage' but self._visited_tabs is empty (doesn't include
it) -- and checks that the fix marks the current tab visited BEFORE the
prompt is built, so 'Coverage' never appears in the 'unvisited' list the
LLM sees, and the LLM call itself is monkeypatched out (no network call,
no LM Studio dependency needed).

Fake two-tab state: 'Coverage' (current, idx=0, one filled field so the
'0 visible empties' condition from the live bug is reproduced) and
'Drivers' (genuinely unvisited).
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

FAKE_STATE = {
    "elements": [
        {"type": "tabitem", "text": "Coverage", "label": "Coverage",
         "bbox": [10, 10, 80, 30], "window_role": "foreground"},
        {"type": "tabitem", "text": "Drivers", "label": "Drivers",
         "bbox": [90, 10, 160, 30], "window_role": "foreground"},
        {"type": "editcontrol", "label": "Bodily Injury", "text": "Bodily Injury",
         "value": "100/300", "bbox": [100, 200, 300, 220], "window_role": "foreground"},
    ]
}

# Reproduce the exact bug precondition: agent is on Coverage (idx=0) but
# _visited_tabs is empty -- Coverage was never added by any switch-path.
agent._current_tab_idx = 0
agent._visited_tabs = set()

captured_prompt = {}


def fake_llm_call(msg):
    captured_prompt["msg"] = msg
    return {"action_type": "done"}   # arbitrary -- we only care about the prompt content


agent._call_openai_compat = fake_llm_call

result = agent._ask_llm_next_gap(FAKE_STATE)

prompt = captured_prompt.get("msg", "")
current_tab_marked_visited = "coverage" in {v.lower() for v in agent._visited_tabs}
coverage_absent_from_unvisited_line = "Coverage" not in prompt.split("Tabs NOT yet visited:")[1].split("\n")[0]

print("=== PROBE: GAP-path current-tab-repick fix ===")
print(f"_visited_tabs after call: {agent._visited_tabs}")
print(f"Current tab (Coverage) marked visited: {current_tab_marked_visited}")
print(f"Prompt's 'unvisited' line: {prompt.split('Tabs NOT yet visited:')[1].split(chr(10))[0].strip()}")
print(f"Coverage absent from that line: {coverage_absent_from_unvisited_line}")

if current_tab_marked_visited and coverage_absent_from_unvisited_line:
    print("\nPASS -- the fix works: current tab is marked visited before the prompt is built, "
          "so the LLM can never be told its own current tab is unvisited.")
    sys.exit(0)
else:
    print("\nFAIL -- the fix did not prevent the current tab from appearing as unvisited.")
    sys.exit(1)
