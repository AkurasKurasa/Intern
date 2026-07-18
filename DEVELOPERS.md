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

**MILESTONE (2026-07-11): MULTI-RECORD WORKS + cross-record contamination chain closed.**
×2 run: both records SUBMITTED (PAI-2026-00441 + 00442), record 2 filled with record-2 data,
form reset clean, per-record scorecards archived (record-2 mechanics best ever: 3.7% waste,
1.9 steps/field, 96.3% exec success). The probes then found and killed FOUR value-leak paths
one at a time (`--start_record` isolation probes, ~3 min each): blob-parse min-fallbacks (5
sites), unbounded line-search peeks (4 sites, `_record_line_span`), LLM invention on absent
fields (deterministic absent=skip, no LLM call), merge resolver rescue (`skip_field` final),
sweep literal-typing (record-is-source-of-truth guard). Verified live: absent fields SKIP,
zero cross-record values, LLM dependency on a blank tab fell to 16.7%.

**Honest gaps remaining (2026-07-11 evening):**
- **Both scorers record-blind** — run scorer (eval_metrics) + bc_fidelity grade every run
  against RECORD 1's answers; record-N runs get fiction scores (correct fills marked ✗,
  contamination marked ✓). Fix before ×10 — its ten scorecards mean nothing otherwise.
- **Sweep guard live-unwatched** (same lookup logic as the proven skips; exercised next full run).
- **Annotated blanks** ('(N/A — no collision coverage)') typed literally from the record —
  ruleset-inference case, filed; extractor currently learns the BACKWARDS rule from raw traces.
- **Skipped fields re-picked** by the model until STUCK fires — cheap since no LLM call, but loopy.
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

**MILESTONE (2026-07-13): FULL ACCEPTANCE RUN PASSED on the single-fill-pipeline
architecture.** One record, all 8 tabs, verify pass, deterministic Submit, 90 steps, zero
fill-pipeline drama (no dropdown escapes, no rescue loops beyond the known self-resolving
spin hiccup). This closes out most of the 2026-07-11 gap list above — updated honestly:
- **Both scorers record-blind** → FIXED (bc-record-gold, eval-record-gold, bc-gold-coverage,
  all 2026-07-11/12). BC gold now covers the whole form (163 fields, not 75).
- **Hard widgets dead-mark instead of filling** → FIXED — single fill pipeline
  (`_fill_element`) proven on a full run; SpinCtrls/fold-edge combos fill via UIA patterns.
- **Transformer↔LLM balance** → LLM fill-decisions now **2.0%** (was 45-50%) — the
  deterministic value short-circuit, confirmed structural on a full run, not just a drill.
  Verb-driven agent loop remains open as the next structural step (see verb-loop node).
- **Filling sequence fidelity / Behavioral Match** → reference wiring (2026-07-12) shipped
  with a bug that made it a no-op: the dead dir (`data/output/traces/forms`) EXISTS (empty,
  0 sessions), so a plain `.exists()` check picked it first and silently shadowed the real
  corpus fallback — the 2026-07-13 acceptance run still printed `No reference sequence
  available`. FIXED same day: check for a directory that actually contains `session_*`
  subfolders, not just existence. Offline-verified AND live-confirmed (2026-07-13 rerun):
  Behavioral Match printed **13.5%** — a real number, not `No reference sequence available`.
- **NEW bug this run caught**: Driver 2/3 fields overwritten with Driver 1's values within a
  single submission (not the multi-record contamination class) — see Task List entry.
- **Still open**: viewport-choice geometry heuristic; spin-value observer-visibility fix
  landed (2026-07-13, OPT2 focused-field path now masks against `_filled_this_tab`) but
  NOT yet live-retested; scale (×10 not yet run on this architecture).

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

**LIVE-CONFIRMED 2026-07-16** (`run_20260716_163430.txt`, first uninterrupted run since
today's fixes — sweep-verify-trusts-write-path, checkbox-verb unification, apostrophe
match, numeric-substring guard): **zero verify-at-fill retries anywhere in the run** —
the sweep-re-verify bug is fixed, no more false "didn't stick" flags. Value Accuracy
100% (59/59 typed values correct). Tab Coverage 100% (all 11 sections including
Discounts — `bc-fidelity-discounts-tab-detection` also resolved on this run, though
`scripts/bc_fidelity.py` itself has no relevant diff, so this is one confirming
observation, not a proven scorer fix — reopen if it regresses). Checkbox verb
unification held correctly on every checkbox (record-NO left unchecked, record-YES
checked, zero force-check errors). Zero errors/tracebacks, natural termination via
deterministic Submit, 8.5 min duration. **Remaining gap, separate from today's
fixes**: Field Match Rate 65.6% (107/163) — fields left blank that should have a
value; not a correctness regression (accuracy of what WAS filled is 100%), a
coverage gap (fields never reached).

---

## Task List and Priority List

