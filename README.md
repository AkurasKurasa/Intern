# Demonstration-driven spreadsheet-to-form automation

You show it how to encode two or three students. It works out which spreadsheet
column belongs in which form field, notices that one field is *computed* rather
than copied, and finishes the rest of the class on its own.

It is not a macro recorder. Nothing about the portal is written down anywhere:
no selectors, no field names, no column mapping. Rename the fields or move the
columns and it still works, because it learns what the fields **mean** rather
than where they **are**.

```
python demonstrate.py     # it watches you do a few students
python automate.py --show # it does the rest, in front of you
```

---

## Contents

- [How the pipeline works](#how-the-pipeline-works)
- [Which part is machine learning](#which-part-is-machine-learning)
- [Setup](#setup)
- [Use case: encoding a class of 50](#use-case-encoding-a-class-of-50)
- [What is in the repository](#what-is-in-the-repository)
- [Results](#results)
- [Running the tests](#running-the-tests)
- [Limitations](#limitations)

---

## How the pipeline works

Three phases. A person only touches the first.

### Phase A — demonstration

**1. Two recorders watch, independently.**

| recorder | what it does |
|---|---|
| `recorder/excel_recorder.py` | polls your Excel selection ~6×/second, logging which cell you clicked, its column header, and its value |
| `recorder/extension/content.js` | injected into the page; fires whenever a value lands in a field, capturing the raw DOM context around it |

Neither knows the other exists. Each produces a timestamped stream of events.

**2. The Reconciler joins them** — `recorder/reconciler.py`

For each value that appeared in a form field, it looks backwards for an Excel
cell holding the same value, selected within the last 60 seconds.

- **Match found** → a confirmed pair: `PROGRAM → Course`
- **No match** → the interesting case. A value you entered with no source cell
  behind it is evidence the field was *computed*, not copied. This is how
  Remarks gets flagged as derived.

Matching on value *and* time matters: value alone breaks when two students share
a grade, time alone breaks on a stale clipboard.

**3. You confirm** — `recorder/confirm.py`

It shows what it thinks it saw. You accept or correct. Only confirmed pairs
become training data, and the number of corrections is itself a reportable
metric.

### Phase B — learning

**4. Feature extraction** — `features/extractor.py`

For every possible (spreadsheet column, form field) pairing it computes **17
numbers** describing how well they fit:

| # | group | measures |
|---|---|---|
| 1–4 | semantic | do the names *mean* the same thing |
| 5–8 | lexical | edit distance, shared words, abbreviations |
| 9–12 | value shape | do the column's values fit what the field accepts |
| 13–15 | structural | type compatibility, required-ness, fill state |
| 16 | positional | are they in the same relative position |
| 17 | option set | do the column's distinct values match a dropdown's options |

This is the load-bearing module: **the same code runs at training time and at
execution time.** Nothing in it may read a demonstration-only signal, or the
model would score perfectly in training and collapse on a portal it has not
seen. There is a test asserting exactly that.

**5. The matcher scores every pairing** — `model/matcher.py` ← *the machine learning*

**6. Rule induction** — `rules/`

Separately, for any field flagged as derived: test every already-filled field as
a possible cause, keep only those whose values **perfectly separate** the
outcomes, and if exactly one survives, work out the direction and the cutoff.

It refuses to guess. If two fields both explain the data, it says so and asks
for more students.

### Phase C — execution

**7. The Resolver assigns, or abstains** — `resolver/assign.py`

Fields are partitioned first — mappable, derived, control, unmapped — because a
derived field left in the matrix would compete for a source column and displace
a correct mapping.

Then the **Hungarian algorithm** finds the best one-to-one assignment overall,
not merely the best match per column. Two thresholds follow: a pairing is
accepted only if it scores well **and** beats the runner-up clearly. Otherwise
it abstains, which is a first-class output rather than an error.

**8. The Executor fills and verifies** — `executor/runner.py`

- finds each student's row by Student ID, and checks the printed name agrees
- writes the mapped values
- reads Grade back **off the form**, applies the rule, sets Remarks
- re-reads every field to confirm what it wrote is what is there
- a row that fails verification is cleared rather than saved

---

## Which part is machine learning

**Exactly one component is trained: the matcher.** `model/matcher.py`

A small neural network, **1,121 parameters**:

```
17 features → 32 → ReLU → Dropout(0.2) → 16 → ReLU → 1 → probability
```

Trained on *your* demonstration in about a second. Confirmed pairs are the
positive examples; every other column-field combination is a negative. It
outputs "how likely is it that this column belongs in this field" for each
candidate, and that matrix is what the Resolver assigns over.

It is deliberately tiny. Three demonstrated rows across seven columns give
roughly 21 positives and 126 negatives — anything larger memorises that
immediately.

**There is also a borrowed pretrained model.** Features 1–4 use
`all-MiniLM-L6-v2` to measure whether "PROGRAM" and "Course" mean similar
things. It is used purely as an **input** and is not trained here. This is what
handles synonyms: plain string matching scores 0/24 across the test variants.

### What is *not* machine learning

Worth stating precisely:

- **the recorders** — event capture
- **the Reconciler** — value plus time-window matching, deterministic
- **the rule inducer** — logical induction; perfect separation, interval
  arithmetic, snapping to a round number. Not statistical.
- **the Resolver** — Hungarian algorithm plus two thresholds
- **the Executor** — browser automation and readback checks

One of eight steps is learned. That is intentional: when it fails you can point
at which component failed, and the other seven are auditable.

---

## Setup

**Requirements:** Windows, Python 3.11+, Microsoft Excel, Chrome.

```bash
git clone https://github.com/RJGanzon/Intern.git
cd Intern

pip install pandas openpyxl xlwings playwright scipy torch sentence-transformers pytest
python -m playwright install chromium
```

Generate the synthetic grade sheets (50 invented students — no real records):

```bash
python data/sheets/make_sheets.py
python data/sheets/verify_sheets.py     # 20 checks, should all pass
```

Start the mock portal and leave it running:

```bash
python mocksite/serve.py
```

It serves with caching disabled, which matters: a cached script will silently
run an old version of the page and produce errors that appear nowhere on screen.

---

## Use case: encoding a class of 50

The scenario is a registrar with a grade sheet and a portal that wants the same
data typed in by hand — 50 students × 4 fields, every grading period.

### Step 1 — show it a few students

```bash
python demonstrate.py
```

Open `data/sheets/grade_sheet.xlsx` on the SUMMARY tab when prompted. Chrome
opens on the portal with the recorder loaded.

For each student, use the normal rhythm: **click the cell in Excel → Ctrl+C →
Ctrl+V into the portal.** Columns H (PROGRAM), I (YEAR LEVEL) and J (FINAL
GRADE), starting at row 15. Then pick **Remarks** yourself — do not copy it from
anywhere. That absence is how the system learns Remarks is a rule.

Events appear live as it sees them:

```
excel    H15  PROGRAM      'BS Information Systems'
browser  row 0  Course     'BS Information Systems'
excel    I15  YEAR LEVEL   '2.0'
browser  row 0  Year 1-5   '2'
```

Press **Ctrl+C** when you have done a few. It reconciles, asks you to confirm,
then tells you whether it has seen enough:

```
  rows demonstrated   7
  fields learned      Course, Grade 0-100, Year 1-5
  rule for Remarks    Remarks is set to Passed when Grade 0-100 is 75 or
                      higher, otherwise Failed.

  READY - you can stop. Everything below is automatic.
```

If it is **not** ready it tells you why and what to do. The common ones:

| it says | why | fix |
|---|---|---|
| every student got the same result | no negative example, so no boundary | demonstrate someone who failed |
| Grade and Year explain it equally well | your only failing student is also your only year-1 student | demonstrate a failing student who is not year 1 |
| nothing reconciled | Excel was not being watched | check Excel was open and you clicked the cell before copying |

Do all the students in **one** session — `automate.py` reads one session file.

### Step 2 — let it do the rest

```bash
python automate.py --session data/demos/<your session>.jsonl --show
```

Six stages print as it goes. `--show` opens a visible browser so you can watch
it fill, and holds the window open at the end so you can check the result.

```
 4. Matching columns to fields
  YEAR LEVEL   -> Year 1-5       confidence 1.00
  FINAL GRADE  -> Grade 0-100    confidence 1.00
  PROGRAM      -> Course         confidence 1.00
  FINAL        -> ABSTAINED (score 0.00, margin 0.04)
  left empty: Recommendations optional

 5. Filling the portal
  50 rows filled and verified, 0 failed
```

Note what it did **not** do: `MIDTERM` and `FINAL` are also numeric 0–100
columns, and it left them alone. `Recommendations` has no source column and
stayed empty. Abstaining correctly is as important as mapping correctly.

Add `--commit` to actually save. Every run writes a log to `data/runs/`
containing what was filled, what was verified, and the portal's own final
records.

### Step 3 — prove it adapts

The same demonstration, against a portal it has never seen:

```bash
python automate.py --session <same session> --variant v2_relabeled --show
```

`v2_relabeled` calls the fields *Degree Program*, *Final Rating* and *Academic
Standing*. No re-demonstration, no configuration change.

---

## What is in the repository

```
demonstrate.py            watch a demonstration, then learn from it
automate.py               the whole pipeline, end to end

recorder/                 Phase A
  excel_recorder.py         Excel selection watcher (xlwings/COM)
  extension/content.js      browser recorder; also a real MV3 extension
  reconciler.py             joins the two streams into confirmed pairs
  confirm.py                the human confirmation gate

labeling/resolve.py       the six-rule label cascade - ONE implementation,
                          shared by the recorder and the scanner

features/                 Phase B
  extractor.py              the 17 features; runs in training AND execution
  encoders.py               cached sentence embeddings

model/
  matcher.py                the trained network (1,121 params)
  train.py                  training, with label-paraphrase augmentation
  baselines.py              string match / cosine / trained, compared

rules/                    threshold induction
  detect.py                 which field drives the derived one
  induce.py                 direction, cutoff, and the interval demos allow
  options.py                map an outcome onto a live dropdown option

resolver/assign.py        partition, Hungarian assignment, abstention

executor/                 Phase C
  scanner.py                live page -> field descriptors
  sheet_reader.py           the spreadsheet side
  runner.py                 fill, verify, commit, log

mocksite/                 the evaluation instrument - 8 portal variants
eval/                     the accuracy table, ablations, HiTL loop
tests/                    181 tests
data/sheets/make_sheets.py  builds the synthetic grade sheets
```

### The portal variants

`mocksite/` holds eight versions of the same portal, each changing one thing:

| variant | change | what it tests |
|---|---|---|
| v0_base | none | the baseline |
| v1_reordered | columns in a different order | position independence |
| v2_relabeled | fields renamed | semantic matching |
| v3_extra_fields | adds Section and Adviser | correct non-assignment |
| v4_unassociated | labels stripped from the controls | label-cascade fallback |
| v5_near_duplicates | adds Grade (Recomputed), Year Enrolled | abstention |
| v6a_options | PASSED/FAILED in caps | option matching |
| v6b_scale | the 1.00–5.00 grading scale | induced operator direction |

Styling is identical across all eight, so only structure and labelling vary.

---

## Results

```bash
python eval/run_variants.py      # the accuracy table and ablations
python eval/hitl.py              # the corrections curve
```

**Field-mapping accuracy** — correct assignments out of mappable columns:

| variant | string match | cosine only | trained | trained, no feature 16 |
|---|---|---|---|---|
| v0_base | 0/3 | 1/3 | 3/3 | 3/3 |
| v1_reordered | 0/3 | 1/3 | 1/3 | 3/3 |
| v2_relabeled | 0/3 | 1/3 | 2/3 | 2/3 |
| v3_extra_fields | 0/3 | 1/3 | 0/3 | 2/3 |
| v4_unassociated | 0/3 | 1/3 | 0/3 | 2/3 |
| v5_near_duplicates | 0/3 | 0/3 | 0/3 | 1/3 |
| v6a_options | 0/3 | 1/3 | 3/3 | 3/3 |
| v6b_scale | 0/3 | 1/3 | 2/3 | 2/3 |
| **total** | **0/24** | **7/24** | **11/24** | **18/24** |

String matching scores **zero** — not one base pairing is an exact match, and
`PROGRAM → Course` is a pure synonym.

**Ablations** (total correct across all variants):

| setting | correct |
|---|---|
| all 17 features | 11/24 |
| **without positional (16)** | **18/24** |
| without value shape (9–12) | 17/24 |
| without option set (17) | 11/24 |

Feature 16 is net **harmful** here, costing 7 of 24. The shipped configuration
drops it.

**Rule induction**, both scales, zero cutoff error:

| scale | induced rule | true mark |
|---|---|---|
| 0–100 | Passed when Grade **≥ 75** | 75 ✓ |
| 1.00–5.00 | Passed when Grade **≤ 3.00** | 3.00 ✓ |

The operator flips because it is read off the data, never assumed.

**Human-in-the-loop:** 15 escalations across 8 portals, of which only 2 were
corrections — the rest confirmed what the system already proposed.

---

## Running the tests

```bash
python -m pytest tests/ -q        # 181 tests
```

They drive real browsers and read the real spreadsheets; there are almost no
mocks. Notable ones:

- `test_features.py::test_no_demonstration_only_signal` — the architectural
  invariant. The portal carries the answer in a `data-key` attribute; if any
  feature ever read it, the model would score perfectly in training and fail on
  an unseen portal.
- `test_executor.py::test_readback_catches_a_value_the_field_altered` — feeds a
  value longer than the field's maxlength so the browser truncates it, and
  asserts the row fails rather than saving a wrong value.
- `test_architecture_conformance.py` — shape, not behaviour. Fails when the code
  drifts from the design document.

---

## Limitations

Stated plainly, because they matter for interpreting the results:

- **The matcher does not generalise from a single demonstration session.** 3/3
  on the portal it learned from, collapsing on some it has not seen. The
  ablation shows most of that damage is one feature. Demonstrating on more than
  one portal is the obvious next experiment.
- **One demonstrated row is never enough.** On a sheet-style portal a cell's
  label covers its column *and* its student; the column's own name only emerges
  by comparing rows. Two is a hard floor.
- **Rule induction needs more rows than field mapping.** Mapping settled at
  three rows; the Remarks rule needed six, because Year separated pass from fail
  by coincidence below that.
- **Two of the 17 features are inert on this instrument.** Feature 8
  (abbreviation) never fires because no sheet header abbreviates a field label,
  and feature 15 reads live fill state that only the executor supplies.
- **Scope is deliberately bounded**: single-page forms, one threshold predicate
  over one numeric field, no multi-page wizards, no login flows, no date
  pickers.
- **Everything here is synthetic.** Invented students, a mock portal. Nothing
  measures real encoding error rates or real workloads.

---

## Acknowledgements

The mock grade sheets are modelled on the structure of a real institutional
grade book — its layout, its transmutation table, and its 75 passing mark — with
every identity, score and institution name replaced. No real student records are
used anywhere in this project.
