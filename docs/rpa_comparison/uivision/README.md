# Objective 7, Scope 1, re-run with UI.Vision RPA

The first comparison used a PyAutoGUI script in place of an RPA product (Sec 3.3.5 said
"UI.Vision … was not available on the test computer"). This folder re-runs the Scope 1
comparison with **UI.Vision RPA** itself, the tool the panel chair named. The macro is played by
UI.Vision's desktop app, **Ui.Vision for Desktop 2.1.43** (a9t9 software GmbH), with no
browser involved.

Everything else stays the same: the normal and modified forms, record 1, 5 + 5 runs, and the
scoring. Intern is not re-run; its 10 runs on the same forms and record are reused.

## How the macro works

UI.Vision's desktop automation has no recorder and cannot read a native app's controls, so the
macro (`Objective7_Scope1_Record1.d.js`) does this:
1. One click at the fixed screen position of the first field.
2. Record 1's values in the normal form's Tab order (`uiv.desktop.keyboard.type`).
3. Ctrl+Tab between the 8 tabs.
4. One click on Submit.

Image matching was not used, because the modified form changes every colour. The macro was
built once on the normal form and **not edited** before the modified-form runs. It sends exactly
the same keys and clicks as the PyAutoGUI script, which `tests/test_rpa_uivision.py` checks, so
only the RPA product changed.

## Commands

```
python scripts/rpa_uivision.py build               # once, on the normal form (no input sent)
python scripts/rpa_uivision.py run --only gate     # 1 live run on the normal form, scored at once
python scripts/rpa_uivision.py run                 # 5 normal + 5 modified (about 2 minutes)
python scripts/rpa_score.py --sheet docs/rpa_comparison/run_sheet_uivision.csv --submissions docs/rpa_comparison/submissions
```

Each run opens a fresh form at the top-left of the main screen and keeps it above other windows.
It then starts `uivision-app.exe --run <macro>`, waits for the form's saved submission and the
app's "completed" log line, and closes both.

## Results (2026-11-02, record 1)

| System | Form | Runs finished | Fully correct | Mean field correctness | Policy tab | Time per run |
|---|---|---|---|---|---|---|
| UI.Vision | normal | 5/5 | 5/5 | 100.0% (171/171) | 11/11 | 4.2 s |
| UI.Vision | modified | 5/5 | 0/5 | 98.8% (169/171) | 9/11 | 4.2 s |
| Intern | normal | 5/5 | 0/5 | 85.4% (146/171) | 9/11 | about 1 min |
| Intern | modified | 5/5 | 0/5 | 85.4% (146/171) | 9/11 | about 1 min |

Fisher's exact test on fully correct runs gives p = .008 on the normal form (in favour of
UI.Vision) and p = 1.000 on the modified form.

On the modified form, UI.Vision reported every run as **completed**, but the reordered Policy
Information rows took the wrong keystrokes:
- **Policy Type** was saved as "Personal Injury Protection".
- **Expiration Date** was left empty.
- **Policy Number** was left empty. It is not scored.

The saved values are identical, field for field, to the PyAutoGUI runs on both forms.

**Setup:** the macro was generated from the normal form's Tab order in 5.5 s by a script written
by an AI coding assistant, so there is no human setup time. It worked on its first replay
because it reused the keystroke sequence corrected during the PyAutoGUI run (3 replays,
5.2 min).

## Files

| File | What it is |
|---|---|
| `Objective7_Scope1_Record1.d.js` | The UI.Vision desktop-app macro |
| `build.json` | Screen coordinates and key sequence the macro was built from |
| `logs/*.txt` | Ui.Vision for Desktop's own log for each run (`started` / `completed (Runtime …)`) |
| `score_output.txt` | Output of `rpa_score.py` |
| `../run_sheet_uivision.csv`, `../run_sheet_uivision_scored.csv` | UI.Vision rows and Intern's existing rows |
| `../submissions/UIVision_*.json` | The form's own saved submission for every run |

## Still not covered

- The setup time is not human effort, because the macro was generated.
- UiPath and Power Automate Desktop, which select controls by name and might survive the
  reorder, were not tested.
- Scopes 2 and 3 were re-run with UI.Vision's browser extension: see `../uivision_web/README.md`.
