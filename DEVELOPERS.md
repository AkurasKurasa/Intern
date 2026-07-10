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
3. [Quick Start](#quick-start)
4. [Components](#components)
5. [Task List and Priority List](#task-list-and-priority-list)
6. [Decisions and Concepts](#decisions-and-concepts)
7. [DAgger — How to Implement](#dagger--how-to-implement)
8. [Open Technical Questions](#open-technical-questions)
9. [Strategic & Thesis Concerns](#strategic--thesis-concerns)
10. [Risks & Technical Debt](#risks--technical-debt)
11. [Finished Tasks](#finished-tasks)

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

**MILESTONE (2026-06-18): Scope #1 runs END-TO-END.** One unattended run (~124 steps)
fills all 8 tabs, runs a self-verification pass over every tab, and presses Submit — no
premature submit, no infinite loop, no crash. Committed+pushed (`nav-protocol-scroll-submit`,
3b24421). The **Navigation Protocol** is the governing loop:
*fill → feed the transformer (scroll) → page-done? → next tab → all tabs → verify → submit.*

**MILESTONE (2026-07-08/09): Universal Semantic Action Space + Navigation Protocol v2.**
Two P1-scale pieces landed early (uncommitted work from 07-07/08 sessions, committed 07-09):
- **Universal Semantic Action Space** (P1 "Action Space: Form-Fields → Universal", core
  delivered): verb vocabulary (`semantic_action.py`: FOCUS/SET_VALUE/SELECT_OPTION/TOGGLE/
  INVOKE/SCROLL_TO/HOTKEY/WAIT/VERIFY/DONE), offline demo labeler (`recorder/action_labeler.py`,
  control-type + before/after state diff, validated on all 6,950 eight_Tabs traces), and an
  opt-in `--action_space semantic` training path. **v2 split-head model BEATS the legacy
  baseline: click_acc 0.957 vs 0.878, plus a new typing-target pointer (src_acc 0.856)** →
  `model_eight_tabs_semantic_v2.pt`. v1 (single merged pointer head) scored 0.828 — one
  pointer head per job is the lesson.
- **Navigation Protocol v2 (ranked WHERE + optimal viewport).** The transformer's pointer
  head scores EVERY element; the agent now consumes the ranked top-k instead of argmax:
  (1) *visible-first arbitration* (`_pick_ranked_target`) — masked/dead/filled/blacklisted
  targets fall through to the model's own next-best, on-screen candidates before off-fold;
  "already correct" = auto-marked filled and skipped, structurally (the 06-18 fixation class
  is impossible by construction); (2) *optimal-viewport jump* (`_optimal_viewport_jump`) —
  when zero visible targets remain, slide a viewport-height window over all remaining empty
  fields and jump to the densest one. Replaces the M1-minimal-reveal crawl (one field per
  scroll), the M2 fold-trigger, and the stranding guard. Live-verified 2026-07-09.
- **Fixation escalation + last-tab sweep handoff** — 2nd fixation on the same spot forces
  tab-advance; on the last tab (no unvisited tabs) it hands the page to `_sweep_tab` instead
  of no-oping (the 07-08 Payment-tab infinite loop).
- **Viewport-top fix** — pane top is ~149 (below tab strip), not 0; three visibility checks
  used `y >= 0`, so fields scrolled UP out of the pane kept "visible" stale bboxes and got
  clicked in the tab-strip zone (`_form_viewport_top`, applied in `_nav_fill_field` /
  `_reveal_missing_by_scroll` / `_reveal_target`; `_nav_fill_field` refuses stale-coord clicks).

**MILESTONE (2026-07-09 15:54): FIRST FULLY AUTONOMOUS SUBMIT on Navigation Protocol v2.**
Complete unattended run — model-driven fills across tabs, ranked arbitration steering,
stall-rescue jump-before-sweep, sweep terminating with dead-marks, deterministic
verification pass, **self-pressed Submit** via the verify convergence gate. The end-to-end
loop is closed on the semantic v2 model.

**MILESTONE (2026-07-10 evening): THE GATE PASSED — full uninterrupted acceptance run on
v3 submits end-to-end.** One hands-off run: tab 0 → all tabs → verify → autonomous Submit,
zero human touches, on `model_eight_tabs_semantic_v3.pt` with the whole week's stack live
together for the first time (section-qualified keys, identity executor, ranked arbitration,
model-anchored jump + the same-day ping-pong fixes: density gate, viewport lock,
far-field reveal). The run earlier the same evening wedged in a two-anchor jump ping-pong
at ~step 180 (Drivers); the fixes landed and the rerun went to Submit. CAVEATS (honest):
the run's metrics block was not archived — capture the scorecard on the next run; the five
2026-07-09 complaints are validated wholesale (run completed) but not itemized against the
per-complaint numbers yet.

**Honest gaps remaining (2026-07-10 evening, post acceptance run):**
- **Acceptance-run scorecard not archived** — the passing run's metrics block wasn't saved;
  next run must capture Task Completion / Field Match / LLM% so the milestone has numbers.
- **Label-collision blindness — FIXED (f88d4fc) and now full-run confirmed** (acceptance
  run submitted end-to-end with section-qualified keys live).
- **Filling sequence fidelity** — order is noisy vs the demos (Behavioral Match 0%);
  corpus still contains footer-button noise (clean_demos fix committed, corpus NOT
  re-cleaned, model NOT retrained on it). Root fix = re-clean + retrain, not more logic.
- **Viewport choice is a geometry heuristic** — densest-window jump is agent-side WHERE
  (a crutch by the division-of-labor rule). Principled fix: anchor the jump on the model's
  top-ranked off-screen candidate from `click_topk`, density as tiebreak.
- **Hard widgets dead-mark instead of filling** — SpinCtrls, some checkboxes ('Homeowner'),
  fold-edge comboboxes ('DL Issuing State'). Fix = identity-based executor (live UIA
  resolve; ValuePattern/TogglePattern/type-to-filter; kills the stale-pixel bug class too).
- **Transformer↔LLM balance** — LLM ~45-50% of decisions (target <5%); sweep still drove
  Policyholder's below-fold section. Verb-driven agent loop remains open.
- **Scale** — single record only; no automated scoring harness yet.
See [Task List and Priority List](#task-list-and-priority-list). The scope-agnostic engine (foundation) is built;
finishing scope #1 *correctly* builds the general muscle the other scopes reuse.

### The Three Scopes (Thesis Scope)
Chosen to span the interesting space — *data entry*, *cross-app transfer*,
*conditional judgment* — so the claim is "clones varied GUI workflows," not "fills
one form."

1. **Data Entry Form Filling** — *in progress ([P0](#task-list-and-priority-list)).* Single-app
   key-value entry, (mostly) linear. Loop proven on the Policy section (clones
   order, fills, submits); remaining = multi-tab, multi-record, cold-start.
   Perception = UIA.
2. **Web Form → Excel** — *not started ([P1](#task-list-and-priority-list)).* Cross-application
   transfer (web source → Excel grid); 2D target; mixed perception. Excel
   perception swap **PROVEN** (`ExcelObserver` normalizes to canonical); remaining
   = web source, action on cells, demos, train.
3. **Email / Ticket Triage** — *not started ([P2](#task-list-and-priority-list)).* Decision-making /
   conditional behavior; the strongest personalization claim (two users triage
   differently). Needs branching ([Big Three #3](#task-list-and-priority-list)) + judgment
   cloning. Kept to decisions inferable from *visible* content (avoid hidden-intent).

### North Star — Generalization (Beyond the Thesis)
The thesis is *bounded* to those three, but the **architecture is built to
generalize** — that is the real goal. The novel contribution: a **personalized,
demonstration-learned GUI agent** that learns how *this user* does a task and
reproduces *their* workflow — which scripted RPA (no learning) and generic
computer-use agents (not personalized) don't.

Already partly real: perception is an **adapter** (UIA + Excel today, one shared
schema) and the `transformer(WHERE) + LLM(WHAT)` loop is perception-agnostic. The
path beyond the thesis — a vision perception adapter ([Big Three #1](#task-list-and-priority-list)) + LLM-induced control-flow ([Big Three #3](#task-list-and-priority-list)) — turns "three GUI scopes" into "any GUI workflow learned from demonstration."

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
[Big Three #1](#task-list-and-priority-list).

### Behavioral Cloning Process

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

#### Key features that make navigation learnable
- **`is_filled`** (per element) — does this field currently hold a value, read
  straight from the observation. Without it the model only saw field *labels* and
  was blind to which fields were done → it looped. This is *perception*, not a
  hand-tracked progress signal.
- **`is_focused`** — which element has keyboard focus.
- **Action-space collapse → {click, type}** — junk classes (stray `drag`,
  `backspace` hotkeys) were dropped/remapped so the action-type head stopped
  collapsing onto classes it could never predict. Action-type accuracy 50% → 80%,
  and click accuracy rose for free.

#### Testing variations (does it clone, or just memorize?)
Record demos in multiple fill orders and confirm the agent reproduces each:
- **Top-down** — fields top-to-bottom (baseline). ✅ confirmed
- **Bottom-up** — fields bottom-to-top. ✅ confirmed (93%, proves not a down-bias)
- **Random (fixed)** — a chosen non-obvious order, repeated consistently. *pending*

A clone that handles a *different* demonstrated order proves field-level learning,
not sequence memorization. `scripts/test_clone.py` reports exact% + the offset
distribution (0 = exact, +1 = next-down, −1 = next-up) so you can see the
*direction* it learned.

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

## Task List and Priority List

**Visual mirror: [`treetask/`](treetask/index.html) (`make tree`)** — an interactive 3D
tree of this list. Apex = "Complete Intern"; branches are OUTCOMES you can verify
("the form fills itself", "the score can be trusted"), not mechanisms — jargon lives
in each node's note, every node states what DOING it means. Statuses, priorities and
dependencies (⛓ = blocked) are maintained in `treetask/tasks.js`; keep it in sync with
this list on every guardianship sweep. The priority-table overlay in the tree matches
⭐ PRIORITY ORDER below.

**Goal: COMPLETE SCOPE #1 (the form) first — then generalize.**

**MILESTONE (2026-06-18): Scope #1 runs END-TO-END.** One unattended run (~124 steps) fills all 8 tabs, runs a self-verification pass over every tab, and presses Submit. Roles: transformer=WHERE, LLM=WHAT, agent=HOW.

---

### 🔴 CURRENT STAGE (P0 — Complete Scope #1)
*Definition of Done: Agent fills all 5 records, all 8 tabs (including Driver/Vehicle sub-sections) in demonstrated order, submits each, with no human help, high field-match, and minimal waste.*

- [x] **Combobox click-fill** — Fix combobox spiral by clicking to open and select.
- [x] **False-done guard** — Notepad source data no longer triggers premature completion.
- [x] **Option B (WHERE/HOW division)** — Use focused widget type to decide action, bypassing unstable action-type head.
- [x] **Tab-targeting** — Transformer's pointer predicts and targets tab items directly (Policy/Policyholder/Vehicle/etc.).
- [x] **Tab-routing** — A tab-click now navigates to the tab the model clicked, not blind incremental advance.
- [x] **Tab-visit coverage** — Keep agent-side visited tracking to prevent loops and ensure all tabs are visited.
- [x] **Empty-field fixation** — Add `'attempted'` feature so transformer stops re-targeting fields already acted on.
- [x] **Drift lock** — Lock form window focus and prevent click coordinates outside the form boundaries.
- [x] **Idempotent typing** — Select-all before pasting so retried fields overwrite instead of appending.
- [x] **`(leave blank)` handler** — Skip fields if value indicates blank/none/na.
- [x] **Real panel scrolling** — Implement UIA `ScrollPattern.Scroll` rather than mouse scrolling.
- [x] **Checkbox TogglePattern** — Query and set checkbox state deterministically.
- [x] **Verification pass** — Perform automated verification pass over all tabs before final Submit.
- [x] **Section-qualified identity keys** *(2026-07-09, f88d4fc — live-verified on Drivers tab: D2/D3 sections fill; sweep section-corrects wrong-section values; per-element fail counters killed the 15× dead-mark loop. Keys use raw `section_*` pane labels (geometry), NOT ScopeConfig — must not silently degrade to the colliding bare label.)*
- [~] **Identity-based executor** *(2026-07-09, f88d4fc — `_resolve_live_control` + `_act_on_element` built, routed through `_nav_fill_field`, live-verified: a dozen ValuePattern fills on Drivers. REMAINING: route the OPT2 fill path + verify-fixes through it; then retire snap/stale-coord guard + pixel visibility checks.)*
- [x] **Re-clean corpus + retrain semantic model** *(2026-07-09 — `eight_Tabs_clean2`, v3: click_acc 0.945, src_acc ~0.85, val_acc 0.758, section-aware `attempted`.)*
- [x] **Model-anchored viewport jump** *(2026-07-09, f88d4fc — anchor = model's top off-screen `click_topk` candidate, density fallback; unit-verified all three cases.)*
- [x] **Viewport-jump ping-pong (lock until progress + far-field reveal)** *(FOUND 2026-07-10 acceptance attempt, step ~180 Drivers: two anchors — 'DL Issuing State' ↔ 'Accidents (3 yr)' — alternated 14 jumps, zero fills, run wedged. THREE holes, fixed in two passes same day: (1) model-anchor branch skipped the "already densest" gate the fallback has — jumped to a 1-empty window with 2 empties visible → density gate added; (2) loop-breaker was single-slot (`_last_jump_anchor`) — caught A→A, blind to A→B→A → viewport lock `_jump_anchors_since_progress` set (no re-jump to ANY anchor visited since last progress; clears when the ranked picker finds work); (3) ROOT of the blind landings: wx SetFocus reveals the anchor at the NEAR edge and `_maximize_reveal`'s ScrollPattern paging no-ops on deep tabs (the known P0 scroll bug) → promised window never comes on screen → "all candidates masked" → re-jump, lock burning REAL fields as collateral. FIX: far-field reveal — jump focuses the window's far-side field (down → bottom-most empty; up → the anchor), wx exposes the whole window in ONE SetFocus, ScrollPattern dependency removed from the jump entirely. Offline probe `scratch/probe_jump_pingpong.py` passes all 5 cases; CONFIRMED by the passing acceptance run same evening.)*
- [x] **FULL ACCEPTANCE RUN on v3 [PASSED 2026-07-10 evening]** — one uninterrupted run, tab 0 → all tabs → verify → autonomous Submit, zero touches, whole week's stack live together. The end-to-end claim on v3 is closed. *(Caveat: metrics block not archived — capture the scorecard next run. NEXT gate = Multi-record ×5.)*
- [~] **Section-aware eval scorer** — `eval_metrics` FIXED 2026-07-10 (41cff4c): section-first
  value matching via pane geometry (same partition as agent identity keys); D2/D3 fills now
  scored against their own values. REMAINING: audit `bc_fidelity`'s gold-key matching for the
  same bug class before trusting BC SCORE numbers.
- [ ] **Scroll no-ops on tabs** — Fix `ScrollPattern.Scroll` failure on Claims/History/Drivers so all below-fold fields are reached, then remove the verification "accept-after-2-tries" band-aid. *(2026-07-09: optimal-viewport jump + viewport-top fix improve reach; deep-tab scroll still unverified end-to-end.)*
- [ ] **Hard-to-fill widgets** — Implement type-to-filter select for 50-state dropdowns and digit keystrokes/read-back for numeric SpinCtrl widgets.
- [ ] **Value quality (LLM mapping)** — Improve label-to-record mapping and inject section keys so LLM doesn't grab incorrect intake lines or wrong sections.
- [ ] **Deterministic verification polish** — Remove verification band-aids, cut per-field LLM reasoning calls, and speed up the validation pass.
- [ ] **Multi-record scaling (×5) [NEXT — the scope's Definition of Done]** — Implement automated record advance, per-record data refresh, and reset loops for all 5 records. Unblocked by the passed acceptance run; pair the first ×5 attempt with an archived scorecard per record.
- [ ] **Automate scoring harness** — Extend `scripts/bc_fidelity.py` to report blank fields, print full breakdowns, save scorecards, and fix Unicode print issues (do before fixing correctness).
- [ ] **Strip WHERE-crutches** (Stage 2.5) — Remove agent-side navigation helpers (`_try_advance_tab`, `_focus_first_empty_field`, auto-advance-at-bottom) to let the transformer navigate fully. *(2026-07-09: ranked arbitration replaces several crutches at the source — WHERE stays the model's own ranking, agent only legality-filters; M2 + stranding guard deleted rather than added-to.)*
- [ ] **Close ruleset-inference loop** — Fix `_compress_session` to decode new trace format and capture notepad/source values to infer skip/leave-blank rules.

---

### 🟢 P1 — Generalize (Post Scope #1 Completion)
- [~] **Perception: Accessibility Tree → Vision** *(component landed via PR #8: CV+OCR observer,
  drop-in via the observer seam, works across all 8 tabs; `--perception vision` wired into
  record/run; debug flipbook + live viewer (`make see`) added 2026-07-10. First live agent probe
  (2026-07-10): navigation works from pixels, 0 fields filled — gaps below.)*
  - [ ] **Vision: focus inference [BLOCKER for vision fills]** — pixels expose no keyboard focus →
    `focused_element_id` is always None → the OPT2 fill trigger ("focused empty field") never
    fires → agent navigates but never types. Fix: agent-side inferred focus (last clicked
    fillable) when the observer reports None, or visual caret/highlight detection.
  - [ ] **Vision: label fragmentation** — OCR splits/mangles labels ('Policy Number' → 'Number',
    '| (Renewal Policy ()Paperles'); record lookup + sweep proposals + UIA name cross-check all
    miss. One clean label ('Underwriter') filled fine via the identity executor — label quality
    IS the fill rate. Fix: merge adjacent word boxes per row in cv_detector; strip punct noise.
  - [ ] **Vision: window-chrome phantoms** — caption-bar buttons detected as checkboxes; agent
    clicked (1890,20) next to the CLOSE button. Fix: exclude the captured window's caption band
    from detection (generic: title-bar height, not pixels).
  - [~] **Vision: no combobox typing** — dropdowns detected as edits (0 comboboxcontrol on a
    tab with 3); per-type fill mechanics pick the wrong path. Arrow-glyph heuristic added
    2026-07-10 (unverified live — arrows were outside the test capture).
  - [x] **Vision: focus inference** *(2026-07-10, 424beea — agent stamps last clicked fillable
    as focus when the observer reports none; first pixel-driven fill live-verified.)*
  - [x] **Vision: label fragmentation** *(2026-07-10, 424beea — whole-line label assembly via
    Tesseract line structure + punctuation cleanup; all 10 Tab-1 labels read whole/exact.)*
  - [x] **Vision: window-chrome phantoms** *(2026-07-10, 424beea — client-area capture.)*
  - [x] **Vision: stable element identity across frames** *(2026-07-10, 819a4a7 — detector ids
    were per-frame detection order; cv0007 named different fields in consecutive frames, so the
    validator compared fills against the wrong element and dead-marked its own successes. Fix:
    frame-to-frame tracker (label bucket + nearest center, 1:1 greedy) carries ids over;
    parser-agnostic. Unit-verified incl. the live order-flip failure; NOT yet live-verified.)*
  - [ ] **Vision REAL FIX: learned screen parser** — the classical detector needs a heuristic
    per widget style and will never generalize. Replace cv_detector's core with a pretrained
    screen-parsing model (OmniParser-class, local ONNX; evaluate ShowUI/UGround) behind the
    same `detect_elements()` seam; Tesseract stays for values; the identity tracker sits on top
    unchanged; acceptance = `perception_eval` beats the classical baseline. Remaining classical
    gaps meanwhile: OCR noise in read-back equality ('PAT' vs 'PAI' → fuzzy compare), label/value
    bleed on filled fields, occlusion detection.
  - [ ] Research grounding stacks: Compare OS-World, ShowUI, Microsoft Computer Use, or custom-trained VLM models for grounding.
  - [ ] Modularize Observer input: Ensure screenshot capturing and OCR fallback are decoupled from the accessibility tree, producing identical canonical element representations.
  - [ ] VLM Prompt Engineering: Construct prompt templates that map coordinate grids to semantic labels for the target application.
  - [ ] Hybrid grounding model: Implement fallback checks where VLM reads values and UIA/coordinates determine bounding boxes.
- [~] **Action Space: Form-Fields → Universal** *(core landed 2026-07-08 — see Current Status)*
  - [x] Semantic verb vocabulary: `components/agent/semantic_action.py` (`Verb` enum + `SemanticAction` dataclass, legacy-dict conversion both ways; executor accepts either).
  - [x] Demo → verb labeler: `components/recorder/action_labeler.py` (control-type + state-diff, no hardcode; per-window element-id collision fixed).
  - [x] Semantic training path: `--action_space semantic` through dataset/train/predict/checkpoint; split pointer heads (click verbs → `click_elem`, FOCUS/SET_VALUE → repurposed `source_elem`); v2 beats baseline (0.957 vs 0.878).
  - [ ] Verb-driven agent loop: agent still consumes legacy dicts from `predict()`; dispatch mechanics by predicted verb and retire the corresponding guards.
  - [ ] Pluggable Executor Refactor: Create abstract base `ActionExecutor` class and migrate pyautogui mappings into concrete sub-actions.
  - [ ] Drag & Drop Action: Code coordinate-to-coordinate click-and-drag logic.
  - [ ] Keyboard Hotkeys: Code window-aware key combos (e.g., Ctrl+S, Ctrl+P).
  - [ ] Double Click & Right Click: Implement standard mouse gesture variants.
  - [ ] File Dialog Handling: Automate path input for Windows native Open/Save dialogs.
  - [ ] Demo Recording Expansion: Update `DemoRecorder` to record drag coordinates, double-clicks, and hotkeys correctly.
- [ ] **Finish scope abstraction** — Declare a scope (goal, config, observer, source, model) in a single place.
- [ ] **Excel full transfer (Scope #2)**
  - [ ] Configure ExcelObserver: Fully test `ExcelObserver` against live Excel sheets to output cell text, formulas, coordinates, and sheet names.
  - [ ] Excel Action implementation: Implement target coordinate mapping for moving active cell selection via clicks or arrow keys.
  - [ ] Excel Trace recording: Record 15-20 demos transferring web form data to an Excel template.
  - [ ] Train Excel BC Model: Train a task-specific network and measure cloning fidelity.
- [ ] **Random-order test** — Retrain on non-standard demonstrated paths to verify order cloning vs memorization.

### 🔵 P2 — Scope #3 (Email/Triage)
- [ ] **Workflow: Linear → Control Flow** (Decision-Making/Judgment Cloning)
  - [ ] Multi-trace branch dataset: Record user responses to varied inputs (e.g., active policies get one response, lapsed policies get a warning email, missing fields prompt an inquiry).
  - [ ] Workflow induction engine: Build a parser that extracts sequence transitions from multiple traces, identifying key conditional branch variables.
  - [ ] Branching spec syntax: Define a schema (JSON or Markdown-based) representing the execution graph.
  - [ ] DAgger loop integration: Fully wire `CorrectionHandler` to pause the agent on invalid branches, record user correction, and retrain in real-time.
- [ ] **Email/ticket triage (Scope #3)**
  - [ ] Email perception hook: Create an observer for a target mail client (e.g., Outlook UIA or web client).
  - [ ] Triage categorization: Define classification tasks for LLM (e.g., policy renewal request, claims update, billing issue).
  - [ ] Train Triage BC Model: Train policy to select appropriate quick-reply templates based on email category.

### 🟡 P3 — Polish & Non-Blocking Gaps
- [ ] **LLM value errors** — Integrate value lookup constraints (e.g. matching policy number patterns) or ask the user when confidence falls below a threshold.
- [ ] **Prompt caching & cost optimization** — Implement prompt caching for Gemini/Groq/Anthropic to reuse system specs and avoid paying for target/source context on every step.
- [ ] **Memory component / State persistence** — Enable context transfer between records (currently runs blank slate).
- [ ] **Execution Observability Dashboard** — Generate local HTML trace logs containing step-by-step screenshots, transformer probabilities, and LLM reasoning steps.
- [ ] **Robust Crash Recovery** — Implement auto-restart for target windows and recovery from frozen combobox dropdowns.
- [ ] **Cross-task shared backbone** — Train a shared model trunk with task-specific headers.
- [ ] **Automate DEVELOPERS.md upkeep** — Add automated hooks/scripts to update progress.

### 🟣 P4 — Academic & Benchmarks
- [ ] **Thesis revision** — Complete Chapter 3 (Methodology) and Chapter 4 (Evaluation Results).
- [ ] **Data collection** — Amass 500,000 trace steps for the thesis dataset.
- [ ] **Benchmarks** — Compare performance on form-filling and ELO cloning against general agents (e.g., OS-World).
- [ ] **Chess fidelity benchmark** — Train the transformer architecture on chess PGN game traces to measure style replication (openings, blunders, ELO matching) as a proxy for pure decision cloning.

### 🟤 P5 — Reinforcement Learning Phase
- [ ] **RL environment setup** — Build a Gym-style wrapper around the wxPython/target application that computes rewards based on field correctness.
- [ ] **KL-Divergence constraint** — Add a KL penalty term to the RL policy update to prevent the agent from deviating from the human's demonstrated interaction style.
- [ ] **StateValidator Reward Function** — Integrate verification metrics from `_verify_pass` as rewards for the RL agent.

## Decisions and Concepts

**WHAT does the transformer learn — Pure (A) vs Division-of-labor (B)? → CHOSE B.**
The 3-tab navigation marathon (2026-06-11) exposed that the transformer's
action-type head (deciding click-vs-type per step) is **unstable** — it whipsaws
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

## DAgger — How to Implement *(if/when pure-BC drift needs it)*
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

## Open Technical Questions
- **How are we different or simply just a worse version of Codex's new Record and Replay?**
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

## Strategic & Thesis Concerns
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

## Risks & Technical Debt
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

Completed work and solved problems, preserved for reference.

### THE GATE: v3 Acceptance Run Passed (2026-07-10 evening)
- **Full uninterrupted acceptance run on v3 SUBMITTED end-to-end** — tab 0 → all tabs →
  verify → autonomous Submit, zero human touches. First time the whole week's stack ran
  to the finish line together: section-qualified identity keys, identity executor, ranked
  arbitration, model-anchored viewport jump, verify convergence gate.
- **Viewport-jump ping-pong killed same day** (the run earlier that evening wedged ~step 180,
  Drivers, two anchors alternating 14 jumps / zero fills). Three-layer fix, offline-probed
  (`scratch/probe_jump_pingpong.py`, 5/5) then confirmed by the passing run:
  density gate on the model-anchor branch; viewport lock (`_jump_anchors_since_progress` —
  no re-jump to any anchor visited since last progress); **far-field reveal** (jump focuses
  the chosen window's far-side field, wx exposes the whole window in one SetFocus —
  ScrollPattern dependency deleted from the jump; it was silently no-oping on deep tabs and
  causing every blind landing).
- **Console log readability** — white log text, green divider between steps (run_task
  `_RunFormatter`).
- LESSON: "jump promised N fields, landed on none" = the reveal mechanic, not the picker —
  wx SetFocus reveals at the NEAR edge, so whoever scrolls must aim at the FAR side of the
  window they want on screen.

### Universal Semantic Action Space + Navigation Protocol v2 (2026-07-07 → 07-09)
- **Semantic Action Space core.** Verb vocabulary + `SemanticAction` dataclass with legacy
  interop (`semantic_action.py`); executor accepts both shapes, verified byte-identical
  results. Offline labeler (`action_labeler.py`) validated on 6,950 traces; found+fixed
  element-id-per-window collision (form + Notepad both emit `elem_8` — diff keys on
  `(element_id, window_role)`). Renamed from `trace_translator.py` (name collision with the
  unrelated HTML/CV detector package). Tests: `test_semantic_action.py`,
  `test_action_labeler.py`, executor-equivalence suite.
- **Split-pointer-head training** (`--action_space semantic`): v1 merged both WHERE jobs
  onto one head → click_acc 0.828 (WORSE than 0.878 baseline); v2 split (click verbs →
  `click_elem`, FOCUS/SET_VALUE → repurposed `source_elem`, `lambda_src`=`lambda_click`,
  new `src_acc` metric) → **click_acc 0.957, src_acc 0.856**. LESSON: one pointer head per
  job — mixing where-to-click with where-to-type measurably hurts both.
- **Ranked-target arbitration.** `predict()` exposes pointer-head top-k (`click_topk`);
  `_pick_ranked_target` walks the model's OWN ranking, skipping dead/attempted/filled/
  blacklisted/background/decorative targets, visible-first. Fixation on an already-correct
  field (the 07-07 Claims 37-step stall) is impossible by construction — "already correct"
  auto-marks filled and moves on. no_change clicks blacklist both the clicked point and the
  containing element's center (snap-drift-proof).
- **Optimal-viewport jump.** When zero visible targets remain: densest window over all
  remaining empty fields (two-pointer sweep over virtual y-coords), scroll its top field to
  the pane top. Replaced the one-field-per-scroll crawl (wx SetFocus auto-scroll reveals at
  the NEAR edge), the M2 fold-trigger, and the stranding guard (which let one skipped field
  veto every scroll). Live-verified 2026-07-09.
- **Fixation escalation.** `_fixation_hits`: 2nd fixation on the same spot overrides NAV
  "fill"→"tab"; deterministic geometry fallback clicks the first unvisited tab; on the LAST
  tab hands the page to `_sweep_tab` (was: silent no-op → the 07-08 Payment infinite loop).
- **Viewport-top fix.** `_form_viewport_top` (pane starts below the tab strip, not y=0);
  fixed three `y >= 0` visibility checks that let scrolled-off fields pass as "visible" with
  stale bboxes → tab-strip mis-clicks; `_nav_fill_field` refuses stale-coord clicks (counts
  as fill-failure → dead-mark after 2). Also fixed `_reveal_target`'s label re-find clicking
  the static text label instead of the edit control (no type filter).
- **Makefile** — `make help/record/train/train-semantic/run/run-semantic/form/test`.

### Core Loop & Navigation Cloning Milestone (2026-06-18)
- **END-TO-END Scope #1 (2026-06-18).** One unattended run fills all 8 tabs, runs a self-verification pass over every tab, and Submits (~124 steps) — no premature submit, no infinite loop, no crash. The Navigation Protocol (*fill → feed transformer via scroll → page-done? → next tab → all tabs → verify → submit*) is the governing loop. Key mechanisms: M2 expose-scroll feeds the transformer, real UIA ScrollPattern scroll, explicit loop/cycle detector, deterministic verify (UIA value + checkbox TogglePattern, section-aware), submit chokepoint (idempotent, gated on a clean verify). Committed `nav-protocol-scroll-submit` 3b24421.
- **Order cloning proven.** Top-down (74% exact) vs bottom-up (93% exact) on each order's own data. Same architecture, opposite orders, each learned its own.
- **Learned the finish.** Clicks Submit on its own via tail-oversampling — no hardcoded end-game rule.
- **`is_filled` perception feature.** Model can see which fields are done -> stopped the end-game looping.
- **Action-space collapse to {click, type}** + wired into agent (action-type accuracy 50% → 80%, click accuracy up for free).
- **Recorder combobox fix.** Clicks while a dropdown is open are value-selections (land on the field under the dropdown) -> dropped at record time to avoid phantom order pollution.
- **Recorder focus fix.** Clicks always get a fresh snapshot (was reusing stale state during bursts).
- **Added utility scripts:** `clean_demos.py`, `test_clone.py`, `oversample_tails.py`, `replicate.py`.

### Intelligence & Training
- **GPU training.** CUDA 12.4 / PyTorch 2.6.0+cu124, RTX 4050.
- **Best-acc checkpoint.** Saves on `val_acc + click_acc`, not val_loss.
- **Dataset init cache.** `.dataset_cache.pkl`, retrain init ~1 sec.
- **LayerNorm on pointer heads.** Fixed bilinear Q×K divergence (197M loss bug).
- **Data augmentation.** `augment_traces.py` (bbox/click/confidence jitter).
- **Reorganized tasks/ directory.** model.pt, ruleset.md under `tasks/form_filling/`.

### Agent, Merge Logic, & Generalization Foundation
- **Form-specific hardcodes killed -> injected ScopeConfig.** Application-blind agent: `_detect_section`, `_KNOWN_TABS`, `_TAB_PANE_NAMES`, and the `RECORD N OF M` delimiter moved to per-scope config.
- **Perception adapter seam.** Injectable observers (UIA/Excel), canonical element schema (`observers/schema.py`), and loud validation. Excel perception swap proven.
- **WHERE/WHAT division.** Transformer drives element + click/type; LLM supplies the value.
- **Crutch-gating in pure mode.** Gated off `VISITED-ADVANCE` + `LLM-takeover-when-weak` under `disable_auto_handlers`.
- **Fix LLM click position.** `_merge` resolves LLM target by label; transformer click is fallback.

### Ruleset & Spec System
- **Correctional ruleset.** `RuleExtractor.correct()` + auto-call on record end.
- **Spec injection.** `ruleset.md` appended to LLM system prompt.

### Evaluation & Metrics
- **Per-run metrics.** `eval_metrics.evaluate_run` (TCR, action/value accuracy).
- **BC fidelity scorer.** `bc_fidelity.py` vs gold standard, trend in `bc_progress.jsonl`.

### Infrastructure
- **Capsule registry.** Per-task model routing.
- **`.gitignore`.** `data/demos/`, traces, caches, model binaries excluded.
