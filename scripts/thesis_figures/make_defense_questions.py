"""
make_defense_questions.py
=========================
Builds docs/defense_questions.pdf: the questions a thesis panel is most
likely to ask about Intern, each with a short suggested answer and, where the
question is a trap, what to watch out for.

Direct request 2026-10-07: "What are the possible questions you think will
most likely be asked, create a PDF, please."

Same rule as Chapter 4 (build_chapter4.py): no result number is typed by
hand. Every measured figure is read from objective_metrics.collect_all() at
build time, so re-running this after new runs updates the answers. Figures
from earlier experiments that the metrics script does not compute (the
June 2026 clone test) are labelled with their source.

The PDF is printed by the Playwright Chromium already used across the
project, so no new dependency is needed.

Usage
    python scripts/thesis_figures/make_defense_questions.py
"""
from __future__ import annotations

import html
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

from objective_metrics import collect_all  # noqa: E402

OUT = os.path.join(ROOT, "docs", "defense_questions.pdf")


def figures():
    """Every number the answers quote, read live from the metric files."""
    o = collect_all()

    def cell(key, scope):
        c = o[key].scopes.get(scope)
        if c is None or c.value is None:
            return "not measured"
        n = f"{c.n:,}" if c.n is not None else "?"
        return f"{c.display()} (n={n})"

    verdicts = {o[k].number: o[k].verdict() for k in o}
    count = lambda v: sum(1 for x in verdicts.values() if x == v)  # noqa: E731
    return {
        "acc": cell("obj3", "S1"),
        "amb": cell("obj2", "S1"),
        "trans": cell("obj4", "S1"),
        "adapt2": cell("obj6", "S2"),
        "adapt3": cell("obj6", "S3"),
        "e2e1": cell("obj8", "S1"),
        "e2e2": cell("obj8", "S2"),
        "e2e3": cell("obj8", "S3"),
        "err1": cell("obj9", "S1"),
        "err2": cell("obj9", "S2"),
        "err3": cell("obj9", "S3"),
        "met": count("MET"),
        "partial": count("PARTIALLY MET"),
        "notmet": count("NOT MET"),
        "noteval": count("NOT EVALUATED"),
        "noteval_list": ", ".join(str(n) for n, v in sorted(verdicts.items()) if v == "NOT EVALUATED"),
    }


