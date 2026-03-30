# Intern — Architecture Redesign: Transformer-First System

**Document Type:** Technical Design Document
**Date:** March 30, 2026
**Authors:** Andrei Calma, Ralph Ganzon
**Deadline:** Friday, April 3, 2026
**Status:** In Progress

---

## 1. Background

This document is the technical response to the group's capstone meeting held on March 30, 2026, where the following core issue was identified:

> *"The current system is heavily reliant on the Large Language Model (LLM), which is causing slow system performance and integration difficulties. The ideal role of the LLM is reasoning tasks only — not core decision-making or action generation."*

The group agreed that the **Transformer model is the main product** of this capstone — the LLM is a supporting tool, not the protagonist. This document defines the problem precisely, proposes a new architecture, and lays out the implementation steps required by the April 3 deadline.

---

## 2. What the System Does Today

Intern is a desktop UI automation system with two AI components working together:

| Component | Role | Location |
|---|---|---|
| **TransformerAgentNetwork** | Learned behavioral policy trained on human recordings | `components/learning_models/transformer/transformer.py` |
| **LLM (Groq / Claude / Gemini)** | Decides what action to take next | `components/agent/agent.py` |
| **ActionExecutor** | Physically moves the mouse and types | `components/agent/executor.py` |

### 2.1 Current Execution Flow

```
run_agent.py
     │
     ▼
LLMAgent.run()  (up to 60 steps)
     │
     ├── Step N:
     │     ├── 1. Observe screen (UIAutomationObserver)
     │     ├── 2. Call LLM API ← internet round-trip every step
     │     │       LLM reads screen + data → decides action
     │     ├── 3. Convert LLM decision to coordinates
     │     └── 4. ActionExecutor fires real mouse/keyboard
     │
     └── Transformer is only used if LLM is unavailable (fallback)
```

### 2.2 The Two Paths — Currently Mutually Exclusive

In `agent.py`, the decision logic is a simple if/else:

```python
if self._llm_client:
    # LLM decides everything — transformer is ignored
    llm_action = self._ask_llm(state)
    prediction = self._llm_action_to_prediction(llm_action, state)
else:
    # Transformer decides everything — LLM is ignored
    prediction = self._predict(state)
```

The two systems **do not cooperate**. One runs, the other sits idle.

---

## 3. Identified Problems

### Problem 1 — LLM Called Every Single Step

The LLM is invoked on **every step** of the agent loop — up to 60 API calls per task. Each call:
- Makes an HTTP request to an external server (Groq, Anthropic, etc.)
- Waits 1–3 seconds for the response
- Parses and converts the response

**Result:** A single form-filling session can take **2–3 minutes** of waiting purely on API latency. This is unacceptable for a production automation system.

---

### Problem 2 — The Transformer Is Treated as a Backup

The model we trained — the actual academic contribution of this project — only runs when there is **no API key**. In normal usage with a Groq key, the transformer never executes. The LLM does everything.

This is backwards. The transformer should be the engine. The LLM should be the advisor.

---

### Problem 3 — No Confidence Signal

The transformer makes a prediction but discards the information about *how confident* that prediction is:

```python
# In transformer.py — this throws away the raw logit distribution
idx = out.type_logits.argmax(-1).item()  # Winner-take-all, no confidence kept
```

Without a confidence score, there is no way to decide when to trust the transformer and when to ask the LLM for help. So the current design defaults to asking the LLM always — which causes Problem 1.

---

### Problem 4 — Incompatible Action Schemas

The transformer and LLM produce different output formats that must be manually converted:

| Transformer output | LLM output |
|---|---|
| `click_position: [x, y]` (pixel coords) | `target: "First Name"` (label string) |
| `source_elem_idx: 3` (element pointer) | `text: "John"` (literal text) |
| `key_count: 5` (number) | `keys: ["tab"]` (key names) |

The conversion layer (`_llm_action_to_prediction`) loses information and can fail when the LLM's label wording doesn't exactly match what's on screen.

---

### Problem 5 — The Better Architecture Already Exists But Is Unused

The `Planner` class (`components/recorder/planner/planner.py`) was written with the correct architecture:

```python
def plan(self, state):
    # Step 1: Transformer predicts first (always)
    decision = self._transformer_predict(state, history)

    # Step 2: LLM only checks every N steps (optional)
    if self.provider != "none" and (self._step % self.llm_every == 0):
        decision = self._llm_evaluate(state, history, decision)

    return decision
```

This is exactly what the meeting described as the ideal role. But `run_agent.py` uses `LLMAgent`, not `Planner`.

---

## 4. Proposed Architecture — Transformer-First

### 4.1 Core Principle

