# Intern

*Your workflow, cloned.*

Intern watches you work, learns your actions, and over time handles GUI tasks
the way you would: by looking at the screen, moving the mouse, and pressing
keys. No file-system shortcuts, no app-specific scripting — only what a human
operator could do.

This document is for developers working on Intern itself.


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
python run_agent.py
```

Configure `run_agent.py` knobs at the top:

| Constant         | Default                  | Notes                                |
|------------------|--------------------------|--------------------------------------|
| `PROVIDER`       | `"lmstudio"`             | Switch to `"groq"` / `"anthropic"` for real reasoning. |
| `MAX_STEPS`      | `150`                    | Hard cap per record.                 |
| `RECORD_START`   | `1`                      | First record to fill (1-based).      |
| `RECORD_END`     | `1`                      | Inclusive. Set higher to loop.       |
| `SOURCE_WINDOW`  | `"data_entry_intake"`    | Title fragment of the source window. |
| `USE_VLM`        | `True`                   | Enable VLM (live `scan_tab` per tab).|
| `USE_VLM_PRESCAN`| `False`                  | Walk full document upfront. Heavy on rate limits.|


## Repository Layout

```
components/
  agent/
    agent.py                 LLMAgent — main loop, provider abstraction
    task_plugins/            Task-specific plugins (form-fill, etc.)
      base_plugin.py         TaskPlugin ABC
      form_filler_plugin.py  Auto-fill / auto-skip / tab-advance / scan
  data_sources/
    base.py                  DataSource ABC
    notepad_source.py        Win32 WM_GETTEXT + parse_records helpers
  observers/
    vlm/
      vision_observer/       VLM screenshot → key/value extraction
      visual_data_reader/    pre_scan + scan_tab + rescan_after_scroll
  trace_translator/          Action-trace → text trace
