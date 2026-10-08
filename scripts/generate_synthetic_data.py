"""
generate_synthetic_data.py
==========================
Writes synthetic sample files for each of the three scopes into
data/synthetic/scope_1, scope_2 and scope_3.

Every file in every scope is one state -> action -> next_state step, the
shape of a real Scope 1 trace step (direct request 2026-10-08: Scope 2 and
Scope 3 "similar to Scope #1 in terms of State-Action"): the screen as UI
elements, what the user does next, and the screen after it. Scope 2 is the
grade portal with the sheet in the background; Scope 3 is the opened email
with its decision buttons.

They are NOT evaluation data. Every file carries "synthetic": true, and all of
them live under data/synthetic/, a path none of the thesis metric scripts read
from (see scripts/thesis_figures/objective_metrics.py). The folder is
gitignored.

Output is deterministic for a given --seed, so a regenerated set is identical.

Usage
    python scripts/generate_synthetic_data.py              # 10,000 per scope
    python scripts/generate_synthetic_data.py --count 50   # smaller set
"""
from __future__ import annotations

import argparse
import json
import os
import random
from datetime import datetime, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT_ROOT = os.path.join(REPO, "data", "synthetic")

BASE_TIME = datetime(2026, 8, 1, 9, 0, 0)

# --------------------------------------------------------------------------
# Scope 1: one recorded step on the car insurance form
# --------------------------------------------------------------------------

S1_TABS = ["Policy", "Driver 1", "Driver 2", "Vehicle", "Coverage", "Claims", "Payment", "Review"]
S1_FIELDS = {
    "Policy": ["Policy Number", "Effective Date", "Term", "Agent Code"],
    "Driver 1": ["First Name", "Last Name", "Date of Birth", "License Number"],
    "Driver 2": ["First Name", "Last Name", "Date of Birth", "License Number"],
    "Vehicle": ["VIN", "Make", "Model", "Year"],
    "Coverage": ["Liability Limit", "Deductible", "Collision", "Comprehensive"],
    "Claims": ["Prior Claims", "Claim Date", "Claim Amount", "At Fault"],
    "Payment": ["Billing Address", "Payment Method", "Card Number", "Plan"],
    "Review": ["Confirm", "Notes", "Signature", "Submit"],
}
S1_CONTROL = {"Term": "ComboBoxControl", "Plan": "ComboBoxControl", "Collision": "CheckBoxControl",
              "Comprehensive": "CheckBoxControl", "At Fault": "CheckBoxControl", "Submit": "ButtonControl",
              "Confirm": "CheckBoxControl"}


def _s1_element(eid: int, label: str, x: int, y: int, focused: bool) -> dict:
    ctrl = S1_CONTROL.get(label, "EditControl")
    return {
        "element_id": f"elem_{eid}",
        "type": ctrl.replace("Control", "").lower() + "control",
        "control_type": ctrl,
        "bbox": [x, y, x + 260, y + 28],
        "text": label,
        "value": "",
        "label": label,
        "enabled": True,
        "visible": True,
        "focused": focused,
        "confidence": 1.0,
        "source": "uia",
    }


def scope1_step(i: int, rng: random.Random) -> dict:
    tab = S1_TABS[i % len(S1_TABS)]
    fields = S1_FIELDS[tab]
    target = rng.randrange(len(fields))
    elements = [_s1_element(k, f, 420, 180 + 44 * k, k == target) for k, f in enumerate(fields)]
    label = fields[target]
    action_type = "click" if S1_CONTROL.get(label) in ("ButtonControl", "CheckBoxControl") else "type"
    value = "" if action_type == "click" else f"sample-{rng.randrange(10**5):05d}"
    next_elements = [dict(e) for e in elements]
    if action_type == "type":
        next_elements[target]["value"] = value
    return {
        "synthetic": True,
        "trace_id": f"synthetic_step_{i:05d}",
        "timestamp": (BASE_TIME + timedelta(seconds=2 * i)).isoformat(),
        "duration": round(rng.uniform(0.4, 2.5), 2),
        "type": "form_filling",
        "tab": tab,
        "state": {"source": "uia", "window_title": "Car Insurance Entry",
                  "focused_element_id": f"elem_{target}", "elements": elements},
        "action": {"type": action_type, "target_element_id": f"elem_{target}",
                   "target_label": label, "value": value},
        "next_state": {"source": "uia", "window_title": "Car Insurance Entry",
                       "focused_element_id": f"elem_{target}", "elements": next_elements},
    }


