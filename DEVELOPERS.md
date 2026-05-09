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

### Form Filling
- [ ] **Improper tab inputs** — Tab key sometimes advances before the focused
      field has a typed value, leaving fields blank.
- [ ] **Refusing to scroll** — Form does not scroll to reveal fields below the
      fold; agent can't reach them.
- [ ] **Refusing to move forward** — Tab-advance heuristics get stuck; agent
      sits on the same tab past the no-change limit.
- [ ] **Non-submission of records after completion** — Last-tab Submit /
      Submit & New click is missed when UIA can't surface the button name.
- [ ] **No verification post-fill** — Agent never re-observes a field after
      typing to confirm the value landed. wxPython validation rejections are
      silent.
- [ ] **No mid-record crash recovery** — If the agent dies mid-record, the
      half-filled record is abandoned. No resume; record must restart.
- [ ] **No per-tab timeout** — A wedged UIA call can hang indefinitely. The
      stuck-guard advances on no-change but cannot escape a UIA freeze.
- [ ] **No "are we still on the right form?" check** — If the user alt-tabs
      mid-run, keystrokes land in whatever window is currently focused.

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
