# Objective 7 — thesis draft text (paste after the runs)

> **Update 2026-11-01:** the comparison has been run (PyAutoGUI fallback baseline; UI.Vision was not installed). The filled-in version of this text, with the measured numbers, is in `Objective7_Insertion_Guide.pdf` (kept outside the repo). This file stays as the template.

Drafted 2026-10-31 for the manuscript `Final Document.docx (18).pdf` (278 pp.). Page numbers below are the printed page numbers in that version.

**Rules for using this file**

- Everything marked `[VERIFY]` is a number or fact not yet measured. Replace it with the value from `python scripts/rpa_score.py` (or the logs named next to it). Do not paste a `[VERIFY]` into the manuscript.
- Where a paragraph has **[IF …]** alternatives, keep the one that matches the actual result and delete the others. Do not keep a favourable sentence the data does not support.
- Voice: third person ("the researchers"), past tense for what was done, APA 7. Citations already in the manuscript's reference list: Sculley et al. (2015), Lipton and Steinhardt (2018), Tuyishime et al. (2023), Zhou et al. (2023), Peffers et al. (2007). Two new references are needed (see §6).
- Verdict: one tool, one scope, no workload study. Objective 7 can be at most **PARTIALLY MET**. Agree the final wording with Ms. Pantig. Never mark it MET.

---

## 1. Section 3.3.5 Comparative Evaluation — replaces pp. 92–94

> Replace the whole of Section 3.3.5 (from "Objective 7 called for…" to "…these expectations remain untested.") with the text below. The "Role of Baselines" paragraph is kept almost as it was.

### 3.3.5 Comparative Evaluation

Objective 7 called for a comparison between Intern and existing automation tools in terms of setup time, adaptability to interface changes, execution errors, and cognitive workload. The comparison was originally designed around three baseline tools across all three scopes. Following the recommendation of the panel at the final defense, a reduced comparison was conducted: Intern was tested against one record-and-replay tool on the Scope 1 form, measuring setup time, task completion, execution errors, and whether each system still worked after the layout of the form was changed. This section describes the comparison as it was conducted. The full three-tool design is retained as future work in Section 5.3.

**Role of Baselines.** A baseline is a reference system that represents the current state of practice, or what a user would reasonably employ in the absence of the proposed solution. Rather than serving as a competitor, a baseline provides an objective anchor that makes it possible to isolate and quantify the contribution of a novel system (Sculley et al., 2015). A poorly chosen baseline, whether too weak or fundamentally different in scope, can inflate or deflate the apparent effectiveness of the proposed approach (Lipton & Steinhardt, 2018). The baseline for this study was therefore chosen to be a practical alternative that an office practitioner would genuinely consider, and to embody a limitation identified in Chapter 1: brittleness under interface change.

**Baseline System.** UI.Vision RPA, with its XModules extension for desktop automation, was selected as the baseline. UI.Vision represents record-and-replay automation, which replays a fixed sequence of steps without learning or generalization, and it is the existing tool closest to Intern's demonstration-based input. Because UI.Vision does not provide a recorder for desktop applications, its desktop steps were constructed by hand. Image matching was not used, because the modified form changes every colour on screen and image matching would fail for that reason alone, before the effect of the layout change could be observed. The macro therefore drove the form through the keyboard, as a record-and-replay user would: a single click at fixed screen coordinates placed the cursor in the first field, after which the macro typed each value of the record from a comma-separated file in form order, pressed Tab to move to the next field, switched form tabs at each tab boundary, and pressed Submit. A scripted PyAutoGUI baseline using the same keyboard approach was prepared as a fallback in case UI.Vision could not drive the wxPython form within 15 minutes. [IF the fallback was used: replace "UI.Vision" with "PyAutoGUI" throughout this section and state the reason the UI.Vision check failed: `[VERIFY]`.] UiPath Community Edition and Power Automate Desktop, which select interface elements by name rather than by position, were not tested within the study period.

**Task and Interfaces.** Both systems performed the same task: entering record 1 of the Scope 1 intake packet into the car-insurance data-entry form and submitting it. Two versions of the form were used. The standard condition used the original form. The modified condition used a variant built by the researchers in which the colour theme was changed throughout and the six rows of the Policy Information section were placed in a different order (Policy Type, Expiration Date, Policy Number, Policy Term, Effective Date, Policy Status, instead of Policy Number, Policy Status, Policy Type, Policy Term, Effective Date, Expiration Date). Every field kept its label and internal name, so the change affected the visual appearance and on-screen order of the form but not what each field was called.