# --------------------------------------------------------------------------
# Shared: one UI element in the same schema as a real UIA snapshot.
# Scopes 2 and 3 also say which window it is in (window_role), the way the
# real recorder marks Scope 1's background Notepad, so a step can hold both
# the window being worked in and the source it reads from.
# --------------------------------------------------------------------------

def _element(eid: int, label: str, ctrl: str, bbox, value: str = "", focused: bool = False,
             window_role: str = "active", window_title: str = "") -> dict:
    return {
        "element_id": f"elem_{eid}",
        "type": ctrl.replace("Control", "").lower() + "control",
        "control_type": ctrl,
        "bbox": list(bbox),
        "text": label,
        "value": value,
        "label": label,
        "enabled": True,
        "visible": True,
        "focused": focused,
        "confidence": 1.0,
        "source": "uia",
        "window_role": window_role,
        "window_title": window_title,
    }


def _with_value(elements: list, element_id: str, value: str, focus: bool = True) -> list:
    """The screen after an action: the target holds the value and has focus."""
    out = []
    for e in elements:
        e = dict(e)
        if e["element_id"] == element_id:
            e["value"] = value
        if focus and e["window_role"] == "active":
            e["focused"] = e["element_id"] == element_id
        out.append(e)
    return out


# --------------------------------------------------------------------------
# Scope 2: one step of copying one value from the sheet into the portal
# --------------------------------------------------------------------------
#
# Direct request 2026-10-08: make Scope #2 (and #3) "similar to Scope #1 in
# terms of State-Action". Before, a Scope 2 file was a finished-row RESULT
# (what ended up in the portal), with no screen and no action. Now each file
# is one step: the portal row as it looks (active window) plus that student's
# sheet cells (background window, like Scope 1's Notepad), the action the
# user takes next, and the screen after it.

S2_COURSES = ["BS Information Systems", "BS Information Technology", "BS Computer Science"]
S2_PORTAL = "Student Grade Portal"
S2_SHEET = "grade_sheet.xlsx - Excel"
# Portal fields in the order they are filled. The source column, or None for
# a field the user DERIVES (Remarks follows from the grade, it is not copied).
S2_FIELDS = [
    ("Course", "EditControl", "PROGRAM"),
    ("Year 1-5", "EditControl", "YEAR LEVEL"),
    ("Grade 0-100", "EditControl", "FINAL GRADE"),
    ("Remarks", "ComboBoxControl", None),
]
S2_PASS_MARK = 75


def _s2_student(n: int) -> dict:
    """One student's sheet values, fixed by the student number so every step
    for the same student agrees."""
    r = random.Random(f"s2-student-{n}")
    grade = r.randint(60, 99)
    return {
        "STUDENT NUMBER": f"2021-{10000 + n:05d}",
        "NAME OF STUDENT": f"Student {n:05d}",
        "MIDTERM": str(r.randint(60, 99)),     # decoy: a grade column that is NOT the final grade
        "FINAL": str(r.randint(60, 99)),       # decoy
        "PROGRAM": r.choice(S2_COURSES),
        "YEAR LEVEL": str(r.randint(1, 5)),
        "FINAL GRADE": str(grade),
    }