def questions(f):
    """(section, [(likely, question, answer, watch_out)]). likely=True marks
    the questions most worth rehearsing out loud."""
    return [
        ("The idea and why it matters", [
            (True, "In one sentence, what is Intern?",
             "A desktop agent that watches how a person does a repetitive computer task, learns "
             "their way of doing it, and then does it for them by looking at the screen and using "
             "the mouse and keyboard, like a person would.",
             "Keep it to one sentence. Panels test whether you can explain it simply."),
            (True, "How is this different from RPA tools like UiPath or Power Automate?",
             "RPA has to be scripted by hand by a developer, step by step, and breaks when the screen "
             "changes. Intern learns the task from demonstrations instead of a script, and it learns "
             "this user's own way of doing it.",
             "Don't claim Intern beats RPA. The RPA comparison (Objectives 11 and 12) has not been "
             "measured yet. Say the comparison tool is built and the data is still to be collected."),
            (True, "How is it different from AI computer-use agents (Claude computer use, OpenAI Operator)?",
             "Those agents do a task their own way, from a written instruction, usually with a large "
             "cloud model. Intern copies how this particular user does it (behavioral cloning), runs "
             "small models locally, and only asks a language model for the parts its learned habits "
             "can't settle.",
             "If asked 'why not just use one of them?', the honest answer is personalization and "
             "running locally, not that Intern is more capable today."),
            (False, "Who would actually use this?",
             "Office staff who re-type the same kind of data every day: insurance intake forms, "
             "copying grades from a spreadsheet into a school portal, and sorting a busy inbox. "
             "Those are the three scopes.",
             ""),
        ]),
        ("How it works (Methods)", [
            (True, "What exactly does the Transformer learn, and what does the LLM do?",
             "The Transformer learns WHERE to act and in what order: which field or button comes next, "
             "the way the user did it. The LLM supplies WHAT value to type when a value has to be "
             "read from the source. Mechanical HOW (type into a text box, open a dropdown) follows "
             "the widget type.",
             "Expect 'so the LLM does the real work?' Answer: the order and the workflow, which is "
             "the personal part, are learned; the LLM only reads values."),
            (False, "Why behavioral cloning and not reinforcement learning?",
             "Cloning learns directly from a few demonstrations of the real task, which is what a "
             "user can give. Reinforcement learning needs a reward and many thousands of trial runs "
             "on the live screen, which is slow and risky for real applications.",
             "Admit the known weakness of cloning: small mistakes pile up over long tasks. That is "
             "exactly why Scope #1's long form fails (see Results)."),
            (True, "How does Intern 'see' the screen?",
             "Through Windows UI Automation, the same accessibility information screen readers use: "
             "every element's type, label, value and position. Perception is a swappable adapter, "
             "so a vision-based reader could replace it without touching the learning part.",
             "Objective 1 (vision-based perception, 95% detection) was not evaluated. Say so plainly: "
             "the system uses the accessibility tree, and vision is future work."),
            (False, "Did you hardcode anything for the specific tasks?",
             "No field names, form layouts or task steps are written in the agent. Each scope passes "
             "its own small configuration. There are about five universal widget rules (for example, "
             "a dropdown is opened before choosing), which apply to any application.",
             "Be precise: 'no task-specific rules' is true; 'zero rules at all' is not."),
            (False, "How do you measure your objectives?",
             "One script (objective_metrics.py) computes every result straight from the run logs. "
             "An objective counts as met only if every scope that was measured met it. 'Not evaluated' "
             "means there is no data, which is different from 'not met'.",
             ""),
        ]),
        ("Results", [
            (True, "How many of your 12 objectives did you meet?",
             f"{f['met']} met, {f['partial']} partially met, {f['notmet']} not met, and {f['noteval']} "
             f"not evaluated (Objectives {f['noteval_list']}). Every number comes straight from the logs; "
             "none were adjusted.",
             "Don't get defensive. Lead with what works, then say what doesn't and why."),
            (True, f"Your action-prediction accuracy is {f['acc']} against a 90% target. Why so low?",
             "The model was trained on a limited number of demonstrations of a long form with eight "
             "tabs, where tab switches are rare events. Rare steps are exactly what cloning learns "
             "worst. The plan is a larger recording campaign and retraining.",
             "Don't blame the data without a plan. Say what more data would fix and what it wouldn't."),
            (True, f"Scope #1 completes {f['e2e1']} of runs end to end. Does it work at all?",
             "The pieces work: in a June 2026 clone test (test_clone.py, earlier version), a model "
             "trained on top-down demonstrations filled top-down (74% exact) and one trained on "
             "bottom-up filled bottom-up (93%), proving it copies the user's order. What fails is "
             "finishing the whole 176-field form: errors pile up across tabs.",
             "This is the hardest question. Show the clone-test evidence, then own the end-to-end failure."),
            (True, f"Scope #2 completes {f['e2e2']}. Isn't that too good to be true?",
             "Completion there means every row was processed and verified without a crash. It does "
             "not mean every field was filled: some fields (for example 'Course') are left empty when "
             "the system isn't sure, because an empty field is safer than a wrong one.",
             "Don't let '100%' stand unqualified. A panel member who opens the output will see empty fields."),
            (True, f"Adaptability is {f['adapt2']} on Scope #2. Is that meaningful?",
             "It lands exactly on the 75% threshold with a small sample (3 fields across 8 portal "
             "variants), and only 3 of 8 variants were fully resolved. It is a pass, but a weak one; "
             f"Scope #3 measured {f['adapt3']}.",
             "Say 'weak pass' before they do."),
            (False, f"Scope #3 completes {f['e2e3']} and hands {f['err3']} to a person. Why?",
             "By design. When neither the user's habits nor the reasoning step is sure, the email is "
             "left for a person instead of guessed. A wrong reply sent in the user's name costs more "
             "than one the user handles.",
             "Have one example ready of an email it correctly refused to guess on."),
            (False, f"Objective 2 (encoding ambiguity) is {f['amb']} against under 5%. What does that mean?",
             "About one in ten recorded screen states looked identical to another state but needed a "
             "different action, so the model couldn't tell them apart. Adding which fields are filled "
             "and which has focus already cut this; more state detail would cut it further.",
             ""),
            (True, f"Why are Objectives {f['noteval_list']} not evaluated?",
             "Vision perception (1) is not built. Scalability (7) needs training at several data "
             "sizes, after the recording campaign. The RPA comparison (11, 12) needs timed runs of a "
             "traditional RPA tool on the same task; the comparison and significance-test tool is "
             "built and tested, but the runs have not been done.",
             "Never fill these gaps with estimates during the defense."),
            (False, "Should we accept a thesis that misses most of its numeric targets?",
             "The contribution is a working, personalized, demonstration-learned agent across three "
             "different kinds of work, with an honest measurement pipeline that reports what the logs "
             "say. The misses are explained and each has a concrete cause and next step.",
             "This is for your adviser to help frame. Agree the line before the defense."),
        ]),
        ("The three scopes in detail", [
            (False, "Scope #3: how does it decide to reply, forward, schedule, or leave an email alone?",
             "First from the user's habits: a model trained on how they handled similar emails, then "
             "their history with that sender. Only what habits can't settle goes to a local LLM. The "
             "demo can be run in Habits only, Habits + reasoning, or Reasoning only.",
             ""),
            (True, "Does it send emails on my behalf?",
             "No. It only creates drafts and calendar events. The email interface in the code has no "
             "send function at all, so it cannot send even by mistake.",
             "This answer reassures the panel. Say it with confidence."),
            (False, "What if the LLM makes up a meeting time?",
             "It can't: dates and times are read with fixed rules from the email's own words, and "
             "only when the email states both a date and a clock time. Otherwise the email waits for "
             "a person. Checks like 'Am I free next Friday?' answer 'unclear' rather than guess.",
             ""),
            (False, "Scope #2: how does it know which spreadsheet column goes into which form field?",
             "It reads each field's label and looks for it among the column names, like a person "
             "would. Fields the names can't settle go to a local LLM, which is allowed to answer "
             "'none'. Pass/fail is derived by a rule learned from a demonstration, not configured.",
             "The 'Course' field is currently left empty. Say it's a known gap with a fix in progress."),
            (False, "Scope #1: why does it struggle with the long form when short ones work?",
             "Each step depends on the previous one, so one wrong click shifts everything after it. "
             "On a 176-field, 8-tab form those small errors add up, and the first click from a blank "
             "screen is the hardest. Letting the user correct mistakes during runs (DAgger) is the "
             "standard fix and is the next step.",
             ""),
        ]),
        ("Generalization, speed, and data", [
            (True, "Would it work on an application it has never seen?",
             "The agent itself has no application-specific code, and the screen reader plugs in per "
             "app. A new application still needs demonstrations of the new task. That is the point: "
             "it learns from the user, not from a developer's script.",
             "Don't claim zero-shot generalization. Scope #2's eight portal variants are the evidence you have."),
            (False, "Is it faster than doing the task by hand?",
             "Not measured against a person yet. That is part of the RPA comparison still to be run. "
             "In fast mode, Scope #1 writes known values directly into fields instead of typing them.",
             "Don't guess a speed-up figure."),
            (False, "How many demonstrations does a new task need?",
             "For one 10-field section, about 20 to 30 passes were needed. Whether the full form scales "
             "on data alone is an open question, and it's exactly what Objective 7 (scalability) will measure.",
             ""),
            (False, "What happens if the window moves or the screen resolution changes?",
             "Clicks currently use screen positions read from the accessibility tree at that moment, "
             "so they follow the window. A change mid-step can still miss. This is listed as a known "
             "risk.",
             ""),
        ]),
        ("Privacy, safety, and ethics", [
            (True, "Where does the recorded data go? Is it private?",
             "Everything stays on the user's machine. Demonstrations are stored locally, the language "
             "model runs locally in LM Studio, and the demo uses a practice mailbox, not a real account.",
             ""),
            (False, "What stops it from doing something irreversible?",
             "It runs as a dry run unless told otherwise, creates drafts instead of sending, verifies "
             "every value it writes by reading it back, refuses to guess when unsure, and the user can "
             "stop it at any time.",
             ""),
            (False, "Could someone use this to impersonate a user?",
             "It acts only on the user's own machine with their own access, the same as the user "
             "sitting there. Sending stays with the user because drafts are never sent automatically.",
             ""),
        ]),
        ("Validity of your method", [
            (True, "Your sample sizes are small. Are the results statistically meaningful?",
             "Several are small (for example n=19 for the ambiguity measure), and that is stated next "
             "to every number. Statistical-significance testing is Objective 12, which is not "
             "evaluated yet.",
             "Say the n out loud whenever you quote a percentage."),
            (False, "You built the test applications yourselves. Isn't that grading your own homework?",
             "Yes, that is a real threat to validity. The mitigation is that the Scope #2 portal comes "
             "in eight variants that each change one thing, and the agent has no code specific to any of them.",
             ""),
            (True, "Did you use AI tools to build this?",
             "Answer honestly and specifically: which tools were used, for what (for example, coding "
             "assistance), and what the team decided and verified itself, such as the design decisions, "
             "the experiments, and the tests.",
             "Agree on this answer as a team before the defense. Inconsistent answers here are worse "
             "than any single answer."),
        ]),
        ("Looking ahead", [
            (False, "What is the biggest limitation?",
             "Long tasks: the cloned policy loses its place on long forms. Everything else (perception "
             "adapter, value reading, safety, measurement) is in place around it.",
             ""),
            (False, "What would you do with six more months?",
             "A larger recording campaign and retraining; letting users correct mistakes during runs; "
             "the RPA comparison with significance tests; a vision-based perception adapter.",
             ""),
            (False, "What did you learn?",
             "Prepare a personal answer: for example, that honest measurement changed the conclusion "
             "more than any single model improvement did.",
             "Make this personal and short."),
        ]),
    ]


