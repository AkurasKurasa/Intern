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
- [ ] `scope1_tab_order` — Tab-visit order: model jumps/revisits tabs (skips Policyholder/Vehicle/Coverage). Fix via cleaner demos (consistent left-to-right order), NOT a hardcoded order. **Concrete live evidence, 2026-08-07**: after 3 rounds chasing what looked like a code bug in `execution_stuck_loop_wrong_tab_field` (pane-detection timing), a 5th live re-test caught the SAME visible symptom (Policy -> Vehicle, Policyholder skipped) with a completely different, unambiguous cause — no pane-lookup failure, no fallback path at all: `[OPT2] TRANSFORMER navigates -> click @ (1074,134) ptr_conf=0.38` landed directly on the Vehicle tab button while still on Policy (5/13 fields filled). This is exactly this task's own symptom, this time proven to be the model's own prediction, not code. Reinforces: this needs training-data fixes (tab-order consistency in demos), not more agent.py patches. Compounds with the still-unresolved `execution_stuck_loop_wrong_tab_field` code bug — both produce the identical visible symptom (Policy -> Vehicle, no Policyholder), so don't assume one without checking the log for which mechanism actually fired.
- [ ] `scope1_leave_blank_bug` — `(leave blank)` typed literally: `_lookup_field`'s skip-check does `.strip("()")` → `"leave blank"` which isn't in the skip-set. Fix: substring-match leave-blank/none in both the lookup and the LLM value path.
- [ ] `scope1_checkbox_coldstart` — Checkbox cold-start / first-click: reliable first action from a blank screen (Renewal checkbox handling).
- [x] `scope1_tab_focus_first_input` — `car_insurance_form_wx.py` now focuses the first fillable control on a tab whenever the tab changes (human click, agent click, Submit & New's reset, or initial launch) — not wherever construction order happened to leave it. Found while implementing (2026-08-06): default launch focus was landing on an unrelated Policyholder field while Policy was the visible tab. Directly relevant to `scope1_checkbox_coldstart` above (same class of problem: what's focused when a tab first appears) but not a full fix for it — checkbox-specific handling is still open.
- [ ] `scope1_combobox_retry` — Combobox retry: open → miss → Escape → retry timing.
- [ ] `scope1_record_advance_5` — Record advance ×5: proven 1→2, need all 5 clean.
- [ ] `scope1_per_record_refresh` — Per-record data refresh: `refresh(record_num)` for records 2–5 (only record 1 verified).
- [ ] `scope1_e2e_metric` — End-to-end completion metric: 0% → ~100%, Field-Match high, low wasted steps.
- [ ] `scope1_expected_vs_actual` — Expected-vs-actual diff report at submit, per-record correctness.
- [ ] `scope1_rerecord_50k` — **~50,000-step end-to-end re-recording** (2026-08-06, in progress): the current 19-session/10,407-step dataset fails its own quality gate — 0% scroll coverage, only 26% of sessions reach Submit, 81.7% transition-mapping accuracy (target ≥90%), 10.88% encoding ambiguity (target <5%). Re-record with `scripts/recording_quality_gate.py` checked every few sessions, not just at the end. This is the single blocker for several downstream items — see `requires` below.
  - Progress: 2,966 steps / 5 sessions in `data/demos/eight_Tabs/` as of 2026-08-06 (~6% of 50k target).
  - **Required step, not yet automated**: raw captures under `data/demos/*` have `action: null` — `mouse`/`keyboard` events aren't converted into a structured `action.action_type` until `scripts/backfill_actions.py --trace-dir <dir>` is run. Every analysis script (`recording_quality_gate.py`, `validate_transitions.py`, `bc_fidelity.py`) silently reads `action: {}` defaults on unbackfilled data and reports misleadingly — one full quality-gate pass on this batch was garbage (0% submit, 0% field coverage, no scroll) purely because backfill hadn't run yet, not because the recordings were bad. Run backfill before every quality-gate check on freshly recorded data.
  - **Code bug fixed**: `bc_fidelity._tab_of()` mapped 9 Policy-tab keys (`effective_date`, `expiration_date`, `agent_id`, `agent_name`, `agency_name`, `underwriter`, `renewal_flag`, `paperless`, `esign` — the ones without a `policy_` prefix) to "Other" instead of "Policy", silently under-crediting the 10%-weighted `tab_coverage` term of the fidelity score used in Thesis Ch.4. Fixed via an explicit `_UNPREFIXED_POLICY_KEYS` override.
  - **Scroll check reframed** (per correction: "the system scrolls, not the recorder"): `recording_quality_gate.py` no longer flags "NO SCROLL" — it now measures per-tab field coverage (did Tab-navigation actually reach every known field in a section) instead of counting scroll-wheel events. After the backfill fix above, real scroll actions were confirmed present in the raw captures anyway.
  - **Third bug found — user directly disputed the "0/5 reached Submit" reading ("I swear to God, I pressed Submit"), and was right**: `recording_quality_gate._clicked_element()` returned the *first* element in `state["elements"]` whose bbox contained the click point, not the most specific one. Elements overlap (window > panel > button) and the window is listed first in UIA capture order, so every click anywhere in the form silently resolved to the window element instead of the button actually clicked — `submit_reached` could never fire. Fixed to pick the smallest-area containing bbox. Real result: **3/5 sessions reached Submit**, not 0/5. Also incidentally surfaced that the recorder's live form window still has the old two-button "Submit & New" / "Submit" layout (bboxes ~1224-1335 / ~1348-1442) — the running process predates `scope1_unify_submit_buttons` and needs restarting to pick up the single-button fix.
  - **After all three fixes, this batch's real numbers**: field coverage 83-100% on Policy/Policyholder/Vehicle (the only 3 tabs `bc_fidelity._LABEL_TO_KEY` currently covers), tab order 100% consistent, 3/5 sessions reached Submit (the 2 that didn't were short 115-210 step sessions, plausibly practice/partial passes), transition-mapping accuracy 75.3% (below the 90% target — a new signal, invisible before the backfill fix), encoding ambiguity still 9.83% (target <5%).
  - **2026-08-06, early train-on-partial-batch experiment**: at the user's request, trained on this batch as-is (2,505 train / 442 val samples, `--epochs 50 --device cuda`) before the 50k campaign finished, to get a real read on data quality rather than wait blind. Result: best checkpoint at epoch 41, val_acc=0.824, val_click_acc=0.298 — plateaued in a 0.24-0.40 band from ~epoch 30 on, no clear upward trend by epoch 50. Roughly flat vs. the pre-existing committed checkpoint (epoch 65 on the old 19-session set: val_acc=0.684, val_click_acc=0.309) — not a regression, but not progress either, and well short of the 90% target. Consistent with the batch's own known issues (2/5 sessions partial, 75.3% transition-mapping accuracy). `model.pt` is git-tracked and currently shows as locally modified, not committed — the prior checkpoint is fully recoverable via `git checkout -- tasks/form_filling/model.pt` if needed. Reinforces: keep recording toward 50k rather than treat an early partial-batch model as representative.
  - **Known tooling blind spot, not yet fixed**: `tabs_covered` reports 3/8 for every session, but manually grepping raw element labels confirms 4 of 5 sessions actually reached Coverage/Driver/Claims/Payment fields (`deductible`, `claim number`, `card number`, etc. all present) — `_LABEL_TO_KEY` only maps Policy/Policyholder/Vehicle, so the quality gate is structurally blind past tab 3. Needs the intake field schema for the remaining 6 tabs before tab coverage past Vehicle can be measured at all.
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
- [x] `learning_embedding_hash_fallback_fixed` — **Root-caused why val_click_acc plateaued at ~29-31% across three differently-sized training runs in one session (2,947 / 6,401 / 13,609 samples) — size was never going to fix this.** `_embed_text()` (`transformer.py`) has two documented paths: a real sentence-transformer embedding, and a SHA256-hash fallback with zero semantic structure — but the function that primes the real embeddings, `_prime_embed_cache()`, was defined and never called anywhere in the pipeline. Every element label in every training run to date, ever, was encoded via the hash fallback: 384 of the model's 395 input features per element were content-addressed noise, not text semantics. Verified directly: `sim(embed("First Name"), embed("Last Name"))=0.79` vs `sim(embed("First Name"), embed("Submit"))=0.22` under the real model — meaningful structure the hash path cannot produce (hash similarity is ~uncorrelated regardless of label relatedness). This directly undercuts the "data-quality-limited, not architecture-limited" assumption baked into this task's own note above, and into `scope1_rerecord_50k`'s framing generally — a chunk of the plateau was architectural the whole time. Fixed: new `TrajectoryDataset._prime_all_embeddings()` collects every unique element label across the dataset and batch-embeds them via the real model before tensor encoding starts, wired into both the cache-hit and fresh-build code paths. Smoke-tested end-to-end on the 6-session batch (433 unique labels, ~157s one-time cost including model load) — confirmed the same real similarity structure flows through the actual dataset, not just the standalone check.

  **Validated 2026-08-07 with a real retrain, same dataset as the pre-fix small-batch run (2,947 samples) for a clean A/B**: val_click_acc **29.8% → 61.2%** (epoch 45/50, val_acc 84.5%), roughly double, the only variable changed being this fix. This is the largest single-change improvement this project has seen and directly falsifies the "data-quality-limited, not architecture-limited" framing this task and `scope1_rerecord_50k` both carried into today. Still short of the 90% target — next step is likely re-running the full combined dataset (13,609 samples) with the fix now active, since data volume may finally start helping now that the representation problem is fixed (not run yet, deliberately, given how many long retrains happened today already).