```
The Transformer is the main engine.
The LLM is the safety net.
```

The transformer should predict and execute every action. The LLM is only consulted in specific situations where the transformer is uncertain or stuck.

### 4.2 New Execution Flow

```
run_agent.py  (new version)
     │
     ▼
TransformerEngine.run()
     │
     ├── Step N:
     │     ├── 1. Observe screen
     │     ├── 2. Transformer predicts action + confidence score
     │     │
     │     ├── If confidence ≥ 0.8:
     │     │     └── Execute directly — no LLM call
     │     │
     │     ├── If confidence 0.5–0.8:
     │     │     ├── LLM validates the transformer's plan
     │     │     └── LLM says "follow" or "override"
     │     │
     │     └── If confidence < 0.5 OR stuck OR validation failed:
     │           ├── LLM takes control for this step
     │           └── Log for retraining
     │
     └── Transformer handles ~80% of steps without LLM
```

### 4.3 Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TransformerEngine                            │
│                                                                     │
│  Observe Screen                                                     │
│       │                                                             │
│       ▼                                                             │
│  TransformerAgentNetwork.predict()                                  │
│       │                                                             │
│       ├── action_type  (click / keyboard / no_op)                   │
│       ├── click_position  [x, y]                                    │
│       ├── key_count                                                 │
│       ├── source_elem_idx                                           │
│       └── confidence  ← NEW (max softmax of type_logits)           │
│                                                                     │
│       │                                                             │
│       ▼                                                             │
│  ┌────────────────────────────────────┐                             │
│  │      Confidence Router             │                             │
│  ├────────────────────────────────────┤                             │
│  │ confidence ≥ 0.8  → Execute now   │ ← ~80% of steps            │
│  │ confidence 0.5–0.8 → LLM validate │ ← ~15% of steps            │
│  │ confidence < 0.5   → LLM decide   │ ← ~5% of steps             │
│  └────────────────────────────────────┘                             │
│                                                                     │
│       │                                                             │
│       ▼                                                             │
│  ActionExecutor  (pyautogui)                                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                     Validation fails?
                              │
                              ▼
                    ┌─────────────────┐
                    │  Log for        │
                    │  retraining     │ → ContinualLearner
                    └─────────────────┘
```

---

## 5. Implementation Plan

### Step 1 — Add Confidence Score to Transformer Output

**File:** `components/learning_models/transformer/transformer.py`
**Function:** `predict()`
**Change:** Extract max softmax probability before `argmax` and include in return dict.

**Current code:**
```python
idx = out.type_logits.argmax(-1).item()
result = {"action_type": _ACTION_LABELS.get(idx, "no_op")}
```

**New code:**
```python
probs = torch.softmax(out.type_logits, dim=-1)
confidence = probs.max(-1).values.item()
idx = probs.argmax(-1).item()

result = {
    "action_type": _ACTION_LABELS.get(idx, "no_op"),
    "confidence":  round(confidence, 4),   # e.g. 0.91
}
```

**Why this works:** If the transformer strongly favors one action type (e.g., 91% click, 7% keyboard, 2% no_op), confidence is 0.91 — high enough to act without LLM. If it's split (45% click, 40% keyboard), confidence is 0.45 — low enough to ask for help.

---

### Step 2 — Build the Confidence Router

**File:** `components/agent/agent.py` (or new `components/agent/engine.py`)
**Change:** Replace the `if self._llm_client` binary switch with a three-way routing decision.

```python
HIGH_CONFIDENCE   = 0.80   # Execute directly
MEDIUM_CONFIDENCE = 0.50   # Ask LLM to validate
# Below MEDIUM    → LLM decides

def _route(self, prediction: Dict, state: Dict) -> Dict:
    confidence = prediction.get("confidence", 0.0)

    if confidence >= HIGH_CONFIDENCE:
        # Transformer is sure — execute directly, no LLM
        return prediction

    elif confidence >= MEDIUM_CONFIDENCE:
        # Transformer is somewhat sure — ask LLM to validate
        if self._llm_client:
            llm_verdict = self._llm_validate(state, prediction)
            if llm_verdict.get("decision") == "override":
                return self._llm_action_to_prediction(llm_verdict, state)
        return prediction   # Follow transformer if LLM unavailable

    else:
        # Transformer is unsure — LLM decides
        if self._llm_client:
            return self._llm_action_to_prediction(self._ask_llm(state), state)
        return prediction   # Best we can do without LLM
```

---

### Step 3 — Replace `LLMAgent` with `TransformerEngine` in `run_agent.py`

**File:** `run_agent.py`
**Change:** Swap the agent class and remove the LLM-first assumption.

**Current:**
```python
from agent.agent import LLMAgent