**Procedure.** One researcher operated both systems on the same computer. Before each run, the form was closed and reopened in the required version. For UI.Vision, setup time was measured with a stopwatch from the moment the tool was open with nothing built to the first replay that filled record 1 correctly on the original form, with a cap of 45 minutes. The macro was built on the original form only and was not edited before the runs on the modified form. For Intern, setup consists of recording demonstrations, training the model, and a first successful run. Repeating this process within the comparison was not feasible, so Intern's setup time was taken from the historical cost of building the Scope 1 model: the number of recorded demonstration sessions and their total recording time, obtained from the timestamps in the recorded traces, and the training time, obtained from the timestamps in the training log. Each system then executed the task five times on the original form and five times on the modified form, for twenty runs in total. Repeating the same record measures whether each system performs the same task reliably; it does not measure generalization to new records.

**Measures.** Setup time was measured separately from execution. For each run, the researchers recorded whether the system reached Submit without human help and the wall-clock minutes for the run. Both systems submitted through the same form, which saves every submission to a file, so both were scored in the same way, from the same kind of file, against the same intake record. A field was counted as correct when its submitted value matched the intake record, and a checkbox that should be unchecked was counted as correct when it was left unchecked. Following the scoring script used elsewhere in the study, the policy number and policy status were excluded from the field count, leaving 171 scored fields, 11 of them on the Policy tab; because the policy number is in fact entered from the record, whether it was saved is reported separately. A run was counted as fully correct only when every scored field was correct, consistent with the distinction between completion and success drawn in Section 3.3.3 (Zhou et al., 2023). Execution errors were counted as the number of scored fields that were wrong.

**Analysis.** For each system and form version, the researchers report the number of runs finished without help, the number of fully correct runs, the mean field correctness, the Policy-tab correctness, and the mean minutes per run. The number of fully correct runs was compared between the two systems on each form version using Fisher's exact test, two-sided, at α = .05 (Agresti, 2013) `[VERIFY reference]`. With five runs per system and condition, only a large difference can reach significance; for example, 0 of 5 against 5 of 5 gives p = .008, and 0 of 5 against 4 of 5 gives p = .048. Setup time is a single measurement per system and was therefore reported descriptively rather than tested. Cognitive workload was not studied. [IF the optional NASA-TLX was completed: "The operator completed one NASA Task Load Index questionnaire for each system after setup (Hart & Staveland, 1988); with one respondent, these scores are reported descriptively only."]

**Expected Outcome.** Because Intern's setup consists of many recorded demonstrations and a training run, its setup time was expected to be longer than that of a single hand-built macro. Because the keyboard macro follows a fixed Tab order, it was expected to place values into the wrong fields on the modified form without reporting an error, whereas Intern locates fields by their labels and was expected to be less affected by the reordering. These expectations were tested as described above.

---

## 2. Chapter 4 — new Section 4.1.7 (Objective 7 results)

> Insert before the current "4.1.7 Summary of Objective Attainment" (p. 111), and renumber that to **4.1.8**. Add an Objective 7 row to Figure 11 (or a note under it). All numbers come from `python scripts/rpa_score.py` and `docs/rpa_comparison/run_sheet_scored.csv`.

### 4.1.7 Comparative Evaluation

Objective 7 called for Intern to show quantifiable improvements (>10–20%) over traditional rule-based RPA tools in setup time, adaptability to interface changes, execution errors, and cognitive workload, with a statistically significant reduction compared with baseline tools (Objective 7a). As described in Section 3.3.5, a reduced comparison was conducted against one record-and-replay tool, UI.Vision RPA, on the Scope 1 form, with five runs per system on the original form and five on the modified form. Table `[VERIFY: table number]` summarizes the results.

**Table [VERIFY].** *Intern and UI.Vision RPA on the Scope 1 form, record 1*

