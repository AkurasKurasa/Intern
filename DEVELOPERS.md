# Intern

*Your workflow, cloned.*

Intern watches you work, learns your actions, and over time handles GUI tasks
the way you would: by looking at the screen, moving the mouse, and pressing
keys. No file-system shortcuts, no app-specific scripting — only what a human
operator could do.

**The vision:** a general agent that learns a task *from a user's
demonstrations* — how *they* solve it — builds a workflow from those learned
actions, and executes it. The car-insurance form is the current **vertical
slice** that proves the loop end to end; everything else generalizes outward
from it.

This document is for developers working on Intern itself.

---

## Table of Contents

1. [Current Status](#current-status)
2. [How It Works](#how-it-works)
3. [Behavioral Cloning Process](#behavioral-cloning-process)
4. [Solved Problems](#solved-problems) — **what's behind us**
5. [Roadblocks Ahead](#roadblocks-ahead) — **the Big Three + Fundamental**
6. [Roadmaps to a General Agent](#roadmaps-to-a-general-agent)
7. [Quick Start](#quick-start)
8. [Repository Layout](#repository-layout)
9. [Components](#components)
10. [Current Goal](#current-goal)
11. [Wish List — Path to Full BC](#wish-list--path-to-full-behavioral-cloning)
12. [Task List](#task-list) — **P0 complete scope #1 → P1–P4**
13. [Scopes & North Star](#scopes--north-star)
14. [Questions, Concerns, and Concepts](#questions-concerns-and-concepts) — **open threads + design concepts**
15. [Finished Tasks](#finished-tasks)

---

## Current Status

**The core loop works end-to-end on the vertical slice.** Proven this far:

- **Navigation cloning confirmed** — the transformer reproduces the user's
  demonstrated field order. Trained on top-down → navigates top-down (74% exact);
  trained on bottom-up → navigates bottom-up (93% exact). Same architecture,
  opposite orders, each learned its own → **not hardcoded, genuinely cloning.**
- **End-to-end fill + submit** — live, transformer-driven: fills every field in
  the learned order (100% value accuracy via LLM), **clicks Submit on its own**
  (learned via tail-oversampling, not a hardcoded rule), advances to the next
  record.
- **Division holds:** transformer = WHERE (which element) + WHAT (click vs type);
  LLM = the value.

**Honest gaps remaining on the slice:** the form fills only **~4% (7/176 fields,
1 of 8 tabs, 1 record)** — it navigates and clones correctly but does not yet
*complete the task*. Closing that (multi-tab, multi-record, cold-start, looping)
is **the current priority** — see [Task List → P0](#task-list). The
scope-agnostic engine is already built (foundation); finishing scope #1 builds the
general muscle the other scopes reuse.

---

## How It Works

```
   ┌──────────────────────────┐         ┌──────────────────────────┐
   │   Source window          │         │   Target window          │
   │   (Notepad / Excel /     │         │   (wxPython form / web / │
   │    PDF / web / etc.)     │         │    spreadsheet / etc.)   │
   └────────────┬─────────────┘         └────────────┬─────────────┘
                │                                    │
                │ VLM screenshot                     │ UIA tree + OCR
                │ scan_tab / rescan                  │ snapshot
                ▼                                    ▼
         ┌──────────────────────────────────────────────────┐
         │                Observation (state)               │
         │   focused element │ field labels │ values │ bbox │
         └────────────────────────┬─────────────────────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │  LLMAgent.run() loop │
                       └──────────┬───────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
      ┌──────────────┐   ┌────────────────┐   ┌──────────────┐
      │  TaskPlugin  │   │     LLM        │   │ Transformer  │
      │ (form-fill,  │   │ (Anthropic /   │   │  (BC policy, │
      │  scroll,     │   │  Groq / Gemini)│   │   predicts   │
      │  tab-switch) │   │  decide what   │   │   click/key) │
      └──────┬───────┘   │  to do next    │   └──────┬───────┘
             │           └────────┬───────┘          │
             └────────────────────┼──────────────────┘
                                  ▼
                       ┌──────────────────────┐
                       │   ActionExecutor     │
                       │  (pyautogui)         │
                       └──────────┬───────────┘
                                  ▼
                          click / type / scroll
                                  ▼
                          Target window updates
```

The agent re-observes after every action and loops until the plugin signals
"done" or `max_steps` is reached.

**Perception is an adapter.** Today it's the UIA tree (accurate on native
controls). The downstream pipeline consumes a semantic element list
(`bbox, role, name, value, states, …`), so the perception source is swappable —
UIA now, a vision/VLM adapter later — without touching the learning layer. See
[Roadblock #1](#1-perception-uia--vision).

---

## Behavioral Cloning Process

The full loop for teaching Intern a task from human demonstrations.

```
┌─────────────────────────────────────────────────────────────┐
│  TRAINING PIPELINE                                          │
│                                                             │
│   STEP 1 — RECORD                                           │
│     python app/main.py   (GUI recorder)  or record_trace.py │
│     Human demos the task (target form + Notepad source)     │
│                      ↓                                      │
│   STEP 2 — CLEAN                                            │
│     python scripts/clean_demos.py <src> <dst>              │
│     Drops dropdown-SELECTION clicks (value-picks that land   │
│     on the field under an open dropdown), off-form-window    │
│     junk, pane clicks, consecutive dupes.                    │
│                      ↓                                      │
│   STEP 2b — (optional) OVERSAMPLE THE FINISH                │
│     python scripts/oversample_tails.py <clean> <dst>       │
│     Copies each cycle's tail (… → Submit) K× so the rare    │
│     "form complete → Submit" transition is well-represented │
│     → model LEARNS to submit (no hardcoded completion rule). │
│                      ↓                                      │
│   STEP 3 — TRAIN                                            │
│     python train.py --trace_dir <dir> --epochs 80 \         │
│       --d_model 128 --num_layers 4 --dim_feedforward 256    │
│     Trains TransformerAgentNetwork. Best checkpoint on       │
│     (val_acc + click_acc).                                   │
│                                                             │
│  Goal: imprint human demo behavior into model.pt            │
└─────────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────────┐
│  INFERENCE + EVALUATION                                     │
│                                                             │
│   STEP 4 — RUN & EVALUATE                                   │
│     python run_task.py                                      │
│     Transformer: WHERE to click + WHAT action (click/type)  │
│     LLM (LM Studio): supplies the value when action = type  │
│                                                             │
│   Offline clone check (no live form):                       │
│     python scripts/test_clone.py <session_dir>              │
│     Compares model's click_elem_idx to the user's clicked    │
│     element per frame → exact% + offset distribution.        │
│                                                             │
│   Auto-runs after a live run:                               │
│     eval_metrics.py  → TCR, field/value accuracy            │
│     bc_fidelity.py   → BC score vs gold standard            │
│     rule_extractor.py→ infers task ruleset from trace       │
└─────────────────────────────────────────────────────────────┘
                        ↓
       (poor metrics → clean/record more → retrain → repeat;
        DAgger: on a failure the agent watches ~4s for the
        user to do the right action and saves it as a trace)
```

**Division of responsibility:**
- **Transformer** — *where* to click (which element) **and** *what* action
  (click vs type), learned from demos.
- **LLM** — *what* value to type (from source data). Reserved for the part that
  needs understanding, not clean lookups.

### Key features that make navigation learnable
- **`is_filled`** (per element) — does this field currently hold a value, read
  straight from the observation. Without it the model only saw field *labels* and
  was blind to which fields were done → it looped. This is *perception*, not a
  hand-tracked progress signal.
- **`is_focused`** — which element has keyboard focus.
- **Action-space collapse → {click, type}** — junk classes (stray `drag`,
  `backspace` hotkeys) were dropped/remapped so the action-type head stopped
  collapsing onto classes it could never predict. Action-type accuracy 50% → 80%,
  and click accuracy rose for free.

### Testing variations (does it clone, or just memorize?)
Record demos in multiple fill orders and confirm the agent reproduces each:
- **Top-down** — fields top-to-bottom (baseline). ✅ confirmed
- **Bottom-up** — fields bottom-to-top. ✅ confirmed (93%, proves not a down-bias)
- **Random (fixed)** — a chosen non-obvious order, repeated consistently. *pending*

A clone that handles a *different* demonstrated order proves field-level learning,
not sequence memorization. `scripts/test_clone.py` reports exact% + the offset
distribution (0 = exact, +1 = next-down, −1 = next-up) so you can see the
*direction* it learned.

---

## Solved Problems

What's *behind* us — the mirror of the roadblocks ahead. Each was a real blocker;
each is now closed with a verification that guards against regression.

### Behavioral cloning — the core thesis
- **The transformer clones the demonstrated order, not a bias.** Top-down→74% /
  bottom-up→93% exact on each order's own data. Same architecture, opposite
  orders → genuine cloning. *(Proof: offset-distribution analysis, `test_clone.py`.)*
- **WHERE/WHAT division works.** Transformer drives element + click/type; LLM
  supplies the value. Live: 100% value accuracy when on the right field.
- **Learned the finish.** Clicks Submit on its own via tail-oversampling — no
  hardcoded end-game rule.

### Perception / data quality
- **Looping from fill-blindness.** Model re-entered filled fields because state
  embedded only labels, never values. Fixed with the `is_filled` feature + folding
  value into the embedding → click_acc 0.95→1.00.
- **Action-type collapse.** Junk classes (backspace/drag) wrecked the action head.
  Collapsed action-space to {click, type} → action-type 29/51% → 80/80%.
- **Combobox phantom fields.** Clicks while a dropdown was open landed on the field
  beneath it → recorded a phantom order. Fixed at record time (drop selection-clicks).

### Generalization foundation *(Tier 1 — in progress, two closed)*
- **✅ Form-specific hardcodes killed → injected `ScopeConfig`.** `_detect_section`,
  `_KNOWN_TABS`, `_TAB_PANE_NAMES`, the `RECORD N OF M` delimiter — all moved out of
  agent.py into a per-scope config with generic defaults. The agent is now
  **application-blind**; a new scope passes its own config with zero agent edits.
  *(Guard: `tests/test_detect_section.py` — 8/8, locks behavior + proves the default
  scope is a no-op. Live-verified: form fills + submits unchanged.)*
- **✅ Perception adapter seam.** The observer is now **injectable** (UIA / Excel /
  web plug into one slot), and a canonical element schema (`observers/schema.py`)
  defines the one language every adapter must speak. The agent **validates the
  adapter on first observe and fails LOUD** — no more silent blank-screen when an
  adapter emits a different dialect (e.g. Excel `type=cell` / value-under-`text`).
  *(Guard: `tests/test_perception_schema.py` — 8/8, conforming passes clean, Excel's
  raw dialect is flagged with actionable errors.)*

> Still open in Tier 1: generalize the data-source layer, and the scope
> abstraction (declare a scope in one place). See [Task List](#task-list).

---

## Roadblocks Ahead

The honest obstacle map between the current vertical slice and a general
learn-from-demonstration agent. Split into the **Big Three** (architectural —
what makes it *generalize*) and the **Fundamental** roadblocks (what makes it
*actually work and be trustworthy*).

### The Big Three (Architectural)

#### 1. Perception: UIA → Vision
Today perception reads the **UIA accessibility tree** — which hands us
`bbox, role, label, value, focused, filled` for free, but only on apps that
*expose* a clean tree (native/wxPython forms). **Most apps don't** — web,
Electron, canvas, custom-drawn UIs, games. A general agent must **see pixels**:
screenshot → a vision model that **localizes** elements *and* does **semantic
contextualization** (what each thing is, its label, its state).

- The schema generalizes if it mirrors the **accessibility element model**
  (`role + name + value + states[] + actions[] + structure`), not the form
  subset. `filled` is form-specific → it becomes a derived check; general state
  lives in `states[]`.
- **But the abstracted element list alone is lossy** — it drops color coding,
  charts, progress fills, badges, grayed-out cues, scene-level meaning, and
  *continuous/non-widget* surfaces (sliders, maps, drawing, terminals) entirely.
  General perception is therefore **pixels + element overlay + scene
  understanding**, with a VLM reasoning over both — which is exactly why frontier
  computer-use agents feed the raw screenshot, not just a parsed tree.
- **A VLM is the preferable general perceiver** (it reasons over pixels +
  meaning), but it is *not* free: its **grounding precision** (exact click
  coordinates) is the #1 weakness, plus cost/latency per step and
  non-deterministic reliability (dangerous for unattended/irreversible actions).
- **Best-of-both now:** hybrid — UIA/a11y for precise grounding where it exists,
  VLM for understanding and for apps with no tree.
- **The clean part:** the learning layer is **perception-agnostic** — it consumes
  the semantic element list regardless of source. Swap the *adapter*, keep the
  brain. The VLM replaces perception; it does **not** learn the user's workflow —
  that stays Intern's job.

> **Biggest single change.** Everything downstream depends on perception, and
> leaving clean native controls is the main generalization wall.

#### 2. Action Space: form-fields → universal
Today: click / type / select (+ a combobox handler). A general agent needs a full
vocabulary — `click, double_click, type, select, drag, scroll, hotkey, wait,
verify, menu, file-dialog`. Tractable: the action-type head already supports
multiple classes; the executor is modular (one handler per action). This is
**enumerable engineering**, not research — add handlers + demos that exercise
each action.

#### 3. Workflow Representation: linear → control flow
Today's task is **linear** — same steps, same order, every run. Real tasks have
**control flow**:
- **Branch** — *if* status = lapsed, fill reinstatement; *else* skip.
- **Loop** — fill a block *for each* driver (count varies: 1 or 5).
- **Conditional skip** — *if* already filled, skip; *if* popup, dismiss.
- **Error path** — *if* validation error, fix and retry.

**Why it's hard:** a demonstration is *one linear path* — it shows *what* you did,
not the *rule* for *when*. Recovering "do 4 driver blocks because there are 4
drivers" (a loop) or "skip reinstatement because status = active" (a condition)
means **inferring the program behind the actions** from *varied* demos — classic
**program synthesis from examples**, research-grade. The pragmatic path: **let an
LLM be the synthesizer** — feed it multiple demo traces, have it propose a
workflow *with* `if`/`for`, then **execute + DAgger-correct** it. No formal
synthesis engine; the LLM induces, execution validates.

### Fundamental Roadblocks

These don't block *generalization* — they block *working reliably and being
trustworthy*. Several are sneakier and more dangerous than the Big Three.

- **A. Hidden intent / partial observability. ⚠️ a ceiling, not a bug.** The
  screen doesn't show *why* you act. Same screen → different action based on
  knowledge in your head. Some workflows are **unlearnable from screen+action
  alone** because the deciding info isn't on screen.
- **B. Error detection & recovery.** Does the agent *know* it failed (click
  missed, value didn't save, validation error)? Without self-monitoring, errors
  compound silently.
- **C. Verification — "did it do the task correctly?"** Confirming success (not
  just "ran N steps"). We struggled to even *measure* on one form.
- **D. Safety / irreversibility / trust.** Submit, delete, send, pay are
  irreversible. An unattended general agent needs guardrails, confirmation gates,
  sandboxing, rollback — and earned trust to run at all.
- **E. Data-collection burden.** Every task needs clean demos; recording is
  laborious and noisy (combobox/pane pollution). Scaling across tasks is a
  bottleneck.
- **F. Timing / async / readiness.** Knowing *when* the screen is ready and an
  action completed (dropdown render races, spinners, network waits).
- **G. Cross-app / system-level.** Real tasks span apps, files, tabs, OS dialogs,
  copy-paste, window switches (already 2 apps: Notepad → form).
- **H. Long-horizon memory.** Tracking what's done across many screens / which
  record/iteration, past the model's context window.
- **I. Concept drift / maintenance.** App UI updates → learned workflow breaks.
  Workflows rot and need re-learning.
- **J. Cost / latency.** VLM + LLM per step = slow and expensive, brutal locally.
- **K. Privacy / security.** Screen-recording captures PII and secrets — handle
  responsibly.
- **L. Task segmentation.** Cutting a continuous stream of user activity into
  discrete, learnable workflows.

> **The scariest are A, C, D** — fundamental (hidden intent caps what's
> learnable) and trust-critical (without verification + safety, a "working"
> general agent is unshippable). They get less attention than perception because
> they're less glamorous.

---

## Roadmaps to a General Agent

Four coherent paths, differing in how the **workflow** is represented.

| Roadmap | Workflow = | Pros | Cons |
|---|---|---|---|
| **1. Pure BC** *(current)* | implicit in model weights | captures style, no rules | no inspectable workflow, data-hungry, brittle at rare states |
| **2. Workflow induction (RPA-style)** | explicit step graph | inspectable, editable, reliable, no retrain | rigid on novel states; control-flow induction is hard |
| **3. Hybrid ⭐** | explicit skeleton + ML in the gaps | a workflow you can *see*, executed *robustly* | most moving parts |
| **4. LLM-skill agent** | a learned prompt/skill | flexible, minimal training | LLM cost/reliability, captures *what* > *how/style* |

**Recommended: Roadmap 3 (Hybrid).** It's the only one that delivers all three
sub-goals (*learn how the user solves* → *create a workflow* → *execute*) **and
reuses what's built**: the BC transformer becomes the perception/execution muscle
under an explicit, induced workflow the LLM can reason over.

```
Perception adapter (UIA │ Vision-VLM)   →  semantic elements (+ pixels, scene)
        ↓
Workflow (LLM-induced from demos: steps + if/for)        ← Intern's novel value
        ↓  per step
Transformer (which element / WHERE) + LLM (value / WHAT)
        ↓
Executor (modular handlers) + verification + safety gates + DAgger correction
```

**Strategic fork:** build perception from scratch (own UIA→vision) **vs** stand
on an existing computer-use/VLM model for perception+grounding and make Intern's
contribution the **learn-the-user's-workflow** layer on top. The latter is far
more realistic solo.

**Staged reach:** Stage 0 ✅ vertical slice → Stage 1 domain-general (several
native apps, linear, achievable solo) → Stage 2 vision perception → Stage 3
control flow → Stage 4 broadly general (frontier / team-scale). Depth first, then
breadth.

---

## Quick Start

```bash
# 1. Install Python deps (Windows; UIA + pyautogui are Win-specific)
pip install -r requirements.txt

# 2. Put API keys in .env at repo root
#    GROQ_API_KEY=...        # free, used for VLM + (optionally) LLM
#    GEMINI_API_KEY=...      # free, used as VLM fallback when Groq 429s
#    ANTHROPIC_API_KEY=...   # paid, best LLM reasoning quality

# 3. Open the target form
python car_insurance_entry/car_insurance_form_wx.py

# 4. Open the source data in Notepad
notepad data_entry_tasks/data_entry_intake.txt

# 5. Run the agent
python run_task.py
```

Configure `run_task.py` knobs at the top:

| Constant         | Default                  | Notes                                |
|------------------|--------------------------|--------------------------------------|
| `PROVIDER`       | `"lmstudio"`             | Switch to `"groq"` / `"anthropic"` for real reasoning. |
| `MAX_STEPS`      | `200`                    | Hard cap per run.                    |
| `SOURCE_WINDOW`  | `"Notepad"`              | Title fragment of the source window. |

---

## Repository Layout

```
app/
  main.py                    GUI recorder (Start/Stop, replay, frame counter)
components/
  agent/
    agent.py                 LLMAgent — main loop, provider abstraction, merge
    capsule.py               Per-task model routing (goal → .pt file)
    task_plugins/            Task-specific plugins (form-fill, etc.)
  data_sources/
    notepad_source.py        Win32 WM_GETTEXT + parse_records helpers
  intelligence/
    model/
      transformer.py         TransformerAgentNetwork — BC policy model
    rule_extractor.py        LLM-based task spec generator / corrector
    training/                bc / rl / continual trainers
  observers/
    ui_observer/             UIA tree walker (semantic element list)
    vlm/                     VLM screenshot → key/value extraction
  recorder/
    recorder.py              DemoRecorder (on-demand subprocess snapshots)
    correction_handler/      DAgger: watch-for-user-correction on failure
tasks/
  registry.json              Global capsule registry (goal → model path)
  form_filling/
    model.pt                 Trained BC checkpoint
    ruleset.md               Inferred task spec (auto-updated each session)
car_insurance_entry/         wxPython target form (test fixture)
data_entry_tasks/            Source intake .txt files
data/demos/                  Recorded sessions (gitignored)
scripts/
  clean_demos.py             Drop dropdown-selection / junk / dupe clicks
  oversample_tails.py        Emphasize the … → Submit finish
  test_clone.py              Offline clone check (exact% + offset)
  augment_traces.py          Dataset augmentation (jitter)
  eval_metrics.py            TCR / field / value accuracy
  bc_fidelity.py             BC fidelity score vs gold standard
run_task.py                  Agent entrypoint (transformer + LLM)
replicate.py                 Duplicate a recorded session N× (terminal)
train.py                     BC training entrypoint
build_capsule.py             Package model + metadata into capsule
```

---

## Components

### Agent
- **`components/agent/agent.py`** — `LLMAgent` orchestration loop, multi-provider
  LLM support (Anthropic / Groq / Gemini / LM Studio). `_merge` glues transformer
  (WHERE/WHAT) + LLM (value). `disable_auto_handlers` runs pure transformer+LLM
  with legacy heuristics gated off.
- **`components/agent/capsule.py`** — Routes goal + window title to the correct
  `.pt` checkpoint via `tasks/registry.json`.

### Transformer (BC Policy)
- **`components/intelligence/model/transformer.py`** — `TransformerAgentNetwork`.
  Causal transformer trained via behavioral cloning. Predicts: action type
  (click/type after action-space collapse), which element to click (pointer head),
  which source element holds the value (source pointer head).
- Input per element: `is_real, bbox(4), confidence, window_role, is_focused,
  ctrl_type, is_filled, text_embedding(384)` (= `ELEM_FEATURES`).
- **LayerNorm on pointer heads** — prevents bilinear Q×K divergence.

### Observers
- **UI Automation Observer** — Walks the UIA tree → semantic element list
  (`element_id, type, label, bbox, value, focused, window_role`).
- **Vision Observer / Visual Data Reader** — Screenshot → VLM → JSON of visible
  field/value pairs. The slot for the future vision *perception adapter*.
- **OCR fallback** — Tesseract over background pixels when UIA returns empty.

### Recorder & Corrections
- **`components/recorder/recorder.py`** — `DemoRecorder`. On-demand subprocess
  UIA snapshots (off the main GIL). Clicks always get a fresh snapshot (focus
  accuracy). Filters: own-GUI-window clicks, F-keys, dropdown-selection clicks.
- **`correction_handler/`** — DAgger hook: on a validation failure the agent
  opens a ~4s window, captures the user's correcting action + state, saves it as
  a trace.

### Data Sources
- **`NotepadDataSource`** — Win32 `WM_GETTEXT` + `_parse_records` multi-record
  parser + field-line lookup helpers.

### Action Executor
- pyautogui mouse/keyboard. All actions go through this — no OS-level shortcuts a
  human couldn't do.

### Rule Extractor
- **`components/intelligence/rule_extractor.py`** — `extract()` derives rules from
  a completed run; `correct()` reads spec + new demo → corrected spec.

### Chain-of-Thought (LM Studio only)
Injects a `<think>…</think>` reasoning step for local models, stripped before JSON
parsing. Not used for Anthropic (native reasoning is better). Disable by removing
the CoT lines in `_call_openai_compat()`.

---

## Current Goal

> **Finish the vertical slice, then prove generalization.**
>
> Near-term: complete the third order-cloning test (random), make multi-record
> fill+submit reliable, and tighten the cold-start. Then the real leap —
> [Roadblock #1 + #3](#roadblocks-ahead): a perception adapter (UIA→vision) and
> an LLM-induced workflow layer, so the same loop learns *new* apps/tasks from
> demonstration.

---

## Wish List — Path to Full Behavioral Cloning

### Stage 1 — Data volume & quality *(days)*
- [ ] Record clean, consistent demos per order (top-down ✅, bottom-up ✅, random).
- [ ] Always `clean_demos.py` before training (strips combobox/pane noise).
- [ ] Retrain after each batch; check `test_clone.py` exact% + offset.

### Stage 2 — Model quality *(parallel)*
- [x] Scale model to `d_model=128, num_layers=4`.
- [x] Action-space collapse → {click, type} (action-type 50% → 80%).
- [x] `is_filled` perception feature (stopped end-game looping).

### Stage 3 — Reliability
- [ ] Multi-record: fill + submit all records in one unattended run.
- [ ] Cold-start: reliable first click (DAgger or learned start signal).
- [ ] Combobox: kill open→miss→retry (timing).

### Stage 4 — Generalization (the leap)
- [ ] Perception adapter interface (UIA + vision swappable).
- [ ] Explicit workflow induction from demos (LLM-as-synthesizer).
- [ ] Second app/task on the same architecture, zero code changes.

### Stage 5 — RL phase (future)
- [ ] Actor-Critic / PPO with the BC transformer as Actor; KL penalty vs BC to
      preserve style. Online fine-tuning from `StateValidator` reward.

---

## Task List

**Priority: COMPLETE SCOPE #1 (the form) first — then generalize.**

The scope-agnostic *engine* is built (foundation below). But the form itself only
fills **~4% (7/176 fields, 1 of 8 tabs, 1 record)** — it does not yet *complete a
task*. Chasing a 2nd scope before the 1st completes is premature: you'd spread
across scopes while none works end-to-end.

> **Reframe — finishing the form IS generalization work.** Its remaining gaps —
> multi-tab navigation, multi-record, cold-start, looping — are *general*
> capabilities every scope hits. Solve them once, on the form. Only
> combobox-timing / pixel-perfect are throwaway polish. So "finish scope #1" is
> not a detour from the thesis; it builds the muscle Excel + triage will reuse.

> **The foundation refactor was still right to do early** (cheap on one scope, no
> dependency on the form being finished). What was premature was *jumping to a 2nd
> scope* — that's the order we corrected.

---

### ✅ Foundation — scope-agnostic engine *(DONE — keep)*
- [x] **ScopeConfig** — form hardcodes (`_detect_section`, `_KNOWN_TABS`,
      `_TAB_PANE_NAMES`, `RECORD N OF M`) → injected config. Agent app-blind.
- [x] **Perception adapter seam** — `Observer` base (capture→normalize→validate);
      observer injectable; canonical element schema (`observers/schema.py`); loud
      validation. UIA identity (live-verified), **Excel perception swap PROVEN**.
- [x] **DataSource injection** *(partial)* — `data_source` injectable; agent reads
      source I/O through the seam. Follow-up: extract `_refresh_record_cache`
      orchestration.
- [ ] **Scope abstraction** — declare a scope (goal+config+observer+source+model)
      in ONE place, not scattered in `run_task.py`. *Defer: do when wiring scope #2.*

---

### 🔴 P0 — Complete Scope #1 (the form)  *— THE focus*

**Definition of done:** agent fills **all 5 records**, **all 8 tabs + Driver/Vehicle
sections**, in the demonstrated order, **submits each**, **no human help** —
Completion ~100%, Field-Match high, low wasted steps.

**Where we are (2026-06-12, evening):** the model fills **3 FULL tabs**
(Policy → Policyholder incl. below-fold phones → Vehicle, 13 fields) in one run —
**19 fields, 100% value-acc, 100% action-acc, 2.8% wasted, ZERO drift.** Drift,
empty-field fixation, and below-fold reach are all SOLVED (see Stage 2). The model
only knows **3 tabs** (the demos go Policy→Policyholder→Vehicle→Submit&New→next
record), so after Vehicle it correctly loops back toward a new record — it has no
training for Coverage→Payment. **The remaining gate is DATA: demos through all 8
tabs.** Commit d859b5e.

**Dependency chain:**
```
[DONE] Option B + combobox click-fill + false-done guard (Policy @0.9, no whipsaw)
[DONE] multi-tab traversal (transformer predicts+switches tabs itself)
[DONE] 'attempted' feature (kills empty-optional loop) + form-window lock +
       in-form click guard (kills drift; Tab reaches below-fold) → 3 FULL tabs
   → [NEXT] 8-tab demos + retrain → all tabs fill (kills the "loop back after Vehicle")
   → multi-record ×5 + per-record data
   → end-to-end verification (Completion ~100%)
```

#### Stage 1 — Multi-tab traversal
- [x] **Combobox click-fill** — empty combobox click → open+select (no spiral).
- [x] **False-done guard** — Notepad intake text no longer triggers false completion.
- [x] **Option B (WHERE/HOW division)** — the **focused widget's type** decides
      fill-vs-navigate, replacing the unstable action-type head. Result: Policy tab
      fills cleanly @~0.9 conf, **whipsaw gone.** (See Decisions.)
- [x] **TAB-TARGETING — SOLVED (2026-06-12).** Recorded 20 transition-dense passes
      (3 fields/tab → click-switch) into `three_Tabs` (now 40 total: 20 full + 20
      dense), retrained on the combined set. **The transformer's pointer now predicts
      the tabs itself** — live: Policy → (pointer 992,136, conf 0.93) → Policyholder
      → (pointer 1070,136, conf 0.73) → Vehicle, filling fields on each. **0% wasted,
      1.8 steps/field, 100% value-acc.** No oversampling — real dense data did it.
- [x] **MULTI-TAB TRAVERSAL WORKS** — transformer-driven 3-tab switch + fill, clean.
      *(P0 Stage 1 core goal achieved.)*

#### Stage 2 — Reliability: no stalling  *(engineering)*
- [x] **Empty-field fixation → SOLVED (2026-06-12).** `'attempted'` state-feature
      (ELEM_FEATURES 394→395): once a field is acted on this session, attempted=1 so
      the transformer stops re-targeting it. Killed the Suffix loop `is_filled` can't
      (empty field's is_filled never flips). Retrain: val 0.60→0.781, click 0.705→0.855.
- [x] **Drift → SOLVED (2026-06-12).** Form-window LOCK (capture hwnd at GO,
      re-assert foreground every step) + in-form click guard (target outside the live
      form rect → Tab, not a drifting click). No more typing into PowerShell / clicking
      Notepad. Observation + action always on the form.
- [x] **Below-fold reach → SOLVED (2026-06-12).** The guard's Tab doubles as scroll:
      wx ScrolledPanel auto-scrolls the focused field into view, so Tab reaches
      off-screen fields (Home/Cell Phone filled). Backstop: record∩visible
      scroll-to-reveal trigger on the live form viewport.
- [x] **'99' double-type → FIXED (2026-06-12).** Typing now idempotent (select-all
      before paste) so a retried step overwrites instead of appending.
- [ ] **Checkbox cold-start / first-click** — reliable first action from a blank
      screen (Renewal checkbox handling; DAgger / start-signal).
- [ ] **Combobox retry** — open→miss→Escape→retry; timing.

#### Stage 3 — Multi-record: all 5  *(data + eng)*
- [ ] **Record advance ×5** — proven 1→2; need all 5 clean.
- [ ] **Per-record data refresh** — `refresh(record_num)` for records 2–5 (only
      record 1 verified).

#### Stage 4 — Verification: prove it's done  *(engineering)*
- [ ] **End-to-end metric** — Completion 0% → ~100%, Field-Match 12% → high.
- [ ] **Expected-vs-actual diff at submit** — per-record correctness report.

> **Who does what:** YOU = record **full 8-tab demos** (Policy→…→Payment, all
> fields, Submit&New) — the current gate — + run live tests. ME = retrain, cold-start,
> per-record refresh, verification harness. BOTH = retrain + test loop.

---

### 🟠 P1 — Generalize *(only AFTER #1 completes a task)*
- [ ] **Finish scope abstraction** (foundation #4).
- [ ] **Excel full transfer** — wire source/target + executor for cells, record
      Excel demos, train, measure clone. *Perception swap already PROVEN; this is
      the action + data + model half.*
- [ ] **Random-order test** — closes "clones *any* order, not a bias."

### 🔵 P2 — Scope #3
- [ ] **Email / ticket triage** — needs Action-Space (Roadblock #2) +
      Control-Flow (Roadblock #3). Surface those deps before starting.

### 🟢 P3 — Polish & known issues *(non-blocking)*
- [ ] **LLM value errors** — local LLM sometimes returns the wrong field's value
      (e.g. Policy Number into Policy Term). LLM keeps owning values; fix = better
      prompt or lookup-as-validator (not silent replacement).
- [ ] **No prompt caching** — each step is a fresh LLM call; no batching / budget.
- [ ] **No memory component** — each record runs from a blank slate.
- [ ] **Observability** — no structured trace / record-level summary; no screenshot
      history for VLM mis-reads. *(expected-vs-actual diff is P0 Stage 4.)*
- [ ] **Combobox / mid-record crash recovery.**
- [ ] **Cross-task shared backbone** (trunk + per-task heads).
- [ ] Ghost cursor overlay; training-readiness indicator; DAgger productionized.

> **Architectural limits** (UIA-only / no vision, `is_focused`+`is_filled` under
> vision, no control flow, no memory) are tracked in
> [Roadblocks Ahead](#roadblocks-ahead) — not duplicated here. **Recently solved**
> (form hardcodes, RECORD delimiter, unit-test net) are in
> [Solved Problems](#solved-problems).

### ⚪ P4 — Non-system: thesis / data / benchmark *(parallel track)*
- [ ] **Thesis** — finish Chapter 3; revise paper if nominated.
- [ ] **Data collection** — 500,000 traces in a convenience-sampling setting.
- [ ] **Benchmark** — compare against similar systems.
- [ ] **Chess fidelity benchmark** — clone a human's play (openings, tendencies,
      time management, blunders); clone-ELO ≈ human-ELO + matching style = BC of
      decision-making, not rote actions.

---

## Scopes & North Star

*Reference — the thesis-completion criteria and the generalization vision. The
actionable work lives in the [Task List](#task-list) (P0–P4); this is the* why.

### The three scopes *(thesis = all three)*
Chosen to span the interesting space — *data entry*, *cross-app transfer*,
*conditional judgment* — so the claim is "clones varied GUI workflows," not "fills
one form."

1. **Data Entry Form Filling** — *in progress ([P0](#task-list)).* Single-app
   key-value entry, (mostly) linear. Loop proven on the Policy section (clones
   order, fills, submits); remaining = multi-tab, multi-record, cold-start.
   Perception = UIA.
2. **Web Form → Excel** — *not started ([P1](#task-list)).* Cross-application
   transfer (web source → Excel grid); 2D target; mixed perception. Excel
   perception swap **PROVEN** (`ExcelObserver` normalizes to canonical); remaining
   = web source, action on cells, demos, train.
3. **Email / Ticket Triage** — *not started ([P2](#task-list)).* Decision-making /
   conditional behavior; the strongest personalization claim (two users triage
   differently). Needs branching ([Roadblock #3](#roadblocks-ahead)) + judgment
   cloning. Kept to decisions inferable from *visible* content (avoid hidden-intent,
   Roadblock A).

### North Star — generalization (beyond the thesis)
The thesis is *bounded* to those three, but the **architecture is built to
generalize** — that is the real goal. The novel contribution: a **personalized,
demonstration-learned GUI agent** that learns how *this user* does a task and
reproduces *their* workflow — which scripted RPA (no learning) and generic
computer-use agents (not personalized) don't.

Already partly real: perception is an **adapter** (UIA + Excel today, one shared
schema) and the `transformer(WHERE) + LLM(WHAT)` loop is perception-agnostic. The
path beyond the thesis — a vision perception adapter
([Roadblock #1](#roadblocks-ahead)) + LLM-induced control-flow
([Roadblock #3](#roadblocks-ahead)) — turns "three GUI scopes" into "any GUI
workflow learned from demonstration."

---

## Questions, Concerns, and Concepts

Open threads, honest unknowns/risks, and the **design concepts** (architectural
decisions + approaches like the WHERE/HOW division and DAgger) that shape how the
system evolves. **Questions** = unknowns to resolve · **Concerns** = risks to watch
· **Concepts** = decisions made + design patterns to build toward.

### Decisions & concepts

**WHAT does the transformer learn — Pure (A) vs Division-of-labor (B)? → CHOSE B.**
The 3-tab navigation marathon (2026-06-11) exposed that the transformer's
*action-type head* (deciding click-vs-type per step) is **unstable** — it whipsaws
between all-click (combobox/field spiral) and all-keyboard (tabs through, never
clicks) across retrains. Two ways forward:

- **Option A — Pure transformer.** The transformer learns *everything*, including
  click-vs-type, purely from demos, **zero rules.** Honest cost: a days-to-weeks
  data + training grind (more consistent + transition-dense demos, bigger model,
  stable recipe, likely DAgger), with real risk of a BC ceiling (long-horizon +
  rare events + compounding error is the textbook case where pure BC underperforms).
- **Option B — Division of labor *(CHOSEN)*.** The **transformer drives WHERE** —
  which element, what order, *when to switch tabs* (the learned, personalized
  navigation = the thesis claim, 100% intact). **Widget TYPE drives HOW** — type
  into an editcontrol, click a tabitem, open+select a combobox, toggle a checkbox
  (universal GUI mechanics, identical for every user/app — *not* form-specific).

**Why B:** click-vs-type is **not personalization** — it's universal plumbing, true
for every app. Forcing the unstable head to learn it blocks scope #1 for zero
thesis benefit. B keeps the personalization claim ("learns the user's navigation/
order/workflow") fully intact while freeing the one unstable decision onto rules
that **already exist in the agent** (combobox/checkbox handlers etc. are already
widget heuristics — the agent was never "pure"). Net: ~5 universal widget→action
rules, no form-specifics, navigation still 100% learned. Finishes scope #1 in
hours vs weeks. *(If a reviewer asks: the claim is "clones the user's workflow,"
not "learned how to physically touch every widget with zero rules.")*

### DAgger — how to implement *(if/when pure-BC drift needs it)*
The principled fix for the live drift seen on 2026-06-11 (agent leaves the form,
oscillates) = train on the states the agent ITSELF visits, not just clean demos.
Loop: train → run policy → label the correct action for visited (drift) states →
aggregate → retrain → repeat. Labeling is the only real choice:
- **Manual (closest to ready):** the `CorrectionHandler` seam already exists but
  **captures 0 steps (broken)** — fix it so after a failed/no-change step the agent
  pauses, the user performs the right action, and `(state, action)` is recorded.
  Targeted (only fix mistakes), far cheaper than re-recording passes.
- **Autonomous (no human):** demo-retrieval oracle — embed the visited state
  (`encode_state`), find the nearest demo state, copy its action as the label.
  Approximate but fully automatic; fits the "learn from demos, not manual" goal.
- **Blocker:** `CorrectionHandler` capturing 0 is *why DAgger isn't usable yet* —
  un-breaking it is the first concrete step. Needs a few run→correct→retrain rounds
  to converge.

### Open technical questions
- **Loss-weighting is less plug-and-play — how do we improve it in the future?**
  The 2026-06-12 rare-event fix (up-weight the rare action's frames in the loss so
  the model learns it without distorting the data — see Concepts) still requires
  *manually labelling which action class is rare* (here: clicks whose target is a
  `tabitem`). For a truly general system, that labelling should be **automatic.**
  Directions to explore:
  - **Auto-detect rare classes by frequency** — at dataset build, histogram the
    target action classes (per element-type / per action), then weight each
    **inversely to its frequency** (`w ∝ 1/freq`). Zero manual tagging; any rare
    action self-emphasizes. The general version of what we hand-built for tabs.
  - **Focal loss** — automatically down-weights easy/common examples and focuses
    gradient on hard/rare ones, *without* any class labels at all. The most
    plug-and-play; no frequency bookkeeping.
  - **Make rare actions CONDITIONAL (state feature)** — give the model a signal
    that predicts the rare action (e.g. "section-complete fraction"), so rarity
    stops mattering. More robust but needs the trigger identified per action.
  → Likely end-state: **inverse-frequency auto-weighting (or focal loss) as the
  universal default**, with optional state-features for robustness where a clear
  trigger exists. Reusable across every scope's rare actions.
- **Will full-form cloning scale on data alone?** Policy tab (~10 fields) needed
  ~20–30 passes. The full form is 176 fields / 8 tabs with rare **tab-transition**
  events. Unknown whether ~30–50 passes clone it, or whether long-trajectory BC
  needs far more demos than is feasible to hand-record. *(The `three_Tabs` probe
  is the cheap test of this.)*
- **Cold-start without hardcoding.** First click from a blank screen is ~0% and
  proven resistant to data + model size. Needs DAgger / a learned start-signal —
  but a deterministic anchor is off the table (no hardcoding). Open: how?
- **Is the >95% transformer-dependency target even right?** Stuck ~62–68%. By
  design the LLM owns *values*, so the transformer can't drive the value-typing
  steps. Maybe the honest ceiling is lower and the metric should exclude
  value-steps — needs a definition decision.
- **Excel action half is untested.** Perception swap is proven, but *acting* on
  cells (click → type → Enter, grid navigation) isn't. Will the form-specific
  executor handlers (combobox/checkbox) interfere on Excel? Probe before P1.
- **Cycle-restart loop root cause** — new record re-clicks the already-checked
  Renewal checkbox → Tab → no-change loop. Is it a per-record state-reset bug or a
  model issue? Decides whether it's an easy fix or needs data.

### Strategic / thesis concerns
- **Is perception-swap enough evidence, or must we show full Excel completion?**
  We proved Excel *perception* conforms (zero edits). The stronger claim — a model
  actually completing a task on a 2nd app — needs action + demos + training. How
  much does the thesis require?
- **Scope #3 (triage) is blocked on un-scheduled roadblocks** — Action-Space (#2)
  and Control-Flow (#3) are parked in North Star but triage *depends* on both.
  They must be scheduled, or scope #3 stalls.
- **Web perception may force the vision adapter early.** Scope #2's web source may
  lack a clean accessibility tree → could pull Roadblock #1 (vision) forward of
  plan. De-risk: probe the web source's tree before committing.

### Risks / debt
- **Slow iteration loop** — every model test = record → retrain → live run.
  Velocity risk for the P0 reliability work.
- **Is the DAgger / correction loop actually working?** `CorrectionHandler`
  captures *0 steps* in live runs. Either nothing to correct, or it's not wired —
  verify before relying on it for cold-start.
- **Stale tests** — `test_transformer_bc / html_detector / two_state` fail to
  collect (`ModuleNotFound: components.intelligence.model.dataset`). Fix or delete.
- **Pixel-coordinate brittleness** — clicks use absolute screen px from the
  observer; sensitive to resolution / window position. Robustness concern at scale.

---

## Finished Tasks

Completed work, preserved for reference.

### Navigation cloning (2026-06)
- **Proved the transformer clones the demonstrated order** — top-down (74%) vs
  bottom-up (93%), same architecture → not a bias, genuine cloning.
- **`is_filled` perception feature** — model can see which fields are done →
  stopped the end-game looping.
- **Action-space collapse → {click, type}** — action-type accuracy 50% → 80%,
  click accuracy up for free.
- **Wired action-type into the agent** — transformer decides click vs type (was a
  hardcoded "fillable & empty" rule).
- **Recorder combobox fix** — clicks while a dropdown is open are value-selections
  (land on the field under the dropdown) → dropped at record time. Killed the
  phantom "Expiration Date" pollution.
- **Tail-oversampling** — emphasized the `… → Submit` finish → model learned to
  submit on its own (no hardcoded completion rule).
- **End-to-end run** — fills the whole form in the learned order + clicks Submit +
  advances record, transformer-driven, no crutch, no human.
- **Recorder focus fix** — clicks always get a fresh snapshot (was reusing stale
  state during bursts → focus stuck).
- **`scripts/clean_demos.py`, `test_clone.py`, `oversample_tails.py`,
  `replicate.py`** added.

### Intelligence & Training
- **GPU training** — CUDA 12.4 / PyTorch 2.6.0+cu124, RTX 4050.
- **Best-acc checkpoint** — saves on `val_acc + click_acc`, not val_loss.
- **Dataset init cache** — `.dataset_cache.pkl`, retrain init ~1 sec.
- **LayerNorm on pointer heads** — fixed bilinear Q×K divergence (197M loss bug).
- **Data augmentation** — `augment_traces.py` (bbox/click/confidence jitter).
- **Tasks/ reorganization** — model.pt, ruleset.md under `tasks/form_filling/`.

### Agent & Merge Logic
- **Crutch-gating in pure mode** — VISITED-ADVANCE + LLM-takeover-when-weak gated
  off under `disable_auto_handlers` (they fought the model).
- **Fix LLM click position** — `_merge` resolves LLM target by label; transformer
  click is fallback.

### Ruleset & Spec System
- **Correctional ruleset** — `RuleExtractor.correct()` + auto-call on record end.
- **Spec injection** — `ruleset.md` appended to LLM system prompt.

### Evaluation
- **Per-run metrics** — `eval_metrics.evaluate_run` (TCR, action/value accuracy).
- **BC fidelity scorer** — `bc_fidelity.py` vs gold standard, trend in
  `bc_progress.jsonl`.

### Infrastructure
- **Capsule registry** — per-task model routing.
- **`.gitignore`** — `data/demos/`, traces, caches, model binaries excluded.
