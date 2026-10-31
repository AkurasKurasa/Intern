# Objective 7 — RPA comparison (panel minimum)

The panel asked for this at the final defense (8 Oct 2026):

- **Dr. Esquivel:** "At minimum, test Intern against one simple tool (such as UI.Vision) on the Scope 1 form, measuring setup time and whether it still works after the layout changes."
- **Mr. Mallari:** use the same tasks and test conditions, and compare setup time, task completion, execution errors, and performance when the GUI changes.
- **Dr. Aviles:** no comparison with existing RPA tools was presented.

This folder runs exactly that minimum. The design was fixed on 2026-10-24 (DEVELOPERS.md, `evaluation_rpa_comparison`); this README turns it into a step-by-step plan.

---

## 1. Design (fixed before testing)

| | |
|---|---|
| **Systems** | Intern vs UI.Vision RPA (+ XModules for desktop) |
| **Task** | Fill record 1 of `data_entry_tasks/data_entry_intake.txt` into the Scope 1 form, then Submit |
| **Normal form** | `practice_apps/car_insurance_entry/car_insurance_form_wx.py` |
| **Modified form** | `practice_apps/car_insurance_entry/car_insurance_form_wx_MESSY_TEST.py`: different colour theme, and the Policy Information rows in a different order. Every field keeps its label and name. |
| **Runs** | 5 on the normal form + 5 on the modified form, per system (20 runs) |
| **Rule** | The RPA automation is built on the normal form only. It is **not edited** before the modified-form runs. |
| **Measures** | Setup time · finished without help · fields wrong · fully correct · minutes per run |

Why "same record, 5 times": it measures whether each system does the same job reliably, and keeps the work to one afternoon. It does not measure generalisation to new records. Say so in the thesis.

---

## 2. Which tool, and is it viable?

| Option | Can it drive the wx form? | Setup effort | What it tests | Verdict |
|---|---|---|---|---|
| **UI.Vision + XModules** | Yes, but desktop mode has **no recorder**. Steps are added by hand: `XClick` on an image or coordinates, `XType` for keys. | Medium | Record-and-replay, the closest analogue to Intern. Named by the panel chair. | **Use this.** |
| **PyAutoGUI script** | Yes, with the same keyboard approach | Low | Hard-coded sequence | **Fallback** if UI.Vision fails the 15-minute gate. Already listed as a baseline in Sec 3.3.5. |
| Power Automate Desktop | Yes, records native controls by name | Medium–high | A selector-based RPA that may *survive* the reorder | Not for the minimum. Mention as future work. |
| UiPath Community | Yes | High (install + account) | Commercial RPA | Out of budget |

**How UI.Vision should drive this form.** Image matching fails on the modified form's new colours before it can show anything else. So drive the form with the **keyboard**, the way a record-and-replay user would:
1. One `XClick` at fixed screen **coordinates** to put focus in the first field.
2. A loop over a CSV of record 1's values in form order: `XType` each value, then `${KEY_TAB}`.
3. A next-tab keystroke at each tab boundary, then Submit.

The base intake packet is already printed in form order, so building the CSV is mostly copy-paste. That copy-paste is part of RPA setup and is timed.

On the modified form, the Tab order inside Policy Information is different, so this macro should type values into the wrong boxes without any error. Writing down exactly which value lands where is the most useful result of the whole test.

---

## 3. Go / no-go gates (do these first, ~25 min)

**Gate 1 — UI.Vision can drive the form (15 min max, not timed as setup).**
Install the UI.Vision extension and the XModules installer, then enable Desktop Automation. On the normal form, check that a tiny test macro can:
- [ ] type into a text box
- [ ] set a dropdown by typing its option text (e.g. `Full Coverage`)
- [ ] tick a checkbox with `${KEY_SPACE}`, and the tick shows up in the saved submission JSON
- [ ] move to the next form tab (Ctrl+Tab, or `XClick` on the tab header)
- [ ] press Submit

If any box can't be ticked within 15 minutes → switch to the PyAutoGUI fallback with the same design, and write down why.

**Gate 2 — Intern runs on the modified form (1 run).**
The MESSY form has never been run live (DEVELOPERS.md, "not yet exercised").
- [ ] Launch `car_insurance_form_wx_MESSY_TEST.py`, run `python run_task.py --start_record 1 --end_record 1`, and confirm it reaches Submit.

If Intern fails here, keep going anyway. A failure is a result; report it.

**Gate 3 — the scorer is calibrated.**
- [ ] Fill record 1 **by hand** once, Submit, and run `python scripts/rpa_score.py` on it. It should report 0 fields wrong. If not, the scorer and the form disagree, and that must be fixed before any real run is scored.

---

## 4. Running it

