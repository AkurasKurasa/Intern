# Milestone 10 — click-by-click walkthrough

This is the "what do I actually click" guide. `rpa_protocol.md` explains *why*;
this one just tells you what to do.

Power Automate Desktop is **already installed** on this machine.

---

## Do it in two parts

**Part A — the adaptability test.** About 20-30 minutes. This is the part that
matters and it is genuinely not hard. Do this first.

**Part B — the full pipeline with Excel.** About 40 more minutes, and much
fiddlier. Only do it if Part A went smoothly.

If you only ever do Part A, you still have the result the milestone exists for.

---

# PART A — does a recording survive the interface changing?

## A1. Start the portal

Open a terminal in the project folder:

```
cd mocksite
python -m http.server 8765 --bind 127.0.0.1
```

**Leave this window open.** If you close it the portal dies.

Check it works: open `http://127.0.0.1:8765/v0_base/index.html` in a browser.
You should see the grade sheet with 50 students.

## A2. Open Power Automate Desktop

Search "Power Automate" in the Start menu and open it.

It will ask you to sign in with a Microsoft account. Any personal account
works, it is free. This is a one-time thing.

Click **New flow**. Name it `GradePortal`. Click **Create**.

You now see three panels:
- **left** — a searchable list of actions
- **middle** — your flow (empty right now)
- **right** — variables

## A3. Make it open the portal

In the left panel search box, type `launch new`.

Drag **Launch new Microsoft Edge** into the middle panel. A dialog opens. Set:

- **Launch mode**: `Launch new instance`
- **Initial URL**: `http://127.0.0.1:8765/v0_base/index.html`
- **Window state**: `Maximized`

Click **Save**.

Press **Run** (the ▶ button at the top). Edge should open on the portal.

> If Edge asks about an extension for Power Automate, allow it. It needs that
> to see inside the page.

**Leave that browser window open.** The next step needs it.

## A4. Record filling in one student

This is the part people get stuck on, so read it before doing it.

In the left panel search `populate text`. Drag **Populate text field on web
page** into the flow, *below* the launch action.

In the dialog:

1. **UI element** — click the dropdown, then **Add UI element**.
2. Your screen dims slightly and a small bar appears. Move your mouse over the
   browser. Elements light up with a **red outline** as you hover.
3. Hover over the **Course box in the very first student row** (Abad, Andrea A.
   — the top row). When the red outline is around that one box:
   **hold Ctrl and left-click.**
4. The dialog comes back with the element captured.
5. In **Text to fill in**, type: `BS Information Systems`
6. Click **Save**.

Now repeat that three more times:

| action to drag in | which box to Ctrl+click | value |
|---|---|---|
| Populate text field on web page | **Year** in row 1 | `2` |
| Populate text field on web page | **Grade** in row 1 | `85` |
| Set drop-down list value on web page | **Remarks** in row 1 | `Passed` |

For the Remarks one, search `drop-down` in the actions list. In its dialog set
**Operation** to `Select option by name` and the option name to `Passed`.

Your flow now has 5 actions.

> Those are the real values for student 2021-10001 from the spreadsheet, so
> the row will be genuinely correct when it works.

## A5. Check it works on V0

Close the browser window that is open. Press **Run**.

Edge should open, and the first student's row should fill in with Course, Year,
Grade and Remarks.

**If that works, you have a working RPA automation.** That is the baseline.

**Now write down how long that took.**

Open `eval/rpa_measurements.json` in VS Code. It is a form with blanks in it.
Find this bit:

```json
  "setup": {
    "human_demonstration_seconds": null,
    "configuration_seconds": null,
    "time_to_first_verified_row_seconds": null
  },
```

`null` means "not filled in yet". Replace it with a number — **seconds**, no
quotes. If opening Power Automate to seeing that first row correctly filled
took you about 25 minutes, that is 1500 seconds:

```json
    "time_to_first_verified_row_seconds": 1500
```

Rough is fine. A number that is roughly right beats a blank.

## A6. Now the actual experiment

**Do not change anything else in the flow.** You are only going to change which
page it opens.

### What to expect, and why it is not a clean sweep

Every box now has a unique id built from its field name and the student's
number, e.g. `grade_2021-10001` — the shape a server-rendered form produces.
Power Automate will build its selector from that id, which is the strongest
selector it can have. That is deliberate: giving the RPA tool its best shot is
what makes the result worth quoting.

So the honest prediction is **not** that everything breaks:

| variant | what changed | ids | expected |
|---|---|---|---|
| V1 reordered | columns moved on screen | unchanged | **should still work** |
| V2 relabeled | fields renamed, so ids renamed too | changed | **should break** |
| V4 unassociated | labels stripped off the boxes | unchanged | **should still work** |

If that is what you observe, the finding is sharper than "RPA is fragile":

> Moving things on screen does not break a selector. **Renaming the underlying
> field does** — and a rename is what happens when a school actually updates
> its portal, because developers rename the field in code, not just on screen.

V4 is the mirror image and worth saying out loud: it breaks *this project's*
label-reading approach (2/3) while leaving RPA untouched. Report that. A
comparison where your own system loses one round is far more credible than a
clean sweep.

