# Objective 7, Scopes 2 and 3, re-run with UI.Vision RPA

The earlier Scope 2 and 3 baselines were scripts that imitated an RPA tool (`scripts/rpa_scope2.py`
used a position replay; `scripts/rpa_scope3.py` used mail rules in Python). Here **UI.Vision RPA**
itself does the work. This is the same product as the Scope 1 run in `../uivision/`, here as its
browser extension, version 10.0.303. The tasks, data, interface changes and scoring are unchanged,
and Intern's runs are reused.

## How UI.Vision was run

- **Extension:** the official package is downloaded from the Chrome Web Store servers and
  unpacked unchanged, with its store key, so it keeps its store id. It is loaded into Playwright's
  Chromium, because branded Chrome no longer accepts extensions from the command line.
- **Running a macro:** UI.Vision is operated through its own window, the way a person uses it.
  The macro file is imported, selected, and run with **Play**. Playwright only presses those
  buttons, opens the page to work on, and reads the results afterwards.
- **One setting changed from the defaults:** the side panel is off, so UI.Vision uses its own
  window. A side panel can't be opened by a script.

```
python scripts/rpa_uivision_web.py setup     # download + unpack the extension
python scripts/rpa_uivision_web.py scope2    # 8 portal versions
python scripts/rpa_uivision_web.py scope3    # 5 inbox conditions
```

## Scope 2: grade sheet to web portal

**The macro** (`Objective7_Scope2_GradeSheet.json`, 201 commands) is what UI.Vision's recorder
saves when the 50 rows are entered on the base portal:
- For each row, `type` Course, Year and Grade, and `select` Remarks.
- At the end, `click` Save All Rows.

Each step's locator and backup locators come from the recorder's own code (`inspector.js`, MIT),
run on `v0_base`:
- **Main locator:** `id=` the cell's identifier, for example `course_2021-10001`.
- **Backups**, tried by UI.Vision if the main one fails: name, three XPaths, CSS.

The macro is built once and replayed unedited on every portal version. It is scored on what the
portal **saved**, after its own Save validation.

| Portal version | UI.Vision rows saved correctly | What UI.Vision did | Intern rows correct | Intern year/grade/remarks |
|---|---|---|---|---|
| Original | **50/50** | completed | 0/50 | 150/150 |
| Fields reordered | **50/50** | completed (ids unchanged) | 0/50 | 150/150 |
| Fields relabelled | 0/50 | stopped at row 1: `id=grade_…` not found (renamed) | 0/50 | 50/150 |
| Extra fields inserted | **50/50** | completed | 0/50 | 150/150 |
| Labels not linked | **50/50** | completed | 0/50 | 150/150 |
| Near-duplicate fields | **50/50** | completed | 0/50 | 150/150 |
| Dropdown options changed | 0/50 | stopped at row 1: no option `Passed` (now `PASSED`) | 0/50 | 150/150 |
| Grade scale changed | 0/50 | typed all rows; portal refused to save 0–100 grades on a 1–5 scale | 0/50 | 50/150 |
| **Total** | **250/400** | | **0/400** | **1000/1200** |

**Comparison with the earlier position-replay script:** it got 150/400. It failed on every
portal whose layout changed. A real recorder that finds cells by their identifier survives
reordering, inserted fields, unlinked labels and near-duplicates. It fails only when the
identifier itself, the option text, or the value range changes.

**Intern** ran without its language model, which normally fills Course. It saved no fully correct
row on any portal, but it wrote year, grade and remarks correctly on six portals. That includes
the changed-options portal, where UI.Vision stopped.

## Scope 3: inbox routing

**The macro** (`Objective7_Scope3_MailRules.json`, a UI.Vision JavaScript macro) applies the same
mail rules as before:
- exact sender → the action taken before,
- subject keywords → schedule or leave alone,
- no-reply senders → leave alone,
- anything else → left for a person.

It works on the app's own practice inbox page: open each message, read sender and subject, then
press Reply / Forward / Schedule / Leave alone or go back. The page is served by a stand-in
server, so no decision goes into the app's training data.

| Condition | UI.Vision correct (of 25) | Handed to a person | Wrong action | Same decisions as the scripted rules | McNemar vs Intern |
|---|---|---|---|---|---|
| Original | 12 | 13 | 0 | 25/25 | p = .008 (UI.Vision better) |
| Different sender mailbox | 8 | 17 | 0 | 25/25 | p = .388 |
| `[EXTERNAL]` subject tag | 12 | 13 | 0 | 25/25 | p = .008 |
| Subject synonyms | 5 | 20 | 0 | 25/25 | p = 1.000 |
| All three changes | 1 | 24 | 0 | 25/25 | p = .375 |

Intern routed 4 of 25 correctly in every condition, with the same decision every time.

## Files

| File | What it is |
|---|---|
| `Objective7_Scope2_GradeSheet.json`, `Objective7_Scope3_MailRules.json` | The two UI.Vision macros (importable) |
| `scope2_<version>_log.txt`, `scope3_<condition>_log.txt` | UI.Vision's own log for each run |
| `scope2_<version>_portal.json` | What the portal held (staged) and saved (committed) after each run |
| `scope2_results.json`, `scope3_results.json` | Scores, with Intern's and the scripted baseline's results alongside |

## Limits

- **Setup time:** the macros were generated by an AI coding assistant, not recorded by a person,
  so there's no human setup time. The Scope 2 locators come from UI.Vision's own recorder code.
- **Scoring Scope 2:** UI.Vision is scored on what the portal saved. Intern ran without saving, so
  it's scored on the values read back after writing.
- **Tools not tested:** UiPath and Power Automate Desktop.
