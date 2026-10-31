# Objective 7 — ODRF responses (paste after the runs)

Drafted 2026-10-31 for `GroupINTERNS_ODRF (Final Defense)`, "Response" and "Page Number" columns. Same style as the responses already on the form: third person, what was done, and where to find it.

Before pasting:
- Replace every `[VERIFY]` with the measured value from `python scripts/rpa_score.py`.
- Replace every `[p. VERIFY]` with the page number in the revised manuscript.
- Keep only the **[IF …]** alternative that matches the result.
- Confirm the verdict ("partially met") with Ms. Pantig first.

Page-number targets: Sec 3.3.5 (currently pp. 92–94), the new Sec 4.1.7 (inserted after the current p. 111), Sec 4.1.8 summary, Sec 5.1 (p. 124), Sec 5.2 (p. 127), Sec 5.3 (p. 134). These will move after the edits.

---

## 1. Dr. James A. Esquivel — "Compare Intern with existing automation tools (Objective 7)…"

The comment is listed twice on the form. Put this response in the first row; in the second, write "See response above."

**Response**

> The researchers conducted the comparison at the minimum the panel recommended. Intern was tested against UI.Vision RPA, a record-and-replay tool, on the Scope 1 form, using the same intake record and the same scoring for both systems. Each system ran five times on the original form and five times on a modified version in which the colour theme was changed and the Policy Information fields were reordered, with the UI.Vision macro left unedited. UI.Vision required `[VERIFY]` minutes of setup and Intern `[VERIFY]` minutes, the latter taken from the recorded demonstrations and training log. After the layout change, UI.Vision produced `[VERIFY]` and Intern `[VERIFY]` fully correct submissions out of five (Fisher's exact test, p = `[VERIFY]`). [IF applicable: "The UI.Vision macro still submitted the form but typed the reordered Policy fields into the wrong boxes without any error."] Because only one tool and one scope were tested and cognitive workload was not studied, Objective 7 is reported as partially met, and the earlier claim that Intern is easier to set up `[VERIFY: has been withdrawn / is qualified]`. The method is in Section 3.3.5, the results in Section 4.1.7, and the conclusions in Sections 5.1 and 5.2; the full comparison with UiPath and other tools is recommended in Section 5.3.

**Page Number**

> pp. `[p. VERIFY]` (Sec. 3.3.5); pp. `[p. VERIFY]` (Sec. 4.1.7); p. `[p. VERIFY]` (Sec. 5.1); p. `[p. VERIFY]` (Sec. 5.2); p. `[p. VERIFY]` (Sec. 5.3)

---

## 2. Dr. Joey S. Aviles — "No comparison with existing RPA tools was presented."

**Response**

> A comparison is now presented. Intern was compared with UI.Vision RPA on the Scope 1 form under the same task, record, and scoring, on both the original form and a modified form with reordered fields. The results for setup time, task completion, wrong fields, and performance after the change are reported in Section 4.1.7, with the method in Section 3.3.5.

**Page Number**

> pp. `[p. VERIFY]` (Sec. 3.3.5); pp. `[p. VERIFY]` (Sec. 4.1.7)

---

## 3. Dr. Joey S. Aviles — "Address objective 7" (assigned: Ralph)

**Response**

> Objective 7 is now addressed in every chapter that reports objectives. Section 3.3.5 describes the comparison as conducted, Section 4.1.7 reports its results and verdict, and Sections 5.1, 5.2, and 5.3 summarize, conclude, and recommend accordingly. Objective 7 changed from not evaluated to partially met `[VERIFY]`: setup time, task completion, and execution errors were measured against one record-and-replay tool on Scope 1, while cognitive workload and the other scopes were not evaluated. The objective tally is now two met and five partially met `[VERIFY]`.

**Page Number**

> pp. `[p. VERIFY]` (Sec. 3.3.5); pp. `[p. VERIFY]` (Sec. 4.1.7); p. `[p. VERIFY]` (Sec. 4.1.8); pp. `[p. VERIFY]` (Secs. 5.1–5.3)

---

## 4. Mr. Reniel Mallari — "…comparison between Intern and traditional RPA using the same tasks and test conditions…" (assigned: Calma)

**Response**

> The researchers compared Intern and UI.Vision RPA under the same task and test conditions: the same Scope 1 form, the same intake record, the same computer and operator, five runs per system on the original form and five on a modified form, and the same scoring of each submission against the intake record. Both systems were evaluated on setup time, task completion without help, execution errors (fields entered incorrectly), and performance after the interface changed. On the modified form, UI.Vision produced `[VERIFY]` and Intern `[VERIFY]` fully correct submissions out of five, and the mean number of wrong fields was `[VERIFY]` and `[VERIFY]`. The comparison covers Scope 1 only; extending it to the other scopes and to more tools is recommended in Section 5.3.

**Page Number**

> pp. `[p. VERIFY]` (Sec. 3.3.5); pp. `[p. VERIFY]` (Sec. 4.1.7); p. `[p. VERIFY]` (Sec. 5.3)

---

## Note on an existing response (Daniel's, already on the form)

The response to "If Intern completes a task but enters incorrect data, should it be considered successful?" says "only 60–78% of fields were filled correctly in Scope 1". That figure is understated: the scorer that produced it counts correctly unchecked boxes as unfilled, so even a perfect fill scores at most 75–88%. See `thesis_drafts.md` §7. Decide with the adviser whether to re-score or add a lower-bound note, then update this response to match the manuscript.