CSS = """
@page { size: A4; margin: 18mm 18mm 20mm; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", Arial, sans-serif; color: #18212F; font-size: 10.5pt; line-height: 1.45; }
h1 { font-size: 22pt; margin: 0 0 4pt; }
.sub { color: #5A6475; margin: 0 0 14pt; }
.how { border: 1px solid #DCE1E7; border-radius: 6px; padding: 10pt 12pt; margin-bottom: 14pt; }
.how p { margin: 0 0 4pt; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; padding-bottom: 3pt; border-bottom: 2px solid #18212F; break-after: avoid; }
.q { margin: 0 0 10pt; break-inside: avoid; }
.qt { font-weight: 700; margin: 0 0 2pt; }
.star { color: #9A5B00; }
.a { margin: 0 0 3pt; }
.w { margin: 0; padding: 3pt 8pt; border-left: 3px solid #9A5B00; background: #FBF1E2; font-size: 9.5pt; }
.foot { color: #5A6475; font-size: 8.5pt; margin-top: 18pt; }
"""


def build_html(f) -> str:
    out = [f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"]
    out.append("<h1>Intern: likely defense questions</h1>")
    out.append(f"<p class='sub'>Suggested answers use results computed from the logs on "
               f"{date.today():%B %d, %Y}. Re-run this script before the defense to refresh them.</p>")
    out.append("<div class='how'><p><b>How to use this.</b> Questions marked "
               "<span class='star'>&#9733;</span> are the ones most likely to come up; rehearse those "
               "out loud first. Each answer is a starting point; say it in your own words.</p>"
               "<p>The shaded <b>Watch out</b> notes flag questions that are traps, or where "
               "overclaiming would cost you.</p></div>")
    n = 0
    for section, qs in questions(f):
        out.append(f"<h2>{html.escape(section)}</h2>")
        for likely, q, a, w in qs:
            n += 1
            star = "<span class='star'>&#9733; </span>" if likely else ""
            out.append(f"<div class='q'><p class='qt'>{star}{n}. {html.escape(q)}</p>"
                       f"<p class='a'>{html.escape(a)}</p>")
            if w:
                out.append(f"<p class='w'><b>Watch out:</b> {html.escape(w)}</p>")
            out.append("</div>")
    out.append("<p class='foot'>Generated by scripts/thesis_figures/make_defense_questions.py from "
               "objective_metrics.py. The clone-test figures (74% / 93%) come from test_clone.py, "
               "June 2026, an earlier version of the system.</p>")
    out.append("</body></html>")
    return "".join(out)


def main():
    from playwright.sync_api import sync_playwright

    page_html = build_html(figures())
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content(page_html, wait_until="load")
        pg.pdf(path=OUT, format="A4", print_background=True,
               margin={"top": "18mm", "bottom": "20mm", "left": "18mm", "right": "18mm"})
        b.close()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