- [x] `learning_val_click_acc_inflated` — **The reported 61.2% val_click_acc (from `learning_embedding_hash_fallback_fixed`'s validated retrain) was not honest — found and fixed 2026-08-07.** Built `scripts/eval_click_by_tab.py` to break click accuracy down per-tab, and its first run came back 41.1% — a huge, suspicious gap from the training-reported 61.2% on what should've been the same checkpoint. Root-caused in two layers:
  1. **Eval-script bug (mine)**: it rebuilt the validation split from the *current* `data/demos/eight_Tabs` folder, which has grown to 7 sessions since the training run happened (the ongoing recording campaign keeps adding sessions) — same `seed=42` on a differently-sized dataset produces a completely different, meaningless split, not a reconstruction of the model's actual held-out set. Fixed by isolating (via directory junctions) the exact 5-session/2,947-sample snapshot the checkpoint trained on. Re-run: **48.1%**, not 41.1%.
  2. **Real bug in `train.py`/`transformer.py`**: `TrajectoryDataset` is a single shared instance across `train_ds`/`val_ds` (`random_split` only splits indices), and `__getitem__` applied `aug_drop_prob`'s element-dropout + shuffle unconditionally — so the "held-out" validation pass was scored on the same randomly-corrupted inputs as training, not clean ones. Randomly hiding ~10% of on-screen fields makes the click choice *easier* (fewer confusable candidates), which silently inflated every val_click_acc this project has ever reported. Fixed: new `TrajectoryDataset._eval_mode` toggle, set `True` only around the val_loader epoch pass in `train()`, so validation now sees the same clean state the model gets live. Regression test: `tests/test_val_augmentation_disabled.py`.

  **The model's true, deployment-relevant click accuracy is ~48%, not 61%.** Per-tab on the correct snapshot: Policy 67.9% (19/28), Policyholder 50.6% (40/79), Vehicle 45.2% (33/73), Coverage/Drivers/Claims/Payment 44.4% (63/142). Confusions are overwhelmingly between adjacent/similarly-named fields in the same tab ("DL Expiration"→"DL Issuing State", "Effective Date"→"Policy Term", "Comprehensive Deductible"→"Collision Deductible") — the model has the right neighborhood, not fine precision.

  **Decision**: retrain immediately on the full current `eight_Tabs` folder (7 sessions, ~2x the data used for the 48.1% checkpoint) with the val-augmentation fix now active, so the next reported number is both bigger-data and honest. Launched 2026-08-07, same hyperparameters as the original run (`d_model=64`, `epochs=50`, `batch_size=16`, `val_split=0.15`, `aug_drop=0.1`) — data volume is the only variable changed, to isolate its effect cleanly. Not yet verified live.

  **CORRECTION, same day, retrain finished**: the retrain's own clean val_click_acc came back 57.8% — an improvement over 48.1%, so at first glance it looked like the data-volume bet paid off. But cross-checking `eval_click_by_tab.py`'s per-tab number against `train.py`'s own reported metric for the *same checkpoint* gave 37.1% vs 57.8% — a huge, suspicious mismatch that shouldn't exist once the aug-bug was fixed on both sides. Root-caused to **two more bugs, both in `eval_click_by_tab.py` itself, not the model**: (1) it read the trace's raw top-level `"action"` field (string type like `"drag"`, pixel-space coordinates) instead of re-deriving history via `_decode_actions()` like training does — fed the model an action-type id (`drag=6`) it never saw during training, since `_decode_actions` always collapses drag/double_click into `CLICK=1` first; (2) far bigger — it never stamped the `attempted` feature (has this field already been acted on this session) onto any element, since that flag doesn't exist in raw trace JSON and is synthesized only inside `TrajectoryDataset` at load time. Every element in every evaluated state looked like a first-ever visit. This is the exact feature `learning_fixation_solved` added to stop empty-field re-fixation — silently absent from every eval this script had ever run. Fixed both (reusing `TrajectoryDataset`'s own precomputed `_attempted_by_file` map + calling `_decode_actions` directly) — the fixed script's number for the new checkpoint now matches `train.py`'s own report exactly (57.8% both ways), first time the two have agreed. Full details + fix: `execution` section, or see the commit fixing `scripts/eval_click_by_tab.py`.

  **Re-measured the ORIGINAL 2,947-sample checkpoint with the now-fixed script: 68.9%, not 48.1%.** So the honest comparison is **68.9% (5 sessions) vs. 57.8% (6 sessions) — the data-volume retrain made accuracy WORSE, not better.** Likely cause: the added data isn't evenly "more of the same" — one single session (`session_20260806_203555`, 3,135 of 6,079 total samples, over half the entire new training set) also has a `drag`-action rate of ~20% vs. near-0% in four of the other five sessions (one other session, `194340`, shares the same elevated rate) — a real, concrete composition lead, not yet confirmed as the root cause. **Action taken**: reverted `model.pt` to the original 68.9% checkpoint (`git checkout --`, safe — the worse checkpoint was never committed). `learning_state_encoder_split_projection` below is now queued against this restored 68.9% baseline, not the regressed one. Before trying "more data" again, the oversized/high-drag-rate session should be inspected for quality before blindly folding it back in.
- [x] `learning_state_encoder_split_projection` — **Tested 2026-08-07, RESULT: NEGATIVE — reverted.** Hypothesis: the confusion pattern in both checkpoints' per-tab breakdowns is overwhelmingly same-neighborhood, similar-label mixups ("DL Expiration"→"DL Issuing State", "Comprehensive Deductible"→"Collision Deductible") — text embeddings for these pairs are close by construction (they share words), while bbox position is what actually distinguishes them, and `StateEncoder`'s single shared `nn.Linear` over the full 395-dim vector (11 structured + 384-dim embedding) let the embedding dominate by sheer dimensionality. Tried: separate linear projections for structured features and the embedding, each a fixed share of `d_model`. **Trained in isolation against the exact 68.9%-baseline dataset, same hyperparameters otherwise: best val_click_acc 45.0%, never approached 68.9% through all 50 epochs.** The position-vs-embedding-dilution hypothesis wasn't the (or wasn't the whole) root cause. Reverted `StateEncoder` to the single-projection version; old tests deleted (code no longer exists to test).
- [x] `learning_rare_field_weighting_and_repeated_label_fix` — **Two fixes landed together 2026-08-07, then a live user report forced a closer look. RESULT SO FAR: negative when bundled, isolation test in progress.**
  1. Rare-action loss weighting only balanced by control TYPE (edit box vs button vs tab), not individual field identity — two edit-box fields of very different real-world frequency (e.g. a name field on every record vs. an admin field on few) got equal loss weight. Changed to weight by the clicked element's specific identity (`_attempt_key`), which subsumes type-level weighting.
  2. **Bigger, directly reported by the user live**: "Driver 1's First Name and Driver 2's First Name being mixed up and being indistinguishable along with similar input names." Confirmed in raw trace data — every driver block labels its name field just "First Name," no qualifier. `_attempt_key` used the label alone as identity, so filling Driver 1's First Name marked the shared key `"first name"` as attempted — Driver 2's First Name (different, still-empty element) then silently read as already-done and got skipped. Real functional bug in both `transformer.py` (train-time) and `agent.py` (live-inference, a second copy of the same logic that has to stay in sync) — not just an accuracy gap. Fixed by disambiguating same-labeled elements by rank among same-labeled elements in list order (stable under scroll, general fix for any repeated-section UI, not hardcoded to drivers). Regression tests: `tests/test_rare_field_weighting.py`, `tests/test_attempt_key_disambiguation.py`.

  **Trained both together against the 68.9% baseline dataset: 43.2% — worse, not better.** Bundling two changes in one test was a discipline mistake (violates "one variable at a time," which held for every other experiment tonight) — can't tell which one hurt, or whether both did. Prime suspect: field-level weighting creates much more extreme per-sample weight variance across dozens of distinct field labels in only ~2,947 samples than the old type-level scheme did, plausibly destabilizing training. Disambiguation alone (type-level weighting restored) was separately tested: **46.9%** — also worse than baseline, confirming the regression wasn't only about weighting.

  **True isolation, done 2026-08-08.** The two changes were structurally intertwined: both the "attempted" input feature and the loss-weighting key were computed through the same `_attempt_key(elem, elements=...)` call, so there was no way to change one without the other. Added two independent `TrajectoryDataset`/`train()` parameters: `disambiguate_attempted` (bool, default `False`) controls only the "attempted" feature; `rare_weight_basis` (`"none"|"type"|"field"`, default `"type"`) controls only the loss-weighting key. Defaults reproduce the pre-7999efc7 (historical, non-regressed) configuration exactly. Regression tests: `tests/test_rare_weight_basis_toggle.py`.

  **Before trusting any comparison, tried to reproduce the 68.9% baseline exactly as a sanity check — found two unrelated real bugs along the way, both fixed:**
  1. `TrajectoryDataset` stored file paths in its on-disk pickle cache exactly as passed to `data_dir`. A dataset built from one working directory, then its cache reloaded by a process running from a *different* working directory, silently resolved every cached relative path against the wrong cwd — `_load_trace()` swallows the resulting `FileNotFoundError` and returns `None`, which becomes an empty `{}` state. No crash, no error — an entire training run silently corrupted into empty-state noise (hit this directly: ~5,900 skipped files, essentially the whole dataset). Fixed by resolving `data_dir` to absolute immediately in `__init__`. Regression tests: `tests/test_dataset_absolute_paths.py`.
  2. `train()`'s checkpoint-saving criterion summed action-TYPE accuracy with click accuracy (`val_acc + click_acc`). ~95% of actions in this task are type "click", so `val_acc` saturates near-ceiling from epoch 1 just by always guessing "click" — after that it's noise, not signal, and it drowned out the genuinely-improving `click_acc` metric. A run saved epoch 1 (`val_acc=0.914, val_click_acc=0.171`) over epoch 44 (`val_acc=0.753, val_click_acc=0.298`) — the *worst* click-targeting epoch in the run beat one of the best. Fixed: select by `click_acc` alone, the metric actually deployed on — it can't be gamed by the dominant action type. Regression test: `tests/test_checkpoint_selection_by_click_acc.py`.

  3. **Third bug, bigger than the first two**: `transformer.train()`'s own Python-level defaults are `lr=1e-4, weight_decay=1e-2` — but every actual training entry point in this codebase (`train.py`, `BCTrainer`, `transformer.py`'s own CLI) overrides those to `lr=1e-3, weight_decay=1e-4`, and `train()`'s own docstring describes `1e-4` weight_decay as "the default" (it isn't, at the function-signature level — a drift between the docstring and the code). Every experiment tonight called `train()` directly without passing these explicitly, so all of them trained at 10x too-low a learning rate and 100x too-strong weight decay — severe, silent underfitting, capping val_click_acc around 28-30% regardless of what else was being tested. Fixed `train()`'s own defaults to match every real caller (`lr=1e-3, weight_decay=1e-4`), so calling it directly no longer silently underperforms the CLI.

  **With all three bugs fixed, re-ran the type-vs-field A/B one more time (same seed/architecture/data, only `rare_weight_basis` differs) — this is the number that actually counts:** `type` basis (control): **49.1%** val_click_acc (peak = saved checkpoint, epoch 42). `field` basis: **43.2%** (peak = saved checkpoint, epoch 44). Control wins by ~6 points — not noise-level this time. **RESULT CONFIRMED: field-level weighting is not an improvement; kept `rare_weight_basis` default at `"type"`.**

  **Where this leaves the bigger picture**: 49.1% is real progress from the 28.3% every earlier run in this investigation was stuck at (confirms the LR/weight_decay bug was a major, genuine underfitting cause, not just noise) — but it's still well short of the historical 68.9% figure, and of the 75%+ thesis target. The gap is no longer a mystery: train click_acc reached 62.9% on this same run while val click_acc topped at 49.1% — a widening train/val gap that's the classic signature of overfitting on a small dataset (~2,947 samples, ~176 distinct fields across 8 tabs, only 5 recording sessions' worth of diversity), not a remaining hyperparameter or architecture problem. More epochs or more tuning on the same data will not close this; more DATA is the next real lever. Also worth noting: this session could not reliably reproduce 68.9% under any tested configuration, which further undermines treating it as a solid target — it may itself have been a favorable, non-representative run (GPU training on `nn.TransformerEncoderLayer` isn't bit-for-bit deterministic even with a fixed seed). **Decision, discussed directly with the user**: don't jump to the originally-planned 50k-frame target (sized without a real scaling curve behind it) — record a moderate next batch first (~2-3x current volume, roughly 6,000-9,000 more samples), re-measure with tonight's now-fixed methodology (correct hyperparams, correct checkpoint selection, correct val-metric reporting), and use two honest data points to decide whether a much larger recording effort is actually justified before committing to one. `scope1_rerecord_50k`'s 50k figure should be treated as provisional pending this.

  **Before the LR/weight_decay bug (#3 above) was found, and matching the deployed checkpoint's exact saved architecture (`num_layers=2, dim_feedforward=128, dropout=0.2` — the `train()` CLI defaults are `4/256/0.1`, a mismatch that caused an earlier failed reproduction attempt), a fresh run could not reproduce anywhere near 68.9%** — best epoch peaked at 30.1% val_click_acc (corrected here: an earlier verbal report of "37.8%" during this same investigation mistakenly read that epoch's *train* click_acc off the same log line, not val — a mixup that also briefly affected two chat responses, both since corrected). Once bug #3 was found and fixed, val_click_acc reached 49.1% on the same data/architecture — most, but likely not all, of the historical 68.9% gap was this bug. **Conclusion: GPU training on this small a dataset (~2,947 samples) also has run-to-run variance too large for single-run point estimates to be tightly comparable** — `nn.TransformerEncoderLayer` isn't guaranteed bit-for-bit deterministic on CUDA even with a fixed seed, and this project has been treating 43.2%/46.9%/57.8%/68.9% as precise, comparable numbers when they may each just be one draw from a wide distribution. This is a new, generalizable methodology finding, not specific to this experiment — future single-variable comparisons should be read as directional, not exact, unless corroborated by multiple seeds.

  **First real recording session added, 2026-08-08 late night**: recorded a new session via the Tkinter GUI recorder (`app/main.py`, sharing the same `DemoRecorder` backend as the Electron one) after two false starts — two earlier "recordings" (`session_20260808_140418`, `session_20260808_142551`, ~2,100 frames combined) turned out to have **zero keyboard actions** and elevated drag-noise rates (16-26%), meaning they were leftover clicks from troubleshooting the recorder itself, not real form-filling demonstrations. Confirmed via direct inspection before trusting them (`grep`-style per-file audit of action-type composition), not assumed. `session_20260808_144216` (645 frames, 285 real keyboard actions, healthy action-type distribution, spans a real 15-minute pass) is the first genuinely usable new session. Retrained on the 5-session baseline + this one real session, same fixed hyperparameters as the 49.1% control: **50.8% val_click_acc** (saved checkpoint = peak, epoch 46) — a real, if modest, +1.7 point improvement. First positive evidence tonight that more (real, well-typed) data helps, after two batches of bad data that didn't.

  **Re-tested `disambiguate_attempted=True` on this same 6-session/50.8% dataset**, since the original 46.9%-regression result for that flag was measured *before* bug #3 (the LR/weight_decay bug) was found — raising the question of whether that result was contaminated by unrelated underfitting rather than reflecting disambiguation's real effect. **Result: still regresses — 48.2% vs 50.8% (disambiguation off), a real -2.6 point cost under the corrected methodology.** Smaller than the original apparent regression (-22 points under the old, bug-confounded measurement) — confirms the LR/weight_decay bug was responsible for most, but not all, of that earlier damage — but the underlying instability theory (rank-based disambiguation depends on accessibility-tree list order, which isn't as stable a signal as hoped) still holds up under clean conditions. **`disambiguate_attempted` stays at its default `False`.**

  **Data quality lesson worth generalizing**: frame *count* alone is not a proxy for data value — verify the action-type composition of any new recording (real typing present? drag/double-click rate reasonable?) before trusting it in a training run, the same way file/session provenance was already being checked. Two batches of high-frame-count, zero-value data happened here specifically because "record more" got interpreted as "produce more logged steps" rather than "produce more real task demonstrations."

  **Given that, re-ran the weighting comparison as a same-conditions A/B instead of chasing the old absolute number**: `rare_weight_basis="type"` (control, historical-equivalent) peaked at 37.8% click_acc; `rare_weight_basis="field"` (experiment) peaked at 39.6% — a ~2-point gap, within noise, not a real signal. **RESULT: field-level weighting does not meaningfully help.** Kept `rare_weight_basis` default at `"type"` (unchanged); `"field"` remains available for future experimentation but was not adopted. Not pursuing Submit-specific extra weighting on top of this, since the premise (field-weighting helps) didn't hold up — logged as explicitly deprioritized, not abandoned, in case it's revisited with a larger dataset or multi-seed methodology where the signal-to-noise ratio might be better.
- [ ] `learning_rl_phase` — *(future)* Actor-Critic / PPO with the BC transformer as Actor; KL penalty vs BC to preserve the user's style. Online fine-tuning from `StateValidator` reward.

---

### Adaptability
Objective 6 (thesis): ≥75% success rate on unseen GUI environments (layout/position/visual variation).

- [ ] `adaptability_unseen_success_75` — Not instrumented — needs a held-out/perturbed-layout test session distinct from training environments; no "unseen" flag exists yet in `eval_metrics.py`.
- [ ] `adaptability_scroll_gap` — Scroll navigation is a three-layer gap, found 2026-08-06: (1) **data** — 0/11,062 recorded steps ever contained a scroll action (demos relied on Tab-to-reveal only); (2) **model** — `scroll_head` is dead code (`learning_scroll_head_dead`); (3) **agent** — `agent.py`'s scroll-reveal/drought-guard/`_try_advance_tab` block (`agent.py:1589-1630`) runs **unconditionally, "in every mode"** — not gated by `disable_auto_handlers` — fully preempting the transformer's own decision every time, which is the opposite of the thesis's transformer=WHERE division. Fix order: record real scroll demos → wire the loss → fix `executor.py`'s scroll (no foreground-focus assert, likely why `pyautogui.scroll` doesn't move the wx `ScrolledPanel`) → strip the hardcoded agent.py block one change at a time. *requires: `scope1_rerecord_50k`*

  **Reframed 2026-08-07 per explicit user direction, and layer (3) partly addressed — see `execution_navigation_protocol` below.** Rather than wait indefinitely for enough real scroll demonstrations to clone scroll *behavior* (layer 1/2 — genuinely blocked on `scope1_rerecord_50k`), navigation was split out as a system-level responsibility instead: the system's only job is keeping an actionable empty target visible; the Transformer never needs to learn WHEN/HOW FAR to scroll at all. Layers 1/2 (real data, a learned scroll head) remain open for when/if scroll needs to be a genuine model decision rather than a system one — not required for the current fix to hold.

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

- [x] `execution_max_steps_ceiling` — **Found and fixed 2026-08-07, while pushing toward a real end-to-end attempt after tonight's four live loop-bug fixes.** `run_task.py`'s `MAX_STEPS` was 200 — but the form has 176 fields, and even the best live run tonight needed ~5-6 raw steps per field (typing + navigating + occasional retries) to make progress. Full completion needs on the order of 800-1000+ steps, not 200 — the run was mathematically guaranteed to stop short regardless of how many more correctness bugs got fixed, independent of the transformer's own accuracy. Very likely the single biggest reason no run has reached Submit yet. Raised to 1000 with headroom, not tightly tuned to a computed minimum — the first real attempt at this ceiling will show the actual number needed. Not yet verified live.

  **CORRECTION, same conversation: user correctly pushed back — an earlier version of this project fit a full record into ~200-250 steps, so ~5-6 steps/field tonight was a real regression, not just "the form is big."** Raising the ceiling was treating the symptom. Investigated instead: counted field-lookup frequency in the big live run and found individual fields visited far more than others — "Education Level" 11 times, "Number of Doors" 8, "Occupation" and "Years Continuously Insured" 6 each. Traced "Education Level": `_merge()` overrode a "type" decision into a "click" (comboboxes need a click to open — deliberate, not itself a bug), executed via the generic execution path with NO toggle-awareness. Step 41 clicked to open (6 new elements appeared); step 42's transformer, still confidently (0.97) targeting the same spot, clicked AGAIN — closing what it had just opened (`state changed: +0/-9 elements`) — and a naive close-tracking heuristic wrongly logged it as "filled." Repeated 11 times before accidentally recovering through a different, already-fixed handler. Confirmed the identical pattern on "Occupation" too.

  This is the EXACT toggle bug fixed twice already tonight (`_open_dropdown_items`), but in a THIRD, previously-untouched place: the two dedicated combobox handlers both guard against it; this was the one spot `_merge()`'s own click override reaches the generic execution path, bypassing both. Fixed the same way — check for already-open dropdown items before letting the click execute, select the match directly (or Escape cleanly) instead of blindly re-clicking. 2 new tests, reusing the already-tested `_open_dropdown_items`/`_option_matches` building blocks. This is very likely the dominant cause of the efficiency regression — a handful of comboboxes eating 8-11 steps each adds up fast across 176 fields. `MAX_STEPS` deliberately left at 1000 rather than reverted, since this fix hasn't been live-verified yet and cutting the budget back down prematurely risks truncating the NEXT diagnostic run too early to see the real, now-hopefully-much-lower steps/field number. Right-size it once there's real evidence, not another guess.

- [x] `execution_drift_solved` — Drift solved (2026-06-12): form-window LOCK (capture hwnd at GO, re-assert every step) + in-form click guard. No more typing into PowerShell / clicking Notepad.
- [x] `execution_belowfold_solved` — Below-fold reach solved: the drift guard's Tab doubles as scroll — wx `ScrolledPanel` auto-scrolls the focused field into view.
- [ ] `execution_action_space_big3_2` — **Big Three #2:** Action space, form-fields → universal. Today: click/type/select(+combobox). Need click, double_click, type, select, drag, scroll, hotkey, wait, verify, menu, file-dialog. Enumerable engineering, not research.
- [ ] `execution_control_flow_big3_3` — **Big Three #3:** Workflow, linear → control flow. Demos are one linear path; real tasks branch/loop/skip/error-recover. Pragmatic path: LLM induces the workflow (if/for) from multiple traces → execute → DAgger-correct.
- [ ] `execution_agentic_integration_85` — End-to-end task completion ≥85% without manual intervention. `eval_metrics.py` now tracks `manual_interventions` (DAgger corrections) as a direct signal. *requires: `scope1_e2e_metric`*

  **FURTHEST E2E PROGRESS TO DATE, recorded 2026-08-07 per direct user instruction:** *"Good, we reached Payment tab, but there was a loop at the end. Take record of this as our furthest E2E."* Run `logs/run_task_20260807_205021.log` reached **all 8 tabs in correct forward order** — Policy → Policyholder → Vehicle → Coverage → Drivers → History → Claims → Payment — with **zero backward tab cycling**, the first time this session that's held all the way through (confirmed via `grep "Stuck guard: advancing to tab"`, one clean forward-only sequence, no repeats). 46/176 fields filled (26.1%), 354 actionable steps, Value Accuracy 76.8% (43/56 typed values correct), Wasted Step Rate 53.4% (still well above the ≤20% target). The run ended in an 18+ step oscillation at the very end between `'Auto-Pay Enrolled'` and `'Last Payment Amount'` on the Payment tab — see `execution_payment_tab_oscillation_fix` immediately below for the diagnosis and fix. This is a baseline to beat, not a finish line — still far from the 85% completion target and the 20% waste ceiling — but the first clean run of all 8 tabs in order, with the accumulating tab-blacklist and checkbox-recognition fixes both holding under real conditions, is genuine, verifiable progress worth anchoring future comparisons to.
- [x] `execution_payment_tab_oscillation_fix` — **Confirmed live 2026-08-07 on the furthest-E2E run above.** An 18+ step oscillation between `'Auto-Pay Enrolled'` (checkbox, already correctly checked) and `'Last Payment Amount'` (editcontrol, legitimately blank — no record value) at the very end of the run. Two independent gaps combined:

  1. OPT2's fill-branch entry condition (`_t_is_type`: "is the focused thing a fillable, empty field?") never checked `attempted_keys`. Fatal specifically for checkboxes: `ui_observer.py` never reports a real checked state via `ValuePattern` (`wx.CheckBox` doesn't expose it), so a checkbox's `value` reads empty **forever**, checked or not — `_t_is_type` was unconditionally `True` for any focused checkbox regardless of attempted state, forcing the full `ask_llm` → `merge` → click dance every single time, relying entirely on a downstream Win32 `BM_GETCHECK` guard to catch the redundant click and convert it to Tab. Wasteful, but not destructive — that guard did correctly stop it from actually unchecking (unlike `execution_filled_combobox_reclick_guard`'s data-corruption risk).
  2. `_merge()` overrides *any* `"type"` decision into a `"click"` whenever the transformer's (collapsed, near-constant-confidence) action-type head says click — regardless of whether the LLM's intended text was real or empty. `_is_leave_blank_prediction` is deliberately gated on `action_type=="keyboard"` (see `execution_combobox_leave_blank_infinite_loop`) so a legitimate combobox click-override is never mistaken for leave-blank — but that same gate meant a genuinely empty answer for `'Last Payment Amount'` never got recognized once merge had already turned it into a click, and the resulting click used the transformer's own ~69%-accurate pointer position, not this field's — landing almost anywhere, apparently drifting back near Auto-Pay Enrolled.

  Fixed: (1) `_t_is_type` now excludes already-attempted fields — same principle as `execution_attempted_combobox_position_drift`, applied at the fill-branch's entry condition instead of one specific sub-handler, so it covers every field type this decision governs. (2) leave-blank recognition now runs on `llm_action` directly, **before** `_merge()` gets a chance to convert a genuinely-empty `"type"` decision into a stray click. 9 new tests (`tests/test_payment_tab_oscillation_fix.py`). Full suite: 230 passed, 9 skipped, same 2 pre-existing unrelated tesseract failures. Not yet re-verified live.

  **REGRESSION, confirmed live 2026-08-07 in the VERY NEXT run** — directly reported by the user: *"Got considerably worse, check the recent logs. Did you save the best run?"* Task Completion Rate collapsed to 1.1% (2/176 fields in 66 steps, vs. 26.1% on the run right before it), and the run never even left the Policy tab. Traced the log: only `Policy Number` and `Effective Date` ever got filled — both BEFORE the low-confidence-fallback escalation (`execution_lowconf_navprotocol_escalation`) fired for the first time. The moment that escalation directly clicked `'Expiration Date'` to bring it into focus (a legitimate, working part of that fix), `'Expiration Date'` was mentioned in the log exactly once, ever — never filled, never revisited. Root cause: fix (1) above was written to exclude *any* already-attempted field, not just checkboxes. `_record_attempt()` marks whatever a click prediction lands on as attempted, unconditionally, regardless of whether that click typed a value or just moved focus — a click on a checkbox genuinely IS the fill action, so excluding attempted checkboxes was correct; but a click on an `editcontrol`/`comboboxcontrol` is *just navigation* (via the escalation fix, or any other navigate-click) — the field stayed genuinely empty but was now permanently excluded from ever being offered to the fill branch again. The best run's log (`logs/run_task_20260807_205021.log`) was never at risk — each run writes to its own uniquely timestamped file, nothing overwrites or deletes old logs — but it's worth stating plainly since the question was asked directly: yes, still there, untouched.

  Fixed: the attempted-exclusion is now scoped to checkbox types only (`_fe2_chk_attempted`, checked before computing `_t_is_type`) — `editcontrol`/`comboboxcontrol`/`input` rely purely on `not _fe2_val` again, exactly as before fix (1) ever existed, since a bare click never changes their value the way it does for a checkbox. 3 of the 4 tests in `TestAttemptedCheckboxNoLongerReEntersFillBranch` kept (checkbox behavior unchanged), the wrong one (`test_attempted_edit_field_is_also_excluded`, which asserted the buggy behavior) replaced with a new `TestAttemptedEditOrComboboxFieldsStayFillable` class proving the opposite: an attempted-but-still-empty edit/combobox field must stay fillable. Full suite: 232 passed, 9 skipped, same 2 pre-existing unrelated failures. Known related limitation, not fixed here (separate concern, noted for later): the low-confidence-fallback escalation clicks whatever `find_visible_empty_target` returns without checking the record's intended value first — for a checkbox that should legitimately stay *unchecked*, this could wrongly check it. Not observed in either live run so far; flagged for future attention rather than fixed speculatively.

  **THIRD ROUND, confirmed live 2026-08-07, the very next run — reported plainly:** *"Still stuck in a loop."* Good news first: no regression this time — 30.7% completion (up from 26.1%), all 8 tabs reached again cleanly. But the pre-merge leave-blank fix (2, above) and the checkbox guard were both firing correctly and STILL not enough: `'Last Payment Amount ($)'` and `'Last Payment Date'` (both `editcontrol`, genuinely blank per the record) got recognized as leave-blank and Tab-skipped *every single time* — but nothing stopped them from being re-asked about again next time focus landed there. Their `value` can never become non-empty (they're supposed to stay blank by design), so the checkbox-only attempted exclusion from the regression fix above doesn't cover them at all — each revisit paid a full ~2s LLM round-trip just to get told "leave it blank" again, over and over.

  The real gap: `self._attempted_keys` conflates two different things — "clicked for navigation, no decision made" (must NOT block future fills, that's the regression from round 2) and "a genuine decision was made here" (SHOULD block future re-asks, regardless of field type). Checkboxes happen to make every click count as a decision (checking IS the fill action), which is why scoping to checkboxes alone worked for round 1's bug — but a deliberate "leave this blank" decision on a text/combobox field is a real decision too, just not a click.

  Fixed with a new, separate set: `self._leave_blank_keys` — populated only by the pre-merge leave-blank check (fix 2 above) when it genuinely recognizes and skips a field, never by a bare navigation click. `_t_is_type` now excludes a field if its key is in `_leave_blank_keys`, for *any* field type — cleanly distinguishing "we decided nothing goes here" (exclude) from "we merely clicked past it" (must not exclude, per round 2's fix). Cleared at the same new-record boundary as `_attempted_keys` and `_advance_blacklist_pos`. 4 new tests (`TestConfirmedBlankFieldsStopReenteringTheFillBranch` in `tests/test_payment_tab_oscillation_fix.py`), including one that explicitly re-confirms a merely-clicked field stays fillable (guarding against reintroducing round 2's exact regression). Full suite: 236 passed, 9 skipped, same 2 pre-existing unrelated failures. Residual, deliberately not fully closed: the transformer's own navigate-branch pointer can still occasionally click a confirmed-blank field's screen position (a cheap wasted click + focus-move, not an LLM call) — bounded by the same general navigation dynamics as any other already-handled field, not a special-cased loop anymore. Not yet re-verified live.

  **FOURTH ROUND, confirmed live 2026-08-07 — reported plainly:** *"Same loop on Auto Enrolled"* — 1175 log lines mentioning `'Auto-Pay Enrolled'` in one run. This time the root cause itself, not another layer on top of the same symptom.

  Traced it precisely: `_merge()`'s `type`→`click` override (~L4560) uses `t_pred['click_position']` — the **transformer's own, separately-learned navigation pointer**, trained to answer *"where do I click next"*, not *"where is the field that's currently focused."* Those are genuinely different questions with different answers. A concrete instance from the log: with `'Auto-Pay Enrolled'` focused, the pointer guessed `(1484, 426)` — a position that missed the checkbox's real bbox entirely. The click landed on some unrelated element instead, and *that* element got marked attempted by `_record_attempt()` — not the checkbox actually intended. `'Auto-Pay Enrolled'` itself was never recorded as attempted at all, so every earlier fix in this chain (the checkbox-scoped attempted exclusion, `_leave_blank_keys`) had nothing to grab onto — attempted-marking was silently landing on the wrong element the entire time, making it look, from the outside, like none of the prior three fixes had ever shipped.

  Fixed: when merge overrides a fill decision into a click, anchor `click_position` to the **already-focused field's own bbox** (`_fe2['bbox']`) instead of the transformer's independent guess. We already know exactly where the focused field is — there's no reason to trust a second, separately-learned signal to answer "click the thing that's already in focus." Applies to every type this override touches (checkbox *and* combobox), not just the one that happened to surface it — comboboxes carried the same latent imprecision, just less visibly, since their click predictions tend to land closer to correct in practice. 6 new tests (`tests/test_fill_click_anchored_to_focused_bbox.py`). Full suite: 242 passed, 9 skipped, same 2 pre-existing unrelated tesseract failures. Not yet re-verified live.

  **FIFTH ROUND, confirmed live 2026-08-07 — reported plainly:** *"Same problem still."* Real progress this time — `'Auto-Pay Enrolled'` mentions dropped from 1175 to 15 in one run, and only *one* of those 15 was the fill branch re-entering (the anchoring fix genuinely closed that). The other 12 were the **navigate branch's own pointer** independently drifting back onto the checkbox's screen position on its own — a separate prediction, unrelated to the fill branch this whole chain has been fixing. Each one was still caught downstream by the Win32 `BM_GETCHECK` guard and safely converted to Tab (never destructive), but nothing stopped the wasted click before it happened.

  This is exactly the "known related limitation" flagged (but deliberately not fixed) back in round 2's writeup, and the residual explicitly named in round 3's writeup — now actually closed. Extended the existing filled-combobox reclick guard (`execution_filled_combobox_reclick_guard`, previously combobox-only) to also cover already-attempted checkboxes: before executing a navigate-branch click, Tab instead if it lands on a checkbox already in `attempted_keys` — no value check needed for checkboxes (unlike comboboxes, which also require a non-empty value), since a checkbox's `value` never reflects real state at all. 3 new tests added to `tests/test_filled_combobox_reclick_guard.py` (a new `TestAlreadyAttemptedCheckboxIsNotReclicked` class). Full suite: 245 passed, 9 skipped, same 2 pre-existing unrelated failures.

  **Also built out per direct request** ("let's drill it — focus only on that specific tab on Payments — until the problem disappears"): discovered `run_task.py` already has a `--start_tab N` flag and `LLMAgent(start_tab_idx=...)` wired end-to-end for exactly this (apparently built in an earlier session, never previously used or documented in this file) — but its help text was stale, listing tabs only "0=Policy … 4=Drivers" against the current 8-tab form. Fixed the help text to list all 8: `0=Policy 1=Policyholder 2=Vehicle 3=Coverage 4=Drivers 5=History 6=Claims 7=Payment`. To drill Payment tab specifically: manually click the Payment tab in the form, then run `python run_task.py --start_tab 7`. No agent.py changes needed — the mechanism itself was already sound, just undiscoverable.

  **SIXTH ROUND, confirmed live 2026-08-07 via the drill tool itself, its first real use — reported plainly:** *"Still not fixed btw, new loop, check most recent logs."* Genuinely the deepest root cause in this whole chain, not another layer — and only became *visible* because the click-anchoring fix (round 4) started working correctly.

  Traced the drill-mode log: `'Down Payment ($)'` and `'Payment Due Date'` (both plain `editcontrol`) looped forever, `"Validator → no_change"` on every single attempt. The LLM correctly resolved the real value each time (`'374.84'`, `'04/01/2026'`) and correctly decided `"type"` — but `_merge()` overrode that into a `"click"` anyway, purely because the transformer's action-type head said `"click"` with high confidence (0.93-0.96). That head is *collapsed* — it says `"click"` with near-constant high confidence **regardless of what's actually focused**, a limitation already documented elsewhere in this file (`"[TRANSFORMER] action-type head collapses"`). The override's own justification (*"trust the transformer — it learned... which elements are clickable vs typeable, e.g. comboboxes need click"*) was never actually true for the collapsed head — it just happened not to matter before, because the override's resulting click used the transformer's *own, independently-guessed position* (round 4's bug), which usually missed the target field and landed somewhere less consequential. Once round 4's fix made that click land **exactly** on the already-focused field every time, this pre-existing flaw became fully visible: clicking an already-focused plain text field does *nothing at all* — no dropdown to open, nothing to toggle — so the correct value the LLM had just resolved was silently discarded, forever, on every visit.

  Fixed at the actual point of failure: the override now also requires the **currently-focused element's own type** to genuinely need a click first — `checkboxcontrol`/`checkbox`/`comboboxcontrol`/`combobox` only. Plain `editcontrol`/`input` fields skip the override entirely and fall straight through to being typed into directly, exactly as the LLM decided. Checkbox and combobox behavior is unchanged (still requires the click, still gated by the same 0.92 confidence floor and tab-strip/blacklist exceptions already in place). 5 new tests (`tests/test_merge_type_click_override_needs_click_first.py`), explicitly covering both the newly-excluded types (editcontrol, input) and the still-included ones (checkbox, combobox), plus a confidence-floor regression guard. Full suite: 250 passed, 9 skipped, same 2 pre-existing unrelated tesseract failures. Not yet re-verified live.
- [ ] `execution_error_rate_10` — Execution error rate ≤10%, wasted steps ≤20%. Both computed in `eval_metrics.py`'s `evaluate_run()` with explicit pass/fail flags now (2026-08-06) — no live run has produced a clean measurement yet (crashed on stale checkpoint / no LM Studio).
- [ ] `execution_llm_value_errors` — LLM occasionally answers into the wrong field (e.g. Policy Number into Policy Term). LLM keeps owning values; fix = better prompt or lookup-as-validator, not silent replacement. **New concrete evidence 2026-08-08**, from diagnosing a reported loop: `'Account Type'` (blank ACH field) was asked about 4 times in a row and answered `'Full Coverage'` (bled over from `Policy Type`) three of those four times — the *same* field, in close succession, got materially different answers. The blank case for this specific pattern is now sidestepped by `execution_llm_fast_path_lookup`'s second fast path (skip the LLM entirely when three independent lookups agree a field is empty) — but the underlying cross-field bleed-over this surfaced is still open and would still affect fields the lookup can't pre-resolve.
- [x] `execution_stuck_loop_wrong_tab_field` — **Confirmed resolved 2026-08-07, 6th live re-test — see bottom of this entry.** Caught live 2026-08-07 during the post-embedding-fix test run: agent burned 65+ of 200 steps re-focusing "Policy Number" (a Policy-tab field) while the Policyholder tab was active, identical log line repeating forever. Root cause in `_focus_first_empty_field()` (`agent.py:3736`): its candidate scan trusts `state["elements"]` bboxes to tell active-tab fields from inactive-tab ones. Verified live (direct UIA queries against the running form) that this doesn't hold — a hidden tab's field can report the *exact same* positive on-screen bbox as when visible, so coordinate-sign filtering can't distinguish them. What IS reliable, also verified live: the inactive tab's own UIA pane genuinely stops existing (`Exists()==False`) while the active tab's pane exists with real children. A second method, `_uia_focus_first_field()`, already implemented exactly this pane-scoped search correctly — sitting right above the buggy one in the same file — but had zero call sites anywhere in the codebase. Same category of bug as `learning_embedding_hash_fallback_fixed` above: a correct fix already written, never wired in. Fixed: `_focus_first_empty_field` now tries the pane-scoped search first for the common case, falling back to the old state-dict scan only if that finds nothing or a caller needs the `min_y` floor it doesn't support (checked: none of the 5 current call sites do). Regression test: `tests/test_focus_first_empty_field_active_tab.py`.

  **Live-tested twice more the same day, both caught real problems the first fix didn't cover:**
  1. First re-test: the "fixed" code fell back to the unsafe scan anyway whenever the pane-scoped search returned `False` for ANY reason — including the legitimate "nothing left on this tab" case, which is exactly the signal callers already use correctly to advance tabs (`_try_advance_tab`). Corrected: for the common case, the pane-scoped result is now authoritative, full stop — no fallback. Test file updated to assert this (`test_uia_false_is_authoritative_no_unsafe_fallback`).
  2. Second re-test (reported directly by the user from watching a live run): with the fallback removed, a *second*, pre-existing bug in `_uia_focus_first_field()` itself surfaced — its active-pane detection iterated `tab_pane_names` in list order and took the FIRST pane that "exists with a positive-coord child," not necessarily the actually-active one. Live evidence: the agent went Policy (3 of 13 fields) → Vehicle → Coverage, skipping Policyholder entirely, because Policy's own pane satisfied the loop's check before Policyholder's did. Fixed: use `self._current_tab_idx` (already reliably maintained — it's what correctly detects "already on this tab") to look up the exact pane name directly, falling back to the old iterate-and-guess scan only if that specific lookup fails. Regression test: `tests/test_uia_focus_first_field_pane_selection.py`.

  **Third live re-test, reported directly by the user (Policy -> Vehicle, Policyholder skipped again; Policy tab also left incomplete).** Two genuinely different causes this time:
  1. **Real code bug, fixed**: the direct pane lookup ran ~3s after the tab-switch click and still came up "not found" for a genuinely-active, 31-field tab — wx hadn't finished registering the new page into the UIA tree yet; a single 0.05s existence check gave it no room to settle, so it fell through to the coordinate-guess scan (found nothing) and the whole tab got skipped. Fixed with a short retry (up to ~4 attempts, 0.2s apart) before accepting "not found." Regression test added (`test_retries_briefly_before_accepting_pane_not_found`).
  2. **Not a code bug — model behavior**: the Policy-tab incompleteness (4 of 13 fields) traced to the TRANSFORMER's own prediction at step 6 — `ptr_conf=0.20` (very low confidence), followed immediately by a direct tab-click to Policyholder at step 7. This is the model choosing to move on early, not a logic error in the navigation code. Ties directly to tonight's validated click_acc ceiling (~61% post-embedding-fix, still well under the 90% target) — the real fix is more/better training data (the ongoing `scope1_rerecord_50k` campaign), not another agent.py patch. Per CLAUDE.md's "do not hard code for tasks" rule, deliberately not adding a code-level "stay on this tab for N fields" override — that would fight the transformer's own learned behavior instead of improving it.

  **Fourth live re-test, reported directly by the user: Policy -> Vehicle again, Policyholder still skipped.** The retry fix (item 1 above) genuinely ran this time — log literally says "not found **after retries**" — and still failed, ~4 seconds after the tab-switch click. That rules out "just needs a bit more time"; 4s is not a plausible settle delay. No focus-drift was logged this run either (ruling out the interference theory from the second re-test), so the cause is still unknown. Rather than guess a fifth blind fix, added rich diagnostic logging at the failure point (dumps the actual foreground window title/hwnd being queried, and every real `PaneControl` name found in the tree at that moment) so the *next* run gives hard evidence instead of another guess.

  Separately, DID fix and verify (via unit test, not yet live) a related-but-distinct issue: the transformer's own click-pointer confidence (`ptr_conf`) was already computed and logged on every click but never acted on — the live logs showed clicks executed at `ptr_conf` as low as 0.20-0.29, including the exact tab-strip click that ended the Policy-tab pass early (4/13 fields). New `_gate_low_confidence_click()` (module-level, unit-tested, `tests/test_gate_low_confidence_click.py`) declines to act on any click below the same 0.30 floor already established for the non-Option-B merge path, falling back to the existing generic Tab-advance instead — this does not hand WHERE-decisions to the LLM (still forbidden in pure/Option-B mode) and isn't task-specific. **Confirmed live, 2026-08-07 (6th re-test)**: fired 9 times in one run, including a run of low-confidence guesses (0.13-0.29) right after "Underwriter" — the exact point where a stray click previously could land on the tab strip and skip ahead. Zero "Tab-click ->" lines anywhere in that run's log; every low-confidence guess got declined and converted to a safe Tab instead. Result: Policy tab filled completely (9/13 fields — Policy Number through Underwriter) and Policyholder was correctly reached and filled (First Name, SSN, continuing). This is the actual fix for the recurring symptom — not the max_tokens speed change from the same session, which only affects step latency, not navigation decisions. The separate pane-detection code bug (`execution_stuck_loop_wrong_tab_field`) still hasn't reproduced since diagnostics were added; still genuinely unknown whether it's fixed, but this specific symptom (Policy -> Vehicle, Policyholder skipped) is now confirmed resolved via the confidence gate.

  **Fifth live re-test: same visible symptom (Policy -> Vehicle, no Policyholder), but the log shows NEITHER the pane-detection failure NOR its diagnostics fired at all this time — no "not found after retries", no DIAG dump.** Instead, a completely clean, single-cause trace: the transformer directly predicted a click onto the Vehicle tab button (`ptr_conf=0.38`, clears the new confidence floor) while still on Policy. This run's occurrence is NOT this bug — it's `scope1_tab_order` (model prediction quality), see that entry for the evidence. This bug (`execution_stuck_loop_wrong_tab_field`, the pane-detection-timing one) has genuinely not reproduced since the diagnostics were added — status unknown, neither confirmed-fixed nor confirmed-still-broken. Both bugs produce the identical visible symptom, so every future recurrence needs the log checked for WHICH mechanism actually fired before assuming either one.
