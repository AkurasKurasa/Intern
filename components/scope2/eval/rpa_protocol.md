# Milestone 10 — comparing against an RPA tool

## Read this part first

### What is RPA?

RPA stands for Robotic Process Automation. It is software that automates
clicking and typing. You record yourself doing a task once, and the tool
replays it as many times as you want.

Companies use it heavily for exactly the problem this project is about:
copying spreadsheet data into a web form, over and over.

### Why does it break?

When an RPA tool records you, it memorises **where** things were on the page:

> "click the 4th input box"
> "click the box whose id is `f_grade`"

That description of where something is, is called a **selector**.

The tool has no idea what "Grade" *means*. It only knows a position. So when
the school updates its portal and moves the Grade column, the recording keeps
typing grades into the 4th box — which is now Remarks.

**It usually does not crash.** It just quietly fills in the wrong fields, and a
person encoding grades might not notice for weeks.

### What is this milestone for?

Your project's central claim is that learning what a field *means* survives
interface changes, and memorising where it *is* does not.

Right now that is an assertion. This milestone turns it into a measurement:
set up a real RPA tool, get it working on V0, then point it at your changed
variants without touching it, and write down what happens.

You already have your system's half of the numbers. This gets the other half.

### Do you have to do this?

No. Milestones 1 to 9 stand on their own, and this one is last in the plan for
a reason. It is one person using one tool once — a case study, not a benchmark.

It is worth doing if you want the thesis to answer "why not just use RPA?" with
evidence instead of an argument. Budget 60 to 90 minutes.

---

## Before you start

**Install Power Automate Desktop.** It is free and already available on
Windows 11 — search the Start menu, or get it from the Microsoft Store. It is
Microsoft's RPA tool. (UiPath Community Edition also works and is also free.
Use whichever you can get running; just write down which one.)

**Get a stopwatch.** Your phone. You need real times, written down as you go,
not guesses afterwards.

**Start the portal.** Open a terminal in the project folder:

```
cd mocksite
python -m http.server 8765 --bind 127.0.0.1
```

Leave that running. The portal is now at
`http://127.0.0.1:8765/v0_base/index.html`.

**Open the spreadsheet**: `data/sheets/grade_sheet.xlsx`, the `SUMMARY` tab.

**Make your answer sheet.** Copy `eval/rpa_measurements.template.json` to
`eval/rpa_measurements.json`. You will fill it in as you go. Every field starts
as `null`, which means "not measured yet" — leaving one as `null` is fine and
honest.

---

## The task

Both systems get the same job:

> Fill in **Course**, **Year** and **Grade** for the students in the portal,
> taking the values from the **PROGRAM**, **YEAR LEVEL** and **FINAL GRADE**
> columns of the spreadsheet, matching each student by **STUDENT NUMBER**.
> Touch nothing else.

Do not give the RPA tool any hint your system did not get. Same spreadsheet,
same portal, same demonstration.

---

## Step 0 — write down your experience level

Open `rpa_measurements.json` and fill in `operator.rpa_experience` **now**,
before you start.

If this is your first time touching an RPA tool, write that. A beginner and an
expert differ enormously in setup time, and a reader who does not know which
one they are looking at cannot use your number for anything.

Being a beginner does not invalidate the result. Hiding it would.

## Step 1 — time the demonstration

This is the one measurement that counts for **both** systems, because you show
both of them the same thing.

1. Start the stopwatch.
2. Fill in 6 students by hand, copying from the spreadsheet: PROGRAM into
   Course, YEAR LEVEL into Year, FINAL GRADE into Grade. Then pick Remarks
   yourself (Passed or Failed).
3. Stop the stopwatch when the sixth student is done.

Write it into `setup.human_demonstration_seconds`.

> Why 6 and not 3? Your system needs 6 rows before it can tell that Remarks
> depends on Grade rather than on Year — with fewer, Year separates Passed
> from Failed by coincidence. Use the same 6 students for both systems.

## Step 2 — time the RPA setup

Now make Power Automate actually work as an automation, not just a one-off
recording. That means: reading the spreadsheet, looping over all the rows, and
fixing any step that does not replay properly.

Start the stopwatch when you begin, stop when you think it will run.

Write it into `setup.configuration_seconds`.

Expect this to be the frustrating part. That frustration is the measurement.

## Step 3 — time to the first row that actually works

From "tool open, nothing set up" to "one student filled in correctly in the
portal, and I have checked it with my own eyes".

Write it into `setup.time_to_first_verified_row_seconds`.

To see your system's number for the same milestone, run:

```
python eval/rpa_comparison.py
```

## Step 4 — run all 50 rows

Let the automation run over the whole `v0_base` portal. Then count how many
students it filled in correctly — spot-check about 10 of them, and look at the
"X of 50 encoded" counter above the table.

Write both numbers into `baseline_run`.

## Step 5 — the important one: change the interface

**Do not fix anything before this step. Do not even open the automation to look
at it. This step is the entire point of the milestone, and editing first
destroys it.**

Take the exact same automation, unchanged, and point it at each of these:

| URL | what changed |
|---|---|
| `http://127.0.0.1:8765/v1_reordered/index.html` | same fields, different order |
| `http://127.0.0.1:8765/v2_relabeled/index.html` | same fields, renamed |
| `http://127.0.0.1:8765/v4_unassociated/index.html` | labels stripped off the boxes |

For each one, write down how many rows came out right, and pick the outcome
that describes what happened:

- **`ran correctly`** — it still worked.
- **`ran but wrong fields`** — it filled things in, in the wrong places.
- **`crashed`** — it stopped with an error.
- **`selector not found`** — it could not find a box it was looking for.

**If you get `ran but wrong fields`, that is the most valuable result in this
whole milestone.** Nothing errors, nothing looks broken, and the grades are
wrong. Write down exactly which values went into which fields. A clean crash is
much less interesting, because a crash is at least visible.

## Step 6 — time the repair

For each variant that broke, time how long it takes you to get it working
again, and write it into `reconfiguration`.

If you give up on one, record the time you spent and add a line to `notes`
saying you gave up. That is a legitimate result.

---

## Producing the table

```
python eval/rpa_comparison.py
```

It measures your system automatically, reads your RPA numbers from the JSON,
and prints the comparison. Anything you left as `null` shows as
`not measured`, and it prints an INCOMPLETE warning until the RPA column is
filled in — so it cannot be mistaken for a finished comparison.

---

## Things to say in the write-up

Include these even where they cut against your own system. A comparison that
only reports its own strengths is not evidence, and a reviewer will notice.

1. **Your skill level is the biggest confound.** One person, learning one tool,
   once. Say so explicitly.

2. **The RPA tool is not being abused — it is working as designed.** Recording
   positions is what it is *for*, and it is genuinely faster to a first working
   automation in many real situations. Showing it breaks under renaming is not
   an attack on the tool, and if your write-up reads like one, a reviewer will
   discount the whole comparison.

3. **You built the variants yourself.** They are fair tests of your claim, but
   they are not a random sample of how real portals change.

4. **Your system's setup time excludes** the one-off ~90 MB model download, and
   assumes Python and the libraries are already installed.

5. **Everything here is synthetic** — invented students, a mock portal. So this
   measures nothing about real encoding error rates or real workloads.

---

## If you cannot get the RPA tool working at all

That is a result, not a failure.

Record how long you spent before stopping, put it in `notes`, and report it as:
"did not reach a working automation in N minutes, at the stated experience
level."

What you must not do is leave the impression that a comparison was run when it
was not.
