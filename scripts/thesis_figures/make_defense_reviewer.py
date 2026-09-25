"""
make_defense_reviewer.py
========================
Builds a plain-language defence reviewer for the Intern thesis as a PDF:
what the system is, how it works, the three scopes, the objectives and their
metrics, the numbers that are actually measured, and a drill sheet of likely
panel questions with short answers.

Two kinds of number appear in the thesis, and this reviewer keeps them apart:

  MEASURED    computed from the system's own logs in this repository
  PLACEHOLDER practice values currently sitting in Chapter 4, not measured

Anyone revising from this should know which is which before being questioned
on where a figure came from.

Usage
    python scripts/thesis_figures/make_defense_reviewer.py
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = r"C:\Users\paula\OneDrive\Desktop\Intern Defense Reviewer.pdf"

INK, MUTED, RULE = "#141414", "#555555", "#CFCFCF"
OK, WARN, BAD, GREY = "#1B6B2F", "#B45309", "#C62828", "#7A7A7A"
BOX = "#F2F2EF"

plt.rcParams.update({"font.family": "DejaVu Sans"})


def page(pdf, title, subtitle=None):
    fig = plt.figure(figsize=(8.27, 11.69))          # A4 portrait
    ax = fig.add_axes([0, 0, 1, 1]); ax.axis("off")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.text(0.07, 0.955, title, fontsize=20, weight="bold", color=INK, va="top")
    y = 0.925
    if subtitle:
        ax.text(0.07, y, subtitle, fontsize=10.5, color=MUTED, va="top")
        y -= 0.022
    ax.plot([0.07, 0.93], [y, y], color=RULE, lw=1)
    return fig, ax, y - 0.03


def h2(ax, y, text, color=INK):
    ax.text(0.07, y, text, fontsize=13, weight="bold", color=color, va="top")
    return y - 0.032


def para(ax, y, text, size=10.3, color=INK, x=0.07, width=0.86, lead=0.0185):
    import textwrap
    # average glyph width at this font size, as a fraction of page width
    chars = max(20, int(width / (size * 0.00086)))
    for line in textwrap.wrap(text, chars):
        ax.text(x, y, line, fontsize=size, color=color, va="top")
        y -= lead
    return y - 0.006


def bullet(ax, y, text, size=10.3, color=INK, x=0.085, mark="\u2022"):
    ax.text(x, y, mark, fontsize=size, color=color, va="top")
    return para(ax, y, text, size=size, color=color, x=x + 0.022, width=0.82)


def box(ax, y, height, color=BOX):
    ax.add_patch(FancyBboxPatch((0.07, y - height), 0.86, height,
                                boxstyle="round,pad=0.008", linewidth=0,
                                facecolor=color, zorder=0))


def table(ax, y, rows, widths, header=True, size=9.3, lead=0.0235):
    xs, x = [], 0.075
    for w in widths:
        xs.append(x); x += w
    for r, row in enumerate(rows):
        bold = header and r == 0
        for xi, cell in zip(xs, row):
            txt, col = (cell if isinstance(cell, tuple) else (cell, INK))
            ax.text(xi, y, txt, fontsize=size, color=col, va="top",
                    weight="bold" if bold else "normal")
        y -= lead
        if bold:
            ax.plot([0.072, 0.93], [y + 0.009, y + 0.009], color=RULE, lw=0.8)
            y -= 0.004
    return y - 0.008


def flow(ax, y, steps, note=None):
    """A left-to-right chain of labelled boxes."""
    n = len(steps)
    w, gap = 0.86 / n - 0.018, 0.018
    x = 0.07
    for i, (title, sub) in enumerate(steps):
        ax.add_patch(FancyBboxPatch((x, y - 0.058), w, 0.058,
                                    boxstyle="round,pad=0.006",
                                    facecolor="white", edgecolor=INK, lw=1.1))
        ax.text(x + w / 2, y - 0.018, title, fontsize=9.6, weight="bold",
                ha="center", va="center", color=INK)
        ax.text(x + w / 2, y - 0.040, sub, fontsize=8.2, ha="center",
                va="center", color=MUTED)
        if i < n - 1:
            ax.add_patch(FancyArrowPatch((x + w + 0.002, y - 0.029),
                                         (x + w + gap - 0.002, y - 0.029),
                                         arrowstyle="-|>", mutation_scale=11,
                                         color=INK, lw=1.1))
        x += w + gap
    y -= 0.072
    if note:
        y = para(ax, y, note, size=9.2, color=MUTED)
    return y


def build() -> str:
    with PdfPages(OUT) as pdf:

        # ---------------- 1. What it is ----------------
        fig, ax, y = page(pdf, "Intern \u2014 Defence Reviewer",
                          "A plain-language guide to the system, the study, and the questions you may be asked")
        y = h2(ax, y, "In one sentence")
        y = para(ax, y, "Intern watches a person do a task on screen once, learns from that recording, and then "
                        "does the task itself using the mouse and keyboard, without anyone writing rules for that "
                        "specific task.", size=11.5)
        y -= 0.012
        y = h2(ax, y, "Why that matters")
        y = bullet(ax, y, "Ordinary automation (RPA) must be configured by hand for every task and breaks when the "
                          "screen changes.")
        y = bullet(ax, y, "Intern is taught by demonstration instead of configuration, so it learns how THIS user "
                          "does the task.")
        y = bullet(ax, y, "It works at the level of the screen, so it needs no access to the target program's "
                          "database or files.")
        y -= 0.012
        y = h2(ax, y, "How it works, end to end")
        y = flow(ax, y, [("1. Record", "watch a person"), ("2. Clean", "tidy the trace"),
                         ("3. Train", "learn the pattern"), ("4. Run", "do it alone")])
        y -= 0.004
        y = h2(ax, y, "The three questions it answers at every step")
        y = flow(ax, y, [("WHERE", "transformer model"), ("WHAT", "language model"),
                         ("HOW", "executor")],
                 "WHERE = which element to act on, learned from the demonstration. WHAT = which value to type, read "
                 "from the source record. HOW = the actual click or keystroke. Splitting them keeps each part simple: "
                 "the model never reasons about values, and the language model never reasons about navigation.")
        y -= 0.006
        y = h2(ax, y, "How it sees the screen")
        y = para(ax, y, "Perception is an adapter, not one fixed method. On the desktop form it reads the Windows "
                        "accessibility tree; in the spreadsheet it reads the Excel object model; on the web and in "
                        "the inbox it reads the page structure. All of them are normalised into the same shape, so "
                        "the rest of the system does not change when the source changes.")
        pdf.savefig(fig); plt.close(fig)

        # ---------------- 2. The three scopes ----------------
        fig, ax, y = page(pdf, "The Three Scopes",
                          "Chosen to span different kinds of work, so the claim is 'varied GUI workflows', not 'one form'")
        for name, sub, lines in [
            ("Scope 1 \u2014 Data-Entry Form", "Single application, mostly linear",
             ["Task: fill a car insurance form from a source record, one client per run.",
              "Perception: Windows UI Automation accessibility tree.",
              "Why it is here: proves the core loop \u2014 watch once, then reproduce the order and the values."]),
            ("Scope 2 \u2014 Web to Excel", "Two applications at once",
             ["Task: read records from a web page and write them into the right cells of a spreadsheet.",
              "Perception: browser page structure plus the Excel object model.",
              "Why it is here: proves the approach is not tied to one application or one way of seeing."]),
            ("Scope 3 \u2014 Email Triage", "Judgement, not just entry",
             ["Task: read an incoming message and decide what to do with it, then carry that action out.",
              "Five decisions: reply, forward, schedule, cold email, leave alone.",
              "Why it is here: the decision is the work; it tests choosing an action, not only performing one."]),
        ]:
            box(ax, y + 0.012, 0.145)
            y = h2(ax, y, name)
            y = para(ax, y, sub, size=9.6, color=MUTED)
            for ln in lines:
                y = bullet(ax, y, ln, size=10.0)
            y -= 0.020

        y = h2(ax, y, "The one-line answer if asked why these three")
        y = para(ax, y, "They differ in the thing that matters for the claim: one application versus two, and "
                        "performing an action versus choosing one. If the same architecture handles all three, the "
                        "result is about the approach rather than about a single task.")
        pdf.savefig(fig); plt.close(fig)

        # ---------------- 3. Objectives ----------------
        fig, ax, y = page(pdf, "The Seven Objectives",
                          "What each one asks, how it is measured, and what the target is")
        rows = [("#", "Objective", "Metric", "Target"),
                ("1", "Perception", "Target Element Recall (TER)", "\u2265 95%"),
                ("2", "Representation", "Encoding Ambiguity Rate (EAR)", "< 5%  (lower better)"),
                ("3", "Learning", "STMC, then Target Accuracy (TA)", "\u2265 90% each"),
                ("4", "Adaptability", "Task success on a changed interface", "\u2265 75%"),
                ("5", "Scalability", "Accuracy held as data grows", "\u2265 90% at every level"),
                ("6", "Execution & Integration", "Completion rate; error rate; redundant steps", "\u2265 85%; \u2264 10%; \u2264 20%"),
                ("7", "Evaluation vs RPA", "Setup time, adaptability, errors", "> 10\u201320% better")]
        y = table(ax, y, rows, [0.03, 0.26, 0.37, 0.24])
        y -= 0.010
        y = h2(ax, y, "What each metric actually counts")
        for t in ["TER: was the element the person clicked present in what the system captured?",
                  "EAR: how often does one name point to more than one element, so the system cannot tell them apart?",
                  "STMC: how often can a recorded action be tied to exactly one element on the previous screen?",
                  "TA: how often does the trained model pick the element the person picked?",
                  "Completion rate: did the run reach the end without a person stepping in?",
                  "Error rate: how many actions failed to do what they were meant to do?"]:
            y = bullet(ax, y, t, size=10.0)
        y -= 0.010
        y = h2(ax, y, "The four verdicts")
        y = para(ax, y, "MET: every scope measured reached the target.   PARTIALLY MET: some did, some did not.   "
                        "NOT MET: none did.   NOT EVALUATED: no data was collected, which is a gap in evidence "
                        "rather than a failure. Knowing this difference is worth a mark on its own.")
        pdf.savefig(fig); plt.close(fig)

        # ---------------- 4. Real numbers ----------------
        fig, ax, y = page(pdf, "Numbers That Are Actually Measured",
                          "Every figure here was computed from the system's own logs. Learn these.")
        y = h2(ax, y, "Scope 1 \u2014 Data-Entry Form")
        rows = [("What", "Value", "Meaning"),
                ("Encoding ambiguity", ("10.88%", BAD), "Above the 5% limit"),
                ("Transition mapping", ("81.69%", BAD), "Below the 90% target"),
                ("Model accuracy", ("52.25%", BAD), "One real training run, 6,401 train / 1,129 validation"),
                ("Completed runs logged", ("0 of 196", GREY), "Known logging defect, see below")]
        y = table(ax, y, rows, [0.26, 0.17, 0.47])
        y = h2(ax, y, "Scope 2 \u2014 Web to Excel")
        rows = [("What", "Value", "Meaning"),
                ("Rows processed", ("131 of 131", OK), "Across 9 automation runs"),
                ("Write fidelity", ("524 of 524", OK), "Each written cell read back and matched"),
                ("Adaptability, pooled", ("75.0%", OK), "18 of 24 fields, across 8 versions"),
                ("Adaptability, strict", ("28.6%", BAD), "2 of 7 changed versions fully correct"),
                ("Escalations", ("0", OK), "No row handed to a person")]
        y = table(ax, y, rows, [0.26, 0.17, 0.47])
        y = h2(ax, y, "Scope 3 \u2014 Email Triage")
        rows = [("What", "Value", "Meaning"),
                ("Decision stability", ("82.7%", OK), "229 of 277 repeats, 43 runs over 27 messages"),
                ("Reached a finished action", ("54.5%", WARN), "The rest were deferred to a person by design"),
                ("Handed to a person", ("43.7%", WARN), "Replies needing real human content")]
        y = table(ax, y, rows, [0.26, 0.17, 0.47])
        y -= 0.004
        box(ax, y + 0.012, 0.115, "#FFF4E5")
        y = h2(ax, y, "Careful: Chapter 4 currently shows practice values", BAD)
        y = para(ax, y, "The percentages printed in the Chapter 4 figures (96 / 95 / 97 for perception, 92 for the "
                        "model, and so on) are placeholders, not measurements. If asked where a figure came from, "
                        "the honest answer is that the section is a draft and the measured values are the ones "
                        "above. Do not defend a placeholder as a result.", size=10.0)
        pdf.savefig(fig); plt.close(fig)

        # ---------------- 5. Drill sheet ----------------
        fig, ax, y = page(pdf, "Drill Sheet",
                          "Likely questions, with short answers you can say out loud")
        qa = [
            ("What is the contribution?",
             "A GUI agent that learns a specific person's workflow from one demonstration and reproduces it, across "
             "three different kinds of application, without task-specific rules."),
            ("How is this different from RPA?",
             "RPA is configured by hand and follows fixed rules. Intern is taught by demonstration and predicts the "
             "next action from the state of the screen."),
            ("How is it different from ChatGPT-style computer-use agents?",
             "Those follow a general instruction. Intern reproduces how this particular user did the task, including "
             "their order of work."),
            ("Does it just memorise coordinates?",
             "No. It is trained on the identity of elements, not their positions, which is why reordering the "
             "interface did not break it while renaming elements did."),
            ("Why the accessibility tree instead of computer vision?",
             "It was sufficient and far cheaper for the environments tested. It is a scope decision, and the thesis "
             "states plainly that the accuracy target was met through a different method than first proposed."),
            ("Why did Scope 3 do worst?",
             "Its encoding is the weakest. Inbox rows repeat the same structure, so element names collide, and that "
             "flows into the learning stage."),
            ("Why is one objective not evaluated?",
             "The comparison against a rule-based tool needs that tool run on the same tasks, which was not done. "
             "It is missing evidence, not a failed test."),
            ("Why is task completion separate from correctness?",
             "A run can reach the end with a wrong value in a field. Reporting only completion would overstate the "
             "result, which is the evaluation gap identified in the review of literature."),
        ]
        for q, a in qa:
            y = para(ax, y, "Q  " + q, size=10.6, color=INK)
            y = para(ax, y, "A  " + a, size=10.0, color=MUTED, x=0.085, width=0.83)
            y -= 0.008
        pdf.savefig(fig); plt.close(fig)

        # ---------------- 6. Weak spots ----------------
        fig, ax, y = page(pdf, "Weak Spots, and How to Answer Them",
                          "Every one of these is answerable. Say the limit first, then the reason, then the fix.")
        for weak, answer in [
            ("Model accuracy is 52%, far below the 90% target.",
             "Measured on one training run with limited clean data. The cause is known: ambiguous element names "
             "reduce what the model can learn. The fix is better encoding and a larger recorded set, not a different "
             "model."),
            ("Successful runs were not recorded.",
             "A defect in the measurement code, not in the agent: the part that writes the metrics is skipped when a "
             "run ends in certain ways. The completion figures are therefore a lower bound."),
            ("The adaptability result rests on 24 field decisions.",
             "Correct, and the thesis says so. It shows a direction \u2014 rearrangement is tolerated, renaming is not "
             "\u2014 rather than establishing a rate."),
            ("Some measures are proxies.",
             "Scope 3's stability measures consistency, not success on a changed interface, and its handoff rate is "
             "a design choice rather than an error. Both are labelled as such rather than presented as the real "
             "metric."),
            ("Three objectives have no data.",
             "Vision perception, the RPA comparison, and parts of scalability were not measured because effort went "
             "into making one environment work first. That is a scope decision, stated openly."),
        ]:
            y = para(ax, y, "\u25b8  " + weak, size=10.6, color=BAD)
            y = para(ax, y, answer, size=10.0, color=INK, x=0.085, width=0.83)
            y -= 0.012

        y -= 0.004
        y = h2(ax, y, "The two sentences worth memorising")
        box(ax, y + 0.012, 0.085)
        y = para(ax, y, "\u201cThe system works end to end in three different environments. Where it falls short, we "
                        "can say exactly which stage is responsible and what would fix it.\u201d",
                 size=10.4, x=0.085, width=0.80)
        y = para(ax, y, "\u201cWe report what we measured, including where we fell short, and we distinguish between "
                        "what failed and what we did not test.\u201d",
                 size=10.4, x=0.085, width=0.80)
        pdf.savefig(fig); plt.close(fig)

        # ---------------- 7. Glossary ----------------
        fig, ax, y = page(pdf, "Glossary",
                          "Terms that may come up, in plain words")
        for term, meaning in [
            ("Behavioural cloning", "Learning to copy what a person did, by training on recordings of them doing it."),
            ("Demonstration", "One recorded run of a person performing the task."),
            ("Trace", "The recorded sequence of screens and actions from a demonstration."),
            ("State", "What is on the screen at one moment, as a list of elements."),
            ("Decision unit", "One point where the system must act: a field, a cell, or a routing choice."),
            ("State-action pair", "One screen together with the action the person took on it. The unit of training."),
            ("Accessibility tree", "A structured list of the controls on screen, published by Windows for screen readers."),
            ("Transformer", "The model type used to predict which element to act on next."),
            ("Element key", "The name used to identify an element. If two elements share one, the system cannot tell them apart."),
            ("Held-out validation", "Data kept aside from training, used to check the model on examples it has not seen."),
            ("Generalisation", "Performing correctly in a situation that differs from the one trained on."),
            ("Wilson interval", "A way of showing how uncertain a percentage is when the sample is small."),
        ]:
            ax.text(0.075, y, term, fontsize=10.4, weight="bold", color=INK, va="top")
            y = para(ax, y - 0.019, meaning, size=10.0, color=MUTED, x=0.085, width=0.82)
            y -= 0.004
        pdf.savefig(fig); plt.close(fig)

    return OUT


if __name__ == "__main__":
    print(build())