| System | Form | Setup (min) | Finished without help | Fully correct | Mean field correctness | Policy tab correct (of 11) | Mean minutes per run |
|---|---|---|---|---|---|---|---|
| UI.Vision | Original | `[VERIFY]` | `[VERIFY]`/5 | `[VERIFY]`/5 | `[VERIFY]`% | `[VERIFY]` | `[VERIFY]` |
| UI.Vision | Modified | — | `[VERIFY]`/5 | `[VERIFY]`/5 | `[VERIFY]`% | `[VERIFY]` | `[VERIFY]` |
| Intern | Original | `[VERIFY]` | `[VERIFY]`/5 | `[VERIFY]`/5 | `[VERIFY]`% | `[VERIFY]` | `[VERIFY]` |
| Intern | Modified | — | `[VERIFY]`/5 | `[VERIFY]`/5 | `[VERIFY]`% | `[VERIFY]` | `[VERIFY]` |

*Note.* Fully correct = every one of the 171 scored fields matched the intake record. Fisher's exact test (two-sided) on fully correct runs: original form p = `[VERIFY]`; modified form p = `[VERIFY]`. Setup time is one measurement per system and was not tested.

**Setup time.** UI.Vision required `[VERIFY]` minutes to build a macro that filled record 1 correctly on the original form. [IF capped: "UI.Vision did not produce a working macro within the 45-minute cap."] Intern's setup consisted of `[VERIFY]` recorded demonstration sessions totalling `[VERIFY]` minutes of recording, followed by `[VERIFY]` minutes of training, for a total of `[VERIFY]` minutes. These figures were taken from the trace timestamps and the training log rather than from a new setup. [IF Intern's setup was longer, which is expected: "Intern therefore required more setup than the record-and-replay baseline, by a factor of about `[VERIFY]`, which is contrary to the expectation, stated when the comparison was designed, that demonstration learning would reduce setup effort. The difference reflects the volume of demonstrations needed to train the model; a single hand-built macro requires no training."] [IF Intern's setup was shorter: report the difference in minutes and as a percentage, and compare it with the >10–20% target.]

**Original form.** On the original form, UI.Vision finished `[VERIFY]` of 5 runs without help and produced `[VERIFY]` fully correct submissions, with a mean field correctness of `[VERIFY]`%. Intern finished `[VERIFY]` of 5 runs and produced `[VERIFY]` fully correct submissions, with a mean field correctness of `[VERIFY]`%. [Describe the fields Intern got wrong, if any, from the `wrong_fields` output: `[VERIFY]`.] The difference in fully correct runs was [IF p < .05: "statistically significant (p = `[VERIFY]`)"] [IF p ≥ .05: "not statistically significant (p = `[VERIFY]`)"].

**Modified form.** On the modified form, the UI.Vision macro, which was not edited, finished `[VERIFY]` of 5 runs and produced `[VERIFY]` fully correct submissions. [IF values landed in the wrong fields: "Because the macro follows the original Tab order, it typed the values intended for the reordered Policy Information rows into different fields — for example, `[VERIFY: value]` intended for `[VERIFY: field]` was entered into `[VERIFY: field]` — and submitted the form without reporting any error. Only `[VERIFY]` of the 11 Policy-tab fields were correct, while the other tabs, which were not changed, were unaffected (`[VERIFY]`%)."] [IF the macro stopped or crashed instead: describe where and why: `[VERIFY]`.] Intern finished `[VERIFY]` of 5 runs on the modified form and produced `[VERIFY]` fully correct submissions, with `[VERIFY]` of the 11 Policy-tab fields correct. The difference in fully correct runs was [significant / not significant] (p = `[VERIFY]`).

**Execution errors.** Across all five runs on each form, UI.Vision entered a mean of `[VERIFY]` wrong fields on the original form and `[VERIFY]` on the modified form; Intern entered `[VERIFY]` and `[VERIFY]`. [State the change from original to modified form for each system as a percentage, and compare with the >10–20% target: `[VERIFY]`.]

**Cognitive workload.** Cognitive workload was not studied. [IF the optional NASA-TLX was completed: "The operator's NASA Task Load Index scores were `[VERIFY]` for setting up UI.Vision and `[VERIFY]` for Intern; with one respondent and one occasion, these values are descriptive only and do not test the workload dimension of Objective 7."]

**Limitations of the comparison.** Five conditions limit what these results can support. First, the operator had `[VERIFY: describe — e.g., no prior / some prior]` experience with UI.Vision before the comparison, recorded before testing began. Second, the operator followed a written step-by-step guide prepared by the researchers, which shortens the setup of a hand-built macro, so the UI.Vision setup time is likely lower than that of a user without such a guide. Third, the modified form was built by the researchers and represents one kind of interface change, a reordering and re-theming of existing fields; it is not a sample of the changes that occur in real applications. Fourth, on this form Intern fills most fields through its label-matched fast-fill path, and the transformer is rarely consulted, so the results mainly test Intern's label-based targeting rather than its learned policy. Fifth, UI.Vision driven by the keyboard represents record-and-replay automation, not every kind of RPA; selector-based tools such as UiPath and Power Automate Desktop, which locate elements by name, might not be affected by the reordering in the same way.

**Verdict.** Objective 7 was **partially met** `[VERIFY: confirm with adviser]`. [IF Intern did better on the modified form: "Intern retained `[VERIFY]` fully correct runs after the layout change while the record-and-replay baseline retained `[VERIFY]`, which supports the adaptability dimension on this form."] [IF setup was longer: "Setup time did not improve relative to the baseline."] Execution errors `[VERIFY: improved / did not improve]`. Cognitive workload was not evaluated, and the comparison covered one tool on one scope, so no general advantage over rule-based RPA can be claimed, and Objective 7a was `[VERIFY: met only for … / not met]`.

> **Alternative verdict** (if the adviser prefers it): "Objective 7 was evaluated in part: setup time, task completion, and execution errors were measured against one baseline on Scope 1, while cognitive workload and the remaining scopes were not evaluated." Use this wording in the tally below instead.

### Renumbered 4.1.8 Summary of Objective Attainment — changed sentences (p. 111)

Replace:

> "Of the seven, two were met, four were partially met, and one was not evaluated; no objective was found to be not met in every scope in which it was measured."

with:

> "Of the seven, two were met and five were partially met `[VERIFY: tally after verdict]`; no objective was found to be not met in every scope in which it was measured."

Replace:

> "Objective 7 was not evaluated."

with:

> "Objective 7 was partially met: it was evaluated against one record-and-replay tool on Scope 1 only, without a study of cognitive workload `[VERIFY: verdict]`."

---

## 3. Section 5.1 Summary — "Evaluation" paragraph (p. 124)

Replace:

> "The comparison of Intern against rule-based RPA tools in terms of setup time, adaptability to interface changes, execution errors, and cognitive workload (Objectives 7 and 7a) was not conducted. Objective 7 was not evaluated. Overall, two objectives were met, four were partially met, and one was not evaluated; …"

with:

> "Intern was compared with UI.Vision RPA, a record-and-replay tool, on the Scope 1 form, with five runs per system on the original form and five on a modified form with reordered fields and a different colour theme. UI.Vision required `[VERIFY]` minutes of setup and Intern `[VERIFY]` minutes, the latter taken from the historical cost of recording and training. On the original form, UI.Vision produced `[VERIFY]` and Intern `[VERIFY]` fully correct submissions out of five. On the modified form, UI.Vision produced `[VERIFY]` and Intern `[VERIFY]` (p = `[VERIFY]`). Cognitive workload was not studied. Objective 7 was partially met `[VERIFY]`. Overall, two objectives were met and five were partially met `[VERIFY]`; …"

> Also fix in Sec 5.1 while there: the Target Accuracy figures 92.0 / 83.0 / 73.0 should match Chapter 4's 92.4 / 83.1 / 73.4 (handoff §6).

## 4. Section 5.2 Conclusions — "Evaluation" paragraph (p. 127)

Replace:

> "Whether learning from demonstration reduces setup effort, improves robustness, or lowers cognitive workload relative to rule-based RPA tools cannot be concluded from this study, as the comparative evaluation was not conducted."

with (keep the alternative that matches the result):

> "[IF Intern did better on the modified form:] Against a record-and-replay baseline on a structured form, learning from demonstration made the automation more robust to a reordering of fields: the baseline `[VERIFY: continued to submit but placed values in the wrong fields]`, while Intern `[VERIFY]`. This robustness came at a higher setup cost, since a learned model requires many demonstrations and a training run where a hand-built macro requires neither `[VERIFY: only if setup was longer]`. Because the comparison involved one tool, one form, and one type of interface change, and did not study cognitive workload, it does not establish a general advantage over rule-based RPA."

> "[IF Intern did not do better:] Against a record-and-replay baseline on a structured form, learning from demonstration did not yield a measurable advantage in `[VERIFY: dimension]`. …"

## 5. Section 5.3 Recommendations — "Conduct the comparative evaluation…" (p. 134)

Replace the paragraph with:

> **Extend the comparative evaluation.** The comparison under Objective 7 was limited to one record-and-replay tool on the Scope 1 form and did not study cognitive workload. Future work should compare Intern with a selector-based tool such as UiPath or Power Automate Desktop, which locates interface elements by name and may tolerate reordering, and with a scripted approach such as PyAutoGUI, on all three scopes; use several records and more than one kind of interface change, including renamed labels and resized windows; include operators who did not build the system; and measure cognitive workload with a standardized instrument such as the NASA Task Load Index (Hart & Staveland, 1988) across several participants. Well-chosen baselines are what make such claims verifiable, since they isolate the contribution of the proposed system (Sculley et al., 2015), while baselines that are too weak or differ in scope can inflate or deflate its apparent effectiveness (Lipton & Steinhardt, 2018). A fresh, timed setup of Intern, rather than historical figures, would also allow setup time to be compared under identical conditions.

---

## 6. References to add (APA 7) — verify each before pasting

- Agresti, A. (2013). *Categorical data analysis* (3rd ed.). Wiley. `[VERIFY edition/year against the copy you cite]`
- Hart, S. G., & Staveland, L. E. (1988). Development of NASA-TLX (Task Load Index): Results of empirical and theoretical research. In P. A. Hancock & N. Meshkati (Eds.), *Advances in psychology* (Vol. 52, pp. 139–183). North-Holland. https://doi.org/10.1016/S0166-4115(08)62386-9 `[VERIFY — only needed if NASA-TLX is mentioned]`
- UI.Vision RPA is software, not a reference in the current list. If your adviser wants software cited, use APA 7's software format: Ui.Vision. (`[VERIFY year]`). *UI.Vision RPA* (Version `[VERIFY]`) [Computer software]. https://ui.vision/

---

## 7. A finding that affects an existing thesis number (decide before defending it)

The Scope 1 field-correctness figure "60–78%" (Sec 4.1.6, pp. 110–111; also quoted in the ODRF response on completed-vs-successful) comes from `bc_fidelity.py`'s `field_match_rate` (read by `scripts/thesis_figures/objective_metrics.py`, `s1_field_correctness`). Checked on 2026-10-31:

- When the gold standard is built from the intake file (as the existing `data/output/reference/gold_standard.json` is), `field_match_rate` counts a correctly **unchecked** box as not matched and also keeps `policy_number`/`policy_status` in the denominator. A **perfect** fill therefore scores at most **75.3% – 88.4%** depending on the record (record 1: 148/173 = 85.5%).
- `expected_vs_actual.py` has the same flaw (record 1: 146/171 = 85.4%; records 1–10: 75.0% – 88.3%).
- So the 60–78% figure is **understated**, by an amount that cannot be recomputed here because `data/output/bc_progress.jsonl` (the per-run scores) is not on this machine.

**What to do:** do not change the number by hand. Either (a) re-score the original Scope 1 submissions with `rpa_score.py`'s rule, if they still exist on the machine that ran them, and report the corrected range, or (b) add one sentence noting that the scorer counted correctly unchecked boxes as unfilled, so the figure is a lower bound. Agree which with the adviser.

## 8. Other open manuscript items (from the handoff, not Objective 7)

- p. 80: the TSR bullet's last sentence implies TSR was computed for Scopes 1–2 (it was not); "independently accomplish complete workflows" overstates.
- p. 125: missing space in "trained on.This" (the handoff says p. 126; in this PDF it is on printed p. 125).
- Sec 5.1 Target Accuracy 92.0 / 83.0 / 73.0 vs Chapter 4's 92.4 / 83.1 / 73.4.
- Appendix I.1 reports 52.25% where Chapter 4 reports 92.4%.
- ODRF page numbers are stale. In this PDF, Sec 5.1's "Evaluation" paragraph and the 5.2 heading are on p. 124, and Sec 5.2's "Evaluation" paragraph is on p. 127 (the form says 122 and 125).
