"""
generate_synthetic_data.py
==========================
Writes synthetic sample files for each of the three scopes into
data/synthetic/scope_1, scope_2 and scope_3.

The files mirror the shape of the real recordings (a Scope 1 trace step, a
Scope 2 processed row, a Scope 3 inbox message) so they can be used to
exercise parsers, loaders and scripts without touching real data.

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
# Scope 2: one processed source row written into the spreadsheet
# --------------------------------------------------------------------------

S2_COURSES = ["BS Information Systems", "BS Information Technology", "BS Computer Science"]


def scope2_row(i: int, rng: random.Random) -> dict:
    grade = rng.randint(60, 99)
    filled = {
        "Year 1-5": str(rng.randint(1, 5)),
        "Grade 0-100": str(grade),
        "Course": rng.choice(S2_COURSES),
        "Remarks": "Passed" if grade >= 75 else "Failed",
    }
    return {
        "synthetic": True,
        "row": i + 1,
        "student_id": f"2021-{10000 + i:05d}",
        "status": "filled",
        "reason": "",
        "filled": filled,
        "verified": dict(filled),
        "escalations": [],
    }


# --------------------------------------------------------------------------
# Scope 3: one incoming inbox message
# --------------------------------------------------------------------------

S3_SENDERS = [
    ("Jordan Reyes", "acmebroker.com"), ("Priya Raman", "vendorco.com"),
    ("Dana Whitfield", "northline.example.com"), ("HR Benefits", "companyhr.example.com"),
    ("IT Alerts", "thirdpartyvendor.com"), ("Registrar Office", "campusportal.edu"),
]
S3_TEMPLATES = [
    ("Quick question about invoice #{n}", "Could you confirm the line item on page 2 of invoice #{n}?"),
    ("Meeting request - {day}", "Would {day} at 2pm work for a short call?"),
    ("FYI: maintenance window {day}", "Systems will be unavailable on {day} from 1am to 3am."),
    ("Please forward to the team", "Sharing the updated schedule; please pass it along."),
    ("Weekly newsletter #{n}", "Here is this week's roundup of updates."),
]
S3_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def scope3_message(i: int, rng: random.Random) -> dict:
    name, domain = rng.choice(S3_SENDERS)
    email = f"{name.lower().replace(' ', '.')}@{domain}"
    subj_t, body_t = rng.choice(S3_TEMPLATES)
    fill = {"n": rng.randint(100, 999), "day": rng.choice(S3_DAYS)}
    body = body_t.format(**fill)
    return {
        "synthetic": True,
        "id": f"synthetic-{i:05d}",
        "thread_id": f"synthetic-thread-{i:05d}",
        "sender": f"{name} <{email}>",
        "sender_email": email,
        "subject": subj_t.format(**fill),
        "snippet": body[:60],
        "body_text": f"Hi,\n\n{body}\n\nThanks,\n{name.split()[0]}",
        "received_at": (BASE_TIME + timedelta(minutes=7 * i)).isoformat(),
        "labels": ["INBOX", "UNREAD"],
    }


# --------------------------------------------------------------------------

GENERATORS = {
    "scope_1": ("step", scope1_step),
    "scope_2": ("row", scope2_row),
    "scope_3": ("message", scope3_message),
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