Before each run: close the form, reopen the right version, and make sure nothing else is focused. Note the time.

### UI.Vision
1. **Setup (timed, 45-min cap).** Start the stopwatch with UI.Vision open and nothing built. Stop it when one replay fills record 1 correctly on the **normal** form. Put the minutes in the `setup` row. If the cap is reached, write `cap reached` in notes and report "no working automation in 45 min".
2. **Normal form × 5.** Run the macro. Put the newest file name from `data/output/submissions/` in `submission`, and fill in `finished_without_help` and `minutes`.
3. **Modified form × 5.** Same macro, **not edited**. Same columns. In `notes`, write what happened ("typed Policy Term into Policy Type", "crashed at…").

### Intern
1. **Setup row.** Intern's setup is demonstration + training + first successful run. A fresh setup (re-recording and retraining) does not fit in the budget, so report the real historical cost: the number of demonstration sessions and their total recording time (trace timestamps), plus the training time (`transformer_training_log.jsonl` timestamps). Write how it was obtained in notes. **Expect this to be longer than UI.Vision's setup.** If so, report it plainly.
2. **Normal × 5, modified × 5:** `python run_task.py --start_record 1 --end_record 1`, then fill the same columns.

### Optional (10 min): workload
After both systems' setup, the operator fills one NASA-TLX form per system. This is one person, once, so report it descriptively only.

---

## 5. Scoring

```
python scripts/rpa_score.py
```

This writes `run_sheet_scored.csv` and prints, per system and form version: runs finished, runs fully correct, mean field correctness, Policy-tab correctness, and minutes. It also runs Fisher's exact test on fully-correct runs, Intern vs UI.Vision, for each form version.

- A field counts as correct when it matches the intake record. A box that should be unchecked counts as correct when it is unchecked. **This fixes a bug in `expected_vs_actual.py`**: that script counts every correctly unchecked box as "missing", so a perfect fill of record 1 scores only 146/171 (85.4%) and no run can ever be fully correct.
- `policy_number` and `policy_status` are skipped as metadata by `bc_fidelity.py`, so the Policy tab is scored over 11 fields, not 13.
- With 5 runs per cell, significance is only possible for a large difference. For example, 0/5 vs 4/5 gives p = .048, and 0/5 vs 5/5 gives p = .008. Setup time is one measurement per system, so it is reported, not tested.

---

## 6. What this can and cannot claim

**Can claim:** on the Scope 1 form, the setup time of each system; whether each finishes record 1 correctly on the original form; and whether each still does after a reorder + re-theme — and if not, how it fails.

**Cannot claim:** a general advantage over RPA (one tool, one task, one record, one operator), cognitive workload reduction (at most one NASA-TLX per system), or anything about Scopes 2 and 3.

**Disclose in the write-up:**
1. The operator's RPA experience level (write it down **before** starting).
2. The operator followed this written guide, which shortens RPA setup.
3. The modified form was built by the researchers, so it is not a random sample of real interface changes.
4. On this form, Intern fills fields mainly through its label-matched fast-fill path, and the transformer is rarely consulted (DEVELOPERS.md, `evaluation_transformer_ablation_test`). A result here mostly tests Intern's **label-based targeting**, not the learned policy.
5. Power Automate Desktop or UiPath select elements by name and might survive the reorder. UI.Vision in keyboard mode represents record-and-replay, not every kind of RPA.

**Objective 7's verdict.** The objective asks for >10–20% improvement in setup time, adaptability, execution error **and** workload, with significance. One tool on one scope, with no workload study, can support at most **PARTIALLY MET** (or report the dimensions measured and keep the rest NOT EVALUATED). Decide with the adviser; do not mark it MET.

---

## 7. Time budget (one Windows machine, one afternoon)

| Step | Time |
|---|---|
| Gates 1–3 | ~25 min |
| UI.Vision setup | ≤ 45 min |
| UI.Vision 10 runs | ~30 min |
| Intern 10 runs | depends on run length; mostly unattended |
| Intern setup figures from logs | ~15 min |
| Scoring + table | ~10 min |

---

## 8. Thesis changes after the runs

- **Sec 3.3.5:** rewrite "as designed" into "as conducted": one baseline (UI.Vision), Scope 1, record 1, 5 + 5 runs, keyboard-driven macro, modified form = MESSY variant. Keep UiPath / PyAutoGUI as the full design for future work.
- **Ch 4:** add Objective 7 results (setup time, completion, fields wrong, the modified-form outcome) with the disclosures above.
- **Ch 5:** update the Summary, Conclusion and Recommendation for Objective 7, and the verdict tally (currently "one not evaluated").
- **ODRF:** responses for Dr. Esquivel (Objective 7), Dr. Aviles ("No comparison…", "Address objective 7"), and Mr. Mallari.