def scope2_step(i: int, rng: random.Random) -> dict:
    n, k = divmod(i, len(S2_FIELDS))           # student n, field k of that student
    sheet = _s2_student(n)
    remarks = "Passed" if int(sheet["FINAL GRADE"]) >= S2_PASS_MARK else "Failed"
    portal_values = {"Course": sheet["PROGRAM"], "Year 1-5": sheet["YEAR LEVEL"],
                     "Grade 0-100": sheet["FINAL GRADE"], "Remarks": remarks}

    elements, eid = [], 0
    # Active window: the student's portal row. Fields before k are already filled.
    for j, (label, ctrl, _col) in enumerate(S2_FIELDS):
        value = portal_values[label] if j < k else ""
        elements.append(_element(eid, label, ctrl, (300 + 170 * j, 260, 460 + 170 * j, 288),
                                 value=value, focused=(j == k), window_title=S2_PORTAL))
        eid += 1
    # Background window: the same student's row in the sheet.
    cell_id = {}
    for j, (header, value) in enumerate(sheet.items()):
        cell_id[header] = f"elem_{eid}"
        elements.append(_element(eid, header, "DataItemControl", (40 + 120 * j, 700, 155 + 120 * j, 720),
                                 value=value, window_role="background", window_title=S2_SHEET))
        eid += 1

    label, ctrl, column = S2_FIELDS[k]
    target = f"elem_{k}"
    value = portal_values[label]
    if column is not None:
        action = {"type": "type", "target_element_id": target, "target_label": label,
                  "value": value, "source_element_id": cell_id[column], "source_label": column}
    else:
        action = {"type": "select", "target_element_id": target, "target_label": label,
                  "value": value, "source_element_id": None,
                  "derived_from": {"element_id": "elem_2", "label": "Grade 0-100",
                                   "rule": f"Passed if >= {S2_PASS_MARK}, else Failed"}}
    return {
        "synthetic": True,
        "trace_id": f"synthetic_step_{i:05d}",
        "timestamp": (BASE_TIME + timedelta(seconds=3 * i)).isoformat(),
        "duration": round(rng.uniform(0.6, 3.0), 2),
        "type": "sheet_to_portal",
        "tab": "Records",
        "row": n + 1,
        "student_id": sheet["STUDENT NUMBER"],
        "state": {"source": "uia", "window_title": S2_PORTAL,
                  "focused_element_id": target, "elements": elements},
        "action": action,
        "next_state": {"source": "uia", "window_title": S2_PORTAL,
                       "focused_element_id": target,
                       "elements": _with_value(elements, target, value)},
    }


# --------------------------------------------------------------------------
# Scope 3: one step of handling one email
# --------------------------------------------------------------------------
#
# Before (same request as Scope 2), a Scope 3 file was only the incoming
# message, without even the decision. Now each file is one step: the open
# email and the four decision buttons as they appear on the inbox page, the
# button the user clicks (the decision), and the screen after it.

S3_SENDERS = [
    ("Jordan Reyes", "acmebroker.com"), ("Priya Raman", "vendorco.com"),
    ("Dana Whitfield", "northline.example.com"), ("HR Benefits", "companyhr.example.com"),
    ("IT Alerts", "thirdpartyvendor.com"), ("Registrar Office", "campusportal.edu"),
]
# (subject, body, the decision a user makes on this kind of email)
S3_TEMPLATES = [
    ("Quick question about invoice #{n}", "Could you confirm the line item on page 2 of invoice #{n}?", "reply"),
    ("Meeting request - {day}", "Would {day} at 2pm work for a short call?", "schedule"),
    ("FYI: maintenance window {day}", "Systems will be unavailable on {day} from 1am to 3am.", "schedule"),
    ("Please forward to the team", "Sharing the updated schedule; please pass it along.", "forward"),
    ("Weekly newsletter #{n}", "Here is this week's roundup of updates.", "leave_alone"),
]
S3_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
S3_PAGE = "Inbox Dispatch"
# The four decision buttons, by the ids the real page uses for them.
S3_BUTTONS = [("Reply", "reply"), ("Forward", "forward"), ("Schedule", "schedule"), ("Leave alone", "leave_alone")]
S3_RESULT = {"reply": "Reply drafted.", "forward": "Forward drafted.",
             "schedule": "Scheduled.", "leave_alone": "Archived."}