If what you actually see differs from the table above, **write down what you
saw, not what was predicted.**

Double-click the **Launch new Microsoft Edge** action. Change the Initial URL,
then Save, then Run. Do this for each of the three:

### Test 1 — reordered columns
```
http://127.0.0.1:8765/v1_reordered/index.html
```
Same fields, different order on screen.

### Test 2 — renamed fields
```
http://127.0.0.1:8765/v2_relabeled/index.html
```
Course is now "Degree Program", Grade is now "Final Rating", and so on.

### Test 3 — labels stripped
```
http://127.0.0.1:8765/v4_unassociated/index.html
```
The boxes have no attached labels at all.

### For each one, write down which of these happened:

- **`ran correctly`** — the right values went into the right fields.
- **`ran but wrong fields`** — it filled things in, but in the wrong boxes.
  **Look carefully.** Did the grade land in Remarks? Did Course get a number?
  This one matters most — write down exactly what went where.
- **`crashed`** — an error message, flow stopped.
- **`selector not found`** — it said it could not find an element.

Put these in `after_ui_change` in your `rpa_measurements.json`.

**Take a screenshot of anything that fills the wrong fields.** That screenshot
is worth more to your thesis than any number in this milestone.

## A7. How long to fix it?

For each one that broke: start a stopwatch, fix the flow so it works on that
variant (re-capture the UI elements), stop when it works.

Write those times into `reconfiguration`.

If one of them defeats you, write down how long you spent and note that you
gave up. That is a real result, not a failure.

---

# PART B — the full pipeline (optional)

Only if Part A went fine. This makes the setup-time comparison fair, because
your system reads the spreadsheet and loops over all 50 students.

## B1. Read the spreadsheet

Add these actions **above** the browser launch:

1. Search `launch excel` → **Launch Excel**
   - Document path: `data\sheets\grade_sheet.xlsx` (use the full path)
   - Make instance visible: on, while you are debugging

2. Search `read from excel` → **Read from Excel worksheet**
   - Retrieve: `Values from a range of cells`
   - Start column `B`, start row `15`
   - End column `J`, end row `64`
   - **First line contains column names**: OFF

   > Row 15 is where the student data starts and row 64 is the last one. The
   > headers are on row 12 with a title block above them, which is why you
   > cannot just say "read the whole sheet".

   This gives you a variable, usually `%ExcelData%`.

## B2. Loop over the students

Search `for each` → **For each**. Set it to iterate over `%ExcelData%`, storing
each row in `%CurrentItem%`.

Drag your four filling actions **inside** the loop.

The columns you want, counting from 0 because the range started at column B:

| what you want | spreadsheet column | reference |
|---|---|---|
| STUDENT NUMBER | B | `%CurrentItem[0]%` |
| PROGRAM | H | `%CurrentItem[6]%` |
| YEAR LEVEL | I | `%CurrentItem[7]%` |
| FINAL GRADE | J | `%CurrentItem[8]%` |

(Columns C, D and E are the student's last name, first name and middle
initial, which you do not need — the portal already prints them.)

Replace your hard-coded values with those. So Course becomes
`%CurrentItem[6]%` instead of `BS Information Systems`.

## B3. The hard part, and it is worth writing about

Your recorded selectors point at **row 1's boxes specifically**. Looping does
not move them to row 2 — every iteration will type into row 1 again, 50 times.

To fix it properly you need a selector with the row number injected into it,
something like `tr:eq(%Index%) > td > input`. In Power Automate that means
editing the UI element's selector by hand and turning on the variable option.

**If you cannot get this working, stop and write down that you could not.**
That difficulty is itself a finding: the tool records positions, and making
positions dynamic is manual work that a person has to reason about. Say how
long you spent before stopping.

Do not let Part B block you. Part A is the result that matters.

---

# When you are done

```
python eval/rpa_comparison.py
```

That prints the comparison table with your numbers next to your system's.
Anything you left blank shows as `not measured`, and it prints an INCOMPLETE
warning until the RPA side is filled in.

---

# If something goes wrong

**"Power Automate wants me to sign in"** — normal, it is free, use any personal
Microsoft account.

**The red outline will not appear when I hover** — the browser extension is not
active. In Edge go to Extensions and make sure the Power Automate one is
enabled, then restart the browser from the flow.

**"Element not found" on the very first run** — the page had not finished
loading. Add a **Wait** action for 2 seconds after the launch.

**The portal shows nothing** — the `python -m http.server` window closed.
Restart it.

**The page looks like it did before my last change** — the browser cached
`portal.js`. Press **Ctrl+F5** in the browser, or close it entirely and re-run
the flow. This bit me while setting the experiment up: the page had the old
script and I spent a while confused by it.

**One action changed all 50 rows** — that was the old behaviour, before each
box had its own id. Hard-refresh as above and re-capture the element.

**I clicked the wrong box** — double-click the action, click the UI element
dropdown, and re-capture it.

Note down anything that cost you real time. Setup friction is one of the things
this milestone is measuring, so those notes are data, not complaints.