car_insurance_entry/         The wxPython target form (test fixture)
data_entry_tasks/             Source intake .txt files
data/output/                 Submission JSONs + run traces
scripts/                     Smoke tests + dev utilities
run_agent.py                 Entrypoint
```


## Components

### Agent
- **`components/agent/agent.py`** — `LLMAgent` orchestration loop, multi-provider
  LLM support (Anthropic / Groq / Gemini / LM Studio).
- **`components/agent/task_plugins/`** — Task plugins. The plugin owns task
  logic; the agent owns the observe → decide → act loop.

### Observers
- **UI Automation Observer** — Walks the UIA tree, returns `{element_id, type,
  label, bbox, value, focused, …}`.
- **Vision Observer / Visual Data Reader** — Screenshot → VLM (Groq llama-4-scout
  or Gemini Flash) → JSON of visible field/value pairs. Used per tab-switch
  (`scan_tab`) and on cache miss (`rescan_after_scroll`).
- **OCR fallback** — Tesseract over background-window pixels when UIA returns
  empty values (Win11 Notepad UIA cap).

### Data Sources
- **`NotepadDataSource`** — Win32 `WM_GETTEXT` reader + `_parse_records`
  multi-record parser + field-line lookup helpers (`_split_kv`,
  `_exact_match`, `_find_field_line`).

### Action Executor
- pyautogui mouse/keyboard. All actions go through this — no OS-level
  shortcuts that humans can't do.

### Trace Translator
- Converts an action trace into natural-language steps for downstream
  consumption (training data, replay, debugging).


## Current Goal

> **Complete the Behavioral Cloning Phase.**
>
> The transformer must fill all 5 records of the car insurance form — all 8 tabs, all fields, correct values — with `PROVIDER="none"` (zero LLM calls), completing in under 400 steps per record, with a success rate of ≥ 80% across 10 consecutive runs.
>
> When this passes, BC is done and development shifts to the Reinforcement Learning phase.


## Live Metrics — Current State

Two test modes: **With LLM** (normal operation) and **Transformer-only** (`PROVIDER="none"`).
The transformer-only score is the real BC progress indicator — LLM is a crutch, not the goal.

### With LLM (`PROVIDER="lmstudio"`) — 2026-05-24

| Metric | Score | Detail |
|--------|-------|--------|
| **Task Completion Rate** | 25% | 5 fields filled / ~20 total |
| **Action Prediction Accuracy** | 0% | 0 on-target / 0 clicks (LLM typed into comboboxes) |
| **Execution Success Rate** | 55.6% | 5 ok / 9 actionable steps |
| **Transformer conf** | 0.74–0.84 | click predictions active (was 0.00 — fixed pointer head explosion) |
| **Training** | val_loss=6.74  click_acc=19.5% | 50 epochs, fixed bilinear divergence (LayerNorm on pointer heads) |

### Transformer-Only (`PROVIDER="none"`) — 2026-05-23

| Metric | Score | Detail |
|--------|-------|--------|
| **Task Completion Rate** | 5% | 1 field filled / ~20 total |
| **Action Prediction Accuracy** | 100% | 1 on-target / 1 click |
| **Execution Success Rate** | 5.6–40% | Fills 1 field (Agency Name or Policy Number), then loops on unresolved keyboard — confirmed across 2 runs |

**What the numbers say right now:**
- LLM fills text fields correctly but fails on all comboboxes (~45% wasted steps).
- Transformer-only score = true BC baseline. Must improve this over time, not the LLM score.
- Main gap: combobox handling + tab navigation across all 8 tabs.

**How to track progress:** after each retrain, run both modes and update this table.


## Wish List — Path to Full Behavioral Cloning

What needs to happen, in order, to reach the BC completion criterion (transformer fills all 5 records, all fields, no LLM, ≥80% success rate).

---

### Stage 0 — Fix what's broken now *(hours)*

These are blocking every downstream improvement.

- [x] **Bootstrap the correctional task spec** *(→ Finished Tasks)* — `scripts/bootstrap_spec.py` ran `correct()` across all 19 sessions. `form_filling.md` exists and injects into agent system prompt.
- [x] **Commit all staged changes** *(→ Finished Tasks)* — All files committed and pushed.
- [x] **Set BC gold standard** *(→ Finished Tasks)* — `scripts/bc_fidelity.py --set-reference-from-source` parses intake .txt directly. 75 fields. No perfect human run needed. Gold standard active.
- [ ] **Get current fidelity baseline** — Run `python run_task.py` once. Check the fidelity score printed at the end. This is your starting point. Then `python scripts/bc_fidelity.py --progress` to see it.
- [ ] **Re-run bootstrap with fixed compressor** — `python scripts/bootstrap_spec.py` — `_compress_session()` is now fixed; re-run produces spec with real tab names, field names, and behavior patterns.
- [ ] **Fix LLM checkbox clicking** — Will emerge naturally from spec once bootstrap re-runs.

---

### Stage 1 — Data volume *(days)*

The transformer's click accuracy is ~40% after 19 sessions. It needs to see each field ~50+ times before positions become reliable.

- [ ] **Record 30+ total sessions** — Each session = one complete form fill (all 5 records, all 8 tabs). Currently at 19. Target: 50 sessions = ~25,000 traces before expecting reliable click accuracy.
- [ ] **Run correctional spec after every new session** — `record_trace.py` calls `RuleExtractor.correct()` automatically on stop. Confirm `form_filling.md` updates each time.
- [ ] **Retrain after every 5 new sessions** — Dataset cache makes init ~5 sec. Full 50-epoch GPU retrain ~20 min. Command: `NO_SENT_TRANSFORMERS=1 python -m components.intelligence.model.transformer --mode train --data_dir data/output/traces/forms --model_path data/models/transformer_form.pt --epochs 50 --d_model 64 --num_layers 2 --max_elements 64 --aug_drop_prob 0.1`
- [ ] **Check fidelity after every retrain** — Run agent → `python scripts/bc_fidelity.py --progress`. Fidelity number goes up = improvement confirmed. Target: ≥80%.

---

### Stage 2 — Model quality *(days, parallel with Stage 1)*

More data alone won't close the gap if the model is too small and undertrained.

- [ ] **Scale model to `d_model=128, num_layers=4`** — Current 164k-param model will underfit at 50 sessions. Bump once dataset exceeds 30 sessions.
- [ ] **Train to 100 epochs** — val_loss was still trending down at epoch 21 (best checkpoint). Extend budget. Early stopping already saves best checkpoint so extra epochs cost nothing if loss plateaus.
- [ ] **Click accuracy target: ≥75%** — Current: ~40%. Each 10-session retrain cycle should move this up. If click_acc stalls below 60% after 40 sessions, revisit click loss weight (currently `lambda_click=2.0` — try 3.0).

---

### Stage 3 — LLM-to-transformer handoff *(after Stage 2)*

LLM currently does all reasoning. Transformer needs to take over progressively.

- [ ] **`PROVIDER="none"` smoke test** — Disable LLM entirely. Run agent on the form. Record: how many fields does the transformer fill correctly? Which tabs does it navigate correctly? This is the BC baseline.
- [ ] **Lower `_HIGH_CONF` threshold** — Currently 0.995 (LLM always decides). Once transformer click_acc ≥ 75%, lower to 0.90 so transformer handles confident clicks, LLM handles ambiguous cases.
- [ ] **Automate correction → retrain trigger** — After each new session, if `form_filling.md` changed, auto-queue a retrain. Removes manual intervention from the loop.

---

### Stage 4 — BC completion proof *(after Stage 3)*

- [ ] **10 consecutive runs, `PROVIDER="none"`, ≥80% field fill accuracy** — All 5 records, all 8 tabs, correct values from Notepad. This is the pass criterion.
- [ ] **Expected-vs-actual diff at submit** — Compare submitted JSON against source data. Surface fill accuracy per field per record. Required to measure the 80% threshold objectively.
- [ ] **Freeze BC checkpoint** — Tag the `.pt` file that passes the criterion. This becomes the Actor initialization for the RL phase.

---

### Stage 5 — RL phase (future)

Unlocked after BC passes.

- [ ] **Actor-Critic with PPO** — BC transformer becomes the Actor. Separate critic network evaluates per-step value. Reward = task outcome + KL penalty against BC policy (prevents style drift). PPO clipping keeps updates stable.
- [ ] **Online fine-tuning** — Agent runs live, reward signal from `StateValidator`. Policy gradient updates weights in real time. Gets agent beyond human-demo quality.

---

## Task List

Priority order — top = most blocking right now.

### 🔴 P1 — Blocking (do first)

- [x] **Fix LLM click position** *(→ Finished Tasks)* — `_merge()` resolves LLM target by label first; transformer click only used as fallback.
- [x] **Boost click loss weight** *(→ Finished Tasks)* — `lambda_click=2.0`.
- [x] **Train longer** *(→ Finished Tasks)* — Default epochs 20 → 50, GPU training on RTX 4050.
- [x] **GPU training** *(→ Finished Tasks)* — CUDA PyTorch 2.6.0+cu124 installed.
- [x] **Dataset init cache** *(→ Finished Tasks)* — `.dataset_cache.pkl` cuts retrain init from 10 min to ~1 sec.
- [x] **Bootstrap correctional spec** *(→ Finished Tasks)* — `scripts/bootstrap_spec.py` ran `correct()` across all 19 sessions.
- [ ] **Fix _compress_session()** — Trace compressor produces near-empty output because JSON structure doesn't match expected fields. LLM gets no specific data → spec stays generic. Must fix before next bootstrap.
- [ ] **Fix LLM checkbox clicking** — Inferred from spec, not hardcoded. Needs `_compress_session()` fixed first so traces surface checkbox behavior patterns.
- [ ] **Record more training traces** — 19 sessions. Target 50. Click_acc ~40%, needs ~75% for reliable fills. See Stage 1.

### 🟡 P2 — Important (do after P1)

- [x] **Correctional ruleset system** *(→ Finished Tasks)* — `RuleExtractor.correct()` + `record_trace.py` auto-calls on session end.
- [x] **Spec injection into agent** *(→ Finished Tasks)* — `LLMAgent.__init__` loads `form_filling.md` into LLM system prompt.
- [ ] **Increase model capacity** — Bump to `d_model=128, num_layers=4` after 30+ sessions.
- [ ] **PROVIDER="none" smoke test** — Run agent with LLM disabled. Measures pure transformer capability. See Stage 3.
- [ ] **Automate correction → retrain loop** — After each session, if spec changed, queue retrain. See Stage 3.

### 🟢 P3 — Nice to have (do when P1+P2 are solid)

- [ ] **Actor-Critic Model (PPO) for RL phase** — BC transformer becomes the Actor (pretrained, fine-tuned by RL). A separate small critic network evaluates state value step-by-step, replacing sparse win/loss signal with dense per-step feedback. PPO's clipping mechanism limits policy drift per update, naturally preserving user style while improving performance. Reward = task outcome + KL penalty against BC policy to prevent the agent from diverging beyond the user's learned behavior. Entry point to the full RL phase.
- [ ] **Online RL fine-tuning** — BC-trained transformer as starting policy → reward = `StateValidator` ok/done signal → policy gradient updates weights in real time. Gets agent beyond human-demo quality.
- [ ] **Cross-task shared backbone** — Single transformer trunk, task-specific heads per capsule. Click/keyboard patterns transfer between form types.
- [ ] **No mid-record crash recovery** — Resume from last successfully filled field if agent dies mid-record.
- [ ] **Expected-vs-actual diff at submit** — Compare submitted values against source data. Surface fill accuracy per record.
- [ ] **Unit tests for core logic** — `_parse_records`, `_lookup_field`, `encode_state`, tab-advance. Catch regressions before they reach a live run.
- [ ] **Generalize to Excel / Shopify / web** — New capsule per scope. Same architecture, new training traces.
- [ ] **Ghost cursor overlay** — A separate translucent cursor (distinct color/icon from the system cursor) that tracks Intern's intended click target before each action executes. Implemented as a small always-on-top transparent window (`tkinter` or `wx` overlay) that animates to the target `click_position` coordinate. User's real cursor is untouched — ghost cursor is read-only visual feedback. Useful for demos, debugging click accuracy, and showing what the transformer/LLM decided without interfering with user control.
- [ ] **Training readiness indicator** — Circular progress bar (shown in a small HUD or CLI output) that gauges how close the current capsule is to being "useful" without LLM assistance. Computed from: session count vs target (e.g. 50), best val_loss vs threshold, and live click_acc from the last training run. Fills toward 100% as those metrics improve. Gives a single at-a-glance answer to "do I need to record more demos?" without reading training logs.

---

### Why completing these tasks finishes the Behavioral Cloning Phase

Behavioral Cloning is complete when the transformer has learned enough from human demonstrations to act correctly on its own — no reasoning, no fallback, no guidance. Each task in the list above directly closes a gap between where the transformer is now and that standard:

- **More traces** give the transformer enough signal to learn click positions and field semantics. Without them, the model is pattern-matching on noise.
- **Correctional spec → LLM system prompt** makes the LLM a better teacher while the transformer catches up. The spec converges toward a precise task description with every session.
- **Larger model + more epochs** are the training-side levers that turn raw data into a well-calibrated policy.
- **PROVIDER="none" smoke test** is the proof. If the transformer fills all 5 records correctly with LLM disabled, BC is done.

At that point BC has done its job: the transformer is a competent, autonomous policy ready to be the Actor in the RL phase.


## Roadmap

### Component Maintenance
- [ ] **Recorder** — Capture human runs as training traces.
- [ ] **Intelligence**
  - [ ] LLM provider tuning (cache control, thinking budgets, batching)
  - [ ] Training pipelines (BC, RL, continual)
- [ ] **Observers**
  - [ ] Screen Observer — review code and purpose
  - [ ] UI Observer — review code and purpose
  - [ ] VLM
    - [ ] Vision Observer — finish implementation, wire into live loop
    - [ ] Visual Data Reader — review code and purpose
  - [ ] Excel Observer
  - [ ] Web Observer
- [ ] **Trace Translator** — review effectiveness and efficiency
- [ ] **Workflow Builder** — review code and purpose; verify execution states
      and flow control
- [ ] **Executor** — translate agent predictions into concrete actions

### Cross-Cutting
- [ ] Reconcile LLM (reasoning) and Transformer (action policy) — when to use
      which, and how they hand off.
- [ ] Standardize data-source interface (Notepad, Excel, web, PDF).


## Known Issues

Specific failure modes worth fixing. Each item names the symptom so someone
picking it up has the failure mode in hand.

### VLM
- [ ] **No dedicated VLM** — Currently piggybacks on Groq llama-4-scout (free
      tier hits 429 fast) with Gemini Flash fallback. Both general-purpose,
      not specialized for form / document reading.

### LLM
- [ ] **No prompt caching** — Anthropic backend would benefit from
      `cache_control` on the goal + screen-state preamble. Every step pays
      full prompt cost.
- [ ] **Each step is an LLM call** — No batching across steps, no
      thinking-budget tuning, no parallel pre-fetching of next-step decisions.

### Architecture
- [ ] **No memory component** — No short-term scratchpad (per-record working
      notes) and no long-term store (cross-record patterns the agent learns
      and reuses). Each record runs from a blank slate.

### Performance & Observability
- [ ] **No prefetch** — Record N+1's source data could be VLM-extracted while
      the agent fills record N. The agent currently sits idle during VLM
      calls.
- [ ] **No structured trace** — Logging is human-readable only. No
      record-level summary at end (e.g. "record 1: 78/80 fields filled, 2
      blanks").
- [ ] **No expected-vs-actual diff** at submit time — Can't tell whether the
      fill was correct without manually comparing the form against the
      source.
- [ ] **No screenshot history** — When the VLM mis-reads, there is no
      archived image to inspect what it saw.
- [ ] **VLM key vs UIA label mismatch** — VLM emits "First Name", UIA may
      surface "First Name:" or "First Name " (trailing space). Fuzzy match
      exists but does not log when it fires, so silent mis-mappings go
      unnoticed.
- [ ] **No "field done" signal** — `_filled_this_tab` is populated
      heuristically. If the agent types but the UI rejects the value, the key
      stays in the set forever and the field is never retried.

### Generality
- [ ] **`RECORD N OF M` delimiter hardcoded** — Only this intake format
      parses. Any other source layout is unparseable.
- [ ] **No unit tests** — Every fix is run-and-pray. A regression suite would
      catch `_parse_records`, `_lookup_field`, and tab-advance bugs before
      they reach a real run.


## Non-System Work

### Documentation / Thesis
- [ ] Finish Chapter 3
- [ ] Revise paper if nominated

### Data Collection
- [ ] 500,000 traces in a convenience-sampling setting

### Benchmark
- [ ] Compare against similar systems

### Scopes
- [ ] Car Insurance Entry Form (current dev fixture)
- [ ] Excel
- [ ] Shopify
- [ ] Generalization

## Finished Tasks

Completed work, preserved for reference. Items here were once in P1/P2/P3 or the Wish List.

### Intelligence & Training
- **GPU training** — CUDA 12.4 / PyTorch 2.6.0+cu124 installed. RTX 4050 Laptop GPU active. Was: P1 Blocking.
- **Boost click loss weight** — `lambda_click` raised 1.0 → 2.0. Extra gradient pressure on click head. Was: P1 Blocking.
- **Train longer** — Default epochs 20 → 50. val_loss still trending at epoch 21 so budget increased. Best checkpoint auto-saved. Was: P1 Blocking.
- **Dataset init cache** — `TrajectoryDataset.__init__` saves filtered file paths + action metadata to `.dataset_cache.pkl`. Invalidated when any session file is newer than cache. Cuts retrain init from 10 min to ~1 sec on second+ run. Was: P1 Blocking.
- **Lazy loading** — State tensors built on demand in `__getitem__`, not at init. Fixes OOM crash on 11k-trace datasets. Was: implicit P1 blocker (MemoryError).
- **NO_SENT_TRANSFORMERS bypass** — Sentence-transformers caused segfault (exit 139) with torch 2.6.0. Env var skips loading entirely. Was: implicit P1 blocker.
- **Balanced sampler** — Per-class sampling so click/keyboard classes train equally despite imbalance. Fixed index bug after lazy-load tuple reorder. Was: P2.
- **Data augmentation** — `scripts/augment_traces.py` creates ×4 copies per session with bbox jitter ±5px, click jitter ±4px, confidence noise ±0.03. Was: P2.

### Agent & Merge Logic
- **Fix LLM click position** — `_merge()` now calls `_resolve_target()` on the LLM's named target first. Uses element bbox center directly. Transformer click coords only used as fallback when LLM target doesn't resolve. Confirmed working: logs show "LLM target 'E-Signature Obtained' → (1357, 670)". Was: P1 Blocking.

### Ruleset & Spec System
- **Correctional ruleset system** — `RuleExtractor.correct(session_dir, goal)` reads existing `form_filling.md` + new session traces → sends both to LLM → overwrites spec with corrected version. Single truth file, not one file per session. Was: P2.
- **Spec injection into agent** — `LLMAgent.__init__` loads `data/output/rulesets/form_filling.md` at startup and appends to LLM system prompt. Every agent run uses the latest inferred spec. Was: P2.
- **Auto-extract on record** — `record_trace.py` calls `RuleExtractor.correct()` automatically when a session ends (≥5 traces). Spec improves with every recording without manual steps. Was: P2.
- **Bootstrap correctional spec** — `scripts/bootstrap_spec.py` ran `correct()` sequentially across all 19 existing sessions to build the initial `form_filling.md`. Was: Stage 0 blocker.

### Evaluation
- **Per-run evaluation metrics** — `evaluate_run(results)` in `scripts/eval_metrics.py` computes Task Completion Rate, Action Prediction Accuracy, and Execution Success Rate from in-memory agent results. Wired into `run_task.py` via `try/finally` — fires on every run including crashes, early stops, and Ctrl+C. Was: not implemented.
- **`_compress_session()` fixed** — Trace compressor now reads actual JSON structure (`action.action_type`, `action.text`, `action.click_position`, active tab from selected tabitem elements, focused field by `element_id`, click target by bbox proximity). Was: producing near-empty output. Was: P1 blocker for bootstrap quality.
- **BC fidelity scorer** — `scripts/bc_fidelity.py` scores every agent run against a gold standard human submission. Outputs Fidelity Score (0-100%) = `field_match_rate×0.4 + value_accuracy×0.4 + tab_coverage×0.1 + completion_bonus×0.1`. Appends to `data/output/bc_progress.jsonl` for trend tracking. Wired into `run_task.py` — fires after every run. View trend: `python scripts/bc_fidelity.py --progress`. Was: not implemented.

### Infrastructure
- **Capsule registry** — `components/agent/capsule.py` + `build_capsule.py`. Per-task model routing: agent auto-selects `.pt` file based on goal string and window title. Was: architecture item.
- **`.gitignore` for traces** — `data/output/traces/forms/`, `forms_aug/`, `live/` excluded. `.dataset_cache.pkl` excluded. Was: housekeeping.

### Behavioral Fidelity Benchmarks
Tasks specifically designed to measure whether Intern clones *style and decision-making*, not just mechanical actions.

- [ ] **Chess** — Record a human playing games on a chess GUI (e.g. Lichess, Chess.com, or a local engine). Train a capsule on those sessions. Evaluate whether the cloned agent reproduces the same openings, middlegame tendencies, piece preferences, time management, and blunder patterns. ELO of the cloned agent vs ELO of the human is the fidelity metric. If clone ELO ≈ human ELO and playing style matches, BC is working beyond rote memorization — it captured decision-making under uncertainty.