def scope3_step(i: int, rng: random.Random) -> dict:
    name, domain = rng.choice(S3_SENDERS)
    email = f"{name.lower().replace(' ', '.')}@{domain}"
    subj_t, body_t, decision = rng.choice(S3_TEMPLATES)
    fill = {"n": rng.randint(100, 999), "day": rng.choice(S3_DAYS)}
    subject = subj_t.format(**fill)
    body = f"Hi,\n\n{body_t.format(**fill)}\n\nThanks,\n{name.split()[0]}"

    # Active window: the opened email and its decision buttons.
    elements = [
        _element(0, "Subject", "TextControl", (260, 120, 1100, 150), value=subject, window_title=S3_PAGE),
        _element(1, "From", "TextControl", (260, 160, 900, 184), value=f"{name} <{email}>", window_title=S3_PAGE),
        _element(2, "Message body", "DocumentControl", (260, 200, 1100, 420), value=body, window_title=S3_PAGE),
    ]
    for j, (label, _key) in enumerate(S3_BUTTONS):
        elements.append(_element(3 + j, label, "ButtonControl", (260 + 130 * j, 440, 380 + 130 * j, 472),
                                 window_title=S3_PAGE))
    status = _element(7, "Status", "TextControl", (260, 490, 900, 512), window_title=S3_PAGE)
    elements.append(status)

    target = 3 + [k for _l, k in S3_BUTTONS].index(decision)
    label = S3_BUTTONS[target - 3][0]
    action = {"type": "click", "target_element_id": f"elem_{target}", "target_label": label,
              "value": "", "decision": decision}
    after = _with_value(elements, "elem_7", S3_RESULT[decision], focus=False)
    return {
        "synthetic": True,
        "trace_id": f"synthetic_step_{i:05d}",
        "timestamp": (BASE_TIME + timedelta(minutes=7 * i)).isoformat(),
        "duration": round(rng.uniform(1.0, 6.0), 2),
        "type": "inbox_triage",
        "tab": "Inbox",
        "message": {"id": f"synthetic-{i:05d}", "sender_email": email, "subject": subject,
                    "received_at": (BASE_TIME + timedelta(minutes=7 * i)).isoformat()},
        "state": {"source": "uia", "window_title": S3_PAGE,
                  "focused_element_id": None, "elements": elements},
        "action": action,
        "next_state": {"source": "uia", "window_title": S3_PAGE,
                       "focused_element_id": None, "elements": after},
    }


# --------------------------------------------------------------------------

GENERATORS = {
    "scope_1": ("step", scope1_step),
    # File stems kept as before so existing paths still resolve; every file
    # in every scope is now one state -> action -> next_state step.
    "scope_2": ("row", scope2_step),
    "scope_3": ("message", scope3_step),
}


def generate(count: int, seed: int = 7, out_root: str = OUT_ROOT) -> dict[str, int]:
    written = {}
    for scope, (stem, fn) in GENERATORS.items():
        rng = random.Random(f"{seed}-{scope}")
        folder = os.path.join(out_root, scope)
        os.makedirs(folder, exist_ok=True)
        for i in range(count):
            path = os.path.join(folder, f"{stem}_{i:05d}.json")
            with open(path, "w", encoding="utf8") as fh:
                json.dump(fn(i, rng), fh, indent=1)
        written[scope] = count
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    result = generate(args.count, args.seed)
    for scope, n in result.items():
        print(f"{scope}: {n:,} synthetic files -> {os.path.join(OUT_ROOT, scope)}")
