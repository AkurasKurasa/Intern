# Intern — Demonstration Recording Pipeline

**Document Type:** Technical Reference
**Date:** August 17, 2026
**Status:** Describes the system as it actually behaves today, verified against real code and real recorded sessions — not the design intent, where the two differ this says so explicitly.

---

## 1. The structural fact to keep straight: there are two recorders

`components/recorder/recorder.py` defines two independent classes that don't share input-capture code. Every real recorded session under `tasks/form_filling/traces/` (20 sessions, checked directly) was produced by `ScreenObserver`, not `DemoRecorder` — but `DemoRecorder` is the one wired into the current Electron app's Start/Stop button. Know which one you're looking at before reasoning about a trace file.

| | `DemoRecorder` | `ScreenObserver` |
|---|---|---|
| Triggered by | Electron app (`recorder-start` IPC), `app/main.py`'s `DemoPanel`, `record_trace.py --demo` | `app/main.py`'s `ObserverPanel`, `record_trace.py` (no flag) |
| Capture model | Event-triggered — commits a step only when a field is left | Fixed-interval polling (default every 2s) |
| Input hooking | Raw pynput listeners wired directly in `.run()` | `MouseInput`/`KeyboardInput` — passive pynput listener classes |
| Trace shape | `state`, no `action`/`diff`/`clipboard` | `state`, `action`, `diff`, `clipboard` (via a post-hoc translation step) |
| Written to disk | Incrementally, one file per committed step | All at once, at `stop()` |
| What's actually on disk today | — | **All 20 real sessions in `tasks/form_filling/traces/`** |

---

## 2. `DemoRecorder` — how a step actually gets committed

This is the load-bearing design decision, confirmed directly in code (`components/recorder/recorder.py:1892-1979`) and matching DEVELOPERS.md's own incident note:

> *"Individual keystrokes accumulate silently in `_pending_text`/`_pending_keys` with zero queue push — a step only commits when a field is left (Tab/Enter/click elsewhere). That's correct and load-bearing: every trained checkpoint's data assumes one step per committed field, not per keystroke — changing it would degrade training data."*

The real trigger set, precisely (not the shorthand above):

- **Tab, Enter, Escape** — and also **Backspace, Delete, Home, End, Page Up/Down, arrow keys, Ctrl+A/C/V/X/Z/Y** — all classified as a `hotkey`. Any of these flushes pending text first, *then* queues its own `hotkey` step.
- **A mouse click** — flushes pending text, then queues its own `click` step.
- **A scroll** — flushes pending text (debounced).
- **F10 (quit)** — final flush.

So typing "Delgado" then Tab produces exactly two committed steps: one `keyboard` step (`text: "Delgado"`), then one `hotkey` step (`hotkey: "tab"`).

**Why this matters practically**: the listener callbacks never touch UIA (*"never reads UIA/state — that would hold the GIL and lag input"*, code comment). A separate worker thread drains the event queue and does the actual UIA snapshot + JSON write per commit. This is also why the Electron UI has a `pending` flag on its frame counter (`typing…` indicator) — the counter can't move while you're mid-keystroke, so the UI needed an honest way to show "something is happening" without lying about what's actually being saved.

Each committed step writes immediately: `<output_dir>/session_<timestamp>/live_step_NNNN.json` — crash-safe by construction, never buffered until the end.

---

## 3. `ScreenObserver` — the interval-based path (what's actually on disk)

`start(interval_sec=2.0)` spawns a daemon thread (`_capture_loop`) that, every interval, grabs a full-screen `mss` screenshot **and** a semantic-state snapshot, in this perception-backend priority order: **Vision (CV/OCR) → Excel COM → UIA tree walk → OCR fallback.** UIA is what's actually active for essentially every real Windows recording session.

At `stop()`, the accumulated mouse actions / keyboard groups / clipboard events / frames get passed through `_translate_and_save`, which builds each step's `action` field by priority — **clipboard paste → keyboard text → mouse click/drag → noop** — and writes `live_step_NNNN.json` per frame, plus one `session_manifest.json` per session.

### `session_manifest.json`
Background-window elements (e.g. Notepad holding the full source document, sometimes >800KB) would bloat every single step file if inlined. Instead the full text is externalized **once**, keyed by `window_title|app`, and each step's copy is stripped to a value truncated at 500 chars. Training code re-reads the manifest to restore full text without per-step bloat.

---

## 4. Real trace JSON shape (from an actual recorded step)

`tasks/form_filling/traces/session_20260521_211247/live_step_0001.json`:

```json
{
  "trace_id": "live_step_0001",
  "timestamp": "2026-05-21T21:12:49.058439",
  "duration": 1.275528,
  "type": "gui",
  "state": {
    "application": "Notepad.exe", "window_title": "data_entry_intake.txt - Notepad",
    "process_id": 30616, "screen_resolution": [1920, 1200],
    "focused_element_id": null, "source": "uia", "elements": [ /* 123 elements */ ]
  },
  "mouse":     {"actions": [{"id": "mouse_action_0000", "position": [1158, 213], "type": "click", "timestamp": "2026-05-21T21:12:49.861552"}]},
  "keyboard":  {"actions": []},
  "clipboard": {"events": []},
  "diff":      {},
  "action":    {"action_type": "click", "click_position": [1158, 213]}
}
```

