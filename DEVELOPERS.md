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
5. [Quick Start](#quick-start)
6. [Repository Layout](#repository-layout)
7. [Components](#components)
8. [Current Goal](#current-goal)
9. [Task List](#task-list) — **3 Scopes + 8 capability dimensions** (Perception,
   Representation, Learning, Adaptability, Scalability, Execution and Integration,
   Evaluation, UI/UX — mirrors the Task Tree's branches; incl. Big Three,
   Fundamental roadblocks, Hybrid roadmap, RL phase — merged here)
10. [Scopes & North Star](#scopes--north-star)
11. [Questions, Concerns, and Concepts](#questions-concerns-and-concepts) — **open threads + design concepts**
12. [Finished Tasks](#finished-tasks)

---

## Current Status

> 🌲 **Task Tree** — a 3D node-graph progress map grown from the [Task List](#task-list)
> below: a hub node per phase, a small node per task, summit at 100%:
> `treetask/index.html`. Keep it synced: whenever a checkbox here flips, flip the
> matching `done` flag in that file's `PHASES` data. See [Task List](#task-list) for
> the sync convention.

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

**Honest gaps remaining on the slice (2026-06-14):** the model now **navigates all 8
tabs and fills deep tabs** (Drivers/History/Claims, click_acc 0.88) — but doesn't yet
*complete the task* end-to-end. Open: erratic tab-visit order, `(leave blank)` typed
literally, scroll not actually moving, single record only. Closing those is **the
current priority** — see [Task List → P0](#task-list). The scope-agnostic engine is
already built (foundation); finishing scope #1 builds the general muscle the other
scopes reuse.

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
[Big Three #1](#task-list).

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
> [Big Three #1 + #3](#task-list): a perception adapter (UIA→vision) and
> an LLM-induced workflow layer, so the same loop learns *new* apps/tasks from
> demonstration.

---

## Task List

> **Sync with Task Tree:** this list is the source of truth; `treetask/index.html`
> mirrors it as a 3D node graph. Structured around the same 11 branches as the Task
> Tree — 3 Scopes (what domains) + 8 capability/process dimensions (how it works) —
> not roadmap phases (P0–P5) anymore, so a checkbox's *category* is what it's about,
> not when it was planned. Every task has a stable id (referenced in Task Tree's
> `PHASES` data) and, where real, a `requires` — a prerequisite that must be done
> before that task can start. When a checkbox here changes, or we make a choice
> about *how* to solve something, update the matching Task Tree entry in the same
> pass, not as a separate cleanup step.

**Priority: COMPLETE SCOPE #1 (the form) first — then generalize.** The
scope-agnostic engine is built. The form navigates all 8 tabs and fills deep tabs,
but doesn't yet complete a task end-to-end (tab order erratic, scroll dead, single
record). Chasing Scope #2/#3 before Scope #1 completes is premature — its
remaining gaps (multi-tab nav, multi-record, cold-start) are *general* capabilities
every scope will hit, so finishing it isn't a detour from the thesis, it's building
the muscle Excel + triage will reuse.

> **LESSON (2026-06-14):** agent guards perturb the transformer's action-history
> (hist_len=4) → can destabilize a brittle model (piled ~8 guards once, regressed
> navigation, reverted). Add/strip agent logic **ONE at a time, re-test each**. The
> durable fix for fixation/mis-prediction is **more demos**, not more guards.

---

### Scope #1 — Data Entry Form Filling *(in progress — THE current focus)*
Single-app key-value entry into the car insurance form, (mostly) linear
navigation. Perception = UIA. **Definition of done:** agent fills all 5 records,
all 8 tabs + Driver/Vehicle sections, in the demonstrated order, submits each, no
human help.

- [x] `scope1_combobox_fill` — Combobox click-fill: empty combobox click → open+select, no spiral.
- [x] `scope1_false_done_guard` — Notepad intake text no longer triggers false completion.
- [x] `scope1_option_b` — Option B (WHERE/HOW division): the focused widget's *type* decides fill-vs-navigate, replacing the unstable action-type head. Whipsaw gone.
- [x] `scope1_tab_targeting` — Tab-targeting solved (2026-06-12): transformer's pointer predicts tab switches itself. 0% wasted, 100% value-acc.
- [x] `scope1_multi_tab_works` — Multi-tab traversal works: transformer-driven 3-tab switch + fill, clean.
- [x] `scope1_all_8_tabs` — All 8 tabs trained (2026-06-14, `model_eight_tabs.pt`, click_acc 0.88 at the time). **Stale as of 2026-08-06** — see `learning_stale_checkpoint_fixed`.
- [x] `scope1_tab_click_fix` — Tab-click now navigates to the tab actually clicked (sorted-index), not blind current+1.
- [ ] `scope1_tab_order` — Tab-visit order: model jumps/revisits tabs (skips Policyholder/Vehicle/Coverage). Fix via cleaner demos (consistent left-to-right order), NOT a hardcoded order.
- [ ] `scope1_leave_blank_bug` — `(leave blank)` typed literally: `_lookup_field`'s skip-check does `.strip("()")` → `"leave blank"` which isn't in the skip-set. Fix: substring-match leave-blank/none in both the lookup and the LLM value path.
- [ ] `scope1_checkbox_coldstart` — Checkbox cold-start / first-click: reliable first action from a blank screen (Renewal checkbox handling).
- [x] `scope1_tab_focus_first_input` — `car_insurance_form_wx.py` now focuses the first fillable control on a tab whenever the tab changes (human click, agent click, Submit & New's reset, or initial launch) — not wherever construction order happened to leave it. Found while implementing (2026-08-06): default launch focus was landing on an unrelated Policyholder field while Policy was the visible tab. Directly relevant to `scope1_checkbox_coldstart` above (same class of problem: what's focused when a tab first appears) but not a full fix for it — checkbox-specific handling is still open.
- [ ] `scope1_combobox_retry` — Combobox retry: open → miss → Escape → retry timing.
- [ ] `scope1_record_advance_5` — Record advance ×5: proven 1→2, need all 5 clean.
- [ ] `scope1_per_record_refresh` — Per-record data refresh: `refresh(record_num)` for records 2–5 (only record 1 verified).
- [ ] `scope1_e2e_metric` — End-to-end completion metric: 0% → ~100%, Field-Match high, low wasted steps.
- [ ] `scope1_expected_vs_actual` — Expected-vs-actual diff report at submit, per-record correctness.
- [ ] `scope1_rerecord_50k` — **~50,000-step end-to-end re-recording** (2026-08-06, in progress): the current 19-session/10,407-step dataset fails its own quality gate — 0% scroll coverage, only 26% of sessions reach Submit, 81.7% transition-mapping accuracy (target ≥90%), 10.88% encoding ambiguity (target <5%). Re-record with `scripts/recording_quality_gate.py` checked every few sessions, not just at the end. This is the single blocker for several downstream items — see `requires` below.
- [ ] `scope1_speed` — **Speed / execution time.** `eval_metrics.py` already tracks this per run (`avg_step_time_sec`, `steps_per_minute`, `run_duration_sec`, `time_to_first_action_sec`) — no successful live run has completed yet to establish a real baseline, so there's no target number here yet, just the instrumentation.

---

### Scope #2 — Web Form → Excel *(not started — only after Scope #1 completes a task)*
Cross-application transfer (web source → Excel grid); 2D target; mixed
perception.

- [x] `scope2_excel_perception_proven` — Excel perception swap proven: `ExcelObserver` normalizes to the canonical schema.
- [ ] `scope2_excel_full_transfer` — Wire source/target + executor for cells, record Excel demos, train, measure clone. *requires: `scope1_record_advance_5`, `scope1_e2e_metric`*

---

### Scope #3 — Email / Ticket Triage *(not started)*
Decision-making / conditional behavior — the strongest personalization claim
(two users triage differently). Kept to decisions inferable from *visible*
content (avoid hidden-intent).

- [ ] `scope3_email_triage` — Email / ticket triage. *requires: `execution_action_space_big3_2`, `execution_control_flow_big3_3`*

---

### Perception
Objective 1 (thesis): ≥95% detection accuracy across multiple GUI environments.

- [x] `perception_adapter_seam` — Observer base (capture→normalize→validate), injectable, canonical element schema (`observers/schema.py`), loud validation on mismatch. Excel perception swap proven.
- [ ] `perception_vision_big3_1` — **Big Three #1:** UIA → Vision. Today reads the UIA a11y tree (free, but only works on apps with a clean tree). General agent must see pixels: screenshot → VLM that localizes + semantically reads elements, hybrid UIA-for-grounding + VLM-for-understanding. A CV+OCR vision observer prototype exists (`components/observers/vlm/`) and has been recorded/trained/run against once — not yet proven at the 95% bar.
- [ ] `perception_detection_accuracy_95` — Measured detection accuracy (F1) ≥95% across multiple environments. Tooling exists (`scripts/perception_eval.py --log`) — no logged runs yet.

---

### Representation
Objective 2 (thesis): <5% encoding-ambiguity error rate.

- [x] `representation_datasource_injection` — DataSource injection (partial): `data_source` is injectable, agent reads source I/O through the seam. Follow-up: extract `_refresh_record_cache` orchestration.
- [ ] `representation_encoding_ambiguity_5` — Encoding ambiguity <5% (duplicate type+label signatures making a click target un-identifiable). Measured 2026-08-06 on current dataset: **10.88%** — above target. `scripts/encoding_ambiguity.py --log`.
- [ ] `representation_transition_mapping_90` — State-transition mapping correctness ≥90% (does each recorded action visibly do something?). Measured 2026-08-06: **81.7%**. `scripts/validate_transitions.py --log`.

---

### Learning
Objectives 3 & 5 (thesis): ≥90% action prediction accuracy (training + held-out eval).

- [x] `learning_fixation_solved` — Empty-field fixation solved (2026-06-12): `'attempted'` state-feature (ELEM_FEATURES 394→395) — once a field is acted on this session, the transformer stops re-targeting it.
- [x] `learning_double_type_fixed` — `'99'` double-type fixed: typing is idempotent (select-all before paste).
- [x] `learning_training_metrics_wired` — Per-epoch/total training time + throughput now logged (`transformer_training_log.jsonl`), 2026-08-06.
- [x] `learning_stale_checkpoint_fixed` — `model.pt` was stale (trained for 394 elem features; code encodes 395 since the `'attempted'` fix — drifted, never retrained, crashed every live run on Step 1). Retrained on GPU 2026-08-06 against the current 19 sessions: epoch 65/80, `val_acc=68.4%`, `val_click_acc=30.9%`.
- [ ] `learning_scroll_head_dead` — `TransformerAgentNetwork.scroll_head` is defined but never wired into `forward()` or the loss — scroll has no learnable target/magnitude, only a bare type-label. Found 2026-08-06 during the navigation investigation. *requires: `scope1_rerecord_50k`* (nothing to wire a loss against without real scroll examples — 0/11,062 prior steps had one).
- [ ] `learning_action_accuracy_90` — Action prediction accuracy ≥90% (train + held-out). Currently 30.9% click_acc (`learning_stale_checkpoint_fixed`'s retrain) — data-quality-limited, not architecture-limited. *requires: `scope1_rerecord_50k`*
- [ ] `learning_rl_phase` — *(future)* Actor-Critic / PPO with the BC transformer as Actor; KL penalty vs BC to preserve the user's style. Online fine-tuning from `StateValidator` reward.

---

### Adaptability
Objective 6 (thesis): ≥75% success rate on unseen GUI environments (layout/position/visual variation).

- [ ] `adaptability_unseen_success_75` — Not instrumented — needs a held-out/perturbed-layout test session distinct from training environments; no "unseen" flag exists yet in `eval_metrics.py`.
- [ ] `adaptability_scroll_gap` — Scroll navigation is a three-layer gap, found 2026-08-06: (1) **data** — 0/11,062 recorded steps ever contained a scroll action (demos relied on Tab-to-reveal only); (2) **model** — `scroll_head` is dead code (`learning_scroll_head_dead`); (3) **agent** — `agent.py`'s scroll-reveal/drought-guard/`_try_advance_tab` block (`agent.py:1589-1630`) runs **unconditionally, "in every mode"** — not gated by `disable_auto_handlers` — fully preempting the transformer's own decision every time, which is the opposite of the thesis's transformer=WHERE division. Fix order: record real scroll demos → wire the loss → fix `executor.py`'s scroll (no foreground-focus assert, likely why `pyautogui.scroll` doesn't move the wx `ScrolledPanel`) → strip the hardcoded agent.py block one change at a time. *requires: `scope1_rerecord_50k`*

---

### Scalability
Objective 7 (thesis): ≥90% performance maintained as dataset/task volume grows.

- [x] `scalability_scopeconfig` — ScopeConfig: form hardcodes (`_detect_section`, `_KNOWN_TABS`, `_TAB_PANE_NAMES`, `RECORD N OF M`) moved into injected config — agent is app-blind.
- [ ] `scalability_scope_abstraction` — Declare a scope (goal+config+observer+source+model) in ONE place, not scattered across `run_task.py`. *Deferred: do when wiring Scope #2.*
- [ ] `scalability_perf_at_scale` — Accuracy/training-time trend vs. dataset size. Tooling exists (`scripts/scaling_report.py`) — needs ≥2 training runs logged at different sizes to show a real trend. *requires: `scope1_rerecord_50k`*
- [ ] `scalability_shared_backbone` — Cross-task shared backbone (trunk + per-task heads), for when Scope #2/#3 exist.

---

### Execution and Integration
Objectives 8, 9 & 10 (thesis): ≥85% end-to-end completion without manual
intervention; ≤10% execution error rate; ≤20% wasted steps; unified coordinated
framework.

- [x] `execution_drift_solved` — Drift solved (2026-06-12): form-window LOCK (capture hwnd at GO, re-assert every step) + in-form click guard. No more typing into PowerShell / clicking Notepad.
- [x] `execution_belowfold_solved` — Below-fold reach solved: the drift guard's Tab doubles as scroll — wx `ScrolledPanel` auto-scrolls the focused field into view.
- [ ] `execution_action_space_big3_2` — **Big Three #2:** Action space, form-fields → universal. Today: click/type/select(+combobox). Need click, double_click, type, select, drag, scroll, hotkey, wait, verify, menu, file-dialog. Enumerable engineering, not research.
- [ ] `execution_control_flow_big3_3` — **Big Three #3:** Workflow, linear → control flow. Demos are one linear path; real tasks branch/loop/skip/error-recover. Pragmatic path: LLM induces the workflow (if/for) from multiple traces → execute → DAgger-correct.
- [ ] `execution_agentic_integration_85` — End-to-end task completion ≥85% without manual intervention. `eval_metrics.py` now tracks `manual_interventions` (DAgger corrections) as a direct signal. *requires: `scope1_e2e_metric`*
- [ ] `execution_error_rate_10` — Execution error rate ≤10%, wasted steps ≤20%. Both computed in `eval_metrics.py`'s `evaluate_run()` with explicit pass/fail flags now (2026-08-06) — no live run has produced a clean measurement yet (crashed on stale checkpoint / no LM Studio).
- [ ] `execution_llm_value_errors` — LLM occasionally answers into the wrong field (e.g. Policy Number into Policy Term). LLM keeps owning values; fix = better prompt or lookup-as-validator, not silent replacement.
- [x] `execution_llm_unavailable_vs_blank` — A dead LLM connection (e.g. LM Studio's local server not started) was indistinguishable from the LLM genuinely deciding a field is blank — both collapsed to an empty `prediction["text"]`, so the agent silently Tab-skipped the entire form instead of surfacing that the provider was unreachable. Found + fixed 2026-08-06: new `_is_llm_unavailable()` guard detects `_ask_llm()`'s infra-failure sentinel specifically and halts after 3 consecutive failures instead of blank-filling the rest of the form. Regression test: `tests/test_llm_unavailable.py`.
- [x] `execution_submit_dialog_blocks_run` — Clicking **Submit** (not "Submit & New") popped a blocking native `wx.MessageBox` (missing-fields warning or success confirmation) — a modal dialog owned by the same process as the form. Windows won't let `SetForegroundWindow` bring an owner window back to front while its modal child is open, so `_reassert_form_window()` looped forever ("Re-asserted form foreground" every step, never recovering) the moment Submit was clicked even slightly prematurely — confirmed live 2026-08-06, a run genuinely couldn't progress past it. Fixed at the agent level: detects a foreign foreground window owned by the same PID as the locked form (generic — no hardcoded dialog titles) and dismisses it with Escape before reasserting. Regression test: `tests/test_modal_dialog_dismiss.py`. **Superseded at the root** by `scope1_unify_submit_buttons` below — the dialog-producing button doesn't exist anymore, so this is now a defense-in-depth guard, not the primary fix.
- [x] `scope1_unify_submit_buttons` — Two adjacent buttons (**Submit** `[1348,858,1442,887]` and **Submit & New** `[1224,858,1335,887]`, ~13px apart) were an easy confusion target for a pointer that's only ~31% click-accurate — landing on Submit hit the modal-dialog freeze above, landing on Submit & New silently saved+cleared the form with NO validation regardless of state. Unified into one `_on_submit`: saves/clears/advances unconditionally, no dialog, no required-field gate (removed 2026-08-06 by request — recording/testing needs to reach Submit and reset regardless of completeness; an incomplete record is just visible as empty fields in the saved JSON, nothing silently lost). Removes the two-target ambiguity entirely rather than just handling its failure mode.
- [x] `execution_metrics_not_in_log` — `eval_metrics.py`'s RUN METRICS and `bc_fidelity.py`'s BC SCORE blocks use bare `print()`, which never reaches `logs/latest.log` (only `logger.*()` calls do — `run_task.py`'s `logging.basicConfig` only attaches FileHandlers to the root logger). Found 2026-08-06 while diagnosing a run from its log and getting zero hits on any metrics text. Fixed: both now also emit via a module logger (silent in standalone CLI use, captured when run inside `run_task.py`).
- [x] `execution_correction_watch_speed` — Every failed step (no_change/unexpected/error) blocked for a fixed 4.0s DAgger correction-watch window regardless of whether anyone was actually there to correct it. Proven from a live run's timestamps (2026-08-06): 5 consecutive failures cost 20s of pure dead wait — 62% of that stretch. Fixed: `correction_watch_seconds` is now a constructor param (default 4.0, unchanged for `task_manager.py`/`run_agent.py`/`workflow_builder.py`); `run_task.py` overrides it to 0.5s for unattended/verification runs. Regression test: `tests/test_correction_watch_seconds.py`.
- [ ] `execution_combobox_crash_recovery` — Combobox / mid-record crash recovery.
- [ ] `execution_no_prompt_caching` — No prompt caching: each step is a fresh LLM call, no batching/budget.
- [ ] `execution_no_memory` — No memory component: each record runs from a blank slate.
- [ ] `execution_observability` — No structured trace / record-level summary; no screenshot history for VLM mis-reads.
- [ ] `execution_ghost_cursor` — Ghost cursor overlay; training-readiness indicator; DAgger productionized.

---

### Evaluation
Objectives 11 & 12 (thesis): quantifiable (10-20%+) and statistically significant
improvement over traditional RPA tools on setup time, adaptability, execution
error, cognitive workload.

- [x] `evaluation_bc_score_bug_fixed` — BC fidelity scorer was scoring stale submissions: `score_run()` picked "newest by mtime" in `data/output/submissions/`, which also receives unrelated auto-saves from the standalone form app — a run that crashed at step 1 still got a plausible BC score off a leftover blank submission. Fixed 2026-08-06: refuses to score anything older than the run's own start time (`agent._run_start_ts`).
- [ ] `evaluation_rpa_comparison` — RPA baseline comparison (setup time, adaptability, execution error, cognitive-workload proxy via intervention rate) with real significance testing. Tooling exists (`scripts/compare_baseline.py`, `scripts/setup_time_tracker.py`) — needs real RPA-tool measurements collected manually, and enough Intern runs to compare against. *requires: `execution_agentic_integration_85`, `execution_error_rate_10`*
- [ ] `evaluation_objectives_dashboard` — Unified dashboard pulling every metric source into one pass/fail view per objective (`scripts/objectives_report.py`, built 2026-08-06) — exists and runs, but most underlying metrics still show "no data" pending real runs.
- [ ] `evaluation_benchmark` — Benchmark: compare against similar systems.
- [ ] `evaluation_chess_fidelity` — Chess fidelity benchmark: clone a human's play (openings, tendencies, time management, blunders); clone-ELO ≈ human-ELO + matching style.
- [ ] `evaluation_thesis_writing` — Thesis: finish Chapter 3; revise paper if nominated.
- [ ] `evaluation_data_collection_500k` — Data collection: 500,000 traces in a convenience-sampling setting.

---

### UI/UX
Not a thesis objective — developer tooling. The demo recorder's interface.

- [x] `uiux_electron_scaffold` — Electron app scaffolded and launches correctly (`app_electron/`): orange/white theme, `main.js`/`preload.js`/renderer, talks to the existing Python `DemoRecorder` over a stdio JSON bridge (`app/recorder_bridge.py`) since the recording logic (pynput/UIA/win32) has to stay in Python. Fixed an `ELECTRON_RUN_AS_NODE` env var forcing plain-Node mode instead of the real app. Started and finished scaffold 2026-08-06.
- [ ] `uiux_electron_feature_parity` — Full feature parity with the old Tkinter recorder (Screen Observer panel, F8 replay-newest hotkey wiring through Electron) + packaging to a standalone `.exe` via `electron-builder`. Note: a separate `ui/ui-ux` branch (CustomTkinter, no DemoRecorder wiring) exists and was explicitly NOT adopted for this work.

---

## Scopes & North Star

*Reference — the thesis-completion criteria and the generalization vision. The
actionable work lives in the [Task List](#task-list) (Scope #1/#2/#3 + the 8
capability dimensions); this is the* why.

### The three scopes *(thesis = all three)*
Chosen to span the interesting space — *data entry*, *cross-app transfer*,
*conditional judgment* — so the claim is "clones varied GUI workflows," not "fills
one form."

1. **Data Entry Form Filling** — *in progress ([Scope #1](#task-list)).* Single-app
   key-value entry, (mostly) linear. Loop proven on the Policy section (clones
   order, fills, submits); remaining = multi-tab, multi-record, cold-start.
   Perception = UIA.
2. **Web Form → Excel** — *not started ([Scope #2](#task-list)).* Cross-application
   transfer (web source → Excel grid); 2D target; mixed perception. Excel
   perception swap **PROVEN** (`ExcelObserver` normalizes to canonical); remaining
   = web source, action on cells, demos, train.
3. **Email / Ticket Triage** — *not started ([Scope #3](#task-list)).* Decision-making /
   conditional behavior; the strongest personalization claim (two users triage
   differently). Needs branching ([Big Three #3](#task-list)) + judgment
   cloning. Kept to decisions inferable from *visible* content (avoid hidden-intent,
   Fundamental roadblock A).

### North Star — generalization (beyond the thesis)
The thesis is *bounded* to those three, but the **architecture is built to
generalize** — that is the real goal. The novel contribution: a **personalized,
demonstration-learned GUI agent** that learns how *this user* does a task and
reproduces *their* workflow — which scripted RPA (no learning) and generic
computer-use agents (not personalized) don't.

Already partly real: perception is an **adapter** (UIA + Excel today, one shared
schema) and the `transformer(WHERE) + LLM(WHAT)` loop is perception-agnostic. The
path beyond the thesis — a vision perception adapter
([Big Three #1](#task-list)) + LLM-induced control-flow
([Big Three #3](#task-list)) — turns "three GUI scopes" into "any GUI
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
  lack a clean accessibility tree → could pull Big Three #1 (vision) forward of
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
