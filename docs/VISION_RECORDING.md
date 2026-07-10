# Recording & Training a Vision Model

How to record demonstrations with **vision** perception (screenshot + CV/OCR) and
train a model that drives the agent from pixels instead of the UIA accessibility
tree.

> **Why a new model is needed:** the current model was trained on UIA states.
> Vision states are different (no keyboard-focus signal, comboboxes look like plain
> fields, slightly different element set), so the UIA model behaves out-of-distribution
> on vision. A vision-trained model fixes that. There's no shortcut — the existing
> demos are UIA-only with **no screenshots**, so they can't be converted; you must
> re-record with vision.

---

## Verified before writing this

The record → train pipeline was checked end-to-end (not just by reading code):

- ✅ Vision element types (`editcontrol`, `comboboxcontrol`, `checkboxcontrol`,
  `buttoncontrol`, `tabitemcontrol`) are in the trainer's `_INTERACTIVE` **and**
  `_CLICK_TARGET_CTRL_TYPES` sets — so vision traces aren't skipped and clicks resolve.
- ✅ `CVVisionObserver` output `encode_state`s to the model's `(128, 395)` input.
- ✅ A synthetic vision trace run through **`train.py`** produced **9/9 click labels
  resolved to real elements** and saved a current **395-feature** checkpoint.
- ✅ `ScreenObserver(perception="vision")` selects the vision observer, skips UIA/Excel,
  and captures valid trainable states; `_translate_and_save` passes the vision state
  into traces unchanged.

**One known caveat (acceptable):** vision captures only the foreground form, so there
are no *background* (Notepad source) elements. The model's source-pointer head — which
maps a typed value to a source element — therefore won't train. That's fine: at run
time the **LLM supplies the value** (via `NotepadDataSource`), not that head. The
**click / navigation** head (the thesis claim) trains fully.

---

## Prerequisites

- The vision deps (already installed here): `opencv-python`, `pytesseract`, `mss`,
  `Pillow`, plus a system Tesseract install. See
  `components/observers/vlm/vision_observer/requirements.txt`.
- **Record on the primary monitor at 100% display scaling.** The recorder logs mouse
  clicks in absolute screen pixels and runs CV on the full-screen capture; at 100%
  scaling on the primary monitor these share one coordinate space, so a click maps to
  the element it landed on. (Other scalings/monitors can introduce an offset.)

---

## Step 1 — Record vision demonstrations

Open the form (maximized) and the Notepad source, then:

```bash
py -3.14 record_trace.py --perception vision --output data/output/traces/vision
```

- Demonstrate the task the way you want it cloned: fill fields in a **consistent
  order**, switch tabs, submit, advance records.
- Press **Ctrl+C** to stop; traces save to a `session_*` folder under the output dir.
- Each run = one session. **Record many passes** — the UIA model needed ~20–30 passes
  for one tab and ~20 full demos for all 8 tabs; vision will need a similar order.
  More, cleaner, consistent passes = better cloning.
- Vision perception is slower per frame (~1–2 s for CV+OCR), so demonstrate at a
  relaxed pace.

> Tip: keep the form on the primary monitor and don't cover it while recording.

## Step 2 — (optional) Clean the demos

```bash
py -3.14 scripts/clean_demos.py data/output/traces/vision data/output/traces/vision_clean
```

Drops dropdown-selection clicks, off-window junk, and consecutive duplicates.

## Step 3 — Train

```bash
py -3.14 train.py \
  --trace_dir data/output/traces/vision \
  --save_path tasks/form_filling/model_vision.pt \
  --epochs 80 --d_model 128 --num_layers 4 --dim_feedforward 256 --hist_len 4
```

Produces a current 395-feature checkpoint at `model_vision.pt`. (Use `--device cpu`
to force CPU; default auto-selects CUDA on the RTX 4050.)

## Step 4 — Check it cloned (offline)

```bash
py -3.14 scripts/test_clone.py data/output/traces/vision/session_<latest>
```

Reports exact-match % and the click-offset distribution — i.e. did the model learn to
click the elements you demonstrated.

## Step 5 — Run the agent on vision

With LM Studio running and the form open:

```bash
py -3.14 run_task.py --perception vision --model tasks/form_filling/model_vision.pt
```

The agent screenshots the live form each step, perceives via CV/OCR, predicts the
field, and acts — live, on the real form.

---

## What changed in the code

| File | Change |
|---|---|
| `components/recorder/recorder.py` | `ScreenObserver(perception="vision")` — runs CV/OCR on the captured frame, records demos as vision states |
| `record_trace.py` | `--perception {auto,vision}` flag |
| `run_task.py` | `--perception {uia,vision}` flag — run the agent on vision |
| `components/observers/vlm/vision_observer/` | the CV vision observer + detector (the perception component) |
| `scripts/perception_eval.py` | vision-vs-UIA scorer (`--live`) |

---

## Honest expectations

- A clean end-to-end vision run depends on **enough good demos** — under-recording will
  clone poorly, same as it would for UIA.
- The live vision agent runs at a more deliberate pace than UIA (screenshot + OCR per
  step).
- This is the foundation for the real goal: a live agent that perceives **any** app
  from pixels, not just ones with a clean accessibility tree.
