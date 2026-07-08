# Intern — project guide for Claude

Behavioral-cloning GUI agent. A user records GUI demonstrations; a **transformer
clones the navigation** and an **LLM supplies field values**. Test fixture: a
wxPython 8-tab car-insurance form filled from a Notepad intake file.

# Working relationship

- No sycophancy. Never agree by default — especially on decisions and
  implementation choices. If my reasoning has a hole, my design has a better
  alternative, or my instruction conflicts with the project's own rules, say
  so BEFORE implementing, with the concrete counter-argument. Agreement must
  be earned by the idea, not by who said it.
- Be direct, matter-of-fact, and concise.
- Be critical; challenge my reasoning.
- Don't include timeline estimates in plans.
- Don't be lazy. Do things the right way, not the easy way.
- Don't add yourself as a co-author to git commits.

# Tooling

- Prefer Makefile targets (`make help`) over direct tool invocation.
- Use your Edit tool for changes; Search tool for searching.
- Use Mermaid diagrams for complex systems.

## HARD RULES (override defaults — follow exactly)
- **NO HARDCODE.** Never bake in field names, tab names, pixel coords, app/window
  names, or per-form value lists. Logic must key on **widget type, geometry,
  the element's own label, or the record** — so it works on any form. "(leave
  blank)/none" value-skip is the one borderline heuristic; the real fix is to
  **infer it** (ruleset-inference), not code it.
- **Division of labor:** transformer = **WHERE** (which field/tab, learned) ·
  LLM = **WHAT** (the value) · agent = **HOW** (universal mechanics: open
  combobox, check box, type, scroll, foreground-lock). Don't let the agent make
  navigation (WHERE) decisions unless explicitly accepted as a temporary crutch.
- **Human-like control only.** Observe on-screen (UIA/vision), act via
  mouse/keyboard. No file reads of the target app, no app-specific scripting.
- **Guards perturb the model.** The transformer's prediction depends on recent
  action-history; injecting agent actions (Tabs, nudges) can destabilize a weak
  model. Add/remove agent logic **ONE at a time, re-test each**. Root fix for
  fixation/mis-prediction = **more demos**, not more guards.
- **Commits:** plain messages, **no "Co-Authored-By: Claude" trailer**. Branch
  off master before committing if asked to commit; only commit/push when asked.

## Workflow (the loop)
1. **Record** demos: `python app/main.py` (GUI recorder; out_dir defaults to
   `data/demos/eight_Tabs`). Fill all tabs → Submit & New → Stop & Save. Never
   use the duplicate/copy button (oversampling destabilizes).
2. **Clean:** `python scripts/clean_demos.py <src> <dst>` (strips dropdown-select
   /junk/dupes).
3. **Train:** `python train.py --trace_dir data/demos/<x>_clean --save_path
   tasks/form_filling/model_eight_tabs.pt --epochs 80`. Watch `click_acc`
   (≥0.85 = no fixation). Rare-action loss-weighting + the `attempted` feature
   auto-apply.
4. **Run:** `python run_task.py --model tasks/form_filling/model_eight_tabs.pt`.
   Needs the form + Notepad intake open; click the form at the "GO" prompt.

## Key files
- `components/agent/agent.py` — the run loop, all HOW-mechanics, guards.
- `components/intelligence/model/transformer.py` — BC policy; `ELEM_FEATURES`
  (incl. `is_filled`, `attempted`), dataset builder, `predict()`.
- `components/agent/executor.py` — pyautogui actions (idempotent paste).
- `components/intelligence/rule_extractor.py` — ruleset inference (loop currently
  OPEN; see DEVELOPERS.md P0).
- `car_insurance_entry/car_insurance_form_wx.py` — the test form (8 ScrolledPanel tabs).
- `data_entry_tasks/data_entry_intake.txt` — the 10-record intake.
- Model `tasks/form_filling/model_eight_tabs.pt` is **untracked** (binary).

## Status & priorities
- **Single source of truth = `DEVELOPERS.md`** → `Task List` → `⭐ PRIORITY ORDER`.
  Read it before starting. Current P0 order: close ruleset-inference loop →
  fix scroll-actually-moves → tab-visit order → strip WHERE-crutches →
  multi-record ×5 → verification.
- Session post-its + lessons live in the auto-memory (`MEMORY.md` index).

## DEVELOPERS.md Guardianship

Claude is the **guardian of `DEVELOPERS.md`** — it is the single source of progress
truth for this project. This means:

### Problem & solution tracking
- **During every task**, keep a running log of problems encountered, the solutions
  considered/attempted, and the final fix that resolved each problem.
- When a problem is solved, record it clearly: what broke, why, and what fixed it.
  This feeds into `Solved Problems`, `Lessons`, and the task-list annotations in
  DEVELOPERS.md so nothing is forgotten.

### Consistent, complete updates after each accomplished task
- **After completing a task**, review DEVELOPERS.md and update **every section the
  completed work affects.** Don't touch sections that are unrelated, but if a
  completed task changes the status of a checklist item, closes a gap, shifts a
  dependency, adds a lesson, or retires a blocker — update ALL of those places:
  - `Current Status` — overall project state, honest gaps
  - `Task List → ⭐ PRIORITY ORDER` — re-order / check off as needed
  - `Task List` stages — mark `[x]`, update caveats, remove stale band-aids
  - `Solved Problems` — add newly-solved items with verification details
  - `Finished Tasks` — append completed work
  - Dependency chains — update `[DONE]` / `[NEXT]` markers
  - `Current Goal` — adjust if the goal shifted
  - `Open technical questions` / `Risks` — close resolved items
  - Any other section that references the completed work
- **Be atomic:** each update pass should leave DEVELOPERS.md internally consistent
  (no section claiming something is open that another section marks done).
- **Be honest:** if a task is *partially* done or introduced new gaps, say so —
  don't mark it `[x]` and hide caveats.

### Ground rules
- Read `DEVELOPERS.md` at the start of any session that involves code changes.
- After a significant change, do a consistency sweep: grep for references to the
  thing that changed and update them all.
- Preserve the existing structure and voice; don't reorganize sections unless asked.
- When in doubt about whether something is worth updating, update it — staleness
  is worse than a small extra edit.

## Environment
- Windows, PowerShell primary (Bash tool available). LLM via LM Studio
  (`http://localhost:1234`, provider=lmstudio) by default.