agent = LLMAgent(
    goal=GOAL,
    provider="groq",       # LLM is primary
    model_path=MODEL_PATH,
)
results = agent.run(max_steps=60)
```

**New:**
```python
from agent.engine import TransformerEngine   # new file

agent = TransformerEngine(
    goal=GOAL,
    model_path=MODEL_PATH,
    provider="groq",           # LLM is now optional safety net
    high_confidence=0.80,
    medium_confidence=0.50,
)
results = agent.run(max_steps=60)
```

---

### Step 4 — Improve the Planner (Alternative Approach)

The `Planner` class is already close to the right design. It needs two small changes:

**File:** `components/recorder/planner/planner.py`

**Change 1:** Replace fixed `llm_every` polling with confidence-based triggering.

```python
# Current (calls LLM every N steps regardless):
if self.provider != "none" and (self._step % self.llm_every == 0):
    decision = self._llm_evaluate(state, hist, decision)

# New (calls LLM only when transformer is uncertain):
if self.provider != "none" and decision.confidence < self.confidence_threshold:
    decision = self._llm_evaluate(state, hist, decision)
```

**Change 2:** Pass actual confidence to LLM prompt so it can make better decisions:

```python
# In _llm_evaluate(), add to the prompt:
f"Transformer confidence: {decision.confidence:.0%}\n"
f"Transformer suggests: {decision.action_type} at {decision.click_position}\n"
f"FORM LABELS: {labels}\n"
f"Decide: follow or override?"
```

---

### Step 5 — Log Low-Confidence Steps for Retraining

Every time the transformer has low confidence or the LLM overrides it, log it as a training example. This creates a feedback loop where the model gets better at the exact situations it currently struggles with.

**Add to agent loop:**
```python
if confidence < MEDIUM_CONFIDENCE or llm_overrode:
    self._retraining_logger.log({
        "state": state,
        "transformer_prediction": transformer_pred,
        "final_action": executed_action,
        "source": "llm_override" if llm_overrode else "low_confidence",
    })
```

These logged steps feed into `ContinualLearner` automatically — the model improves on its own weaknesses over time.

---

## 6. Summary of Changes Required

| # | What | File | Effort |
|---|---|---|---|
| 1 | Add confidence score to `predict()` return | `transformer.py` | Small |
| 2 | Build confidence router in agent loop | `agent.py` or new `engine.py` | Medium |
| 3 | Swap `LLMAgent` → `TransformerEngine` in entry point | `run_agent.py` | Small |
| 4 | Update `Planner` to use confidence instead of polling | `planner.py` | Small |
| 5 | Log low-confidence steps for retraining | `agent.py` | Small |

All five changes can realistically be completed by the April 3 deadline.

---

## 7. Expected Outcomes After Implementation

| Metric | Before | After |
|---|---|---|
| LLM calls per task | ~60 (every step) | ~10 (uncertain steps only) |
| Speed per step | 1–3 seconds (API wait) | ~0.1 seconds (local model) |
| Total task time | 2–3 minutes | 20–40 seconds |
| Works without API key | No (transformer only as fallback) | Yes (transformer runs by default) |
| Works offline | No | Yes |
| Improves over time | Only if manually retrained | Yes (auto-retrains on failures) |

---

## 8. Role Definitions (Final Architecture)

| Component | Role | Analogy |
|---|---|---|
| **TransformerAgentNetwork** | Executes most actions autonomously from learned behavior | The experienced employee who knows the job |
| **LLM** | Advises when the transformer is uncertain or stuck | The manager called in for edge cases |
| **BCTrainer** | Teaches the transformer from human demonstrations | The training program |
| **ContinualLearner** | Automatically improves the transformer over time | On-the-job learning |
| **ActionExecutor** | Physically carries out the decisions | The hands |
| **UIAutomationObserver** | Reads the current state of the screen | The eyes |

---

## 9. What This Means for the Capstone

The **Transformer model is the main academic contribution** of this project. It is the component that:
- Is trained on human data (original research)
- Learns generalizable behavior (not hardcoded rules)
- Improves continuously from experience (novel engineering)
- Works offline with zero API cost (practical value)

The LLM is a commodity tool available to anyone with an API key. It is not what makes this project novel or interesting. By making the transformer the primary engine, the capstone demonstrates a working **imitation learning system** — which is the actual thesis.

The goal is not "an LLM that can fill forms." The goal is **"a model that learned to fill forms by watching a human do it."** That distinction is everything.

---

*Prepared March 30, 2026 — Intern Capstone Project Group*
*Next steps: Implement changes by April 3 → Present progress at adviser consultation April 4–5*