Element schema (union of real keys seen): `element_id, type, control_type, bbox, text, value, label, automation_id, class_name, enabled, visible, focused, confidence, source, window_role, window_title, app, pid, metadata`. `window_role` is `"active"` (foreground window) or `"background"` (source-data windows like Notepad).

Sessions recorded **before ~May 2026** also carry a `next_state` field; it was deliberately removed afterward as redundant (*"it equals the next step's state; training code never reads it"*) — this matters for §7 below.

---

## 5. How a recording actually starts (Electron path)

```
renderer.js  btnStart click
    → window.recorderAPI.start(outputDir)
preload.js
    → ipcRenderer.invoke("recorder-start", outputDir)
main.js
    → ipcMain.handle("recorder-start") → writes {"cmd":"start",...} to the bridge's stdin
app/recorder_bridge.py
    → Bridge.start() constructs DemoRecorder(output_dir=..., trace_type="form_filling")
      and runs .run() in a background thread
components/recorder/recorder.py
    → DemoRecorder.run() — per §2 above
```

Two other entry points exist and are worth knowing about: the legacy `app/main.py` Tkinter UI constructs `DemoRecorder`/`ScreenObserver` directly (no bridge), and `scripts/record_trace.py` is the CLI path (`--demo` flag selects `DemoRecorder`, otherwise `ScreenObserver`). There's also a separate, older `scripts/demo_recorder.py` that duplicates the commit-on-blur idea in its own standalone functions and writes a different, simpler `human_step_NNNN.json` format (`state`/`next_state` only) — superseded by `record_trace.py --demo`, but still present and runnable. Don't confuse it with the real `DemoRecorder` class.

---

## 6. Clean → Train pipeline

```
RECORD                                  live_step_NNNN.json under session_*/
   ↓
CLEAN     scripts/clean_demos.py <src> <dst>      — drops noise, see §7
   ↓ (optional)
OVERSAMPLE  scripts/oversample_tails.py           — duplicates each session's "…→Submit" tail
   ↓
TRAIN     scripts/train.py --trace_dir <dir> --epochs 80 ...
```

`train.py` constructs `BCTrainer(trace_dir=..., save_path="tasks/form_filling/model.pt", ...)`, which delegates to `TransformerAgentNetwork.train()`. The dataset loader globs `session_*/` subfolders (and flat `*.json` files) under each `--trace_dir`, treats each session as one contiguous trajectory so the sliding-window history never crosses a session boundary, and re-hydrates `session_manifest.json` text where present.

**Output**: `torch.save({"epoch", "model_state_dict", "val_loss", "val_acc", "val_click_acc", "hyperparams": {...}}, save_path)` — a checkpoint dict, only overwritten when validation click-accuracy improves.

---

## 7. Known issues — verified directly, not previously written down anywhere

Both confirmed live against the real repo, not inferred:

1. **The OCR-fallback perception path is currently broken.** `recorder.py`'s last-resort branch (used only if Vision/Excel/UIA are all unavailable) imports `TraceTranslator` and calls `_state_from_pil(...)`. Running that import directly:
   ```
   ImportError: cannot import name 'HTMLDetector' from 'trace_translator.trace_translator.trace_translator'
   ```
   `trace_translator/__init__.py` imports classes (`HTMLDetector`, `CVDetector`, `UIElementExtractor`) that the actual `trace_translator.py` doesn't define, and the real `TraceTranslator` class has no `_state_from_pil` method and no `use_cv`/`use_html` constructor params. Masked in practice because UIA is available on nearly every real Windows session — but any recording that genuinely needed the OCR fallback would crash `stop()`.

2. **`clean_demos.py` silently breaks on current-format recordings.** It resolves what a click landed on entirely via `t.get("next_state", {})`. As noted in §4, sessions from ~May 2026 onward have no `next_state` field at all (removed as redundant). Run against those, `next_state` defaults to `{}`, every click resolves to no element, and **every mouse-driven step gets classified as junk and dropped** — only keyboard-only steps would survive. Test this against a current-format session before trusting its output; it was last verified correct only against the older `next_state`-carrying format.

3. **`components/trace_translator/`'s README overstates its actual role.** The README describes a full YOLO/SAM/Tesseract CV pipeline; the real code is a thinner format-glue layer (`state_to_trace`/`states_to_trace`, a bbox-IoU diff) that expects some other observer to have already produced the element list. For real recordings in this repo, it's not in the pipeline at all — `DemoRecorder`/`ScreenObserver` normalize directly against UIA's own output.

Also worth knowing (already documented in `DEVELOPERS.md`, summarized here for one-stop reference): `DemoRecorder` runs UIA snapshots **in-process** on a dedicated worker thread today, not via a subprocess — an earlier subprocess/multiprocessing design was removed entirely after being traced to a permanent hang (a UIA call against a vanished window, inside a process that showed 0% CPU, never recovering). That worker thread has to call `self._init_com()` first or Windows segfaults on the first real `uiautomation` call — the method existed but was never wired in until this was found live.