**Visual mirror: [`treetask/`](treetask/index.html) (`make tree`; deployed on Cloudflare Pages 2026-07-12)** — an interactive 3D
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
*Definition of Done: Agent fills all 10 records, all 8 tabs (including Driver/Vehicle sub-sections) in demonstrated order, submits each, with no human help, high field-match, and minimal waste.*

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
- [x] **Identity-based executor → SINGLE FILL PIPELINE [FULL ACCEPTANCE PASSED 2026-07-13]** *(2026-07-09 f88d4fc built `_resolve_live_control`+`_act_on_element`; 2026-07-11 INVERSION landed, user-driven — the three bolted-on combobox rescues were a band-aid: new `_fill_element` = THE pipeline (reliable mechanics first, read-back verified; legacy pixel/paste last). All three combobox paths route through it, rescue copies + per-site dropdown dances DELETED; `_nav_fill_field` already identity-first. OPT2 edit typing keeps paste→keystroke→pattern ladder deliberately (paste = correct cheap tier for plain edits). Drill passed 2026-07-11 22:44. **FULL ACCEPTANCE RUN 2026-07-13 (record 1, single-record, all 8 tabs, verify pass, deterministic Submit, 90 steps): zero fill-pipeline drama** — no dropdown escapes, no rescue loops beyond the known self-resolving spin hiccup. LLM fill-decisions 2.0% (was 45-55%) — the deterministic short-circuit confirmed structural on a full run, not just a drill. Value accuracy 100% (run scorer) / 96.2% (163-field BC gold). REMAINING (split out, non-blocking): verify-fix read-back unification, retire snap/stale-coord guards, and a freshly-found bug below.)*
- [x] **Driver 2/3 fields overwritten with Driver 1's values [CLOSED 2026-07-14, LIVE-CONFIRMED]** —
  NOT the multi-record contamination bug (this is within ONE submission, across sections).
  FIRST INSTANCE (verify-pass path) FIXED 2026-07-13 morning: `_view_mismatches` walked
  EVERY element UIA reports incl. OFF-SCREEN ones with stale bbox → `_detect_section`
  mis-picked the section → bare-key fallback grabbed Driver 1's value. FIX: restrict
  `_view_mismatches` to on-screen elements. LIVE-CONFIRMED clean on that rerun.
  **REGRESSED SAME DAY, different call site**: a later `--start_record 1 --records 1` run
  scored `d2_dl_exp: expected '03/22/2027', got '07/14/2028'` (Driver 1's DL Expiration value)
  — BC top-mismatches, not the run's own verify pass (verify reported 0 corrections, so this
  field was wrong at FILL time and verify never caught it as on-screen-visible-and-wrong, or
  visited it before the value existed). ROOT CAUSE (confirmed by reading `_auto_fill`,
  agent.py:3969-3995): it calls `self._detect_section(state, focused)` (line 3992) using
  WHATEVER `state` it was last handed — the SAME stale-off-screen-bbox vulnerability the
  morning fix patched, but only inside `_view_mismatches`'s call site. `_detect_section`
  itself (line 3761) was never hardened, and every OTHER call site (`_auto_fill`,
  dead-widget rescue at 2875, ranked-picker at 7114, OPT2 combobox paths) still hands it
  whatever `state`/pane list it happens to have, with no guarantee the section panes it's
  geometry-testing against are freshly observed. **GAP: the fix was scoped to one caller,
  not the shared vulnerable function.** DO: either (a) have `_detect_section` refuse to
  trust a pane whose bbox wasn't refreshed this step (needs a "last observed" timestamp/tag
  per element, not currently tracked), or (b) force a fresh `_observe()` immediately before
  every `_detect_section` call site that fires off a possibly-stale `state`. Confirmed NOT
  a scorer bug — `_LABEL_TO_KEY`/`_DRIVER_LABEL_TO_SUFFIX` correctly built `d2_dl_exp` as the
  gold key; the SUBMITTED value itself was wrong, i.e. the live agent, not bc_fidelity.
  **[UPDATE 2026-07-13 — CONFIRMED + FIXED]**: a further `--records 1` rerun reproduced the
  EXACT same mismatch (`d2_dl_exp: expected '03/22/2027', got '07/14/2028'`) on a run that
  terminated NATURALLY at 100% completion — proving it wasn't a fluke of the metrics-mislabel
  bug above. Traced via the run's own log: `_detect_section` returned `""` for `Driver 2 DL
  Expiration` specifically (the LAST field in the Driver 2 block, right before `[ Driver 3 ]`
  starts) while sibling fields moments earlier (`Driver 2 First Name`/`DOB`/`DL Number`) all
  correctly detected `'Driver 2'` at the same call site. NOT a stale-bbox case this time —
  the Driver 2 section-pane element was simply ABSENT from `state`'s elements at that exact
  scroll position (scrolled off the top), so `section_panes` came back empty. FIX
  (agent.py:3782-3810, `_detect_section`): added a sticky-section fallback —
  `self._last_nonempty_section` updates every time a section IS resolved, and is used as the
  fallback when no pane is found ABOVE the field BUT section panes exist SOMEWHERE in the
  view (distinguishing "sectioned tab, header scrolled past" from "genuinely unsectioned
  tab" — the latter has zero section panes anywhere, resetting sticky to `""` so it can't
  leak across tabs). Generic: keys only on presence/absence of `section_*`-prefixed panes,
  no tab/field names. Syntax-checked; NOT yet live-retested — needs a fresh run confirming
  `d2_dl_exp` resolves correctly.
- [x] **Spin value invisible to the observer after a successful fill [FIXED 2026-07-13]** *(found 22:49 acceptance run)* — 'Years at Address': the identity-executor write LANDS (rescue read-back verifies), but the OBSERVED element's value stays '' → model re-targets the filled field (~15 wasted steps) → wrongly dead-marked. ROOT CAUSE (narrowed further this session): NOT the ranked-picker (`_pick_ranked_target` already section-masks against `_filled_this_tab` correctly, lines ~7092-7098) — the actual re-trigger site is the separate OPT2 "focused empty field" direct-fill path (agent.py ~1950-1977), which decides fill-vs-navigate purely from the CURRENTLY FOCUSED element's observed type+value (`_fe2_val`) and never consulted `_filled_this_tab` at all. FIX: added a guard immediately before that decision — build the same section-qualified key (`_detect_section` + label, matching the exact convention the dead-widget rescue itself uses at line 2874-2876) and skip the auto-fill branch if that key is already in `_filled_this_tab`, trusting the rescue write over a possibly-stale observed value. Syntax-checked (`ast.parse`). **PARTIALLY LIVE-RETESTED 2026-07-13**: `--start_record 1 --records 1` rerun did NOT terminate cleanly (max_steps, 27.3% completion, 128 steps) with long `no_change` streaks (steps ~106-141) — consistent with a stall, though not confirmed to be THIS bug specifically vs. the two other bugs found in the same run (driver-section-crosstalk regression, DOB zero-pad drop below). Needs a clean full-length rerun with `[OPT2]`-tagged debug logging captured to confirm the guard actually fires and that the stall isn't a re-emergence of the same class.
- [x] **DOB submitted without leading zero — 'd3_dob' [ROOT-CAUSED 2026-07-14, NOT A CODE BUG]** — expected `09/05/2006`, got `9/05/2006`. Traced `_parse_records` (notepad_source.py:104-153) line by line: plain string partition/strip, zero numeric coercion anywhere, ruled out as the cause. Then read the LIVE Notepad window's actual text buffer directly (`NotepadDataSource().read_full_text()`, no live agent/form run needed) and found the smoking gun: the window title is `*data_entry_intake.txt - Notepad` (asterisk = unsaved changes), and the live buffer's line 207 literally reads `Date of Birth        : 9/05/2006` — missing the zero IN THE OPEN DOCUMENT ITSELF, while the file on disk (confirmed via a separate `Read`) still has `09/05/2006` correctly. ROOT CAUSE: a stray keystroke landed in that exact spot at some point across the ~15+ live runs today that all reused the same never-reopened Notepad window, silently dropping one character, never saved. Every code-side fix attempt (record-cache tracing, LLM-reformat theory, keystroke-drop-on-paste theory) was chasing a phantom — the agent was faithfully typing exactly what the live document said. FIX: not a code change — a targeted single-character insert directly into the live Edit control (`win32api`/`ctypes` `EM_SETSEL` + `EM_REPLACESEL` at the exact computed character offset, found via an exact-match search on `read_full_text()`'s own output to guarantee uniqueness before touching anything). Verified surgical: exactly +1 character (82736→82737), same line count (2665) before/after — nothing else in the unsaved buffer was disturbed. **LIVE-CONFIRMED end-to-end 2026-07-14**: a subsequent Drivers-tab drill's log shows `Deterministic value: 'Date of Birth' → '09/05/2006' from record 1` and the run's own value-accuracy printout scored it `✓ typed='09/05/2006' expected='09/05/2006'` — exact match, zero-pad bug fully closed.
- [x] **RUN METRICS mislabels a genuine Submit as 'early/max_steps' [FIXED 2026-07-13]** — the same rerun's BC_SCORE block created a real submission JSON and ran RuleExtractor (both post-submit-only steps), i.e. the run DID reach Submit — but RUN METRICS printed `Terminated: early/max_steps` and Task Completion Rate 27.3%. ROOT CAUSE: `evaluate_run` (eval_metrics.py:448) computed `done = any(action_type == "done" ...)` — but Submit fires via a CLICK on the Submit button (`_SUBMIT_KW` match in agent.py), never an `action_type: "done"` sentinel, so the scan never sees it; `run_task.py` also never passed `agent._submitted` into `evaluate_run` at all. FIX: added `submitted` param to `evaluate_run`, `done = bool(submitted) or any(...)`, `run_task.py` now passes `submitted=getattr(agent, "_submitted", False)`. `tcr`/Task Completion Rate already keyed off `done`, so this one fix corrects both the mislabel and the wrong completion percentage. Syntax-checked; not yet live-retested.
- [x] **run_task.py now ALWAYS writes a full plain-text log to disk [BUILT 2026-07-13]** — user had to manually redirect stdout (`> log.txt 2>&1`) to hand Claude a non-truncated run log; easy to forget, so it's now automatic. `run_task.py:27-74`: a `_Tee` class wraps `sys.stdout`/`sys.stderr` right after the UTF-8 `reconfigure()` call — mirrors every write to the console (ANSI intact) AND to `data/output/run_logs/run_<timestamp>.txt` (ANSI-stripped via regex, so the file stays plain/greppable). Catches BOTH the `logging` module's output (its StreamHandler already points at the now-Tee'd stdout) AND raw `print()` calls (RUN METRICS, BC SCORE use `print()`, which a bare `logging.FileHandler` would've missed — considered and rejected that approach for this reason). Logic unit-verified standalone (ANSI stripped correctly, console output unaffected). **LIVE-CONFIRMED 2026-07-13**: user ran two subsequent full acceptance runs unassisted; both auto-wrote to `data/output/run_logs/run_<timestamp>.txt` with no manual redirection, and Claude read both in full via the `Read` tool with zero truncation.
- [x] **Driver-section-crosstalk regression [ROOT-CAUSED + FIXED 2026-07-13, LIVE-CONFIRMED]** — sticky-section fallback (`_detect_section`, see entry above) fixed `d2_dl_exp`: confirmed gone from BC top-mismatches on the very next rerun (`--start_record 1 --records 1`, naturally terminated, 100% completion). **A DIFFERENT instance of the same family immediately surfaced in that same rerun**: `d2_violations: expected '0', got '1'` (Driver 2's own correct '0' got overwritten with Driver 3's '1'). ROOT CAUSE (traced via log, confirmed by reading the code, not just inferred): `_verify_pass` (agent.py:5648-5667 and 5719-5754) resolves the SPECIFIC mismatched element via `_view_mismatches` (which IS section-aware) but then discards that resolved element and calls `self._nav_fill_field(_st, _el, _exp)` with ONLY the bare label string, no `prefer_key`. `_nav_fill_field`'s own internal `_find()` (line 6663) then re-resolves from scratch: its step-1 fallback ("exact label match, prefer empty") silently picks the FIRST candidate by list order once BOTH same-labeled twins (Driver 2's and Driver 3's comboboxes share the bare label 'Violations (3 yr)' — these widgets never carry a driver-number in their own UIA label, same as the DL Expiration case) are non-empty — landing Driver 3's correct fix-value onto Driver 2's widget instead. FIX: both verify-pass fill call sites now pass `prefer_key=` — the deterministic branch passes the `_vk` attempt-key it already computes for the termination-guarantee counter (was sitting right there, just not threaded through); the LLM-fallback branch computes one fresh via `self._attempt_key(_fe, _st)`. Syntax-checked. **OFFLINE-VERIFIED** (`scratch/probe_verify_wrong_twin.py`, copies the exact `_find()`/`_attempt_key` algorithm — no live app needed): reproduces the bug with `prefer_key=None` (resolves to Driver 2's widget, the wrong one) and confirms the fix with `prefer_key` set (resolves to Driver 3's, correct). **[UPDATE 2026-07-13 — the prefer_key fix WAS live-correct, but a full uninterrupted rerun still scored `d2_violations: expected '0', got '1'`]**: full rerun (naturally terminated, 100% completion) traced to `[VERIFY] Drivers: fix 'Violations (3 yr)' → '1'` firing exactly ONCE — one widget got the RIGHT fix-target (prefer_key worked) but the EXPECTED VALUE computed for it was wrong. ROOT CAUSE: a REGRESSION in the sticky-section fallback ITSELF (`_detect_section`, the very fix from the entry above) — v1 used a single scalar `self._last_nonempty_section` shared across ALL fields in a scan. `_view_mismatches` iterates `state.get("elements", [])` in whatever order UIA/the observer returns them — NOT guaranteed top-to-bottom — so if Driver 3's own Violations combobox got processed before Driver 2's straggler field within the same scan, the scalar got overwritten to `'Driver 3'` first and then wrongly answered for Driver 2's field too when its own pane had scrolled off. FIX (v2, agent.py:3761-3823): replaced the scalar with `self._section_pane_tops: Dict[str, float]` — remembers EACH pane's own top-Y by label, order-independent; a field is judged against the pane it actually belongs to (by remembered geometry), never against "whichever pane was seen most recently". Reset alongside the existing `_filled_this_tab.clear()` at all 11 tab-switch call sites (`sed`-applied) so it can't leak across tabs. OFFLINE-VERIFIED (`scratch/probe_section_order_independence.py`): reproduces both processing orders (Driver 2-then-3-then-Driver-2-again, and cold-start Driver-3-first) and confirms v2 resolves correctly in both — the exact scenario that broke v1. **[UPDATE 2026-07-13 — a THIRD instance surfaced on the very next live rerun]**: `d2_violations` was gone (v2 fix confirmed working), but `d3_first`/`d3_dob`/`d3_relation` all came back wrong — `expected 'Tyler' got 'James'`, `expected '09/05/2006' got '07/14/1978'` (both Policyholder's own bare-keyed values), `expected 'Child' got 'Spouse'` (Driver 2's value via fuzzy match). Traced to `[VERIFY] Drivers: fix 'First Name'/'Date of Birth'/'Relationship'` firing on the FIRST verify scan of the Drivers tab, before ANY Driver section pane had been observed/remembered yet this tab-visit — `_detect_section`'s v2 returned `''` (its "no sections found" answer) from a position of ZERO information, and `_lookup_field`'s bare/fuzzy fallback confidently answered with cross-section data. ROOT CAUSE: v2 conflated two different situations under a single `''` return — "confirmed above all known section panes" (safe to bare-lookup) and "no section pane has EVER been seen this tab-visit" (unknown, NOT safe to bare-lookup) both returned `''`. FIX (agent.py:3761-3830): `_detect_section` now returns `None` (falsy, so every OTHER caller's `if section:`/`f"... if section else ..."` guard treats it identically to `''`, zero behavior change there) specifically for the "zero panes remembered yet" case, keeping `''` only for the confirmed case. `_view_mismatches` (agent.py:3960) now checks `is None` explicitly and SKIPS (defers to a later, better-informed scan) rather than guessing — same "defer, don't guess" principle as the viewport-only fix. Verified none of the ~20 other `_detect_section` call sites do unguarded string ops on the result (all use `if section`/`if sec else` patterns, confirmed via grep). Syntax-checked. **[UPDATE 2026-07-14 — the None/defer fix did NOT stop it either]**: next uninterrupted rerun still scored `d3_first`/`d3_dob`/`d3_relation` wrong, byte-identical to the v3 failure. Traced: `_view_mismatches` DID fire the fix (no HTTP call preceded it, confirming the deterministic branch, not the LLM-fallback one) and still produced the wrong expected value — meaning `_section_pane_tops` was NON-empty at that moment (so the `is None` defer never triggered) but held STALE remembered top-Y coordinates from an EARLIER scroll position of the SAME tab (main-pass visit vs. this later verify-pass visit) that no longer corresponded to where these fields actually sit on THIS visit — a 4th distinct way the same geometry-based design breaks. **DECISION (2026-07-14): stopped patching geometry edge cases and rewrote `_detect_section` structurally instead** — the observer (`components/observers/ui_observer/ui_observer.py`, `_walk`) now tags every element with `ancestor_panes`: the labels of its ACTUAL UIA tree ancestors (via `ctrl.GetChildren()` recursion, threading a running list of ancestor pane names down through the walk), a fact about the accessibility TREE, not the screen. `_detect_section` (agent.py:3761-3806, full rewrite, ~95 lines → ~30) now just walks `focused_el["ancestor_panes"]` backward for the nearest one matching the section prefix — no bbox, no scroll position, no remembered state, no ordering dependency, no "have I looked yet" ambiguity, because none of those concepts apply to tree ancestry. All the v1-v4 machinery (`_last_nonempty_section`, `_section_pane_tops`, the `None`-vs-`''` distinction, the 11 tab-switch reset call sites' `_section_pane_tops = {}` additions — now dead but harmless) is superseded; `_view_mismatches`'s now-unreachable `is None` guard was removed. Deliberately did NOT touch the geometry-based `_section_pane_of`/`_attempt_key` (line 7029) — its docstring requires it to mirror `transformer._section_of`'s training-time computation exactly for train/inference parity on the `attempted` ML feature; changing it would silently desync the model from what it was trained on. OFFLINE-VERIFIED (`scratch/probe_structural_section_detect.py`): 5 cases covering every failure mode from all four prior versions (stale-bbox-equivalent straggler field, cold-start first-field-on-tab, order independence, unsectioned field) all resolve correctly, by construction rather than by patched-in special-casing. Syntax-checked across both files. **[UPDATE 2026-07-14 — the structural rewrite's assumption was WRONG for this form, reverted]**: live rerun showed `d3_first`/`d3_dob`/`d3_relation` FIXED, but a WORSE regression appeared — ALL FIVE of Driver 2's fields (`d2_first`, `d2_dob`, `d2_gender`, `d2_dl`, `d2_dl_exp`) came back as Driver 1's (Policyholder's) values. Root cause of the regression: read `car_insurance_form_wx.py`'s actual widget construction (`_section()` line 198, `_row()` line 217) — the colored section-header banner and every field in that section are ALL direct siblings under the SAME tab-page panel; `_form_grid` returns a layout SIZER, not a container widget. There is NO parent-child containment relationship between a section pane and its fields on this form at all — the structural assumption the rewrite was built on simply doesn't hold here, so `ancestor_panes` never contained a section pane for ANY field, and `_detect_section` returned `''` (unsectioned) for everything, routing every Driver 2 lookup through the bare/fuzzy fallback onto Policyholder's data. **REVERTED** `_detect_section` back to the geometry-based v2+v3 design (remembered per-pane top-Y dict + the `None`-vs-`''` unknown/confirmed distinction) — that combination was never actually wrong on its own merits, just missing one reset site. **FOUND THE REAL GAP** while reverting: `_verify_pass`'s own tab-switch loop (agent.py, in the `for _t in _tabs:` loop) manages tab-clicking independently from the rest of the agent and was NEVER among the 11 `_filled_this_tab.clear()` sites that got `_section_pane_tops = {}` added — a 12th call site, missed because it doesn't share that clear() call at all. This exactly explains the v3/v4 failures: the MAIN PASS visits Drivers tab and remembers pane top-Y at ITS scroll position; verify_pass later re-clicks the SAME tab (different scroll position, e.g. reset to top) and, without a reset, judges fields against STALE coordinates from the main pass's visit. FIX: added `self._section_pane_tops = {}` at the top of verify_pass's tab-switch loop, right after `self._visited_tabs.add(_nm)`. The observer's `ancestor_panes` tagging (ui_observer.py `_walk`) is left in place as harmless, unused, truthful infrastructure for a future scope where sections really are nested containers — just not this form. Left `_view_mismatches`'s `is None` defer-guard restored (still correct and needed). OFFLINE-VERIFIED (`scratch/probe_verify_revisit_stale.py`): reproduces the exact stale-cross-visit failure (resolves to `''`/wrong using scroll-position-A coordinates at scroll-position-B) and confirms the reset fixes it (fresh memory at the new position resolves correctly). Syntax-checked. **LIVE-CONFIRMED 2026-07-14**: next uninterrupted rerun (100% completion, naturally terminated) produced exactly ONE BC top-mismatch — `d3_dob: expected '09/05/2006', got '9/05/2006'`, the ALREADY-KNOWN zero-pad bug (unrelated to section detection — see the dedicated DOB entry). Zero crosstalk of any kind across Driver 1/2/3, zero wrong-section fills. Five rounds on this bug family (off-screen stale bbox → order-dependent sticky → cross-tab-revisit stale coordinates → unknown-vs-confirmed ambiguity → structural-tree assumption that didn't hold → the actual missing 12th reset site) — this is the one that closed it.
- [x] **`bc_fidelity.score_run` silently re-grades a STALE submission [FOUND + FIXED 2026-07-13]** — a `--start_tab 4` drill was interrupted by the user (intentionally, Ctrl+C) at step 11, before Submit. `score_run` still printed a full BC SCORE with `d2_violations`/`d3_dob` mismatches — but those were byte-identical to the PREVIOUS run's mismatches, because `score_run` just grabs the newest file in `SUBMISSIONS_DIR` with no check on whether THIS run actually produced one; an interrupted run silently re-grades leftover data from an earlier run with no indication it's stale. Confirmed by comparing timestamps: the "latest" submission (`_173604_`) predated the drill's own start (`_174831`/`_175054`). FIX: `score_run` (scripts/bc_fidelity.py) gained a `run_start_ts` param; `run_task.py` now captures `_rec_start_ts = time.time()` at the top of each record's loop iteration and passes it through. If the newest submission on disk predates `run_start_ts`, `score_run` refuses to grade it and prints `[BC Fidelity] STALE — ...` instead of a misleading score. Syntax-checked; not yet live-retested (needs an interrupted run to confirm the STALE message fires, and a completed run to confirm it still scores normally).
- [x] **Re-clean corpus + retrain semantic model** *(2026-07-09 — `eight_Tabs_clean2`, v3: click_acc 0.945, src_acc ~0.85, val_acc 0.758, section-aware `attempted`.)*
- [x] **Model-anchored viewport jump** *(2026-07-09, f88d4fc — anchor = model's top off-screen `click_topk` candidate, density fallback; unit-verified all three cases.)*
- [x] **Viewport-jump ping-pong (lock until progress + far-field reveal)** *(FOUND 2026-07-10 acceptance attempt, step ~180 Drivers: two anchors — 'DL Issuing State' ↔ 'Accidents (3 yr)' — alternated 14 jumps, zero fills, run wedged. THREE holes, fixed in two passes same day: (1) model-anchor branch skipped the "already densest" gate the fallback has — jumped to a 1-empty window with 2 empties visible → density gate added; (2) loop-breaker was single-slot (`_last_jump_anchor`) — caught A→A, blind to A→B→A → viewport lock `_jump_anchors_since_progress` set (no re-jump to ANY anchor visited since last progress; clears when the ranked picker finds work); (3) ROOT of the blind landings: wx SetFocus reveals the anchor at the NEAR edge and `_maximize_reveal`'s ScrollPattern paging no-ops on deep tabs (the known P0 scroll bug) → promised window never comes on screen → "all candidates masked" → re-jump, lock burning REAL fields as collateral. FIX: far-field reveal — jump focuses the window's far-side field (down → bottom-most empty; up → the anchor), wx exposes the whole window in ONE SetFocus, ScrollPattern dependency removed from the jump entirely. Offline probe `scratch/probe_jump_pingpong.py` passes all 5 cases; CONFIRMED by the passing acceptance run same evening.)*
- [x] **FULL ACCEPTANCE RUN on v3 [PASSED 2026-07-10 evening]** — one uninterrupted run, tab 0 → all tabs → verify → autonomous Submit, zero touches, whole week's stack live together. The end-to-end claim on v3 is closed. *(Caveat: metrics block not archived — capture the scorecard next run. NEXT gate = Multi-record ×10.)*
- [x] **Multi-record ×10 run — 9/10 completed clean, ACCEPTED as DoD [2026-07-14]** — `--records 10` run (`run_20260714_121233.txt`): records 1-9 ALL completed with 100% task completion, natural termination, and **zero crosstalk mismatches on every single one** (`Top mismatches:` empty for all 9) — the strongest validation yet of the whole 5-round driver-section-crosstalk fix chain from earlier the same day, holding across 9 different records' worth of Driver 2/3 data, not just record 1. Record 10 was interrupted by the user at step 0 (a ~4-hour gap between record 9 finishing at 14:43 and record 10 starting at 18:50, then immediate Ctrl+C) — the stale-submission-scoring guard (see entry above) correctly fired `[BC Fidelity] STALE` and refused to fake-grade it instead of silently re-scoring record 9's leftover submission. **USER CALL: 9/10 accepted as sufficient proof** — not a literal 10/10, but the crosstalk-correctness claim this run existed to validate is fully demonstrated across 9 independent records. Field Match Rate ranged 58.5%-76.2% across the 9 records (average ~70%) — this is a SEPARATE, already-tracked gap (`bc-gold-coverage`'s residual "Other" bucket, and genuinely un-mapped/ambiguous fields), not a regression; crosstalk correctness (the thing that was broken) is fully clean. **SPEED FLAGGED BY USER**: each record took 14-21 minutes wall-clock (record 1: 16 min, record 9: 21 min) despite only 79-142 steps — traced to 59-121 LLM HTTP calls PER RECORD (sweep/verify's navigation-decision calls, several seconds each), not the fill-decision path (already ~2-3% LLM via the deterministic short-circuit). This is exactly what `verb-loop`'s LLM-45% attack-plan buckets 2 (deterministic next-tab) and 3 (verbs straight to the identity executor, retiring the sweep/verify LLM-navigation dependency) are meant to fix — confirms priority, not a new problem.
- [x] **LLM-45% bucket 2: sweep's own navigation call made deterministic [BUILT 2026-07-14]** — `_navigation_protocol` (the sweep's LLM-driven navigation decision, agent.py:5549) is what fires 59-121 times/record per the ×10 run above. Read its own prompt: it already builds the empty-field list DETERMINISTICALLY and literally instructs the LLM to "fill the FIRST one" with "the correct source value" — a decision resolvable via the exact same section-aware `_lookup_field` machinery bucket-1's OPT2 short-circuit already uses, just never applied to this call site. FIX: two short-circuits inserted before any prompt-building or HTTP call — (1) try `_lookup_field` (section-aware via `_detect_section`) on the first 5 empty fields; if ANY resolves to a non-empty value, return the fill action directly, no LLM; (2) if the page has zero empty fields and an unvisited tab remains, advance to it directly (same `click_position` resolution the LLM-driven branch already does) — no judgment call needed, so no reason to pay for one. Falls through to the LLM UNCHANGED for the genuinely ambiguous case (label doesn't match any record key, even fuzzy) — preserves the "LLM's real job is ambiguity" design rule, zero risk of new incorrect behavior since the existing downstream section-correct/record-beats-LLM-proposal guards still run on whatever gets returned either way. Syntax-checked. OFFLINE-VERIFIED (`scratch/probe_sweep_deterministic.py`, 4 cases: all-resolvable page, tab-advance, unresolvable-field fallback, nothing-left fallback) — all behave as designed. NOT yet live-retested — expect this to cut the bulk of the 59-121 calls/record, since the ×10 run's own logs showed nearly every sweep fill WAS a clean record-driven value (e.g. 'DL Number' → 'D0012938') that this now resolves without a round-trip. Bucket 3 (verb-loop proper — retiring the sweep/verify LLM-navigation dependency structurally, not just short-circuiting it) is the next lever if this isn't enough.
- [x] **"Keeps looping and looping" on legitimately-blank tabs [FOUND + FIXED 2026-07-14]** — user-reported live, testing the speed fix above: record 10 (a claims-free record) got stuck in repeated `[NAV] STUCK 6 steps → optimal-viewport jump → STUCK → jump → ...` cycles on the Claims tab, 8 separate STUCK events across the run, never converging. ROOT CAUSE: `_navigation_protocol`'s empty-field-list builder (agent.py, the SAME function fixed above) had an "attempted field counts as filled/settled" guard — but it was scoped to CHECKBOXES ONLY (an unchecked box legitimately reads `value=''` forever, so this guard already existed for that reason, added 2026-07-09). Text/combo fields correctly SKIPPED as deterministic-absent-from-record (OPT2's leave-blank guard, ~line 2017, DOES call `_mark_attempted`) hit the EXACT same shape — a skip never writes a value, so the field stays `value=''` forever too — but weren't covered by the guard, so they stayed in the "still needs a value" list FOREVER. On a tab with several genuinely-blank fields (Claims, when the record has no claim — 8-10 fields in a row), the empty list could never shrink to zero, so NEITHER the sweep's own deterministic tab-advance short-circuit (added earlier the same day) NOR the outer STUCK/jump loop's "nothing left, tab done" detection could ever fire — confirmed `_find_missing_field` and `_optimal_viewport_jump` (the functions that actually DRIVE the STUCK/jump cycle) already correctly excluded ALL attempted field types, not just checkboxes — this was the ONE place still narrower. FIX: broadened the guard from `type in (checkboxcontrol, checkbox) and attempted` to just `attempted` — any settled field of any type now correctly leaves the candidate list. Syntax-checked. OFFLINE-VERIFIED (`scratch/probe_settled_blank_convergence.py`): reproduces the old checkbox-only guard leaving all 8 skipped Claims fields in the empty list forever, confirms the fixed guard converges to zero. **LIVE-CONFIRMED 2026-07-14**: next `--start_record 10 --records 1` run reached 100% task completion and terminated NATURALLY (submitted) — the previous attempt on this same record had to be manually killed at 24.4% after repeated STUCK cycles. Convergence bug closed. (Wall-clock for this run was ~16 min, LLM calls 75 — within the same range as before the bucket-2 speed fix, so that fix's impact on THIS metric is inconclusive from one run; the convergence fix is the clear, confirmed win here.) This same retest also caught a NEW regression in the bucket-2 short-circuit itself — see next entry.
- [x] **Sweep short-circuit (bucket 2) regressed driver-crosstalk on its very first live test [FOUND + FIXED 2026-07-14]** — the record-10 retest above that confirmed the convergence fix ALSO surfaced `d2_dl_exp: expected '08/22/2029', got '04/11/2029'` (Driver 2's own DL Expiration got Driver 3's value) — a brand-new instance of the crosstalk family, on a call site that didn't exist until earlier the same day. ROOT CAUSE: the bucket-2 deterministic short-circuit (added a few hours earlier, see entry above) calls `_detect_section`+`_lookup_field` directly but never checked `_detect_section`'s own `None`-vs-`''` distinction (unconfirmed-ambiguous vs. confirmed-unsectioned) — every OTHER call site in the file that does this section-aware lookup was already hardened for this (the `_view_mismatches` defer-on-None fix from earlier the same day), but this new short-circuit was written without it, reintroducing the exact bug class hours after it was closed elsewhere. FIX: added the same `if _dsec is None: continue` guard (try the next lookahead candidate instead of guessing) — trusts a confirmed `''` exactly as before (so unsectioned tabs like Policy/Coverage/Payment, the common case, are unaffected), only skips the genuinely ambiguous case. Syntax-checked. OFFLINE-VERIFIED (`scratch/probe_sweep_shortcircuit_section_confirm.py`, 3 cases: ambiguous-must-defer, confirmed-Driver-2-resolves-correctly, confirmed-unsectioned-still-trusted). NOT yet live-retested. LESSON: every new section-aware lookup call site needs the None-vs-confirmed guard as a matter of course now — it's not optional hardening, it's the baseline contract of calling `_detect_section` at all.
- [x] **Trusted scorers (section-aware + record-aware) [DONE 2026-07-11 — one residual subtask]** —
  `eval_metrics` section-fix 2026-07-10 (41cff4c) + record-aware 2026-07-11; `bc_fidelity`
  record-aware + section-aware + annotation-stripping 2026-07-11. Honest rescore of the ×2 run:
  record 1 = 89.3% field / 100% value / 0 mismatches; record 2 = 63.5% / 100% (old 20%/32.6%
  was grader fiction). All verified offline (archived submissions + reconstructed failure
  cases). Subtask history + the open coverage gap:
  - [x] **bc_fidelity: record-blind gold [FIXED 2026-07-11]** — `_detect_record_num` matches
    the submission's policy number against each intake record (data-driven) and rebuilds gold
    for THAT record on the fly; static reference is the flagged fallback. Report prints which
    gold was used. Verified on archived submissions: 00441→record 1, 00442→record 2.
  - [x] **bc_fidelity: section-blind gold keys [FIXED 2026-07-11]** — `_parse_intake_record`
    now tracks `[Section]` headers: bare labels inside `[Driver N]` / `[Vehicle 2+]` never map
    to ph_*/v_* keys (Driver 3's 'Tyler' had overwritten ph_first); first-occurrence-wins as
    belt. Also strips intake annotations from gold values ('← NOTE …', '[VERIFY — …]').
    RESULT (×2 run rescored honestly): record 1 = 89.3% field match / 100% value acc /
    0 mismatches; record 2 = 63.5% / **100%** (was 20% / 32.6% fiction).
  - [x] **bc_fidelity: gold key-space extended to the whole form [FIXED 2026-07-12,
    offline-verified]** — `_LABEL_TO_KEY` covered Policy/Policyholder/Vehicle only
    (~75 fields); Drivers/Coverage/Claims/Payment fills were invisible to the BC score (the
    record-2 claim contamination never showed in it — not scored wrong, just never checked).
    Fix: ~90 new label→key entries sourced from a real submission JSON's actual keys (not
    guessed) covering Coverage/History/Discounts/Claims/Payment; `_TAB_PREFIXES` gained
    `hist_`/`disc_` (had no tab name at all before); Driver 2/3 needed section-aware suffix
    mapping (`_DRIVER_LABEL_TO_SUFFIX` — same bare labels as Policyholder, e.g. 'First Name'
    → `d2_first`/`d3_first`); genuinely ambiguous labels ('City'/'State'/'ZIP', 'Total Premium
    ($)' appearing in two sections with DIFFERENT keys) got an explicit `_SECTION_LABEL_TO_KEY`
    (section, label) override checked first. Bonus fix found mid-verification: `'(leave
    blank)'` was leaking through as LITERAL gold text (not skipped like `'(none)'`/`'n/a'`) —
    normalized the same way the agent's own `_lookup_field` does. RESULT: record 1 gold
    75→163 fields; both real archived submissions rescored at 100% tab coverage (was ~33%);
    all 10 intake records parse cleanly with Coverage+Payment present; yesterday's
    policyholder/driver identity-separation fix (James vs Tyler) unaffected. Residual
    (cosmetic, not chased): a few Policy-tab keys with no `policy_` prefix (`agent_id`,
    `underwriter`…) display under an 'Other' tab label — doesn't affect scoring correctness.
  - [~] **Behavioral Match unfrozen [FIXED 2026-07-12, offline-verified — first live number
    pending]** — the thesis metric read 0% forever because the reference builder pointed at a
    DEAD dir (`data/output/traces/forms`). Now: reference = the training corpus
    (`data/demos/eight_Tabs_clean2`, `BC_TRACES_DIR` overridable — keep in sync with the
    trained model); extractor handles the demo-recorder trace format (per-keystroke traces
    collapsed to per-field granularity, both formats supported); representative-median
    sequence cached (134s build → 0.02s hit); tab-order term skipped when demos don't flag
    the selected tab (field-sequence-only, no zero-drag). **CALIBRATION: demo-vs-demo
    similarity = 81% — the human consistency ceiling. A perfect clone scores ~80%, not 100%;
    thesis must report agent Behavioral Match against that baseline.**
  - [x] **Run scorer (eval_metrics) record-aware [FIXED 2026-07-11]** — `evaluate_run` now
    takes `record_num` from run_task (which knows which record ran); the old majority-vote
    inference mis-picked record 1 (shared generic values + record 1's larger field count
    biased the vote, ties defaulted to record 1) — kept only as fallback for callers without
    the number. Offline-verified against the exact live failure: contamination-biased run
    scored ✗ "expected 'Marcus D. Chen'" via inference, ✓ via `record_num=2`.
