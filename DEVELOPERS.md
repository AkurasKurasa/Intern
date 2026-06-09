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
4. [Roadblocks Ahead](#roadblocks-ahead) — **the Big Three + Fundamental**
5. [Roadmaps to a General Agent](#roadmaps-to-a-general-agent)
6. [Quick Start](#quick-start)
7. [Repository Layout](#repository-layout)
8. [Components](#components)
9. [Current Goal](#current-goal)
10. [Wish List — Path to Full BC](#wish-list--path-to-full-behavioral-cloning)
11. [Task List](#task-list)
12. [Known Issues](#known-issues)
13. [Non-System Work](#non-system-work)
14. [Finished Tasks](#finished-tasks)

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

**Honest gaps remaining on the slice:** cold-start (first click from a blank
screen), multi-record reliability, combobox retry timing, and the fundamental
limits below. See [Roadblocks](#roadblocks-ahead).

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

Priority order, **generalization-first**: make the form run through a
scope-agnostic pipeline → prove transfer → complete the form scope → polish.
*Not* form-polish-first — gold-plating a single-scope contraption only to refactor
it later (with 3 scopes' coupling) is the trap. Refactor while it's small.

> **Why not finish the form first:** navigation *learning* is proven, but the
> *engine around it* is form-shaped (hardcodes, UIA-coupling, Notepad parser).
> That coupling — not missing features — is what blocks generalization and is the
> real source of dissatisfaction. Fix the engine on one scope, then #2/#3 drop in.

### 🔴 Tier 1 — Foundation: scope-agnostic pipeline *(do now, on one scope)*
- [ ] **Kill form-specific hardcodes → scope config** — `_detect_section`,
      `_KNOWN_TABS`, `_TAB_PANE_NAMES`, `RECORD N OF M`. Constructor/scope params
      with neutral defaults. *(Also serves multi-tab — these ARE the tab logic.)*
      **← start here: cheapest, most concrete, gates everything.**
- [ ] **Formalize the perception adapter seam** — one interface; UIA + Excel as
      drop-in adapters (Excel already emits the schema). Agent consumes it
      identically regardless of source.
- [ ] **Generalize the data-source layer** — `DataSource` ABC not coupled to
      Notepad / RECORD format; each scope plugs in its own source.
- [ ] **Scope abstraction** — declare a scope (goal + adapter + source + model +
      metric) in ONE place, not scattered in `run_task.py`. (Finish capsules.)

> **Gate / litmus test:** the *same* `agent.py` runs a second scope with only a
> new adapter + source + demos, **zero agent edits**. When this passes,
> generalization is unlocked.

### 🟠 Tier 2 — Prove the thesis core (generalization + cloning)
- [ ] **Excel end-to-end** — wire the adapter into the loop, record demos, train,
      measure clone. First *transfer* data point; hard half (perception) is done.
      **Highest thesis value.**
- [ ] **Random-order test** — cheap; closes "clones *any* order, not a bias."

### 🟡 Tier 3 — Complete form scope #1
- [ ] **Multi-tab + multi-record** — driven by the *generalized* section/record
      config from Tier 1 (not new hardcodes).
- [ ] **Eval / verification harness** — per-scope "did it do the task correctly?"
      (expected-vs-actual diff at submit). Needed for every scope.

### 🟢 Tier 4 — Reliability polish *(don't let these gate Tiers 1–2)*
- [ ] **Cold-start** — reliable first click (DAgger or learned start-signal).
- [ ] **Combobox timing** — selection without escape-retry.
- [ ] **LLM value errors** — wrong field value; better prompt or lookup-as-validator.
- [ ] Mid-record crash recovery.
- [ ] Unit tests for `_parse_records`, `_lookup_field`, `encode_state`.

### 🔵 Tier 5 — Scope #3
- [ ] **Email / ticket triage** — the decision/control-flow scope; drops in after
      the pipeline is scope-agnostic.

### Nice to have (anytime)
- [ ] Cross-task shared backbone (trunk + per-task heads).
- [ ] Ghost cursor overlay (read-only visual of intended click).
- [ ] Training-readiness indicator (how close to LLM-free).
- [ ] DAgger productionized — corrections auto-merge + retrain.

---

## Known Issues

### Perception / Observation
- [ ] **UIA-only** — no vision adapter yet; breaks on apps without a clean tree.
- [ ] **`is_focused` / `is_filled` hard under vision** — the most critical signals
      are the hardest to read from pixels.

### LLM
- [ ] **Value errors** — local LLM occasionally returns the wrong field's value
      (e.g. Policy Number into Policy Term). Architecture keeps LLM owning values;
      fix is better prompting or lookup-as-validator (not silent replacement).
- [ ] **No prompt caching / each step an LLM call** — no batching, no thinking
      budget tuning.

### Architecture / Generality
- [ ] **No memory component** — each record runs from a blank slate.
- [ ] **Task-specific code in `agent.py`** — `_detect_section`, `_KNOWN_TABS`,
      `_TAB_PANE_NAMES` are car-form-specific; should be constructor params.
- [ ] **`RECORD N OF M` delimiter hardcoded** — only this intake format parses.
- [ ] **No control flow** — workflows are linear only (see Roadblock #3).

### Observability
- [ ] **No structured trace / record-level summary.**
- [ ] **No expected-vs-actual diff at submit.**
- [ ] **No screenshot history** for VLM mis-reads.
- [ ] **No unit tests** — every fix is run-and-pray.

---

## Non-System Work

### Documentation / Thesis
- [ ] Finish Chapter 3
- [ ] Revise paper if nominated

### Data Collection
- [ ] 500,000 traces in a convenience-sampling setting

### Benchmark
- [ ] Compare against similar systems

### Scopes (Thesis Completion Criteria)

Three GUI scopes. Completing all three = thesis **complete**. Chosen to span the
interesting space — *data entry*, *cross-app transfer*, and *conditional
judgment* — so the claim is "clones varied GUI workflows," not "fills one form."

- [ ] **1. Data Entry Form Filling** *(in progress — vertical slice)*
  - **Dimension:** single-app, key-value field entry, (mostly) linear order.
  - **Status:** loop proven end-to-end on the Policy section (clones order,
    fills, submits). Remaining: multi-tab, multi-record, cold-start, random-order
    test. Perception = UIA.
- [ ] **2. Web Form → Excel**
  - **Dimension:** **cross-application transfer** (read web source → enter into
    an Excel grid); 2D grid target; mixed perception (web + Excel).
  - **Status:** `ExcelObserver` already emits the shared trace-compatible schema
    (perception adapter done). Remaining: wire into the agent loop, record demos,
    train, measure. Web source perception = the harder half.
- [ ] **3. Email / Ticket Triage**
  - **Dimension:** **decision-making / conditional behavior** — per item, the user
    decides (archive / flag / reply / route / delete) from visible content.
    Branching (Roadblock #3) + judgment/style cloning — the strongest
    personalization claim (two users triage differently).
  - **Scope tight:** decisions inferable from *visible* content (sender, subject,
    keywords) to stay learnable (avoid hidden-intent, Roadblock A).
  - **Status:** not started.

> **Why these three:** entry + transfer + judgment. Each adds a dimension the
> others don't, so completing all three demonstrates the learn-from-demonstration
> loop across genuinely different GUI workflows — within the GUI constraint.

### North Star — Generalization (beyond the thesis)

The thesis is *bounded* to the three scopes above, but the **architecture is
built to generalize** and that is the real goal. The novel contribution is a
**personalized, demonstration-learned GUI agent** — it learns how *this user*
does a task and reproduces *their* workflow/style — which neither scripted RPA
(no learning) nor generic computer-use agents (not personalized) do.

The generalization is **already partly real**: perception is an **adapter**
(UIA + Excel COM today, both emitting one shared schema), and the downstream
`transformer(WHERE) + LLM(WHAT)` loop is perception-agnostic. The path beyond the
thesis (a perception VLM adapter for any app + LLM-induced control-flow workflows
— [Roadblocks #1, #3](#roadblocks-ahead)) turns "three GUI scopes" into "any GUI
workflow learned from demonstration."

- [ ] Kill the form-specific hardcodes (`_KNOWN_TABS`, `_detect_section`,
      `RECORD N OF M`) so the *same code* runs every scope unchanged — the
      concrete proof of generality.
- [ ] Vision perception adapter (Roadblock #1) — generality beyond apps with a
      clean accessibility tree.
- [ ] LLM-induced workflows with control flow (Roadblock #3) — beyond linear.

### Behavioral Fidelity Benchmarks
- [ ] **Chess** — clone a human's play (openings, tendencies, time management,
      blunder patterns). Clone ELO ≈ human ELO + matching style = BC capturing
      decision-making, not rote actions.

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