- [x] `execution_llm_unavailable_vs_blank` — A dead LLM connection (e.g. LM Studio's local server not started) was indistinguishable from the LLM genuinely deciding a field is blank — both collapsed to an empty `prediction["text"]`, so the agent silently Tab-skipped the entire form instead of surfacing that the provider was unreachable. Found + fixed 2026-08-06: new `_is_llm_unavailable()` guard detects `_ask_llm()`'s infra-failure sentinel specifically and halts after 3 consecutive failures instead of blank-filling the rest of the form. Regression test: `tests/test_llm_unavailable.py`.
- [x] `execution_submit_dialog_blocks_run` — Clicking **Submit** (not "Submit & New") popped a blocking native `wx.MessageBox` (missing-fields warning or success confirmation) — a modal dialog owned by the same process as the form. Windows won't let `SetForegroundWindow` bring an owner window back to front while its modal child is open, so `_reassert_form_window()` looped forever ("Re-asserted form foreground" every step, never recovering) the moment Submit was clicked even slightly prematurely — confirmed live 2026-08-06, a run genuinely couldn't progress past it. Fixed at the agent level: detects a foreign foreground window owned by the same PID as the locked form (generic — no hardcoded dialog titles) and dismisses it with Escape before reasserting. Regression test: `tests/test_modal_dialog_dismiss.py`. **Superseded at the root** by `scope1_unify_submit_buttons` below — the dialog-producing button doesn't exist anymore, so this is now a defense-in-depth guard, not the primary fix.
- [x] `scope1_unify_submit_buttons` — Two adjacent buttons (**Submit** `[1348,858,1442,887]` and **Submit & New** `[1224,858,1335,887]`, ~13px apart) were an easy confusion target for a pointer that's only ~31% click-accurate — landing on Submit hit the modal-dialog freeze above, landing on Submit & New silently saved+cleared the form with NO validation regardless of state. Unified into one `_on_submit`: saves/clears/advances unconditionally, no dialog, no required-field gate (removed 2026-08-06 by request — recording/testing needs to reach Submit and reset regardless of completeness; an incomplete record is just visible as empty fields in the saved JSON, nothing silently lost). Removes the two-target ambiguity entirely rather than just handling its failure mode.
- [x] `execution_metrics_not_in_log` — `eval_metrics.py`'s RUN METRICS and `bc_fidelity.py`'s BC SCORE blocks use bare `print()`, which never reaches `logs/latest.log` (only `logger.*()` calls do — `run_task.py`'s `logging.basicConfig` only attaches FileHandlers to the root logger). Found 2026-08-06 while diagnosing a run from its log and getting zero hits on any metrics text. Fixed: both now also emit via a module logger (silent in standalone CLI use, captured when run inside `run_task.py`).
- [x] `execution_correction_watch_speed` — Every failed step (no_change/unexpected/error) blocked for a fixed 4.0s DAgger correction-watch window regardless of whether anyone was actually there to correct it. Proven from a live run's timestamps (2026-08-06): 5 consecutive failures cost 20s of pure dead wait — 62% of that stretch. Fixed: `correction_watch_seconds` is now a constructor param (default 4.0, unchanged for `task_manager.py`/`run_agent.py`/`workflow_builder.py`); `run_task.py` overrides it to 0.5s for unattended/verification runs. Regression test: `tests/test_correction_watch_seconds.py`.
- [ ] `execution_combobox_crash_recovery` — Combobox / mid-record crash recovery.
- [ ] `execution_no_prompt_caching` — No prompt caching: each step is a fresh LLM call, no batching/budget. **Investigated 2026-08-07** while diagnosing why live runs felt slow (measured: LLM round-trips 3-8s each, dominating per-step time at 42.9% LLM dependency in one run). Found this isn't just "not implemented yet" — `_call_openai_compat()` deliberately injected a random `[sid:{uuid}]` tag into the system prompt on every call specifically to defeat LM Studio's cache ("breaks server-side KV-cache accumulation" per the original comment). System prompt measured at 3,006 chars (~751 tokens), identical every call within a run.
  First fixed `max_tokens` (2048 -> 512, oversized unused ceiling for a short JSON decision) — measured before/after and it made **no difference** (avg call time 5.2s both before and after), proving output length was never the bottleneck. Traced the `[sid:...]` line's origin via `git blame`/`git log -S`: bundled into an unrelated commit (`4cfa464b`, OCR-cache instance scoping) with no diagnosed bug, no test, no explanation behind it — not a proven fix, just a defensive guess. **Removed it 2026-08-07** — each call already sends a complete, self-contained message list with no threaded conversation history, so there's nothing else that could legitimately bleed between calls even with caching active. Regression test locks in that the system prompt is now byte-identical across calls within a run (`tests/test_llm_call_speed_params.py::test_system_prompt_is_identical_across_calls_so_it_can_be_cached`). Not yet verified live for an actual speedup — that's the next real test.
- [x] `execution_llm_fast_path_lookup` — **Big find, 2026-08-07**: the user recalled a "constant lookup" lever from an earlier version of this project that avoided repeated LLM value-lookups. Checked `_ask_llm()` and found it: the function already computes the focused field's value via a fast, direct, non-network `_lookup_field()` call (with `_refresh_record_cache`/`_peek_notepad` fallbacks) — and every logged run that night showed **100% value accuracy**, meaning that direct lookup was already correct every single time. But the code then handed that exact value to the LLM as a prompt hint ("use EXACTLY this string... do NOT invent") and paid a full ~5s network round-trip just to have it echoed back — on every type/fill step. Fixed: when the direct lookup already has a confident answer, `_ask_llm()` now returns it immediately (`{"action_type":"type","text":_expected,"_fast_path":"lookup"}`), skipping the LLM call entirely. Only fields the record genuinely doesn't answer (blank/derived/ambiguous — the actual reason to consult the LLM at all) still make the network call. Should eliminate close to all of the LLM-dependency steps observed tonight (42-47% of steps, vs. the `<5%` target) — direct evidence this session showed the transformer+lookup path already gets the value right essentially every time. Regression test: `tests/test_ask_llm_fast_path_lookup.py`. Not yet verified live — that's the next real test, alongside the prompt-cache fix above.

  **SECOND FAST PATH added 2026-08-08, confirmed live via the drill tool — reported directly:** *"Check most recent logs, there seems to be a loop of some kind."* Traced it: `'Account Type'` (a blank ACH bank field on a Credit Card payment record) got asked about **4 times in a row**, one real network call per step. The LLM answered `'Full Coverage'` — hallucinated, clearly bled over from the record's own `Policy Type` value — **three times**, before finally answering "leave blank" correctly on the fourth attempt. ~15 seconds and 3 wrong answers spent on a field the deterministic lookup had already checked three independent ways (cache, refresh+retry, direct peek+retry) and found nothing every single time.

  Root cause: the *original* fast path (above) only short-circuited the truthy case — "still empty after three lookup attempts" fell all the way through to a real LLM call every time, treated as "ambiguous, needs judgment," when in practice it's the opposite: the record has genuinely, confidently nothing there, confirmed three separate ways. Asking the LLM anyway turned out to be actively unreliable for this specific case, not just slow — the same field, same effective prompt context, produced inconsistent answers across consecutive calls. This is direct, concrete evidence for `execution_llm_value_errors` below (LLM occasionally answers into the wrong field) — worth keeping as a data point for that fix, whenever it's tackled.

  Fixed: when all three independent lookup attempts agree a field is empty, `_ask_llm()` now returns a confident leave-blank fast path (`{"action_type":"type","text":"","_fast_path":"lookup_blank"}`) instead of falling through to the network — same trust level as the existing truthy fast path, just for the empty case. Its output is recognized directly by the pre-merge leave-blank check (`execution_payment_tab_oscillation_fix`) — confirmed by a dedicated test wiring the two together, not just asserting the shape looks similar. 4 tests total in `tests/test_ask_llm_fast_path_lookup.py` (2 new, 1 rewritten to match the corrected behavior, 1 kept). Full suite: 257 passed, 9 skipped, same 2 pre-existing unrelated failures. Not yet re-verified live.
- [x] `execution_navigation_protocol` — **Implemented 2026-08-07, at the user's explicit direction and framing**: "a system protocol that replace[s] the direct mimicking of user scrolling... the system itself navigates the GUI... to maximize empty targets on screen for the Transformer/Agent to utilize." New `components/agent/navigation_protocol.py` — a pure decision layer (no pyautogui, no sleeps, no side effects): `decide(state, viewport_bottom, dead_scroll_count) -> WAIT / SCROLL / ADVANCE_TAB`. Consolidates logic that was previously duplicated inline in `agent.py`'s step loop under two different names/conditions (`_no_visible_empty_field`, `_visible_field_sig`) into one named, tested surface — both old methods deleted, fully superseded. Wired into the existing scroll-reveal call site; the actual scroll execution/re-observation stays in `agent.py` (requires live I/O) — only the decision moved out. The separate step-count "drought guard" (a different failure signal, not specifically about screen visibility) was deliberately left untouched to limit risk on a path that can't be live-tested by the agent itself. 12 new unit tests, full suite green. Not yet verified live (per the "only the user runs live tasks" rule) — next real test is a live run.
- [x] `execution_verify_at_fill` — **Gap identified 2026-08-07, from a direct user report about the old (deleted) Intern iteration**: "it lacked a verify-at-fill... we don't want to keep coming back to something, we want it finished as the Agent executes or fills it, a constant checkback always consumes too much time." Confirmed the gap existed in the CURRENT codebase too: `StateValidator.validate()` (`components/agent/state_validator/state_validator.py`) only checked whether *something* changed after an action (focus moved for a click, value changed at all for a keyboard action) — never compared the resulting value against what it was actually supposed to be. A field could get typed into successfully (value changes, validator says `ok`) while still being *wrong*, uncaught. **Implemented same day**: right after a type action, the field's post-type value is compared against what the agent itself decided to type (`prediction["text"]`, already computed via `_lookup_field`/LLM — no new lookup needed). Mismatch retries inline (select-all + retype, bounded at 2 attempts) using the same execute() path already used elsewhere, then moves on regardless so a stubborn field can't stall the run — verification happens as part of filling, not a separate re-check pass. Core comparison extracted into pure, testable helpers (`_verify_fill_matches`, `_find_element_by_id`), same pattern as `_gate_low_confidence_click`. 10 new tests.

  **Own bug found and fixed same day, first live run.** The very first field (Policy Number) triggered 2 wasted retries despite typing correctly the first time. Root cause: `element_id` is assigned purely by scan position (`elem_{offset+count}` in `ui_observer.py`) — self-consistent within one observation, but not stable across separate observations. Verify-at-fill captured the focused id from the PRE-typing state and looked it up inside the POST-typing snapshot; any other element on the form gaining/losing text between the two scans shifts every id after it, so the stale id can silently point at the wrong (empty) element. Fixed: re-derive the focused id fresh from `state_after`'s own `focused_element_id` on every check, never reuse an id computed from a different snapshot. 2 more tests. Confirms live testing is finding real bugs fast — this is exactly why the "not yet verified live" caveat matters on every fix tonight.
- [x] `execution_combobox_leave_blank_infinite_loop` — **Confirmed live 2026-08-07, first real live test since tonight's fixes.** Run got stuck ~60 steps on "Trim / Sub-model" (a combobox), 0 additional fields filled the whole time — the exact cycle traced from the log: LLM/lookup correctly resolved `'Sport 2.0T'`, then `_merge()` overrode the "type" decision into a "click" (comboboxes need a click to open their dropdown before a value can be selected — deliberate behavior in `_merge()`'s `TRANSFORMER_TYPE_OVERRIDE_THRESHOLD` branch, not itself a bug). A click prediction has no `"text"` key at all, so the leave-blank guard immediately after `_merge()` read the missing text as "the record says leave this blank" and Tab-skipped away *before the dropdown could even be used*. Next step, the transformer clicked straight back onto the same combobox, and the cycle repeated forever — visible in the log as alternating `[MERGE] TRANSFORMER overrides LLM type→click` / `leave-blank/empty — Tab past (skip)` lines, dozens of times in a row. Fixed: extracted the leave-blank decision into `_is_leave_blank_prediction()`, gated on `action_type == "keyboard"` first — a click override can never be mistaken for a deliberate leave-blank again. 7 new unit tests (the exact failure case plus the normal none/n-a/"leave blank..." markers it must still catch). **Re-verified live same day: the fix worked** — the run clicked "Trim / Sub-model" cleanly with no loop this time. But that exposed the next bug in the chain immediately after (see `execution_false_done_element_id_churn` below).
- [x] `execution_false_done_element_id_churn` — **Confirmed live 2026-08-07, same run that verified the combobox fix.** After the clean combobox click, the run ended itself 6 steps later — 9 steps total, only 6 of 176 fields filled — logging `StateValidator: task appears complete`. Root cause: a permanent status label (`wx.StaticText` in `car_insurance_entry/car_insurance_form_wx.py`, created once at form startup — only its text ever changes, via `.SetLabel()`) had shown `"Submitted #9 — Ready for next record"` since an EARLIER submission, well before this run even started. `element_id` is assigned purely by scan position (`elem_{offset+count}` in `ui_observer.py`) — self-consistent within one observation, not stable across two separate ones. Some other element on screen shifted the label's id between `state_before` and `state_after`, so `StateValidator.validate()`'s id-based new-element diff read the pre-existing label as freshly appeared and matched it against `_DONE_KEYWORDS` — a false "done."

  **Third bug tonight from the exact same root cause** (`element_id` isn't a stable identity across separate observations), after `_attempt_key`'s repeated-label collision (`learning_rare_field_weighting_and_repeated_label_fix`) and verify-at-fill's own stale-id lookup (`execution_verify_at_fill`). Fixed the same way each time: stop trusting `element_id` alone across snapshots — here, an element only counts as a genuinely NEW completion/error signal if its exact text wasn't already present (under any id) in the before-snapshot. 4 new tests. Given this is now the third independent bug from the same cause, `element_id`'s scan-position-only assignment in `ui_observer.py` itself is worth revisiting at the source rather than continuing to patch each downstream symptom — noted as a real candidate for a future, more foundational fix, not undertaken tonight to keep each change isolated and testable.
- [x] `execution_listitem_type_mismatch_loop` — **Confirmed live 2026-08-07, next run after the false-done fix.** Run looped forever on the "Body Type" combobox: opened its dropdown, found 0 list items every single time despite `StateValidator` confirming 8 new elements genuinely appeared, concluded `'Sedan'` wasn't in the dropdown, pressed Escape, repeated — dozens of times, 0 additional fields filled. Root cause: `ui_observer.py`'s `_CTRL_TYPE_MAP` maps the standard UIA `"ListItem"` control type to `"listitem"` (no `"control"` suffix) — only raw, unmapped `ControlTypeName` strings fall through as `"listitemcontrol"`. Three separate places in `agent.py` filtered dropdown items by `"listitemcontrol"` alone (combobox auto-fix, the click-to-fill handler, the type-into-combobox handler) — all missed every real dropdown using the standard, mapped type. Fixed all three to accept both forms, matching the pattern `_INTERACTIVE`/`_SKIP_TYPES` already used correctly elsewhere in the same file. 2 new tests (one directly greps the file for any reintroduced single-type check).
- [x] `execution_tab_click_stricter_confidence` — **Confirmed live 2026-08-07, twice in the same session** — direct action taken on the recurring "Policy → Vehicle, no Policyholder" complaint (`scope1_tab_order`), not just diagnosis. Both times, the model clicked away to the Vehicle tab at confidence 0.38-0.39 while Policyholder still had unfilled fields — comfortably above the general 0.30 click-confidence floor (`_CLICK_CONF_FLOOR`), so nothing blocked it. A wrong tab-strip click is far more costly than a wrong same-tab field click (skips an entire tab, not one step), so tab-strip clicks now require a stricter floor: new `_TAB_CLICK_CONF_FLOOR = 0.50`, applied via a new `_is_tab_strip_click()` bbox-containment check inside `_gate_low_confidence_click()`. General — any `tabitem`/`tabitemcontrol` element on any form, not tuned to which tab. 9 new tests. This doesn't fix the model's underlying accuracy (still the real long-term fix), but should catch this specific costly failure mode going forward.
- [x] `execution_dead_end_click_blacklist` — **Confirmed live 2026-08-07, immediately after the tab-confidence fix.** Run clicked the same empty, optional "Suffix" combobox 30+ times in a row at HIGH confidence (0.91) — correctly recognized each time as "nothing to fill here" (Escape + Tab + mark-attempted), but that code path `continue`d immediately, bypassing the general repeat-action guard that would otherwise have broken the loop after 3 repeats. Confidence alone couldn't catch this — attempted-marking didn't stop the transformer's own pointer from clicking straight back onto the same position next step. Extended `_gate_low_confidence_click()` with a `blacklist` param: once a position repeats `_REPEAT_LIMIT` times via this dead-end path, it's added to the existing `self._nochange_click_pos` set (already reset per tab) and rejected outright on the next attempt, regardless of confidence. New `_round_click_pos()` shares the exact 10px bucketing `_merge()` already used for its own no_change blacklist. 11 new tests. Fourth loop bug found and fixed via live testing tonight (combobox leave-blank override, false-done from element_id churn, listitem type mismatch, now this) — live testing is clearly earning its keep.
- [x] `execution_combobox_fuzzy_match_gap` — **Confirmed live 2026-08-07, fifth loop bug found via live testing tonight.** Run looped 25+ times on "Body Type": the earlier listitem-type fix worked (other comboboxes succeeded through the identical code path this same run), but the real option text ("4-Door Sedan") never matched the wanted value ("Sedan") — matching only checked exact equality and prefix (either direction), and neither string is a prefix of the other. Added `_option_matches()`: exact, then prefix (existing behavior kept), then a new third tier — whole-word-token containment, so "Sedan" matches "4-Door Sedan" by finding it as a complete token, not a raw substring (which would incorrectly match "active" inside "inactive" — an explicit constraint the original code already guarded, preserved here by tokenizing on non-alphanumeric boundaries). Replaced three separate, slightly-inconsistent inline matchers (one of which was exact-only, missing even the prefix tier) with the one shared, tested function. 16 new tests.

  **CORRECTION, same day: this was a real fix but NOT the actual cause of the persisting loop.** The "4-Door Sedan" guess above was wrong — checked `car_insurance_entry/car_insurance_form_wx.py` directly and `BODY_TYPES` contains `"Sedan"` verbatim, an exact-match option. Re-tested live after this fix: the loop was still there. The log's own `"not in dropdown"` message (as opposed to `"not in options"`, which only fires when items were found but none matched) proved `_listitems` was empty every single time — in the SAME run where other dropdowns (Make, Policy Type, Suffix, State) succeeded through the *identical* code moments apart. Not a matching bug at all: the dropdown popup just hadn't finished rendering within the polling window (4 tries x 0.35s = 1.4s max) consistently enough. See `execution_combobox_poll_timing` below for the actual fix. The `_option_matches()` improvement itself is still correct and worth keeping (it's a real gap for any option genuinely requiring word-boundary matching), it just wasn't why this specific loop kept happening.
- [x] `execution_combobox_poll_timing` — **Confirmed live 2026-08-07, the actual fix for the persisting "Sedan"/"Body Type" loop after `execution_combobox_fuzzy_match_gap`'s fix turned out to be real but insufficient.** Widened both combobox dropdown-polling loops (click-to-fill handler, type-into-combobox handler) from 4 tries x 0.35s (1.4s max) to 8 tries x 0.4s (3.2s max) — the loop still exits the instant items appear, so this only costs time on the slow-render case, not the common fast one. Added a warning log when polling is exhausted (`"dropdown for %r still empty after %d tries"`) so a future recurrence gives direct evidence instead of requiring another live-log investigation from scratch. New test locks in a minimum 2s total poll window so this can't silently regress. Sixth loop bug found and fixed via live testing tonight — and the second in a row where the first fix attempt was real but incomplete, caught only because the user re-tested live instead of trusting the first fix.

  **CORRECTION, same day: still not the actual cause.** Re-tested live a third time — the widened 3.2s poll STILL found 0 items, every single attempt, for a value already confirmed correct in the form's own source. That ruled out timing entirely (3.2s of active polling finding nothing isn't "hasn't rendered yet," it's "there's nothing there"). Traced the real cause in the raw log: element count jumped +13 between the PRECEDING step (nominally focused on "Trim / Sub-model", a different, adjacent field) and the "Body Type" step — the prior step's own transformer-driven click (landing at the same screen position by coincidence/model behavior) had already popped Body Type's dropdown open by accident. The dedicated combobox handler then blindly clicked "to open" it again — **a combobox click TOGGLES, it doesn't just open** — so that second click closed it, guaranteeing every subsequent poll found nothing. See `execution_combobox_toggle_fix` below for the real fix.
- [x] `execution_combobox_toggle_fix` — **Confirmed live 2026-08-07, the ACTUAL root cause after two real-but-incomplete prior attempts (fuzzy matching, poll timing).** New `_open_dropdown_items(elements)` — returns the real listitem elements currently on screen, non-empty exactly when some dropdown is already open. Both combobox-fill handlers now check this BEFORE clicking to open a dropdown, and skip the click entirely when one's already open, using the existing items directly instead of blindly toggling it shut. Replaces two near-duplicate inline checks (added during the two earlier, incomplete fix attempts) with one shared, tested function. 9 new tests. Seventh loop bug found via live testing tonight, third attempt on this specific symptom — each prior fix was real and worth keeping, none was sufficient alone, and the actual cause only became visible once the poll-timing fix ruled out "hasn't rendered yet" as an explanation.

  **The toggle fix worked — first live run to make real progress tonight** (110 actionable steps, 35/176 fields, ran the full 200-step budget instead of dying early on a loop). But that surfaced a distinct, new, quantified problem — see `execution_advance_blacklist` below.
- [x] `execution_advance_blacklist` — **Confirmed live 2026-08-07, immediately after the combobox toggle fix finally let a run make real progress.** Roughly a quarter of the run's 200-step budget went to a repeating tug-of-war: Navigation Protocol correctly decided Vehicle tab was exhausted (two scrolls revealed nothing new) and advanced to Coverage — then the VERY NEXT step, the transformer's own click-pointer (0.73-0.76 confidence, not low enough for the existing gate to block) clicked straight back onto Vehicle's tab button. Confirmed via the log: `"Stuck guard: advancing to tab 'Coverage'"` immediately followed by `"Tab-click → navigating to 'Vehicle'"`, 12-13 times in one run, ~4 steps per cycle. New `self._advance_blacklist_pos`: `_try_advance_tab` now blacklists the tab-strip position it's LEAVING (not the one it's going to), unioned into the same confidence-gate blacklist mechanism built earlier tonight for the dead-end combobox loop. Bounded to at most one entry — replaced, not accumulated, on every advance — so a genuinely later revisit to that tab is never permanently blocked, only an immediate reversal of the system's last deliberate move. 6 new tests. Eighth loop/inefficiency bug found via live testing tonight.

  **CORRECTION, found live 2026-08-07 in a later run.** Bounding the blacklist to one entry only blocked an IMMEDIATE reversal — a 2-hop-back click (Coverage → Policyholder, skipping past Vehicle, the only tab that was actually blacklisted) sailed straight through the gate untouched, producing exactly the Policyholder → Vehicle → Coverage → Policyholder cycle that motivated `execution_navprotocol_checkbox_gap`'s investigation. Directly raised by the user afterward: *"How do we fix the thing where the Agent goes back to already visited tabs? This violates the verify-at-fill thing"* — correctly recognizing this as the same underlying principle as `execution_verify_at_fill` ("we don't want to keep coming back to something, we want it finished... a constant checkback always consumes too much time"), not a separate concern needing separate reasoning.

  Fixed: `self._advance_blacklist_pos` now **accumulates** — `_try_advance_tab` calls `.add()` instead of reassigning the set — so every tab left this record stays blacklisted, and a click back to *any* of them is blocked, not just the single most recent one. Cleared only at the new-record boundary, in the same `if self._record_num != self._attempted_record_num` block that already clears `self._attempted_keys` — so a fresh record's tabs are never pre-blocked by the previous record's history. Verified the single choke point this needed to cover: in Option-B/no-autohandlers mode, the transformer's navigate-branch click (the `else` arm when the focused widget isn't a fillable-and-empty field) is the *only* place a tab-strip click can originate from the transformer's own pointer — the fill branch never clicks tabs — so one gate call site was sufficient, no second site to fix. 3 of the 6 existing tests rewritten to cover accumulation instead of replacement (the old `TestBlacklistIsBoundedToOneEntry` class asserted the now-wrong "replace" behavior), the other 5 kept as-is since they test the shared `_gate_low_confidence_click`/blacklist mechanics, unaffected by accumulate-vs-replace. Full suite: 198 passed, 9 skipped, same 2 pre-existing unrelated failures (tesseract OCR not installed on this machine). Not yet re-verified live.
- [x] `execution_backward_tab_click_guard` — **Confirmed live 2026-08-08, the drill tool's next real use — reported directly:** *"Check most recent logs it jumped to VIN damn."*

  A drill run started fresh at Payment (idx 7); Vehicle (idx 2) was **never visited this session**, so `execution_advance_blacklist`'s accumulating blacklist — which only blocks tabs the *system itself* has deliberately left — had nothing to block it. The transformer's own pointer clicked Vehicle's tab strip at `ptr_conf=0.54` — comfortably clearing the general 0.50 tab-strip confidence floor (`execution_tab_click_stricter_confidence`), which distinguishes magnitude but not direction.

  Every successful full-form run this session progressed strictly forward, never backward — the same evidence base `execution_advance_blacklist` already rested on. So backward tab-strip clicks are now blocked **outright**, not just confidence-gated, at the single shared tab-click-detection code every prediction source routes through — the same "finish forward, don't come back" principle already validated there, extended to cover tabs never yet visited this session, which the blacklist mechanism structurally can't reach (it only knows about tabs it has itself left).

  Building this surfaced a second, real, previously-unnoticed gap: `self._current_tab_idx` was **never reset at the new-record boundary**. `car_insurance_form_wx.py`'s own `_on_submit()` always calls `self.nb.SetSelection(0)` — a real Submit puts the *form* on Policy tab regardless of where the agent started, drill mode included — but nothing told the *agent's own tracking* to follow suit. Without also fixing this, the new backward-click guard would have wrongly blocked the legitimate Payment → Policy reset for the next record, mistaking correct behavior for the exact error it exists to catch — caught during design, not live, per this session's "verify before fixing" discipline.

  Fixed both together: `self._current_tab_idx` now resets to `0` in the same record-boundary block that already clears `self._attempted_keys` / `self._advance_blacklist_pos` / `self._leave_blank_keys`. 8 new tests (`tests/test_backward_tab_click_guard.py`), including one that composes both fixes together to prove the reset correctly prevents the guard from misfiring on a real record transition. Full suite: 265 passed, 9 skipped, same 2 pre-existing unrelated failures. Not yet re-verified live.
- [x] `execution_submit_on_last_tab_exhausted` — **Confirmed live 2026-08-08, the very next run after the backward-tab-click guard held cleanly — reported directly:** *"Okay, that's good. The only problem is that it didn't submit."*

  The log already said the right thing, twice: `"Stuck guard: already on last tab (8 tabs total) — signalling done."` — right at the exact moment Payment (the last tab) was confirmed exhausted (Navigation Protocol: 2 consecutive scrolls revealed nothing new). But "signalling done" was only ever a *log message* — `_try_advance_tab()` just returned `False`, and every one of its ~7 call sites treats that identically to "advance failed for some unrelated reason" (pane-detection drift, etc.), falling through to normal per-step processing. Nothing ever looked for or clicked Submit; the run just kept guessing at ordinary fields forever.

  Fixed at the one place in the file that already knows, with certainty, "there is no next tab *and* this tab's content is exhausted" — the exact generic condition for "finished, click the way to finish this record." New module-level `_find_submit_button()` / `_SUBMIT_KW` (`{submit, save, ok, accept, done, finish, new}`) — promoted from a local variable that already existed in a *different* code path (blocking premature submit clicks) to a shared constant, so both use sites can't drift apart. When the last tab is confirmed exhausted, `_try_advance_tab()` now searches for a matching button and clicks it instead of just giving up.

  **Directly asked by the user afterward: "isn't `_find_submit_button` too specific for this task?"** Answered honestly: it generalizes on two axes — tab *position* (dynamically detected, no hardcoded tab count or names) and generic English finish-words (not "the Car Insurance form's Submit button" specifically) — but it's still a **deterministic rule, not learned behavior**, same category as the dead-end click blacklist. The transformer still doesn't know how to submit on its own; this just guarantees something clicks Submit when the situation is unambiguous. Investigated *why* the model doesn't learn it: of 6,079 training samples across 6 sessions, only **3** are a genuine "click Submit" moment (~0.05% of the data) — not a data void, but so rare it's invisible next to thousands of ordinary field clicks. Real fix is more Submit demonstrations and/or isolated rare-action loss reweighting (queued, not done tonight — see the training-data investigation below).

  Also fixed the same-shaped gap this surfaced: `self._current_tab_idx` needed to be `0` immediately after this new Submit click too (the form really does reset to Policy tab), not just at the record-cache boundary — set directly in the same branch, for the same reason as `execution_backward_tab_click_guard`'s record-boundary fix. 10 new tests (`tests/test_submit_on_last_tab_exhausted.py`), including one that explicitly checks the keyword set names no field, tab, or form specific to this task. Full suite: 275 passed, 9 skipped, same 2 pre-existing unrelated failures. Not yet re-verified live.

  **FOLLOW-UP, same day, direct user question: "How do you fix the blacklist tab?"** The whole backward-click guard, and `execution_advance_blacklist` before it, both stand on one assumption: `self._current_tab_idx` accurately reflects reality. Three of its four update sites are solid — they only ever fire right after the agent's own click is confirmed to have landed on a real tab. The fourth is not: at startup, it's simply set to `start_tab_idx` (0 normally, or `--start_tab N` in drill mode) and never independently checked. Drill mode depends on the human clicking the right tab *before* launching — a typo'd `--start_tab`, a missed click, or a click that hadn't registered yet by the first observation would leave the tracker wrong from step 1, with every downstream guard (the backward-click block included) then reasoning from a false premise for the entire run.

  Fixed at the point data enters the system, not by patching every consumer: extracted `_try_advance_tab`'s own inline pane-detection loop (find which tab's panel has real, on-screen elements) into a standalone `_detect_active_tab_idx_raw()` — zero behavior change there, same scan, same fallback. `run()` now calls it once, on the very first step, and cross-checks the assumed `start_tab_idx` against what's actually on screen. Deliberately the **opposite** trust rule from `_try_advance_tab`'s own mid-run mismatch handling (which trusts the tracker over detection, since mid-run noise from a just-completed transition is the normal case there): at this single, fresh, settled first observation there's no transition noise to distrust detection for, so here detection wins and the tracker gets corrected to match it, with a loud warning logged. 8 new tests (`tests/test_startup_tab_verification.py`). Full suite: 283 passed, 9 skipped, same 2 pre-existing unrelated failures. Not yet re-verified live.
- [x] `execution_step_delay_reduced` — **2026-08-07, evidence-based, not a guess.** Real timestamps from tonight's live runs showed ~2-3s per step fairly uniformly regardless of action type (simple type vs combobox) — meaning the earlier LLM-latency fixes worked (network waits are no longer the bottleneck) and the dominant remaining cost is the fixed `STEP_DELAY` pause (1.5s base) applied at 30+ points throughout the step loop. Reduced to 1.0 in `run_task.py` as a moderate, reversible test — not zeroed out, since some settle time is known to be genuinely load-bearing (the pane-detection timing bug earlier this session came from checking the screen too soon after a tab switch). Not yet verified live.
- [x] `execution_verify_at_fill_giveup_stall` — **Confirmed live 2026-08-07, directly reported by the user: "Error, did not leave Policyholder. Check most recent logs, too much wasted steps."** Traced the log: `'Years Continuously Insured'` failed to type its value — `9` sent via clipboard paste, verify-at-fill checked and found the field still empty, retried twice more (same paste mechanism), still empty every time — then logged `"still mismatched after 2 retries — moving on"`. Except it never actually moved on: the exact same field got re-selected and re-attempted **87 times** for the rest of the run (`grep -c "Years Continuously Insured"` → 87). The agent genuinely never left Policyholder tab, not because of any tab-navigation bug (the earlier `execution_advance_blacklist` fix wasn't even reached — the run never got that far), but because it was stuck inside a single field.

  Root cause: `"moving on"` was just a log message — the give-up branch broke out of the retry `for` loop and fell straight through to the rest of the step with no Tab press, no focus change, nothing. The mechanism that WOULD normally rescue a stuck field (the `_stuck` guard at `~L1195`, which Tabs past a field after `_NO_CHANGE_LIMIT` repeats) is deliberately disabled whenever `self._no_autohandlers` is on — quoting the code's own comment: *"DISABLED when disable_auto_handlers — we want to see the pure transformer with no rescue (honest navigation test)."* Since `disable_auto_handlers=True` is how this project runs per `CLAUDE.md`, nothing else was watching for this. And OPT2's own fill-decision is pure geometry — "is the currently-focused thing empty and fillable?" — with zero awareness of `attempted_keys`, so a field that's still focused and still empty just keeps re-winning that check, forever, every single step.

  Fixed two things, both in the verify-at-fill retry loop (`components/agent/agent.py`, ~L2694-2730):
  1. **The give-up path now presses Tab for real**, right where the "moving on" log line already fires. This is *not* re-enabling the disabled stuck-guard — that guard is specifically about honestly testing whether the transformer learns tab-to-tab and field-to-field navigation on its own, and stays off. This is a dead end inside a *different*, already-approved mechanism (`execution_verify_at_fill`'s own bounded retry-then-give-up), which explicitly promised to "move on regardless so a stubborn field can't stall the run" — a promise the code never actually kept until now.
  2. **Root-caused why the paste failed three times in a row in the first place.** The retry loop was blindly trusting that real OS keyboard focus still matched whatever UIA reported as the focused element, and just re-sent `ctrl+a` + paste without re-establishing focus. Three identical failures back-to-back, same field, milliseconds apart, is a lot less consistent with random Windows clipboard flakiness than with a genuine focus mismatch — the exact UIA-reported-vs-real-OS-focus lag already documented in `execution_stuck_loop_wrong_tab_field` ("wx hadn't finished registering it in the UIA tree"). Each retry now clicks the field's own bbox first, to force real focus onto it, before the ctrl+a/retype.

  4 new tests (`tests/test_verify_at_fill.py`, two new classes: `TestGiveUpActuallyMovesOn`, `TestRetryReclicksBeforeRetyping`), simulating the exact updated loop logic (matching this file's existing testing pattern for this inline, non-extracted code). Full suite: 202 passed, 9 skipped, same 2 pre-existing unrelated tesseract failures. Not yet re-verified live — next run should show forward progress off Policyholder, or a different field stalling if this wasn't the only occurrence of the pattern.
- [x] `execution_lowconf_navprotocol_escalation` — **Implemented 2026-08-07 at the user's explicit direction, watching a live run:** *"Too much wasted steps. Whenever there are no longer any targets on the screen (i.e., there is nothing left to fill) I need you to activate Navigation Protocol so that there will be, capiche?"*

  The log backed it up: bursts of 5-6+ consecutive `"pointer low-confidence... Tab fallback"` steps in a row, zero progress, confidence clustered 0.32-0.48 (below both the 0.30 and 0.50 floors). The only existing response to a low-confidence guess was a blind Tab and hope, repeated every single time — no escalation, no deterministic fallback.

  Two pieces:
  1. `components/agent/navigation_protocol.py` gets a new `find_visible_empty_target()` — the same filtering logic `has_visible_empty_target()` already used, but returning the matching **element**, not just `True`/`False`. `has_visible_empty_target()` is now a one-line wrapper over it (`is not None`) — zero behavior change, confirmed by the full existing test suite still passing untouched.
  2. `agent.py` (`run()`) tracks a new `self._lowconf_fallback_streak` (reset on any confident navigate-click or a genuine fill-decision). After **3** consecutive low-confidence/invalid-pointer fallbacks — not on the first one, since a single guess usually self-corrects — instead of another blind Tab: call `find_visible_empty_target()` directly. A real target visible → click it deterministically, bypassing the unconfident pointer for that one step. Nothing visible either (the literal "no targets on screen" case) → scroll right then (or advance the tab if scrolling's already exhausted for this tab), instead of one more wasted Tab.

  11 new tests: `tests/test_navigation_protocol.py` (`TestFindVisibleEmptyTarget`, 4 tests) and `tests/test_lowconf_fallback_escalation.py` (7 tests covering the escalation decision logic itself, mirroring the exact inline logic per this file's established simulation-test pattern for non-extracted `run()` code). Full suite: 221 passed, 9 skipped, same 2 pre-existing unrelated tesseract failures. Not yet re-verified live.
- [x] `execution_filled_combobox_reclick_guard` — **Confirmed live 2026-08-07, investigating a user report ("Stuck on Marital Status") on a run that otherwise looked healthy.** Traced the log:
  - Step 24: `'Marital Status'` correctly filled → `'Married'`, via the dedicated combobox-fill handler.
  - Step 28: the transformer's **own** pointer drifts back onto Marital Status's own (now-closed) combobox — `ptr_conf=0.58`, comfortably above both confidence floors, so nothing blocked it. A plain click **toggles** a closed combobox open (`+6` elements, confirmed directly in the log).
  - Step 29: the next click lands on a list-item position and closes the dropdown again (`-6` elements) — but via the **generic** navigate-click path, which has zero value-matching logic at all (unlike the dedicated fill handler's LLM-value + option-match flow). It can't tell whether it just re-selected `'Married'` or silently overwrote it with whatever happened to be under the cursor.

  Couldn't conclusively prove which outcome occurred *this specific time*: the run's own Value Accuracy metric only tracks typed text fields — it never checks combobox selections against expected values at all, a real blind spot in the eval tooling (noted here, not fixed — a separate task). But the mechanism itself is definitively real, confirmed via the matching `+6`/`-6` element-count deltas in the log, and the failure mode is **silent data corruption**, not merely a wasted step — so it was fixed regardless of not being able to confirm this one instance's actual outcome.

  Extends the label-based `attempted_keys` check from `execution_attempted_combobox_position_drift` (which only ever covered *empty* comboboxes) to **any already-filled, already-attempted** combobox: before executing a navigate-branch click, check whether it lands on a combobox with a non-empty value that's already in `attempted_keys` — if so, Tab past instead of clicking. Deliberately scoped to comboboxes only (a click toggles them open — uniquely risky in a way a plain text-field click isn't) and to already-**attempted** fields only (a combobox still showing an untouched form *default*, e.g. `'Full Coverage'`, must stay clickable — the record may legitimately want it changed; only fields the agent itself already confirmed are protected). 4 new tests (`tests/test_filled_combobox_reclick_guard.py`). Full suite: 221 passed, 9 skipped, same 2 pre-existing unrelated failures. Not yet re-verified live.

  **SEVENTH ROUND, confirmed live 2026-08-08, on the first full end-to-end run since the backward-tab-click and Submit-on-exhaustion fixes — reported directly:** *"Wasted steps in Coverage due to loops."*

  Traced it: the run hadn't actually reached Coverage yet — tab progression confirmed via the log showed it was still in Vehicle — but the exact same mechanism was there in full force. The navigate branch's own pointer oscillated between **two already-filled fields** for **24+ steps straight**: `'Number of Doors'` (a combobox, already caught by this guard) and `'Model'` (a plain `editcontrol`, not covered by it). Each individual re-click on `'Model'` was cheap in isolation — just a click + Tab, no data-corruption risk, since clicking a text field doesn't change its value — which is exactly why this case was deliberately left open in `execution_payment_tab_oscillation_fix`'s round-3 writeup ("bounded by the same general navigation dynamics... not a special-cased loop anymore"). That reasoning was right for a single stray click; it never anticipated a genuine, *repeating, multi-field* oscillation racking up real cumulative cost across dozens of steps.

  Extended the guard a third time: `editcontrol`/`input` fields with a non-empty value that are already in `attempted_keys` are now Tab-skipped too, on the same value-plus-attempted logic already used for the combobox case (a plain field's `value` accurately reflects real state, unlike a checkbox's, so it needs the same non-empty check comboboxes get, unlike checkboxes which don't). 4 new tests (a new `TestAlreadyFilledEditFieldIsNotReclicked` class in `tests/test_filled_combobox_reclick_guard.py`), including one confirming an attempted-but-still-empty field (e.g. a failed type attempt) correctly stays clickable — this guard only blocks fields that are genuinely already filled. Full suite: 287 passed, 9 skipped, same 2 pre-existing unrelated failures. Not yet re-verified live.
- [x] `execution_verb_loop_rewrite_discovery` — **Discovered 2026-08-07, prompted by the user recalling "something in the old Intern... verb_loop... there were multiple."** `git branch -a` surfaced `origin/verb-loop-rewrite`, a branch not referenced anywhere in `DEVELOPERS.md` or the Task Tree before this entry — meaning it was untracked institutional knowledge. Last commit `d0508dae`, 2026-07-26 (~12 days before this session). It's a near-total rewrite of `agent.py` (+5,847 lines net vs `master`) built around a "verb" abstraction — canonical CLICK/TYPE/SWITCH_TAB/SUBMIT/CHECKBOX verbs, each audited into exactly one code path — and it **deletes `navigation_protocol.py` entirely**, solving navigation a completely different way than tonight's work. Its last commit message: `kill viewport-jump ping-pong — THE GATE PASSED (v3 acceptance run submits end-to-end)` — this branch reached a full end-to-end Submit at some point, the exact goal stated earlier tonight ("We need to the end to end"). It also has commits fixing bugs in the same family as tonight's (`_mark_dead crashed on tuple-shaped attempt keys`, `verify's own re-scan overwrote Driver 2/3 with Driver 1's values`) — possibly the same class of repeated-label/stale-state issue `learning_rare_field_weighting_and_repeated_label_fix` never fully re-solved on `master`.

  **Decision, given directly to the user as a genuine fork (not guessed):** keep patching `master`'s current architecture rather than investigating or switching to `verb-loop-rewrite` right now. Reasoning available to the user at decision time: `master`'s current line of fixes (Navigation Protocol, verify-at-fill, tonight's other work) is small, reviewable, and already committed/tested; `verb-loop-rewrite` is a large, 12-day-stale, architecturally incompatible branch (no Navigation Protocol, no checkbox fix, no verify-at-fill, no tab-blacklist, no combobox-attempted-skip) that would require real investigation before it could be trusted, let alone adopted. User chose **"Keep patching master."** Not closed off permanently — flagged here so it isn't lost a second time; worth revisiting once master's current line of fixes is live-verified and stable.
- [x] `execution_attempted_combobox_position_drift` — **Confirmed live 2026-08-07, immediately after the verify-at-fill give-up fix.** That fix worked — no more 87-step infinite loop on one field — but the user's very next report was *"Stuck again and so many wasted steps"*, then *"We should stop wasting steps like that, the work is getting dirty"*, backed by the run's own metrics: 55.6% wasted-step rate, only 15/176 fields filled in 45 total steps.

  Traced the log: `'Suffix'` (an optional combobox, correctly left blank per the record's own data) got the **full** click-open → check-value → escape → mark-attempted → Tab treatment **4 separate times** across a single Policyholder-tab pass — roughly 10 steps spent re-confirming a field the agent had already correctly figured out was blank.

  Root cause: the existing dead-end defense (`self._nochange_click_pos`, built for `execution_dead_end_click_blacklist`) only blacklists an *exact, 10px-bucketed pixel position* after 3 identical clicks in a row. But the transformer's own click-position *estimate* for the same field isn't pixel-identical across separate approaches — it drifts a few pixels each time it circles back to Suffix minutes apart (not consecutively) — enough to dodge the existing bucket and restart a fresh 3-strikes-then-blacklist cycle instead of staying skipped for good. The `"treat as FILL"` branch (`components/agent/agent.py`, ~L2073) never checked `self._attempted_keys` — the label-based, pixel-drift-immune bookkeeping Navigation Protocol and verify-at-fill's own retry loop already rely on — before clicking at all.

  Fixed: check `attempted_keys` **first**. If this exact combobox (by its `_attempt_key`, i.e. its label) was already confirmed blank earlier this record, skip straight to Tab — no click, nothing to open, nothing to verify. 4 new tests (`tests/test_attempted_combobox_skip.py`), including one that deliberately changes the element's `bbox` and `element_id` between two simulated visits to prove the label-based key isn't fooled by drift the way the position-based blacklist was. Full suite: 206 passed, 9 skipped, same 2 pre-existing unrelated tesseract failures.

  **Separately noted but deliberately NOT patched**: the same log also shows long bursts of low pointer-confidence `"Tab fallback"` steps (6+ in a row, confidence clustered 0.32-0.48, below the 0.30/0.50 floor). That's the already-documented ~68.9% click-accuracy ceiling this project has tracked all session (`scope1_tab_order` and others) — a model-quality limitation, not a control-flow bug. Per `CLAUDE.md` ("the real fix is more training data not another patch") and this session's own established precedent on the same class of issue, no new confidence-gate patch was added for it. Not yet re-verified live.
- [x] `execution_navprotocol_checkbox_gap` — **Confirmed live 2026-08-07 — a fresh run, started AFTER every one of that night's fixes, still cycled Policyholder → Vehicle → Coverage → back to Policyholder for 10+ minutes, never reaching Drivers.** Traced one cycle in the log (`grep "Tab-click → navigating to\|Stuck guard: advancing to tab"`) and found a 23-consecutive-step pure-scrolling stretch on Coverage (steps 405–427): element count flat at 152, zero field interaction the entire stretch, just `"Navigation Protocol: scroll moved the view — new fields revealed"` repeating without ever stopping to act.

  Per the discipline earned from two misdiagnoses the same night ("4-Door Sedan" and poll-timing — both wrong, both caught only by re-checking real evidence instead of trusting a guess), checked the actual form source before touching any code: `car_insurance_entry/car_insurance_form_wx.py`'s `_build_coverage_tab` has 18 checkboxes (`_check_grid`) across "Additional Coverages" (10) and "Discounts Applied" (8), and nothing else in that stretch of the tab.

  Root cause: `navigation_protocol.py`'s `_FILLABLE_TYPES` (built earlier that same session, for `execution_navigation_protocol`) never included any checkbox type — `has_visible_empty_target()` could never return `True` for a screen made only of checkboxes, so `decide()` kept returning `SCROLL`. Meanwhile the view kept *genuinely* changing anyway (other comboboxes drifting in/out at the viewport margins as the page scrolled), so the dead-scroll cap never tripped to force a tab advance either — worst of both worlds: never waits, never gives up either.

  Second, related bug found in the same pass: `_SIG_TYPES` said `"checkboxcontrol"` only, but `ui_observer.py`'s `_CTRL_TYPE_MAP` maps real `wx.CheckBox` controls to `"checkbox"` (no suffix) — the exact same mapped-vs-raw split that caused `execution_listitem_type_mismatch_loop` earlier the same night, just resurfacing on a different field type. So checkboxes weren't even registering in the scroll-signature check either.

  Fixed: added both `"checkbox"` and `"checkboxcontrol"` to `_FILLABLE_TYPES` and `_SIG_TYPES`. Checked for the opposite failure mode before committing — does this now force a click on every checkbox, including ones meant to legitimately stay unchecked? No: `NavAction.WAIT` only stops the blind-scroll and hands the step back to the transformer's own normal per-step judgment, it never forces a specific action. And `_record_attempt()` already marks whatever element a click lands on as attempted — generic, not combobox-specific — so once the transformer does act on a checkbox it stops being offered as a target on the next `decide()` call. 6 new tests (`tests/test_navigation_protocol.py`). Full suite: 196 passed, 9 skipped, 2 pre-existing unrelated failures (`tests/test_cv_perception.py`, tesseract OCR not installed on this machine — a separate CV-based perception path, untouched by this fix). Not yet re-verified live.
- [x] `execution_checkbox_reset_default` — **Confirmed 2026-08-08, reported directly:** *"Submit button doesnt reset Checkboxes."* This is a bug in the practice-form fixture itself (`car_insurance_entry/car_insurance_form_wx.py`), not the agent.

  `_clear_all_fields()` (called by `_on_submit()` on every Submit, to reset for the next record) set **every** checkbox to `False` unconditionally, ignoring each checkbox's own declared `default`. Grepping for `default=True` only found 3 hits due to formatting differences in the source; instantiating the actual form headlessly (`wx.App` + `CarInsuranceFrame`, confirmed to work fine without a visible window) and reading `_checkbox_defaults` directly found the real count: **six** checkboxes default to checked on first load — `paperless` (Policy: "Paperless / e-Delivery"), `v_airbags`/`v_abs`/`v_daytime_lights` (Vehicle), `cov_um_uim` (Coverage: "Uninsured/Underinsured Motorist"), `pay_auto_pay` (Payment: "Auto-Pay Enrolled") — and every one of them answers "checked" in every record's own ground-truth data too, so this wasn't an edge case: record 2 onward opened with all six wrongly unchecked, every single run, requiring the agent (or a human) to re-check all six that a fresh form load never had to touch.

  Fixed (first attempt): `_check()` recorded each checkbox's declared default in a new `self._checkbox_defaults` dict at construction time; `_clear_all_fields()` reset each checkbox to its own recorded default instead of a hardcoded `False`. 7 tests, full suite green.

  **CORRECTED same day, direct user spec:** *"When I press Submit I want all the checked checkboxes to become unchecked."* The restore-to-default behavior above was the wrong call — not what was actually wanted. Reverted `_checkbox_defaults` entirely (dead code once nothing reads it) and simplified `_clear_all_fields()` back to unconditionally unchecking every checkbox, matching the direct spec exactly: Submit always clears every checkbox, regardless of its construction-time default.

  While investigating, also found a second, real reason even the *original* plain `ctrl.SetValue(False)` reportedly wasn't visibly working in practice: `agent.py` checks boxes via a raw Win32 `BM_SETCHECK` message sent straight to the native control (deliberately — its own comment says *"pyautogui clicks don't toggle wx checkboxes"*), bypassing wx's click/event pipeline entirely. wx's own Python-level `CheckBox` object never observes that native-level change, so its cached internal value can silently disagree with the real, visible, native state — meaning `ctrl.SetValue(False)` alone isn't guaranteed to re-sync a checkbox that got checked this way. Fixed by also sending `BM_SETCHECK` (unchecked) directly to the native control on reset, via `ctrl.GetHandle()` — the exact same mechanism the agent already uses to check boxes, so the reset can't be defeated by that same desync. A dedicated test (`TestNativeWin32StateIsForcedInSync`) simulates this precisely: checks a box at the *native* level only (bypassing `ctrl.SetValue()` entirely, exactly like the agent does), then confirms `_clear_all_fields()` still forces it unchecked.

  5 new tests (`tests/test_checkbox_reset_to_unchecked.py`, replacing the deleted `tests/test_checkbox_reset_to_default.py`), using a real headless `wx.App`/`CarInsuranceFrame` instance rather than mocking `wx` — this is form-widget behavior, nothing meaningful to fake. Full suite: 255 passed, 9 skipped, same 2 pre-existing unrelated tesseract failures. Not yet re-verified live.
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
- [ ] `evaluation_thesis_writing` — Thesis: Chapters 1-3 done; Chapter 4 (Results/Discussion) drafted but stale (see `evaluation_thesis_ch4_stale`); Chapter 5 (Summary/Conclusions/Recommendations) is empty headers only, not started.
- [ ] `evaluation_thesis_ch4_stale` — `Thesis.docx` Chapter 4's numbers (59 training runs, 43 end-to-end runs, 5,541 states, 60-78% field correctness, scalability objective marked not-fulfilled, etc.) are a snapshot from the *old* Intern — the pre-fix system, before this session's Submit unification/tab-focus/modal-dialog fixes and before the current ~50,000-step recording campaign. Decision: leave Ch.4 as-is (don't touch prose) until the new campaign is recorded and the model is retrained on it, then regenerate every cited number from the actual metrics scripts (`bc_fidelity.py`, `validate_transitions.py`, `encoding_ambiguity.py`, `objectives_report.py`) against the new data — do not hand-edit the numbers without a fresh run backing them. *requires: `scope1_rerecord_50k`*
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