- [ ] **Scroll no-ops on tabs [RE-PRIORITIZED 2026-07-14 — now a real non-convergence bug, not cosmetic]** — Fix `ScrollPattern.Scroll` failure on Claims/History/Drivers so all below-fold fields are reached, then remove the verification "accept-after-2-tries" band-aid. *(2026-07-09: optimal-viewport jump + viewport-top fix improve reach; deep-tab scroll still unverified end-to-end.)* **NEW EVIDENCE (2026-07-14, record 10 test)**: this stopped being a low-priority cosmetic warning. `[VERIFY] pass complete — 29 field(s) corrected` fired at step 238 across SIX tabs (Policy/Policyholder/Vehicle/Coverage/History/Payment) — mostly checkbox/combo fields ('YES (check)', 'NO') that should already have been correct from the main pass. `_confirm_finished` then said "NOT done", triggering a FULL fresh 8-tab verify pass at step 239, which found MORE to "fix." `ScrollPattern call succeeded but panel DID NOT MOVE` warnings fired on nearly every tab-switch throughout. User confirmed live: "it was in a loop" — had to manually kill the run at step 241 (raw counter; only 116 actionable). HYPOTHESIS (not yet confirmed): stale/wrong-position reads from the no-op scroll make verify re-flag already-correct fields as wrong on every pass, so it can never reach the "0 corrections" convergence gate. This may be the SAME underlying bug as `identity-everywhere`'s already-filed residual "verify-fix read-back unification" — worth investigating together rather than as two separate items. Notably records 1-9 of the SAME model completed cleanly the same day; what's different about record 10 (or about this being a later/warmer run) is unknown — don't assume, investigate. DO: reproduce on a fresh record-10-only run with full debug logging (reproduces reliably per this session), root-cause the scroll no-op, and check whether verify's OWN read-back is independently unreliable.
- [x] **Verify-fix read-back/write identity unified for checkboxes [FIXED 2026-07-14, closes the already-filed `verify-fix read-back unification` residual]** — dug into the 29-fields-corrected-every-pass loop above: nearly all 29 were CHECKBOXES (Vehicle safety features, Coverage riders). Read `_is_checked` (agent.py:3882, the function `_view_mismatches`/verify uses to check a checkbox's REAL state) side-by-side with `_act_on_element` (the function that WRITES a checkbox's state): the write path resolves the live control via `_resolve_live_control` — name **plus geometry** disambiguation (nearest bbox-center match among same-named twins, the exact mechanism built 2026-07-09 for repeated-section fields). `_is_checked` did its OWN separate lookup — `root.CheckBoxControl(searchDepth=25, Name=label)` — by NAME ONLY, no geometry check, no guarantee it resolves to the SAME physical UIA node the write touched (wx keeps hidden tab pages' controls in the tree too; a duplicate/stale node with the same label is enough to desync read from write). If the write correctly landed but the read grabbed a different node reporting the OLD toggle state, verify would see "still wrong" and re-fix an already-correct checkbox — every single pass, forever, exactly matching the observed symptom (same ~29 fields, repeatedly, across multiple full 8-tab verify passes). FIX: `_is_checked` now tries `self._resolve_live_control(elem)` FIRST — the SAME identity resolver the write path uses — reading its TogglePattern; only falls back to the old by-name search if that resolution fails. Read and write now always agree on which physical control they mean. Syntax-checked. **Cannot be offline-drilled** — the bug is a live UIA identity-resolution discrepancy between two lookup strategies; no synthetic reproduction is meaningful without a real running form. **LIVE-CONFIRMED 2026-07-14**: next uninterrupted `--start_record 10` run converged cleanly — `[VERIFY] pass complete` went 12 corrected → 4 corrected → **0 corrected**, done, naturally terminated, 100% completion. No more repeat-forever loop.
- [x] **Verify's LLM-fallback branch skips the record's own "confirmed absent = stays blank" answer [FOUND + FIXED 2026-07-14, speed lever]** — same clean run above still spent 10 of 50 LLM calls (20%) on repeated `LLM-fix 'Claim Number'` — record 10 is claims-free, so this field is genuinely absent, and the SWEEP already has an inline "record is source of truth, no value found = stays blank, settled" guard for exactly this case (built 2026-07-11) — but that guard lives DOWNSTREAM of `_navigation_protocol`, and the bucket-2 short-circuit added earlier the same day only short-circuited on a RESOLVED value, not on a CONFIRMED-absent one, so an absent field still fell all the way through to a full LLM round-trip every time verify's LLM-fallback branch called this function. FIX: the same short-circuit loop now also returns `{"action":"fill","field":lbl,"value":""}` directly when the record is loaded but genuinely has nothing for a field (section confirmed, `_lookup_field` returns empty) — mirrors the sweep's own existing downstream philosophy (no literal blacklist, "not found via lookup" = stays blank, same design rule established 2026-07-11), just applied before the LLM call instead of after. Syntax-checked; NOT yet live-retested.
- [x] **Verify scrolls each tab to top before its first scan [BUILT + CHEAPLY VERIFIED 2026-07-14, 6th round of driver-crosstalk]** — `d2_dl_exp` kept getting Driver 3's/Policyholder's value on 6 separate live reruns despite every prior fix in this family. ROOT CAUSE: if a tab's scroll position carried over from an earlier visit, verify's FIRST scan could land mid-way through a repeated section's block (Driver 2's own header already scrolled past, only Driver 3's header visible below) — `_detect_section`'s geometry then finds no qualifying pane ABOVE the field, correctly returning `''` by the letter of the code (nothing above) but WRONG semantically (Driver 2's header exists, just isn't in this view yet). Not the ambiguous/`None` case earlier fixes catch — the available data is genuinely incomplete at that moment. FIX: `_verify_pass`'s tab-switch loop now calls `_scroll_form_to_top` (an existing helper already used elsewhere on tab-switch) before its first scan, guaranteeing every section header gets observed before any of its fields are judged. **METHODOLOGY CHANGE (user-driven)**: instead of a full 15-20 min live agent run to check this, wrote `scratch/probe_verify_scroll_to_top.py` — attaches to the ALREADY-OPEN form, clicks straight to the Drivers tab via `SelectionItemPattern` (no agent, no LLM calls), and checks whether both Driver 2's and Driver 3's section-header panes land on-screen. Confirmed: both headers are visible immediately on tab-open on this specific tab (163-193 and 603-633 px, form client rect 43-815) — no scroll even needed here, so the fix's precondition holds with margin. This is NOT a full live-agent confirmation (doesn't prove the fix holds when verify re-visits a tab mid-scroll from an earlier pass — the exact original failure mode) — it proves the MECHANICAL premise is sound, cheaply, before spending a full run on it. Syntax-checked. Still needs one real end-to-end run to fully close this out, but the cheap probe substantially de-risks it first.

  **RESURFACED 2026-07-16** (`run_20260716_170410.txt`), much bigger impact than originally scoped: investigating why Field Match Rate was only 66.9% (54/163 blank), found nearly the ENTIRE Vehicle-tab spec block missing — VIN, Year, Make, Model, Trim, Body, Color, Doors, Cylinders, Displacement (10 fields) plus 6 safety checkboxes that should be checked (anti-theft/airbags/ABS/daytime-lights/backup-camera/lane-assist), plus `ph_homeowner`/`cov_um_uim`/`cov_acc_forgive`. The log showed the very first field the transformer touched after switching to Vehicle was `'Fuel Type'` (mid-page) — never VIN/Year/Make at the top. **ROOT CAUSE**: exact same bug as above, hitting a different, now-live code path — the original fix only patched `_verify_pass` (agent.py ~5374), which is fully dead code now (removed the same day, per the user's "no revisit" mandate), so the scroll-to-top safeguard it provided no longer runs anywhere live. Grepped all 5 places a tab switch actually happens (GAP-path forced-unvisited-tab click ~1279, GAP-path normal LLM-driven click ~1296, `_try_advance_tab`'s stuck-guard advance ~4693, sweep's two switch sites ~6254/~6283) — **none** called `_scroll_form_to_top`. wx's `ScrolledPanel` keeps whatever scroll offset it last had; mid-page on (re-)entry means the top section is simply invisible and never discovered. **FIX**: added `self._scroll_form_to_top(self._observe())` right after every tab-switch click, before the fresh tab's fields get processed — generic (no field/tab names referenced), matches the agent's HOW-mechanics role. `py_compile`+`pyflakes` clean. NOT yet live-tested — next run should show top-of-tab sections (Vehicle spec block especially) getting filled instead of skipped.

  **LIVE-TESTED 2026-07-16** (`run_20260716_172320.txt`) — **fix fired, hypothesis wrong**: `Scroll-form-top` logged 8 times, confirmed firing right after the Vehicle-tab switch (17:26:04 click → 17:26:06 scroll). But the very next step still went straight to `'Fuel Type'` (mid-page, y=628, same as every prior run) — the top section wasn't hidden by a stale scroll offset; scroll-to-top put it on-screen and the transformer's own pointer skipped past it anyway. Field Match Rate barely moved (65.0% vs 65.6%), confirming nothing changed. Checked the form source (`car_insurance_entry/car_insurance_form_wx.py:395-424`): `'Fuel Type'` is the 3rd field of the SECOND section (`'Engine & Drivetrain'`) — the transformer's learned click-order starts there, never targeting any of the 8 `'Vehicle Identification'` fields (VIN/Year/Make/Model/Trim/Body/Color/Doors) or the first 2 of `'Engine & Drivetrain'` (Cylinders/Displacement) at all. **RETRACTED**: not a scroll/reveal bug for Vehicle specifically — a genuine MODEL/TRAINING-DATA gap, exactly the project's own rule ("root fix for fixation/mis-prediction = more demos, not more guards"). The demos this model trained on likely never demonstrated filling Vehicle Identification's fields, so the transformer never learned to click them. The scroll-to-top fix is KEPT (harmless, may still help other tabs/cases with a genuine scroll-carryover) but is NOT the fix for the missing-Vehicle-fields problem. **REAL FIX NEEDED**: record demos that start Vehicle tab at VIN and fill through Vehicle Identification + the front of Engine & Drivetrain before Fuel Type, then retrain — a data problem, not a code problem.
- [~] **Verb-loop rewrite [SCOPED + Stage 2 in progress, git branch `verb-loop-rewrite`]** — the main step loop decides what to do by checking ~15 separate ad-hoc trackers invented one bug at a time (`_dead_fill_keys`, `_attempted_keys`, `_fixation_hits`, `_filled_this_tab`, `_checked_fields`, `_nochange_click_pos`, `_keystroke_retried`, `_reveal_focus_count`, `_verified_at_fill`...) that overlap in purpose without sharing logic — exactly why bugs like the sweep-re-verify desync above happen: two code paths independently "verify" the same fill two inconsistent ways. Goal: one per-field state record + a small set of explicit self-confirming VERBS (CLICK/TYPE/CHECK/SCROLL/SWITCH_TAB/SUBMIT). **Stage 1 (audit) done**: dead vars identified (`_verify_fix_count`/`_verify_dead_stable`/`_verify_clean_tabs`/`_verify_ran_once`/`_verified_at_fill` — all write-only, served only the now-dead `_verify_pass`/`_view_mismatches`); load-bearing vars catalogued. **Stage 2, CHECK verb done + live-confirmed**: found THREE independent checkbox-toggle implementations (canonical UIA TogglePattern in `_act_on_element`, a hand-rolled Win32 version in the per-step click handler, an unconfirmed fire-and-forget version in the "type intercept" path) — unified all three onto the canonical verb. Live-confirmed `run_20260716_170410.txt`: both migrated call sites fired correctly, zero force-check errors. **TYPE verb audited, no fix needed** — already single-dispatch on the main path and canonical-then-fallback on the sweep path from an earlier session's fix; forcing a rewrite here would touch the core step loop for no found bug, so skipped (no premature abstraction). **Combobox slice done**: found two independent hand-rolled "click open, wait for listitem children, click match" fallbacks (`_fill_element`'s and `_nav_fill_field`'s) with drifted matching logic — only one had ever received today's apostrophe/numeric-match fixes. Extracted into one shared `_combobox_legacy_click_fallback` helper. `py_compile`+`pyflakes` clean. NOT yet live-tested (lower risk — only the last-resort fallback path, reached after the canonical write already failed). **SWITCH_TAB verb audited 2026-07-18**: grepping every tab-switch site found three more that the 2026-07-16 scroll-to-top pass missed — the `"(fixation) page done"` handler, its escalation-fallback sibling, and a GENERIC "any click landed on a tab element" detector that catches the transformer's own organic tab clicks (separate from GAP/fixation/sweep/`_try_advance_tab`, which all `continue` before reaching it). That generic site had a comment describing behavior that was never implemented ("mark a tab switch so the next step scrolls to top") — `_tab_just_switched` was set but only ever read inside a debug log line. Added `_scroll_form_to_top` to all three; tab-switch coverage is now 8 sites total, confirmed complete via a final grep. `py_compile`+`pyflakes` clean, NOT yet live-tested. **SUBMIT verb audited 2026-07-18**: already correctly unified, no fix needed — exactly one function (`_click_submit`) does the actual click, called from 3 sites, idempotent, wrapped by the pre-existing chokepoint (`_allow_submit`/`_point_on_submit`/`_submit_bboxes`) that blocks every other click in the agent from landing on Submit unless this function explicitly opened the gate. Already the target shape from an earlier session. **SCROLL verb audited 2026-07-18**: found and removed one genuinely dead duplicate — `_scroll_form_down`, a generic scroll-down using raw `pyautogui.scroll` with zero movement verification (docstring: "Returns True if scroll was attempted", not succeeded), fully superseded by `_scrollbar_drag` (which verifies the pane actually moved via ScrollPattern) and having zero remaining callers anywhere in the file. Deleted outright. The rest of the scroll functions (`_scroll_into_view`, `_scroll_pane_bottom`, `_scroll_form_to_top`) serve distinct, non-overlapping purposes — no further duplication found. **CLICK verb audited 2026-07-18**: already correctly unified, no fix needed — `executor.py`'s `execute()` is the one dispatch point for every click/keyboard/scroll action in the agent, and confirmation happens externally via the one shared `Validator`, checked generically after every action type. **All 6 verbs now audited**: CHECK/combobox/SWITCH_TAB/SCROLL fixed; TYPE/SUBMIT/CLICK already clean. **State-tracker consolidation, first slice done 2026-07-18**: deliberately not attempting a big-bang merge of all ~9 trackers (real semantic risk — mapped all 31 `_dead_fill_keys`/`_attempted_keys` sites: 7 check attempted alone, 4 check dead alone, 8 check both via OR; collapsing wrong would silently change behavior). Instead found the same duplicate-write problem as CHECK/combobox, for a state write instead of a UI action: `_attempted_keys` already has one writer (`_mark_attempted`), but `_dead_fill_keys` had 9 independent call sites hand-computing keys and calling `.add()` directly. Added `_mark_dead(key_or_elem)` next to `_mark_attempted`, migrated the 6 live call sites; left 3 untouched after confirming via function-boundary grep they're inside the already-dead `_verify_pass`. `py_compile`+`pyflakes` clean. Doesn't yet merge the two sets or touch the other ~7 trackers — deliberately deferred, same incremental discipline.

  **LIVE-CRASHED 2026-07-18** (`run_20260718_131737.txt`), caught + fixed same session: step 132 crashed — `_mark_dead`'s `isinstance(key_or_elem, str)` check wrongly treated any non-str input as an element dict, but `_attempt_key` itself can return a TUPLE (section-qualified `(section, label)` keys, or bbox-fallback `('@', x, y)` keys) — a very common shape on repeated-section tabs (Drivers), not an edge case. A tuple key got re-fed into `_attempt_key`, which called `.get()` on it and crashed (`'tuple' object has no attribute 'get'`). **FIX**: key off dict-ness instead of str-ness — `self._attempt_key(key_or_elem) if isinstance(key_or_elem, dict) else key_or_elem`. Drilled against all 4 real input shapes (dict, str, section-tuple, bbox-tuple) via a standalone harness — all correct. `py_compile`+`pyflakes` clean. NOT yet re-tested against a real run.
- [~] **Hard-to-fill widgets [mechanics probe-verified; run-hooks in all 3 combobox paths, live retest pending]** —
  LIVE RUN 19:09 (user-insisted, rightly): the probe validated `_act_on_element`, but the run
  used the CLICK-FILL combobox path which had NO rescue — 'Texas' failed 2× and skipped. All
  THREE combobox paths (type-path, click-fill, reveal-focus) now escalate to the identity
  executor. Retest live on a FRESH form. Details:
  `scratch/probe_dead_widgets.py` drives the REAL fix path (`_act_on_element`) on exactly the
  two chronic dead widgets: **PASS 'Years Continuously Insured' '' → '3'** (direct UIA
  ValuePattern write — the widget rejects both paste and synthetic keystrokes; RangeValue
  added for spins that need it) and **PASS 'State' '' → 'Texas'** (below-the-fold item in a
  50-option wx.Choice). Combobox strategy ladder, each probed live: ValuePattern write →
  expand+child walk → desktop list walk → full-string type-to-filter (landed on 'South
  Carolina': wx.Choice matches SINGLE chars, not prefixes — the trailing 's') → **first-letter
  cycling with read-back** (press 'T' until Texas; the human move — this one won). Run-loop
  escalation hooks (dead-mark rescue + combobox "not in visible options" rescue) route into
  the same code — exercised in the next full run (grep 'Dead-widget rescue').
- [~] **Value quality + LLM-45% bucket 1: deterministic value short-circuit [IMPLEMENTED 2026-07-11, untested live]** — `_expected` was already resolved exactly (section-aware lookup + refresh + record-bounded peek), then the LLM was paid 2-5s/call to ECHO it back ("Use EXACTLY this string") ~200×/run — the bulk of LLM dependency AND the wrong-line-grab class (LLM echoing imperfectly: 'Middle Name' got the last name, D2/D3 'DL Expiration' got D1's date). Now: resolved value types DIRECTLY, no call; LLM consulted only when the record can't resolve the label (its real job). Steps tagged `deterministic` — new 'Deterministic Steps' metrics line; LLM Dependency now measures REAL calls (verified offline on synthetic results). Expect ~45% → ~15%; buckets 2 (deterministic next-tab) + 3 (verb-loop) take it to <5%.
- [x] **Verify-at-fill (kill the redundant end-pass)** *(filed + implemented 2026-07-10, live-exercised 2026-07-11)* — Too much run time went to re-checking fields AFTER they were already filled. Diagnosis: the verify pass's cost was NOT the deterministic read-back (cheap UIA reads) but the LLM completeness call firing on EVERY scroll-view — even views where every field already held a confirmed value (LLM latency × ~8 views × 9 tabs). FIX: symptom gate — the LLM branch fires only when the view shows an EMPTY live fillable (non-dead, labeled) that the deterministic branch couldn't settle; filled fields were either source-matched by branch 1 or are settled. Branch 1 (deterministic clobber-catch) still reads every view; convergence gate unchanged. LIVE-EXERCISED in the ×2 probe (2026-07-11): both records ran the gated verify and submitted — no wedge, no missed-fill regression. Caveat: wall-time saving not explicitly measured.
- [ ] **Deterministic verification polish** — Remove verification band-aids, cut per-field LLM reasoning calls, and speed up the validation pass.
- [x] **Multi-record scaling (×10) [DONE — the scope's Definition of Done, raised from ×5 to ×10 (2026-07-14) to match the full 10-record intake; RE-VALIDATED 2026-07-15 against everything landed since]** — Runner LIVE-VERIFIED 2026-07-11 (×2 probe): both records SUBMITTED (PAI-2026-00441 + PAI-2026-00442), record 2 filled with genuine record-2 data, form reset clean, per-record scorecards archived. Record-2 mechanics best yet (3.7% waste, 1.9 steps/field, 96.3% exec success). Subtasks from the ×2 probe findings:
  - [x] **Cross-record value contamination [CONFIRMED FIXED — second --start_record 2 probe: zero record-1 values, peeks return nothing, cache stays record-2]** — Fields ABSENT from record N got RECORD 1's values. ROUND 1: the `records.get(record_num, min-fallback)` pattern at FIVE blob-parse sites (capped UIA blobs = file start = record 1 only) → strict bound everywhere + honest prompt message. Offline-verified… and the `--start_record 2` live probe STILL contaminated: **round 2 root = the LINE-SEARCH paths** — `_peek_notepad` / `NotepadDataSource.peek/peek_next_after` scan the whole file via `_find_field_line` (first 'Claim Amount' in file = record 1's, line 244, record 1 spans 17-300) and CACHE the hit; `NotepadDataSource.refresh` had the min-fallback too. FIX: `_record_line_span` helper (notepad_source; line-level record delimiter, overridable like the parser's) — all line searches now bounded to record N's slice; record absent from a multi-record file = search NOTHING (sentinel distinguishes headerless sources, which stay whole-text). Offline-verified against the exact live failure: record 2's claim lookups → None (were record 1's lines), record 1 still finds its own, absent/headerless cases correct. LESSON: the offline probe validated the five sites I knew about; the cheap live probe found the sixth path — audit finds what you suspect, probes find what you don't.
  - [~] **Absent-from-record = leave blank (nobody fills it — LLM, resolver, or sweep)** — three unguarded fillers found and closed one probe at a time: (1) LLM invention on `expected=''` → deterministic absent=skip in `_ask_llm`, no LLM call — VERIFIED live 17:53; (2) merge's `TextResolver` backup "rescued" the empty text from VISIBLE Notepad text (= record 1) → `skip_field: True` honored by merge, resolver not consulted — VERIFIED live 18:00 ('deterministic skip honored' on every absent field, zero inventions in the OPT2 path); (3) the SWEEP typed the LLM's junk LITERALLY via the identity executor ('Claim Number' → '(leave blank)', 'Resolution Date' → 'N/A', 'Description' → 'No description provided' — 18:00 probe, step 17) → sweep guard: **record is the source of truth** — no section-aware record value = field settled stays-blank (dead-marked, not re-proposed); record value present = it BEATS the LLM's proposal. Deliberately NO literal blacklist (user-flagged: that's the ruleset-inference loop's territory per the HARD RULES; `_lookup_field` already resolves explicit '(leave blank)' record values to empty, so one rule covers both). Verify inherits via dead-key exclusion. UNTESTED LIVE (sweep guard). Residual: skipped fields re-picked by the model until STUCK fires (cheap, loopy). Retest = Claims probe: sweep lines must read 'stays blank (settled, no fill)'.
  - [x] **Run `--records 10`** — after the two fixes above; archive all 10 scorecards. DONE 2026-07-14 (`run_20260714_121233.txt`, 9/10 accepted as DoD, see line ~498) and RE-DONE 2026-07-15 (`run_20260715_195128.txt`, 10/10 SUBMITTED, 19:51-22:15, ~2h24m wall-clock) to validate everything landed since — verify-never-converges-hallucination, the hallucination guard, both speed fixes, combobox-substring-match, `_live_field_value`. All 10 completed cleanly, natural termination, 97-100% value accuracy every record, ZERO crosstalk-signature mismatches. Bar cleared — see `rerun-x10-post-fixes` note below (~line 605) for the full breakdown, residual non-crosstalk issues, and the speed numbers that motivated the next round of work.
- [ ] **Automate scoring harness** — Extend `scripts/bc_fidelity.py` to report blank fields, print full breakdowns, save scorecards, and fix Unicode print issues (do before fixing correctness).
- [ ] **Strip WHERE-crutches** (Stage 2.5) — Remove agent-side navigation helpers (`_try_advance_tab`, `_focus_first_empty_field`, auto-advance-at-bottom) to let the transformer navigate fully. *(2026-07-09: ranked arbitration replaces several crutches at the source — WHERE stays the model's own ranking, agent only legality-filters; M2 + stranding guard deleted rather than added-to.)* **AUDIT DONE 2026-07-14 (no removal — no live-test budget left that session, deliberately deferred per the standing "add/remove agent logic ONE at a time, re-test each" rule)**: read both remaining crutch functions in full.
  - `_focus_first_empty_field` (agent.py:4964) — clicks the FIRST unhandled editcontrol/combobox by ELEMENT LIST ORDER, not the transformer's own ranking. **4 call sites**: agent.py:1165, 1313, 1465, 1805.
  - `_try_advance_tab` (agent.py:5099) — clicks the NEXT tab by geometric/index order (detects active tab via positive-vs-negative screen coords, no hardcoded names). **8 call sites**: agent.py:1199, 1324, 1400, 1813, 1915, plus 2502/2562 (fallback comment: "Route through _try_advance_tab which uses the correct indexed bbox instead").
  - Both predate ranked-arbitration (2026-07-10) and were only partially superseded, per the existing 2026-07-09 note — never fully removed.
  - **CLASSIFICATION DONE (same session)**: read every one of the 12 call sites (not just grep — full surrounding context each time). Result was much better than feared: `_focus_first_empty_field`'s all 4 sites (1165, 1313, 1465, 1805) AND 4 of `_try_advance_tab`'s 6 sites (1199, 1324, 1400, 1813) were **provably dead code** — gated behind `_plugin_active = (task_plugin is not None) or pure_transformer or no_autohandlers`, and `run_task.py` hardcodes all three inputs (`task_plugin=None`, `pure_transformer=False`, `disable_auto_handlers=True`) such that `_plugin_active` is unconditionally `True` in every real run. That code has never executed and never will, as configured. The remaining 2 `_try_advance_tab` sites (1915, 2562) plus one unnamed inline block (the "[GAP] LLM stuck re-picking... forcing unvisited tab" mechanism, ~line 1886) are genuinely live, but ALL fire only as a last resort AFTER the model's own LLM/GAP navigation attempt already came back empty — fallback safety nets, not WHERE-steals.
  - **REMOVED 2026-07-14**: the entire dead block (agent.py, was lines 1105-1820, ~716 lines — auto-fill, auto-check, auto-skip, combobox-fix, dup-label-peek, button-escape, pane-escape, tab-complete-scan, and the 8 dead crutch-call-sites within it) deleted from `agent.py`, replaced with a short marker comment. Exact removed code preserved verbatim at `scratch/removed_dead_legacy_autohandlers_20260714.py` (per user's explicit "keep for reference" instruction) — not restored, kept in case `task_plugin`/`pure_transformer` modes ever go live again. Verified safe: `_confirmed_blank_fields` (declared outside the deleted range) still has valid `.clear()` callers outside it; `_tc_has_pending`/`_tc_key`/`_pane_not_handled` (helper functions ONLY defined+used inside the dead zone) confirmed fully gone, no orphaned references. `python -m py_compile` clean, `ast.parse` clean, `from agent.agent import LLMAgent` imports clean. **This was a genuinely safe, zero-behavior-change deletion** — no live test needed to validate it, since the code was provably unreachable before AND after. Net: -706 lines in agent.py (7399 → 6693).
  - **REMAINING 3 SITES — INVESTIGATED, DELIBERATELY LEFT ALONE 2026-07-14**: tried to scope "make them consult the model's own ranking" and found it's not a small patch — all 3 (2 `_try_advance_tab` calls + the inline GAP-stuck tab-forcing block) fire in a branch that runs BEFORE `t_pred = self._predict(state)` is computed for that step (that happens later, in a separate branch these sites `continue` past) — there is no current-step model prediction available to consult at that point. Fixing properly means restructuring WHEN the model gets consulted across the whole step loop (reuse stale last-step prediction, or force an early extra prediction call) — real architectural risk to core navigation timing, not a local patch. **USER DECISION**: leave these 3 alone — they only fire in genuine dead-ends (nothing clickable, scroll exhausted, LLM/GAP gave nothing usable), not common-case WHERE-steals, and doing a core-loop restructure with zero live-test budget left that session wasn't worth the risk. Revisit only if these specific fallbacks are observed causing a real problem.

- [ ] **Dead-code deletion broke `_focused_el` — FOUND + FIXED same session 2026-07-14** — the ~716-line dead-block removal above swept away two lines (`_focused_id`/`_focused_el` computation) that were UNCONDITIONAL (not gated by `_plugin_active`, sitting just before the dead `if` block) but still needed later at the "pre-type clear" check (~line 2018). First two post-removal live runs crashed instantly with `NameError: name '_focused_el' is not defined` (0 steps each). FIX: recomputed `_focused_el` fresh at its actual use site instead of restoring the earlier assignment (safer — `state` may have been reassigned multiple times by that point in the step). Did a full sweep this time: cross-referenced all 94 names assigned in the deleted block against `agent.py` — only `_focused_el` was a real casualty, everything else checks out (unused-but-harmless locals, comment mentions, or common short names reused elsewhere). Syntax-checked, `LLMAgent` imports clean, and a live run afterward ran 106 steps with NO crash (vs. instant death before) — confirms the fix holds. LESSON: "zero risk because provably dead" was true for the GATED logic, but missed that a couple of UNCONDITIONAL setup lines were sitting inside the same line range — dead-code deletion still needs a full cross-reference sweep, not just a gating-condition check.

- [x] **Verify never converges — dead-mark bookkeeping doesn't persist correctly across passes, PLUS the local LLM hallucinates field names on the wrong tab [FOUND 2026-07-14, BOTH FIXES IMPLEMENTED + LIVE-CONFIRMED 2026-07-15]** — after the `_focused_el` fix, a full live run (record 10) ran 106 steps cleanly (91.5% execution success, 100% click accuracy, only 8.5% waste) but never reached Submit — user: "it kept looping instead of actually submitting, genuinely something is wrong." Traced the tail: `[VERIFY] pass complete — 5 field(s) corrected` → immediately re-triggers a FULL fresh 8-tab verify pass (`[GAP] LLM completeness check` → `[VERIFY] deterministic pass over 8 tab(s)`) → `2 field(s) corrected` → re-triggers AGAIN. Corrections trending down (5→2) but never hitting 0 — a slow, wasteful, non-terminating climb, not a flat infinite loop. ROOT SYMPTOM 1: the SAME fields ('Transmission', 'Color', 'Primary Use' on Vehicle; 'Payment Frequency' on Payment) get `'X' won't confirm after 2 tries — accepting (dead)` on one pass, then show up and get the IDENTICAL "accepting (dead)" treatment again on the very next fresh pass. ROOT SYMPTOM 2: `'Claim Number'` (a Claims-tab-only field) gets `LLM-fix 'Claim Number' won't settle after 2 tries` on BOTH the Vehicle tab's verify AND the Payment tab's verify in the SAME run — a field that doesn't exist on either tab.
  - **FIX #1 (Symptom 1, dead-mark bookkeeping)**: root cause — the dead-mark key was `self._attempt_key(e)`, section-qualified via `_section_pane_of`, the SAME geometry-based function that computes the model's training-time `attempted` feature (`transformer.py`, deliberately never patched — would desync live inference from what `model_eight_tabs.pt` was actually trained on). On UNSECTIONED tabs (Vehicle, Payment — no repeated Driver-style sections), that key can flip between passes when a stale off-screen pane briefly contaminates the section read, so a stored dead-mark silently fails to match on the next lookup and the field gets "re-accepted as dead" every fresh pass instead of staying dead once. FIX: added `self._verify_dead_stable: set` (`__init__`, agent.py ~line 651) — a parallel `(tab_name, bare_label.lower())` key with zero geometry involved, so it can't flip. Wired into both `_verify_pass` branches (deterministic-fix branch ~line 5124, LLM-fallback branch ~line 5225): candidates filtered against it up front, dead-marked into BOTH `_dead_fill_keys` and `_verify_dead_stable` once either counter (`_vk` geometry-keyed OR `_stable_vk` tab+label-keyed) exceeds 2 tries; cleared on new-record reset (~line 2536) alongside its siblings.
  - **FIX #2 (Symptom 2, LLM hallucination)**: root cause — `_navigation_protocol`'s prompt (agent.py ~line 5018) tells the LLM "Pick a field ONLY from the EMPTY list" but nothing enforced it; the returned `field` name was trusted verbatim. A small local model will confidently name a familiar-sounding field ("Claim Number") that isn't even on the current page. FIX: added a validation block right after action normalization (agent.py ~line 5071) — rejects any `"fill"` action whose `field` isn't case-insensitively present in that call's own `_empty` candidate list, returning `{"action": "wait"}` instead. Verify's LLM-fallback branch already falls through a non-fill/rejected action to its scroll-and-continue path (~line 5296), so this can't stall the loop — it just skips the bogus "fix" and moves on. This directly explains the live symptom: neither Vehicle's nor Payment's `_empty` list could ever contain "Claim Number", so the guard would have caught both occurrences.
  - **LIVE-CONFIRMED 2026-07-15** (`data/output/run_logs/run_20260715_150701.txt`): full autonomous run, VERIFY corrections trended 25 → 4 → 3 → 1 → **0**, Submit fired on its own (`Submission: PAI-2026-00441_20260715_152324_642175.json`). Dead-marks now STICK: `'Years Continuously Ins'`/`'Years at Address'` dead-marked once (15:20:18-20) and never re-flagged in the two later passes; same for `'Collision Deductible'` (15:21:54) and `'Garaging Location'`/`'Title State'` (15:22:45) — the exact opposite of the old symptom (same fields re-flagged "won't confirm" every fresh pass forever). Fix #1 is proven live. Fix #2 (hallucination guard) was **not exercised** by this particular run — the LLM-fallback branch never fired at all; the deterministic short-circuit resolved every field including `'Claim Number'` itself (`[NAV] sweep: deterministic value 'Claim Number' → 'CLM-2024-88341' — no LLM call`). **PROVEN LIVE SEPARATELY, SAME DAY** (`scratch/probe_navigation_protocol_hallucination_real.py`): rather than wait for the branch to trigger by chance in a full run, called the REAL `agent._navigation_protocol` method directly (constructed a real `LLMAgent`, no live UI needed — the observer is never touched, a hand-built state dict stands in for `observe()`'s output) with `_cached_record` forced empty (the only way to fall past the deterministic short-circuit, which treats any truthy record as authoritative-absent for unmatched fields, into the real LLM-fallback prompt path) and `_llm_json` monkeypatched to return the exact live hallucination (`'Claim Number'` on a fake Vehicle-tab state that doesn't contain it). Result: the guard fired for real — `[NAV] LLM proposed field 'Claim Number' not in this page's EMPTY list ['Transmission', 'Color'] — rejecting as hallucination`, returned `{"action":"wait"}`. Sanity check in the same drill: an honest fill (`'Transmission'`, genuinely in `_empty`) still passed through unaffected — no false positive on the real path. This is stronger than the earlier offline drill (`scratch/probe_verify_hallucination_guard.py`, which only replayed the guard's logic standalone) — it exercises the actual shipped code. Both symptoms are now live-proven via real code paths, not replays or absence-of-failure. Result quality note (separate issue, not a convergence problem): BC score 50.8% (Field Match 75.5% = 123/163, Value Accuracy 100% of filled, Tab Coverage 100%) — the gap is unfilled fields, not wrong values; out of scope for this fix. NEXT: the remaining validation bar is the full ×10 rerun (see `rerun-x10-post-fixes` below / treetask), not a one-record sanity pass.

- [ ] **pyflakes found ~17 unused-local-variable leftovers from the strip-where-crutches deletion [FOUND 2026-07-15, NOT FIXED — cosmetic, zero behavior impact]** — running `python -m pyflakes components/agent/agent.py` as a full-file regression check (while verifying the fix #1/#2 edits above didn't introduce anything new) turned up ~17 pre-existing "assigned but never used" locals: constants/counters like `_NO_CHANGE_LIMIT`, `_DROUGHT_LIMIT`, `_TAB_STEP_LIMIT`, `_pane_escape_last_field`, `_pane_escape_streak`, `_tc_advance_verified`, `_MED_CONF`, `_last_auto_step`, `_TERMINAL_HINTS` (their only consumers lived inside the ~716-line dead auto-handler block removed for strip-where-crutches, 2026-07-14), plus a handful of unrelated pre-existing ones (`active`, `bg`, `all_types`, `W`, `fl`, `_step_exc`, `_tab_cx`). No `F821` (undefined-name) hits anywhere — nothing is broken, this is pure dead-weight. DO: delete these orphaned assignments (same cross-reference-sweep discipline as the `_focused_el` casualty — confirm zero remaining references before deleting each one), rerun pyflakes clean. Low priority, purely cosmetic.

- [~] **Per-record wall-clock speed — root cause was idle sleep, not LLM latency [FOUND + TWO FIXES LANDED + LIVE-TESTED 2026-07-15; live test surfaced one new bug, fixed below]** — after verify-never-converges-hallucination was live-confirmed, user flagged the 16.4-min single-record run as unacceptable and asked for the actual cause. Assumption going in was LLM latency; log evidence disproved it: `run_20260715_150701.txt`'s main-fill phase took 10.4 min but only ~15 LLM calls fired in it (most sub-second — deterministic lookups logged `no LLM call` constantly), and step-by-step timestamps (steps 20-24) showed ~2-5s between EVERY step regardless of whether an LLM fired that step. **ROOT CAUSE**: `STEP_DELAY = 1.5` (`run_task.py:143`) — a fixed `time.sleep(self.step_delay)` applied at 80+ call sites across `agent.py` after nearly every action, over 150-200+ steps/record — several minutes of pure idle wait, not compute. **FIX #1**: cut `STEP_DELAY` 1.5 → 0.7. Zero model-perturbation risk — this is wall-clock pause, not an injected action, so it doesn't touch what the "guards perturb the model" hard rule guards against (that rule is about action-history feeding the transformer's prediction, not sleep duration). **FIX #2 (found analyzing the SAME run's verify tail)**: `_verify_pass` re-scanned ALL 8 tabs on every one of its 5 convergence passes (25→4→3→1→0 corrections, ~6 of the 16.4 min), including tabs that had already reached 0 mismatches on an earlier pass — pure waste, compounding pass over pass. Added `self._verify_clean_tabs: set` (`__init__` ~line 663): a tab that finishes its scan-loop with zero mismatches AND no LLM-fallback trigger gets marked clean; the tab loop (~line 5118) skips clicking into any already-clean tab. Safe by construction — fills are pinned to their own tab via `prefer_key` (no cross-tab writes possible), and dead-marks (`_verify_dead_stable`) are permanent+global regardless of re-scan, so skipping loses nothing. Cleared on new-record reset alongside its siblings. Both fixes are `py_compile`+`pyflakes` clean. Fix #2 could NOT be offline-drilled (inline logic in a 260-line method wired to live UIA reads, not a standalone function like the hallucination guard) — needs a live run. **PROJECTED**: ~16.4 min → 6-9 min combined, no correctness logic touched. **RISK**: too aggressive a `STEP_DELAY` cut and an executor action could fire before the UI actually settles (click before dropdown opens, type before focus lands) → `no_change`/validation-failure regressions; 0.7 is a cautious first step down from 1.5, not the floor — if a live run shows no such regressions it can likely go lower, if it does, walk back up. **ALSO ADDED SAME SESSION**: a wall-clock duration metric on the BC scorecard (`scripts/bc_fidelity.py`) — `duration_seconds = submission_file.mtime − run_start_ts` (the latter was already threaded through for the stale-submission guard, so zero new instrumentation needed), printed in the scorecard, logged to the progress-log entry, and shown as a new `Dur(min)` column in `--progress`'s trend table, so speed-fix impact is now directly measurable run over run instead of hand-diffing log timestamps. **LIVE-TESTED 2026-07-15** (`run_20260715_161121.txt`): user watched it live, interrupted at step 105 saying "it keeps looping, it's an infinite loop." Investigation showed it was NOT unbounded — corrections trended 22→11→2→2→1 exactly like the clean baseline, and both stuck fields (`'Garaging Location'`, `'Title State'`) got dead-marked (termination guarantee firing) one log line before the interrupt; it would very likely have hit 0 on the next pass. `verify-skip-clean-tabs` itself worked as designed (`"0 mismatches, no LLM need — marking clean, skip on next pass"` fired repeatedly, e.g. Policyholder/Coverage/Drivers/Policy). BUT the user's real complaint held up under scrutiny: `'Title State'` got the identical fix (`→ 'California'`) on two consecutive passes before being dead-marked — see the new entry directly below for the confirmed root cause and fix. User's pushback ("in real workflows, it can't be doing that") was correct — bounded-but-wrong is still a real defect, not something to wave off as "well, it terminates eventually."

- [~] **Combobox write succeeds but next verify pass's read-back still sees a mismatch — 'Title State'/'Garaging Location' fixed twice before dead-marking [FOUND 2026-07-15; briefly closed then REOPENED same day 2026-07-16 with new evidence]** — surfaced live-testing the speed fixes (`run_20260715_161121.txt`), user-watched, called it an infinite loop, correctly pushed back on "bounded but still wrong" (`'in real workflows, it can't be doing that'`).
  - **FIRST HYPOTHESIS (WRONG)**: read/write identity desync, same shape as the 2026-07-14 checkbox bug — `_view_mismatches` trusting the observer snapshot's `elem['value']` instead of a live-resolved read. Implemented `self._live_field_value(elem)` (agent.py ~3211), wired into `_field_matches`/`_view_mismatches`, offline-proved on the real code path (`scratch/probe_live_field_value_real.py`, 4/4 pass). **Then live-tested (`run_20260715_173009.txt`) and it had ZERO effect** — `'Title State'` still got fixed twice (17:40:53, 17:41:59) before dead-marking, identical to before the fix. The hypothesis was wrong: the accessor `_live_field_value` reads (`_resolve_live_control` + `GetValuePattern().Value`) turned out to be the SAME one `_act_on_element`'s own write-verification already used, so moving where it's called changed nothing.
  - **REAL ROOT CAUSE, CONFIRMED LIVE** (`scratch/probe_setvalue_echo_or_real.py`, attached directly to the already-open form, no guessing): the combobox WRITE ladder (`_act_on_element`, agent.py ~6147) has 3 of its 4 rungs broken for this control class (a 50-state-style `wx.Choice` dropdown). **Rung 0** (direct `ValuePattern.SetValue`) throws a COM error every single time — reproduced live. **Rung 1** (expand+select-by-name) is a total no-op — this control doesn't support `ExpandCollapsePattern` at all (`GetExpandCollapsePattern()` returns `None`). **Rung 2** (native type-to-filter: `SendKeys(full_word)` + Enter) is NOT prefix matching on this widget — it's single-KEYSTROKE type-ahead, so typing `'California'` only responds to the LAST character sent and lands on `'Alabama'` (first state starting with the trailing `'a'`) — reproduced exactly live. Only **rung 3** (first-letter cycling, press `'C'` repeatedly) actually works — and when tested in isolation it succeeds AND **persists through a full tab-switch round-trip** (Vehicle→Policy→Vehicle), meaning the write mechanism itself isn't inherently flaky. Ruled out `combobox-substring-match` as the cause — `'California'` is an exact option in the dropdown, not a first-letter collision.
  - **FIX APPLIED**: skip rung 2 entirely for multi-character values (agent.py ~6218) — it can never legitimately succeed on this widget class and was actively perturbing the control's position (jumping it to a wrong letter) before rung 3 even started. Added diagnostic logging (`[COMBO] ... rung-4 first-letter-cycling hit ... after N press(es)` and an `ALL write rungs failed` warning) so the next live run shows exactly which rung fires and what value lands, closing the remaining gap with evidence instead of more guessing. `py_compile`+`pyflakes` clean.
  - **STILL OPEN, NARROWED FURTHER (2026-07-15, `run_20260715_181231.txt`)**: the diagnostic logging paid off immediately. Write reports success (`'california'`, 1 press) at 18:23:05; Vehicle isn't revisited again until 18:24:14 — **four** intervening tab switches (Coverage/Claims/Payment/Policyholder) and ~70s — and it's wrong again at 18:24:19. The isolated probe (`probe_setvalue_echo_or_real.py`) only tested ONE tab round-trip and it held; the real run does several. Since the write mechanism is proven solid (native Win32 combobox keypress, not a UIA echo — confirmed earlier), the leading suspect is now **stale-geometry misresolution**: `_resolve_live_control` disambiguates same-named duplicate nodes by nearest bbox-center distance — if the observed elem's `bbox` is stale from before an intervening scroll/tab-switch shift, the "nearest" live match could resolve to a DIFFERENT physical node than the one actually written to (this file already documents wx leaving hidden tab pages' duplicate controls in the tree as the root of the near-identical 2026-07-14 checkbox bug). NOT YET CONFIRMED — next step: log which live node `_resolve_live_control` actually picks (its bbox at call time) both at write time and at the later failed read, to prove/disprove wrong-node resolution directly instead of inferring from timing.
  - **PRACTICAL IMPACT, TWO CONSECUTIVE RUNS (2026-07-15)**: since the rung-2 fix landed, both live runs converged and submitted cleanly despite this bug still occurring — `run_20260715_181231.txt`: 28→2→4→0 corrections, **13.7 min**, submitted; `run_20260715_183114.txt`: 21→2→2→0 corrections, **12.6 min** (fastest yet), submitted. Both down from the 16.4 min baseline — the speed fixes (`STEP_DELAY` cut + `verify-skip-clean-tabs`) are delivering real wins. The dead-mark termination guarantee bounds this bug and it no longer blocks completion — **but it is NOT fixed**, just contained. Don't mistake "run submitted twice in a row" for "solved."

**CLOSED 2026-07-16, user call, not reproducing**: checked the latest run (`run_20260716_133531.txt`) for the three previously-flagged fields (`'Title State'`, `'Garaging Location'`, `'Collision Deductible'`) — all three filled correctly on the FIRST attempt this run, single press, zero retries, zero repeat-fix activity. Whole-run `Top mismatches` list was EMPTY (100% value accuracy across all 113 filled fields). No current live evidence this bug is still occurring. **Caveat noted before closing**: `_verify_pass` no longer runs at all (per the 2026-07-16 "no revisit" change), so the ORIGINAL discovery mechanism for this bug (verify re-fixing the same field across multiple passes) can no longer fire even if the underlying write flakiness still exists somewhere — it would now just silently ship wrong once with no visibility, rather than get caught and logged. User explicitly chose to close rather than chase a non-reproducing bug — reopen if the symptom (a field "succeeding" then reading wrong later) resurfaces in a future run.

**REOPENED 2026-07-16, same day**: found real evidence it's still happening, just no longer VISIBLE the same way. Investigating a user-reported "pauses at a certain point" speed complaint (via new temporary `observe()`-count instrumentation), found step 101 of `run_20260716_141849.txt` made **41 `observe()` calls in ~29 seconds** — turned out to be the SWEEP filling ~20 Coverage-tab fields in one un-tallied step. Of those ~20 fields, **9 needed a `"verify-at-fill retry ... didn't stick first try"`** — the SAME write-flakiness this bug was originally about. It didn't go away; the 2026-07-15 verify-at-fill fix just catches and silently retries it WITHIN the same step now, instead of leaking through to a later verify pass where it used to get discovered and logged as a mismatch. Cost: ~9 extra observe+refill cycles in one step alone — a real and current chunk of wall-clock time. AND: one retry still failed even after the extra attempt — `'Collision Deductible'` expected `'500'`, landed on `'5000'` (BC scorecard: `cov_collision_ded` mismatch) — see the combobox-numeric-substring-collision entry below for that specific bug and its fix. Net: the ORIGINAL write-flakiness (why does the first attempt often not stick) is still unexplained and still costing real time on every run; only its visibility changed. Status set back to partial, not done.

**NEW HYPOTHESIS 2026-07-16** (`run_20260716_144654.txt`): 100% correlation found — checked WHICH fields retry vs. don't in this sweep: `'At-Fault Accidents'→'0'`, `'Not-At-Fault Accidents'→'0'`, `'Total Accidents'→'0'`, `'Moving Violations'→'0'`, `'Comprehensive Claims'→'1'`, `'Total Claims Filed'→'1'` ALL retried; `'DUI/DWI on Record'→'NO'`, `'SR-22/FR-44 Filed'→'NO'`, `'License Suspended'→'NO'` in the SAME sweep never retried. Every retry is a short numeric value (likely SpinCtrl widgets); every non-retry is YES/NO or text. Not random flakiness — a specific widget-type pattern. **HYPOTHESIS**: the write genuinely succeeds, but the sweep's OWN verify-at-fill read-back (`self._observe()` called with ZERO settle delay immediately after `self._nav_fill_field` returns) races wx's UI update for SpinCtrl-style widgets specifically, reading a stale value before the widget visually commits — the retry attempt naturally has more elapsed time before ITS OWN check and succeeds, fitting a timing race better than genuine write unreliability. **CHEAP TEST APPLIED** (agent.py ~6225, the sweep's verify-at-fill site): added `time.sleep(0.15)` between the fill call and the read-back `observe()`, mirroring the settle sleeps the write rungs themselves already use after `SetValue`/`RangeValue` calls. NOT YET LIVE-TESTED — this is a hypothesis test, not a confirmed fix. If the next live run's retry count for these specific numeric fields drops to near-zero, the timing-race theory is confirmed; if retries persist at the same rate, the write itself is genuinely unreliable and this delay was the wrong lever.

**HYPOTHESIS PARTIALLY CONFIRMED THEN CONTRADICTED** (`run_20260716_150010.txt` then `run_20260716_152340.txt`): the 0.15s delay dropped retries 9→3 on the first follow-up run (supported the SpinCtrl timing-race theory) — but the VERY NEXT run showed retries back up to 8, and critically **all 8 were Coverage-tab COMBOBOXES** (`Bodily Injury (k$/k$)`, `Property Damage ($)`, `Collision Deductible`, `Comprehensive Deductible`, `UM/UIM Limit`, `Rental Limit`, `Total Premium ($)`, `Payment Frequency`), not the numeric SpinCtrl fields from before — contradicts a clean "widget type = SpinCtrl" explanation. **REAL ROOT CAUSE FOUND**: the sweep call site (agent.py ~6183) was DISCARDING `_nav_fill_field`'s own return value, then independently re-verifying via a FRESH `self._observe()` full-UIA-snapshot re-lookup by label + `_field_matches` — but the combobox rung-4 cycling ladder (`_act_on_element`, agent.py ~6624) already reads back the value off the SAME live `ctrl` object right after writing it and only returns `True` on a CONFIRMED match. The sweep's second, independent snapshot-based check was redundant and, for comboboxes specifically, prone to seeing a stale a11y-tree value (selection-changed events lag the snapshot) — flagging already-correct fields as "didn't stick". **FIX** (agent.py ~6183-6220): capture `_nav_fill_field`'s return as `_fill_confirmed`; when `True`, trust it and skip the redundant re-observe+re-check entirely (mark verified, refresh state, move on) — only fall through to the settle-delay + re-check + retry-once path when the write function itself couldn't confirm. `py_compile`+`pyflakes` clean (`ast.parse` OK, zero F821). Unifies both runs' observations: SpinCtrl (`RangeValuePattern`) and combobox (rung-4) writes both already self-verify on write; the redundant snapshot re-check was the actual source of false retries in both cases, not real write flakiness. NOT yet live-tested — next run should show retries drop toward zero for confirmed-write cases, with any REMAINING retries now being a genuine signal (the write path itself couldn't confirm), not a false snapshot-lag positive.

- [x] **Substring-match fix falsely matches numeric values whose digits are a PREFIX of another option ('500' inside '5000') [FOUND + FIXED 2026-07-16]** — from `run_20260716_141849.txt`: BC scorecard showed `cov_collision_ded` expected `'500'`, got `'5000'` — traced to the exact fill attempt in the log: `'[COMBO] Collision Deductible: rung-4 cycling (token 500) hit 5000 after 1 press(es) of 5'`. **ROOT CAUSE**: the multi-token substring-match fix (2026-07-15, built for TEXT like `'Pleasure'` inside `'Personal/Pleasure'`) checks containment both directions — but for NUMBERS this produces false positives: `'500'` IS literally a substring of `'5000'` (same leading digits), a digit-sequence collision, not a real semantic match. The form's actual Collision Deductible options (`car_insurance_form_wx.py`) are `['0','100','250','500','1000','2000','2500','5000']` — pressing `'5'` cycles between the two options starting with `'5'` (`'500'` and `'5000'`), and the loose substring check accepted whichever one it landed on first instead of requiring the real one. **FIX** (agent.py ~6602, same rung-4 cycling match block the apostrophe fix touched): added a numeric-exact-only guard — if the target value (after stripping `$`/`,`/`.` decoration) is purely digits, ONLY an exact match counts; the substring-containment checks are skipped entirely for numeric targets, forcing the cycle to actually land on the correct number instead of accepting a same-prefix decoy. Text values (the ORIGINAL reason the substring check exists) are unaffected — still get the full containment + apostrophe-insensitive matching. `py_compile`+`pyflakes` clean. Logic-drilled (4 cases): `'5000'` vs. target `'500'` no longer falsely matches; exact `'500'` vs. `'500'` still matches; the Pleasure text-substring case still works; the Associate's-Degree apostrophe case still works. All PASS. NOT yet live-tested against the real form.

- [x] **ROOT CAUSE FOUND for the Vehicle 'Color'/'Primary Use' mismatches — a genuine, GENERALIZABLE combobox-fill gap, not form-specific [2026-07-14, root-caused; FIXED + LIVE-TESTED 2026-07-15]** — **FIX LANDED** (agent.py ~6243, the same rung the 2026-07-15 diagnostic-logging pass for the Title State investigation already touched): the target value is now split into whitespace/slash/dash-separated tokens, tried longest-first (most specific = least collision-prone); EACH token seeds its own first-letter-cycling attempt (instead of only ever cycling on the whole value's own first letter); the match check now accepts substring containment in BOTH directions (target-in-option OR option-in-target), not just equality/prefix. Fully NO-HARDCODE-compliant — every keystroke sequence is driven by the record's own text and the widget's own observed readback, no option lists baked in. Token-generation logic offline-verified: `'Midnight Black'` → `['Midnight Black', 'Midnight', 'Black']` (the 3rd token `'Black'` correctly seeds a cycle that lands on option `'Black'` via containment); `'Pleasure'` → `['Pleasure']` (the only token correctly matches option `'Personal/Pleasure'` via containment, since `'pleasure' in 'personal/pleasure'`). `py_compile`+`pyflakes` clean. **LIVE-TESTED** (`run_20260715_185601.txt`, `--start_tab 2` drill — a cheap single-tab check, not a full run): every Vehicle-tab combobox filled correctly on the first press — `'Title State'`→`'california'`, `'Primary Use'`→`'commute'`, `'Garaging Location'`→`'private garage'`, `'Color'`→`'gray'`. **CAVEAT**: this drill's record had `Color = 'Gray'` exactly, not `'Midnight Black'` — the multi-word substring-token path itself wasn't actually exercised, only the plain first-letter path. User explicitly accepted this as sufficient evidence to move on rather than chase a live substring-collision case — marked done on that basis. Original root-cause writeup below, preserved for context.
 — BC top-mismatches showed `v_color: expected 'Midnight Black', got 'Maroon'` and `v_usage: expected 'Pleasure', got 'Personal/Pleasure'`, live-reproduced multiple times. Confirmed the intake record's raw text is correct (`Color: Midnight Black`, `Primary Use: Pleasure`) — the bug is NOT a crosstalk/record-lookup issue this time. Checked the form's actual dropdown option lists (`car_insurance_form_wx.py`): `COLORS = [...,"Maroon",...]` has NO "Midnight Black" (closest real option: plain "Black"); `USAGE = ["Personal/Pleasure",...]` has NO plain "Pleasure" (closest: "Personal/Pleasure", which is ALSO the combobox's hardcoded DEFAULT value). The record's free-text value doesn't exactly match ANY dropdown option string. ROOT CAUSE: the combobox-fill ladder's last-resort fallback — first-letter cycling (agent.py ~line 6085, built earlier this project for the 50-state dropdown, since wx.Choice matches single keystrokes not full text) — takes the TARGET value's first letter and cycles to whatever option starts with it. "Midnight Black" → 'M' → the ONLY M-starting color is "Maroon" → lands there, reports success (a valid option got selected, just the WRONG one). "Pleasure" → 'P' → the only P-starting option is "Personal/Pleasure" → lands on the pre-existing default, looks unchanged either way. Not random noise — a deterministic first-letter COLLISION between the intended value and an unrelated option. **THIS LIKELY EXPLAINS WHY THESE SPECIFIC FIELDS NEVER CONVERGE IN VERIFY EITHER** (see the convergence-bug entry above, same session): if first-letter-cycling deterministically picks the WRONG option every single attempt (not stochastically), verify's re-fix attempts via the identical mechanism would fail identically forever, independent of any dead-mark bookkeeping issue — two separate bugs compounding into the same visible symptom (fields that never settle). **GENERALIZATION NOTE (user-asked directly)**: this is NOT a car-insurance-form-specific quirk — ANY form with dropdown fields and free-text source data will hit "record value doesn't exactly match a fixed option list" as a matter of course. The proper fix (try substring/superstring matching — is the record's value CONTAINED IN an option, or does an option CONTAIN the record's value, case-insensitive — before ever falling back to blunt first-letter-cycling) is a general improvement to the SHARED fill pipeline (`_act_on_element`'s combobox ladder), not a per-form patch — directly on-mission for the project's stated generalization goal. DO: add a substring-match rung to the combobox ladder BEFORE the first-letter-cycling rung (rung 4, agent.py ~6085) — e.g. rung 3.5: for each dropdown option, check if `record_value.lower() in option.lower() or option.lower() in record_value.lower()`; prefer the longest/most-specific match. "Midnight Black" contains no COLORS option as a substring (still a genuine gap — Black IS the closest concept, but "black" isn't a substring of "midnight black"... wait it actually IS: "midnight black".contains("black") — so a substring check WOULD correctly resolve this to "Black"). "Pleasure" is a substring of "Personal/Pleasure" — substring check resolves this correctly too. Both mismatches would be FIXED by this one general rule.

- [x] **Rerun the full ×10 pass to validate everything built since the last one [DONE 2026-07-15]** — `run_20260715_195128.txt`, 19:51-22:15 (~2h24m wall-clock). **10/10 records SUBMITTED**, all natural termination (verify converged to 0 on every record — no crashes, no manual interrupts), value accuracy 97-100% on every record. **ZERO crosstalk-signature mismatches** (no Driver 2/3 label-collision pattern anywhere in the 10 top-mismatches lists) — the bar this run existed to clear is CLEARED. Per-record durations: 12.3, 12.2, 14.9, 13.2, **20.3 (outlier)**, 13.6, 13.9, 14.1, 15.2, 12.4 min — average 14.2 min/record. Residual mismatches found are already-tracked, SEPARATE problems, not crosstalk:
  - `cov_collision_ded` wrong on 3 records (500 vs 100, 1000 vs 0) — same `combobox-write-readback-desync` flakiness, still open.
  - `v_color`/`v_usage`/`v_transmission` "mismatches" (e.g. `'Midnight Black'` expected, `'Black'` got) are actually the substring-match fix **working correctly** — it picked the best real option available; the scorer grades against literal intake text with zero tolerance for "closest available option," which isn't an agent bug.
  - **NEW, unexplained**: `ph_education: expected 'Associate Degree', got 'Some College'` (record 5) — doesn't fit any understood bug shape (not a first-letter collision, different starting letters entirely; not an obvious crosstalk pattern). Filed below, not yet investigated.
  - **SPEED NOTE (the reason this took 2h24m to run)**: this total is itself the strongest evidence yet for pushing speed further — even after cutting single-record time 16.4→12-13 min, ×10 scale still means hours of live-test budget per validation pass. Sub-1-minute-per-record isn't reachable by tuning delays further (floor math: ~150-200 steps × ~0.3-0.5s minimum per step, mostly UIA `observe()` overhead, not `sleep` calls ≈ 1.5-3 min floor with the current per-step-observe architecture) — would need batching multiple field fills per observation instead of re-observing after every single action, a real architectural change, not another tuning pass.

- [x] **`'Education Level'` wrong value — apostrophe mismatch broke the combobox substring-match, not a first-letter collision [FOUND 2026-07-15, FIXED 2026-07-16]** — from the ×10 rerun, record 5: `ph_education` (Policyholder tab, non-repeated-section field) expected `'Associate Degree'`, got `'Some College'`. Doesn't fit the multi-token substring fix's shape (`'Associate'` and `'Some'` start with different letters), and Education Level isn't a repeated-section field so unlikely to be crosstalk. **ROOT CAUSE FOUND** (read the actual log around record 5, line 11271): `'[COMBO] Education Level: ALL write rungs failed for value Associate Degree — falling through to legacy click/paste path'` — the fallback landed on `'Some College'` (likely whatever the combobox already held, since the legacy paste path silently no-ops on a non-editable combo). Checked the form source (`car_insurance_entry/car_insurance_form_wx.py:53-55`): the real dropdown option is `"Associate's Degree"` (WITH the possessive apostrophe) — the intake record's value `'Associate Degree'` has NO apostrophe. The multi-token substring-match fix (2026-07-15) already cycles to the right option (`"Associate's Degree"` IS reached by pressing `'A'`), but the match-check compares against the RAW value/option strings — `'associate degree'` is NOT a substring of `"associate's degree"` because the possessive `'s` sits right between `'associate'` and `'degree'`, breaking direct containment. Every OTHER education value in the intake data happens to already include the apostrophe in both the record text and the form option, so this only ever surfaced for this one specific case — genuinely a general punctuation-sensitivity gap, not something specific to "Associate". **FIX** (agent.py ~6596, the rung-4 cycling match check): added an apostrophe-insensitive comparison alongside the existing exact/substring checks — strips `"'s"`/`'’s'` (the possessive suffix as a unit, not just the bare apostrophe — stripping only the apostrophe character left a stray `'s'` that broke the match on the first attempt, caught by the drill before shipping) then any remaining bare apostrophes, on BOTH sides, before the containment check. `py_compile`+`pyflakes` clean. Logic-drilled (4 cases): the exact live case (`'associate degree'` vs. `"associate's degree"`) now matches; exact-match cases (`"bachelor's degree"`) still work; the prior substring fix (Pleasure/Personal-Pleasure) still works; unrelated values still correctly rejected. All PASS. NOT yet live-tested against the real form (this record's specific value doesn't come up on record 1, the default test record — would need `--start_record 5` or similar to re-exercise it live).

- [x] **`_merge`'s transformer-click-override silently discarded a resolved value for ANY fillable field, not just comboboxes — 3m38s / 166-repeat fixation on 'Accidents (3 yr)' [FOUND + FIXED 2026-07-16]** — live-caught during a `--start_tab 6` (no lock) Drivers-tab drift (`run_20260716_022849.txt`) — user: "what the fuck was that loop", rightly alarmed it looked like a generalization failure. It wasn't. **ROOT CAUSE**: two separate parts of the code disagree about type-vs-click, and the wrong one won. "Option B" (agent.py ~1338, documented, intentional) decides fill-vs-navigate purely from the FOCUSED WIDGET'S TYPE — specifically BECAUSE the transformer's own action-type head is known-unreliable (whipsaws click↔keyboard across retrains, per the adjacent comment). It correctly identified `'Accidents (3 yr)'` as a fillable empty spin control needing TYPE, and resolved its real value `'0'` with zero LLM calls (`Deterministic value: 'Accidents (3 yr)' → '0'`). But `_merge` (agent.py ~4627) has its OWN, separate override rule: "if the transformer says click with 92%+ confidence, trust it over the LLM's type decision" — built for comboboxes (which genuinely need a click before you can select an option) but applied UNCONDITIONALLY to every focused fillable. The transformer's action-type head said "click" at conf=0.94 for this plain SpinCtrl field; `_merge`'s override fired, discarded the resolved `'0'` entirely, and returned a bare click with NO text at all — a click can never enter a value into an edit/spin control, so the field stayed empty forever, and the model kept re-anchoring the optimal-viewport-jump on the same still-empty field: **166 occurrences** of `'Accidents (3 yr)'` across **3m38s continuous**, the fixation-escalation safety net never firing once (0 `'FIXATED'` log lines in the whole run). `'0'` being a falsy-LOOKING but genuinely valid answer (zero accidents) made this especially visible, but the underlying bug hits ANY value on ANY non-combobox fillable the transformer is 92%+ confident about clicking — not zero-specific. **FIX** (agent.py ~4627): added a focused-element-type check inside `_merge` itself (`_foc_is_combo`, looked up the same way the OPT2 caller already does) — the transformer-click-override now ONLY fires when the focused field is actually a `comboboxcontrol`, restoring the override to the one case it was designed for and letting edit/spin/checkbox fields keep their resolved type+value. `py_compile`+`pyflakes` clean. **LIVE-PROVED on the real `_merge` method** (no live UI, calls the actual function directly, twice): Case 1 (plain SpinCtrl field, transformer says click conf=0.94, LLM resolved `'0'`) now correctly returns a TYPE action with `text='0'` preserved — the exact live bug, fixed. Case 2 (combobox field, same transformer confidence) still correctly overrides to a click — the original, intended behavior for comboboxes is unchanged. Both PASS. **LIVE-CONFIRMED 2026-07-16** (`run_20260716_024515.txt`, `--start_tab 4 --only_tab` Drivers-tab drill): `'Violations (3 yr)'` → `'0'` typed correctly, `'Accidents (3 yr)'` → `'0'` selected correctly, another driver's `'Violations (3 yr)'` → `'1'` also correct — zero fixation, zero repeat spam, the exact bug from `run_20260716_022849.txt` is gone. Lock + auto-stop also held (3 blocked attempts, clean stop at step 14).

**FOLLOW-ON BUG FOUND SAME DAY** (`run_20260716_124336.txt`, while investigating the missing `'Discounts'` section for a Navigation Protocol question): `'Collision Deductible'`/`'Comprehensive Deductible'`/`'Bodily Injury (k$/k$)'`/`'Property Damage ($)'` all abandoned as `"leave-blank/empty — Tab past (skip)"` in a row on the Coverage tab, DESPITE having real resolved values (`'500'`/`'250'`/`'100/300'`/`'100,000'`) — this Coverage-tab abandonment (not a scroll-reach failure) is very likely what actually caused the GAP-forced tab-advance off Coverage before Discounts was ever reached. **ROOT CAUSE**: restricting the merge override to comboboxes (the fix above) was correct, but the DOWNSTREAM OPT2 leave-blank guard (agent.py ~1396) wasn't updated to match — it reads ANY merged prediction with no `"text"` field as "the value resolved to blank," but a combobox-override click legitimately has no text field at all (a click is step one of opening/selecting a combobox, not a decision that the value is blank). Every legitimate combobox click-override was being misread as a blank-skip, abandoning a real value. **FIX** (agent.py ~1396): the leave-blank check now also requires `prediction.get('action_type') != 'click'` — a click is never treated as blank regardless of missing text; only genuine type/keyboard actions with empty/blank-marker text still skip, exactly as originally designed. Logic-verified (4 cases: click-with-no-text passes through, genuine-empty-type-text still skips, real-value-type-text passes through, explicit `'(leave blank)'` marker still skips) — all PASS. The full method is deep in the 400+-line main step loop, not unit-testable in isolation, so this is a condition-level drill, not a full-method one.

**LIVE-CONFIRMED 2026-07-16** (`run_20260716_131631.txt`): the fix worked exactly as designed — NONE of the previously-broken fields (`Collision Deductible`/`Comprehensive Deductible`/`Bodily Injury`/`Property Damage`) got falsely abandoned this run, every leave-blank entry now looks like a genuinely absent record value, and no more `'[GAP] LLM stuck re-picking — forcing unvisited tab'` force-abandonment on Coverage either. **BUT**: Tab Coverage stayed at 90.9% (Discounts STILL missing) and Field Match Rate barely moved (61.3% vs. 62.0% before) — proves this merge/leave-blank bug and the missing-Discounts gap are two separate, unrelated problems, not cause-and-effect. This specific fix (merge-click-override + leave-blank guard) is DONE and confirmed; the Discounts gap turned out to be a completely different bug (below).

- [x] **Checkbox click handler force-checked EVERY clicked box regardless of the record — not a scroll gap, a missing record-lookup [FOUND + FIXED 2026-07-16]** — first hypothesis was a scroll-to-bottom gap (tab-"done" firing before reaching the below-fold Discounts section). Checked the actual log, not just the aggregate metric, before touching code: Discounts WAS visited and worked on this run — `'Multi-Car'`, `'Good Driver (5+ yr clean)'`, `'Multi-Policy / Bundle'`, `'Good Student'` all got `BM_SETCHECK` activity. Not a scroll/reveal gap at all. **REAL FINDING** (cross-checked against `data_entry_tasks/data_entry_intake.txt`, record 1's actual Discounts values): `'Multi-Car'` (record: NO) got WRONGLY checked, `'Good Student'` (record: NO) got WRONGLY checked, `'Loyalty Discount'` (record: YES) never got touched at all — only `'Good Driver'`/`'Multi-Policy'` (both record: YES) came out correct, and only by coincidence. **ROOT CAUSE** (agent.py ~1824, the main per-step "click landed on a checkbox" handler): this path unconditionally forced EVERY clicked checkbox to CHECKED via `BM_SETCHECK`, never consulting the record at all — it only guarded against re-clicking an ALREADY-checked box (double-toggle), never against checking a box the record says should stay NO. A different, less careful implementation than the sweep's own checkbox path (`_act_on_element`), which already does the record lookup correctly — this per-step path just never had it. Explains the Discounts wrong-checks exactly, and likely explains a much bigger slice of the overall Field Match Rate gap (62%) since checkboxes are everywhere on this form (Vehicle safety features, Coverage's Additional Coverages, etc.), not just Discounts — any checkbox the transformer's pointer ever landed on outside the sweep's own path was getting force-checked regardless of the correct answer. **FIX** (agent.py ~1824): added a record lookup (`_lookup_field`/`_detect_section`, same pattern used everywhere else) BEFORE deciding to check a box — only checks ON when the record actually says yes/checked/true/1; a record value of NO/absent now leaves the checkbox at its unchecked default, matching the sweep's own already-correct behavior. `py_compile`+`pyflakes` clean. Condition-level logic drilled (3 cases: record NO → no check, record YES → checks, record absent → no check) — all PASS; the surrounding code is deep in the main step loop, not unit-testable as a whole. **STILL SEPARATE AND OPEN (at time of writing)**: `'Loyalty Discount'` never got navigated to at all in `run_20260716_131631.txt` — the OTHER open question from the same session ("transformer misses available empty fields on screen").

**LIVE-CONFIRMED 2026-07-16** (`run_20260716_133531.txt`): fix works exactly as designed. `'Multi-Car'` (NO) and `'Good Student'` (NO) now correctly left unchecked; `'Good Driver'`/`'Multi-Policy'`/`'Loyalty Discount'` (all YES) all correctly checked THIS TIME, including Loyalty Discount which was missed entirely last run (transformer navigated to it fine this run — may have been model-order variance, not a separate bug; keep an eye on it but not chasing further right now). **Field Match Rate jumped 61.3% → 69.3%** (100→113/163), BC Score 47.6% → 49.7% — confirms the checkbox bug really was a large chunk of the overall gap, not just Discounts-specific. Duration 9.9 min (vs. 7.9 min best case) — within normal run-to-run variance, not a regression. **ONE ODD LEFTOVER**: Tab Coverage still reports 90.9%/Discounts still "missing" in the aggregate metric, DESPITE every Discounts checkbox now confirmed correct in the log — strongly suggests `scripts/bc_fidelity.py`'s own tab/section-detection has a bug recognizing "Discounts" as a counted section, separate from agent behavior entirely. Filed below, low priority, not blocking.

- [ ] **BC scorer's Tab Coverage never counts `'Discounts'` even when every field in it is confirmed correct — likely a scorer bug, not an agent bug [FOUND 2026-07-16]** — all 8 Discounts checkboxes confirmed correct in the agent log (`Multi-Car`/`Good Student` correctly left unchecked, `Good Driver`/`Multi-Policy`/`Loyalty Discount` correctly checked) — yet Tab Coverage still reports 90.9% with `'Discounts'` absent from the covered-sections list, same as every prior run regardless of actual correctness. Since the underlying field-level behavior is now proven correct, this points at `scripts/bc_fidelity.py`'s own section/tab classification logic (whatever builds the covered-tabs list for the metric) not recognizing `'Discounts'` as a valid/countable section at all — a scorer-side gap, not an agent behavior gap. DO: read `bc_fidelity.py`'s tab-coverage computation, check what determines section membership for the "Discounts Applied" subsection specifically (likely a prefix-matching or section-name mapping that doesn't include `disc_` fields or the "Discounts" section label). Low priority — doesn't affect real agent behavior, only a reported metric's accuracy.

- [x] **Extend deterministic short-circuit into verify's own LLM-fallback branch [task-list item #4, FOUND + FIXED 2026-07-15]** — user asked "how do we increase speed, goddamn" after the ×10 rerun logged 2h24m total; analyzed record 1's verify tail specifically and found the concrete next lever. The Claims tab fired **16 separate real LLM round-trips** (`[NAV] protocol` calls) across just 3 verify passes, each resolving at most one field before the next pass rediscovered the same gap. Root cause: verify's own `_needs_llm` scan (agent.py, the LLM-fallback branch inside `_verify_pass`) stopped at the FIRST empty non-dead field found and called the real LLM (`_navigation_protocol`) for it — even though THAT function's own opening short-circuit (deterministic value / confirmed-absent, built 2026-07-14 for the sweep) would resolve most such fields without an LLM call anyway; it was just never applied to verify's own gate-check first. A confirmed-absent field (record present, genuinely nothing for it) had no dead-mark and no attempted-mark, so it sat there forever, re-triggering a real LLM call on every single pass. **FIX** (agent.py ~5297): the `_needs_llm` scan now runs the SAME section-aware deterministic lookup verify's own branch-1 and the sweep's bucket-2 short-circuit already use, per candidate field, before ever deciding an LLM call is needed — resolvable fields get filled directly (`self._nav_fill_field`, no LLM), confirmed-absent fields get `self._mark_attempted`'d and settled permanently (no LLM, and critically no longer re-flagged on future passes), and only genuinely AMBIGUOUS fields (`_detect_section` returns `None`) still reach the real LLM. `py_compile`+`pyflakes` clean. **LIVE-PROVED on the real code path** (`scratch/probe_verify_llm_fallback_shortcircuit_real.py` — constructs a real `LLMAgent`, calls the ACTUAL `_verify_pass` method, no live UI needed; `_navigation_protocol` monkeypatched to count calls instead of making one): a 3-field fake view (one resolvable, one confirmed-absent, one ambiguous) — resolvable field filled deterministically with zero LLM calls, confirmed-absent field settled via `mark_attempted` with zero LLM calls, ambiguous field correctly still reached the LLM fallback exactly once. All conditions pass. NOT yet live-tested against the real form — should cut a meaningful chunk of the ~14.2 min/record average, especially on tabs with several genuinely-blank optional fields (Claims/Discounts/Other).
  - **REGRESSION FOUND + FIXED SAME DAY (2026-07-16)**: user live-tested via a Claims-tab drill (`run_20260716_014653.txt`) and hit exactly the looping-forever complaint this fix was supposed to eliminate — `'Years Continuously Insured'` fired **8 identical** `'deterministic fix'` log lines in under 30 seconds (the write wasn't sticking, but this new path had ZERO retry cap, unlike branch 1's existing `_verify_fix_count`/`_dead_fill_keys`/`_verify_dead_stable` discipline), and `'Third Party Name'`/`'Third Party Policy'` each `'settled'` **5+ times in a row** — the scan's dead/attempted exclusion check only tested `_dead_fill_keys`, never `_attempted_keys`, so `mark_attempted`'s own bookkeeping was silently ignored on the very next scan iteration. **FIX**: added the missing `k in self._attempted_keys` check alongside the existing dead-keys check, and copied branch 1's exact 2-tries-then-dead-mark counter (`_verify_fix_count` + stable `(tab, label)` key) onto the deterministic-fill path. `py_compile`+`pyflakes` clean. Re-drilled on a TARGETED regression scenario (`scratch/probe_verify_shortcircuit_no_infinite_repeat.py` — simulates a write that never sticks plus a confirmed-absent field, across the REAL `_verify_pass` method's full 8-scan-per-tab loop, not just one static observation): fill attempts now cap at 2 (not 8), settle happens exactly once (not 6 times) — both PASS. **LESSON (self-inflicted, acknowledged directly to the user)**: shipped this fix straight to a live test without a repeat-scenario drill first — the original drill only exercised a single static observation and never simulated the SAME field staying empty across multiple scan iterations, which is exactly where the missing retry-cap bug lived. Cheap-verify-first has to mean testing the ACTUAL failure shape, not just the happy path, or it gives false confidence and burns the user's live-test budget anyway. **LIVE-CONFIRMED 2026-07-16** (`run_20260716_025101.txt`, full single-record run — `only_tab` drills can't reach verify at all since the lock guards `_confirm_finished` to always return `False`, so this needed a real run): Claims tab fixed 4 real mismatches in pass 1, then `'[VERIFY] Claims: 0 mismatches, no LLM need — marking clean, skip on next pass'` in pass 2 — ZERO LLM calls wasted on Claims after that, vs. the original 16 round-trips across 3 passes. Whole run converged 23→2→3→0, submitted, **10.2 min — fastest yet** (down from 16.4 min baseline). No repeat-spam regression. Both the original fix and the regression fix are now proven on the real form, not just drills.

- [x] **`--start_tab` drills wander off the target tab — built a real lock [FOUND 2026-07-15, FIXED 2026-07-16 after 3 rounds]** — user hit this THREE times total.
  - **ROUND 1**: patched the main-loop tab-click-detection site with a snap-back-to-locked-tab override, plus guarded `_confirm_finished`. Live-tested, STILL FAILED — "navigated away again" — because the SWEEP has its OWN separate tab-advance code path (`'[NAV] tab swept clean → switch tab'`) that executes a click directly, never going anywhere near the patched site.
  - **ROUND 2**: patched that specific sweep site too. But a grep afterward found **5+ total places** that can click a tab (prediction-driven, sweep ×2, fixation-recovery, GAP-driven, `_try_advance_tab`) — chasing one at a time as the user hit each live was never going to converge.
  - **ROUND 3 — REAL FIX**, mirrors this project's own Submit-chokepoint pattern exactly: reverted both point-patches, wrapped `self._executor.execute` ONCE (agent.py ~911, right next to the existing Submit guard) — every one of the **44 call sites** that route through `self._executor.execute`, regardless of which internal function calls it, now gets checked against `self._other_tab_bboxes` (all tab-strip bboxes except the locked one, accumulated across steps the same way `_submit_bboxes` already is) via the new `_point_on_other_tab` helper (~5891, mirrors `_point_on_submit` exactly) and blocked if it lands on a different tab. **LIVE-TESTED** (`run_20260716_021901.txt`): lock WORKED — zero `'Tab click detected'` lines, 7 separate navigation attempts (Policy/Policyholder/Vehicle/Coverage/Drivers/History/Payment) all correctly BLOCKED, the run never actually left Claims.
  - **NEW GAP FOUND SAME TEST**: once the locked tab was genuinely done, the agent had nowhere else to go and tried every other tab in turn, one per step, each blocked — 7 wasted ~3-4s cycles (~22s of pure spin) before the user interrupted, exactly the "so much wasted steps" complaint. **FIX**: `self._only_tab_blocked_streak` counter (agent.py ~903) increments on each blocked navigation click, resets on any real action; the main step loop (~939) now breaks cleanly once the streak hits 3, logging `'[ONLY-TAB] locked tab has nothing left to do'` instead of spinning until `max_steps` or a manual interrupt.
  - `py_compile`+`pyflakes` clean on both files (`agent.py`, `run_task.py`). **LIVE-CONFIRMED 2026-07-16** (`run_20260716_022508.txt`): 3 blocked navigation attempts (Policy/Policyholder/Vehicle), then `'[ONLY-TAB] locked tab has nothing left to do (3 consecutive blocked navigation attempts) — stopping drill'` fired and the run ended CLEANLY on its own (17 steps, `'Run ended'` — no manual interrupt needed this time). Zero `'Tab click detected'` lines — never actually left Claims. Both the lock and the stop-when-done fix are proven live. User: "it actually fucking worked, nice."

- [x] **Verify-at-fill, done right — sweep fills went unchecked until the separate end-of-run verify pass caused back-and-forth tab revisits [FOUND + FIXED 2026-07-16, owner: Akuras Kurasa]** — user-flagged directly, with a precise definition of "done": *"consider verify-at-fill to work it has to simply check once after filling, that's it done, we don't navigate back-and-forth and back-and-forth resulting in wasted time loops."* Grounded in real evidence before fixing: `run_20260716_025101.txt` showed `'Garaging Location'`/`'Title State'` (Vehicle) and `'Collision Deductible'` (Coverage) filled once during the main sweep, never checked at that moment, then discovered wrong minutes later by the SEPARATE `_verify_pass` — which re-walks all 8 tabs per attempt, so fixing 3 fields cost 3 extra full-form passes (23→2→3→0 corrections) instead of being caught once at the source. **FIX** (agent.py, sweep's fill call site ~6089): after every sweep fill, do ONE immediate read-back check via `_field_matches` (same tab, no navigation needed) — if it stuck, mark it in a new `self._verified_at_fill` set (`_attempt_key`-keyed, same identity scheme as `_dead_fill_keys`); if it didn't, retry ONCE right there before moving on. Wired into `_view_mismatches` (agent.py ~3399, right next to the existing `_dead_fill_keys` skip) so the end-of-run verify pass TRUSTS already-fill-verified fields instead of rediscovering them cold. `_verified_at_fill` cleared on new-record reset alongside its siblings. `py_compile`+`pyflakes` clean. **LIVE-PROVED on the real `_view_mismatches` method** (no live UI): a field with a genuinely wrong snapshot value is correctly flagged when NOT yet verified-at-fill, and correctly SKIPPED once marked verified-at-fill (even against the same stale-wrong snapshot) — both PASS. **CAVEAT**: only wired into the SWEEP's fill path, not the main per-step OPT2 fill path where most ordinary fills happen — if back-and-forth persists after this on a live run, that's the next site to extend it to. Also uses the same `_attempt_key` geometry scheme already known to be flip-prone on repeated-section tabs (Driver 2/3) — lower risk here since Vehicle/Coverage aren't repeated-section, but worth watching.

**LIVE-TESTED 2026-07-16** (`run_20260716_032607.txt`) — **caveat confirmed as a real problem, user furious**: 16 verify-pass corrections were mostly CHECKBOXES (`'Homeowner'`, `'Salvage Title'`, `'ABS Brakes'`, `'Paperless / e-Delivery'`...) filled via the ordinary per-step OPT2/checkbox/combobox paths the sweep-only fix never touches — exactly the gap flagged above, now proven live. Also: the sweep-side fix itself flagged "didn't stick first try" on **15 different fields** in one run (`Bodily Injury`, `Property Damage`, `Collision Deductible`, `DL Number`, `Accidents (3 yr)`...) — most of this reflects a genuine, previously-underestimated write-reliability issue (same family as `combobox-write-readback-desync`) rather than a bug in the new check itself. **REAL FIX**: rather than duplicate the same check at every scattered fill call site (OPT2 type, checkbox toggle, combobox click-fill — the exact whack-a-mole that took 3 rounds for the tab-lock), built ONE chokepoint in the main step loop instead (agent.py, right after `_record_attempt` ~line 2180): whatever field was FOCUSED when the step's action was decided is the field that step acted on, regardless of which internal mechanism filled it — if its post-action value matches the record, mark it verified via the SAME `self._verified_at_fill` set the sweep-side fix already populates. This covers checkboxes/comboboxes/edit fields uniformly, no matter which of the many fill paths touched them. `py_compile`+`pyflakes` clean.

**LIVE-TESTED 2026-07-16** (`run_20260716_115224.txt`) — **still not enough**: `'Paperless / e-Delivery'`, `'Vehicle Condition'`, `'Lienholder/Lender'`, `'DL Issuing State'` and others STILL showed up as verify-pass corrections (14 in pass 1, **5 total passes** — worse than the 4-pass baseline). **ROOT CAUSE OF THE MISS**: the chokepoint only checked the FOCUSED element — but checkboxes and click-filled comboboxes often DON'T register a UIA focus change the way typed edit fields do, so the field that was actually just filled was never the one the chokepoint was looking at. **FIX**: added a SECOND signal alongside focus — the field actually CLICKED this step, resolved via `_elem_at(state, click_position)` (agent.py ~2137, already computed earlier in the same loop for a DIFFERENT purpose, focus-inference — reused here rather than recomputed), then re-resolved in `state_after`. Checks BOTH the focused element and the clicked element each step; either one matching the record gets marked verified. `py_compile`+`pyflakes` clean. Partially drilled (confirmed `_elem_at` correctly resolves a non-focused checkbox by click position on the real method) — the full chokepoint logic is inline in the massive step loop, not a standalone function, so a complete real-code-path drill isn't practical the way the sweep-side fix's drill was.

**LIVE-TESTED 2026-07-16** (`run_20260716_121508.txt`) — **still failed**: 20 corrections in pass 1, 5 total passes — worse than ever, despite the click+focus chokepoint. Three separate attempts at making the multi-pass verify loop smarter about not re-flagging good fields all failed live. **USER PIVOT (explicit, final)**: stop trying to make revisits smarter, ELIMINATE them — *"I want that it won't go back to the other tabs, if it missed it, it missed it."* **REAL FIX** (`_confirm_finished`, agent.py ~5610): added `self._verify_ran_once` (bool, cleared per-record). `_verify_pass` still walks all 8 tabs ONCE and fixes what it finds in that single walk-through (that's the one-and-only check, not a revisit) — but `_confirm_finished` now submits unconditionally after that ONE call, whether `_verify_pass` returned clean or not, instead of looping back for another full pass when corrections were found. Whatever's still wrong after the single pass is accepted, not chased further. The old CONVERGENCE GATE (same-fields-twice-in-a-row acceptance logic) is now dead code (only one pass ever happens) but left in place, harmless. `py_compile`+`pyflakes` clean. **LIVE-PROVED on the real `_confirm_finished` method** (no live UI, `_verify_pass` mocked to always report gaps): called twice in a row, `_verify_pass` fired exactly ONCE across both calls, both calls returned `True` (submit) — confirms no revisit happens regardless of how many times `_confirm_finished` gets invoked. This directly delivers the user's stated definition of done. NOT yet live-tested against the real form. **NOTE**: this trades completeness for speed by design (user's explicit call) — a field with a flaky write (`combobox-write-readback-desync`) will now simply stay wrong if the single pass catches it wrong, no second chance; watch BC score's field-match-rate/value-accuracy on the next real run to see the actual cost of that tradeoff.

**STILL FAILED 2026-07-16** (`run_20260716_123115.txt`, user interrupted mid-pass): the ONE-PASS version still clicked back into Policy/Policyholder/Vehicle (tabs the main fill loop had already finished) to run its single read-back-and-fix walk — *"it still returned after already filling a certain tab, I don't want it to do that."* Not a revisit-COUNT problem anymore — ANY tab-walk after the main fill phase moved past a tab is unwanted, even the first and only one. **FINAL FIX** (`_confirm_finished`, agent.py ~5614): `_verify_pass` is no longer called AT ALL — once every tab has been visited during the main fill, submit immediately. Whatever the main fill pass produced on each tab is final, no second look for any reason.

**USER FOLLOW-UP QUESTION (important, answered)**: *"why was VERIFY a hard-coded process, I said no hard-coded thing whatsoever"* — clarified the distinction between two different things this project calls "hardcode": (1) baked-in field/tab names or value lists (the HARD RULE) — `_verify_pass`'s code was never this, it's generic, driven by label/type/section detection, not fixed strings; (2) a hardcoded *procedure* — the agent unconditionally deciding to visit every tab in a fixed order after main fill, which is a WHERE-decision made by hand-written agent code, not the transformer. `_verify_pass`'s call site WAS this second kind — the same category as the already-stripped `_try_advance_tab`/`_focus_first_empty_field` WHERE-crutches from earlier this session. Removing the call fixes both complaints at once: no more agent-authored fixed navigation loop, and no more tab revisits.

**LIVE-PROVED on the real `_confirm_finished` method** (no live UI, `_verify_pass` mocked to assert-fail if called): called twice in a row, `_verify_pass` invoked ZERO times, both calls returned `True` (submit) immediately once tab-coverage was met. `py_compile`+`pyflakes` clean. `_verify_pass`/`_verify_ran_once`/`_verified_at_fill` left in the file as dead code (not deleted) in case this trade needs reversing once real accuracy numbers come back. NOT yet live-tested against the real form — this is the actual final form of the fix, pending confirmation it holds live.

- [x] **`_STALL_LIMIT` 6→3 tried and reverted — more frequent stalls, same total waste [2026-07-16]** — speed lever proposed: 6 "STUCK 6 steps" events in one 115-step run each burned 6 non-productive steps before recovery (jump/sweep) kicked in — 36 wasted steps, ~2-3 min of a ~6.5 min main-fill phase. Cut `_STALL_LIMIT` (agent.py ~886) from 6 to 3 on that reasoning. **LIVE-TESTED** (`run_20260716_115224.txt`): made it WORSE, not better — 17 STUCK events fired (vs. 6 before) but total duration was UNCHANGED (10.4 min vs. 10.2 min baseline). **LESSON**: the per-STUCK-event recovery cost (jump computation + sweep) is roughly constant regardless of the trigger threshold — firing sooner-but-more-often just redistributes the waste, it doesn't reduce it. REVERTED back to 6 same day. The real speed lever is elsewhere (the recovery mechanism's own fixed cost, or per-step overhead independent of stall handling) — not this threshold. Do not retry this specific lever without a different theory first.

- [~] **Verb-loop rewrite — scoped 2026-07-16, Stage 1 (audit) started same day, one real speed fix already landed from it** — user asked to scope (not implement) the architectural fix that retires the LLM-driven navigation system entirely. **WHAT THIS IS**: today the trained model already predicts a verb+pointer each step (`semantic_action.py`, v2 `click_acc 0.957`/`src_acc 0.856`) but the agent doesn't act on it — "Option B" derives fill-vs-navigate from the focused widget's raw type INSTEAD of the verb (documented reason: the verb/action-type head is unreliable), and a parallel LLM-driven system (`_navigation_protocol`, the sweep, verify's LLM-fallback, `_merge`'s override rules) makes the real navigation/action decisions, with this session's deterministic short-circuits bolted onto THAT system rather than replacing it. **TARGET**: agent executes the model's predicted verb directly (`SemanticAction.to_legacy_dict()` already exists for this) every step; the LLM's only job shrinks to supplying `SET_VALUE`/`SELECT_OPTION` values when the record lookup is genuinely ambiguous; sweep/verify's separate LLM-navigation call sites retire structurally, not just get short-circuited. **RISK, stated plainly**: every "small" patch this session (tab-lock, `_merge` override, verify shortcircuit) surfaced a DEEPER bug live despite drilling first (5+ scattered tab-click sites, a silently-dropped value, a missing retry cap) — a verb-loop rewrite replaces the ENTIRE per-step decision spine (`_navigation_protocol` + sweep + `_merge` + OPT2 gate + verify fallback), all of which carry non-obvious bookkeeping (`_dead_fill_keys`/`_attempted_keys`/`_verify_fix_count`/`_verify_dead_stable`/`_fixation_hits`/`_filled_this_tab`/`_section_pane_tops`) built up specifically to survive this codebase's known failure modes — a rewrite risks silently dropping protections that exist only as inline comments in the code being replaced. **STAGED PLAN** (order, not timeline): (1) audit — enumerate every piece of that bookkeeping state and decide, per item, keep/replace/drop, as its own deliverable before any code; (2) shadow-run the verb-loop decision alongside the current loop on a live record (log what it WOULD do, don't act on it), diff against actual behavior; (3) cut over ONE verb at a time, safest first (`TOGGLE`, binary/easy to verify), highest-value next (`SET_VALUE`, most of the current LLM-45%); (4) keep the existing verify pass as a safety net until the verb-loop proves it doesn't need rescuing on a real ×10 run; (5) re-run ×10 as the acceptance bar, same as every other structural change this session. **OPEN QUESTION handed back to the user, not yet answered**: is the goal here proving the thesis claim (LLM<5%, research/demo milestone) or making the agent faster/more robust for real use — these point to different priorities. If it's the thesis claim, the staged shadow-run approach above is right. If it's pure speed, the already-landed session patches (`verify-llm-fallback-shortcircuit`, `_merge` fix, etc.) get most of the win with far less risk, and the full rewrite may not be worth it right now.

**STAGE 1 (AUDIT) STARTED 2026-07-16, user call ("let's do the biggest levers")**: enumerated every `self._[...]` bookkeeping variable declared in `__init__`, cross-referenced actual call sites (not just definitions) for each. **Big finding, immediately actionable**: `self._verify_pass` has **ZERO call sites** anywhere in the file — confirmed by grep, not assumption — meaning it's been 100% dead code since the earlier same-day "no revisit, ever" change to `_confirm_finished` removed the only place that ever called it. That makes `_view_mismatches` (its only caller-of), `self._verify_fix_count`, `self._verify_dead_stable`, and `self._verify_clean_tabs` ALSO fully dead (write-only or entirely unreachable). **Worse**: `self._verified_at_fill` was being WRITTEN every single step by the main-loop chokepoint (added earlier the same day) via a FULL UIA tree-walk (`_field_matches` → `_live_field_value` → `_resolve_live_control`) — real, measurable per-step cost — to populate a set that `_view_mismatches` (the only reader) can never reach since `_verify_pass` never runs. Pure wasted work, every step, for zero benefit. **FIX** (agent.py ~2245): removed the entire main-loop chokepoint block. Zero behavior change (proven dead, not guessed) — the SWEEP's own separate verify-at-fill check (different call site, does a real immediate retry when a write doesn't stick, unaffected) stays. `py_compile`+`pyflakes` clean. NOT yet live-tested for the actual time saved. **STILL PENDING from the audit**: `_verify_pass`/`_view_mismatches` themselves (hundreds of lines) plus `_verify_fix_count`/`_verify_dead_stable`/`_verify_clean_tabs`/`_verify_ran_once` are all confirmed-dead but NOT YET DELETED — that's a separate, bigger cleanup (same "copy to `scratch/` first, then remove" discipline as `strip-where-crutches`) deliberately not bundled into this speed-focused pass. The rest of the audit (which of the remaining ~15 state variables are KEEP/REPLACE/DROP for an eventual verb-loop) is not yet done — this was a targeted speed-lever stop within the audit, not the full audit.

- [ ] **Close ruleset-inference loop** — Fix `_compress_session` to decode new trace format and capture notepad/source values to infer skip/leave-blank rules. *(Fresh case 2026-07-11 probe: 'Deductible ($)' typed the literal '(N/A — no collision coverage)' — the RECORD holds that string; `_lookup_field`'s blank-resolver catches exact 'n/a' but not annotated variants. Deliberately NOT hardcoded (user-flagged heuristic creep) — "n/a + annotation = leave empty" is exactly the class of rule the inference loop should learn and feed the prompt. WARNING from the same probe: the current extractor inferred the BACKWARDS rule from the raw trace — 'Use "(N/A — no collision coverage)" as a placeholder' — it codified the bug as best practice. Closing the loop requires inferring from CORRECTED/verified behavior, not raw traces, or bad runs poison the ruleset.)*

---

### 🟢 P1 — Generalize (Post Scope #1 Completion)
- [ ] **Find alternative VLM** — Evaluate vision-language / screen-parsing models to replace or augment the current perception stack (classical CV+OCR now; OmniParser-class parser filed as the real fix). Candidates to benchmark behind the same `detect_elements()` seam: OmniParser v2, ShowUI, UGround, Qwen2.5-VL (local ONNX/GGUF preferred — human-like control rule: on-screen observation only). Accept when `perception_eval` beats the current baseline on box precision/recall + label accuracy.
- [ ] **Find alternative LLM** — Evaluate replacements for the current LM Studio `local-model` as the WHAT-provider (value lookup, sweep proposals). Pain points to beat: 2-5s latency per call, occasional wrong-line grabs, JSON-format drift. Benchmark on the same prompts (value accuracy vs intake, latency, format reliability); candidates: newer local models (Qwen, Llama, Phi families) or the already-wired Groq provider path. Note: the LLM-dependency attack plan (deterministic lookup first) shrinks this component's importance — right-size the effort accordingly.
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
