"""
scripts/rpa_scope3.py
=====================
Objective 7, Scope 3 (inbox routing): Intern vs a rule-based mail-rule
baseline, on the original mock inbox and on a surface-changed copy of it.

Data      components/inbox_router/data/mock_inbox.json. mock-101..105 plus the
          five sent replies are the user's already-handled history: both
          systems learn only from these. The other 25 messages are the test set.
Truth     docs/rpa_comparison/scope3_reference_labels.json, committed before
          either system was run (primary label + acceptable alternatives).
Intern    InboxAgent in "habits" mode (no LLM on the test machine): its trained
          decision model (data/inbox_model.pt) when confident, else the sender
          habits learned from the history, else it hands the message to a person.
Baseline  Mail-client style rules written from the same history: exact sender
          address -> the action taken before, plus common subject keywords;
          anything unmatched is handed to a person.
Change    The "interface change" for email: same messages, same meaning, but a
          different mailbox at the same company, an "[EXTERNAL] " subject tag,
          and common synonyms in the subject (call->chat, meeting->sync ...).
          The correct outcome of every message is unchanged.

Metrics per system and condition (n = 25):
  strict accuracy      decision == primary label
  lenient accuracy     decision in acceptable labels
  handoff rate         left for a person (no decision)
  wrong-action rate    decided, but not an acceptable outcome (the risky case)
  stability            same decision on original and changed copy (truth-free)
Paired comparison: exact McNemar test on lenient correctness.

Usage:  python scripts/rpa_scope3.py
"""
from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IR = ROOT / "components" / "inbox_router"
sys.path.insert(0, str(IR)); sys.path.insert(0, str(ROOT / "components"))

from gmail_client import EmailMessage  # noqa: E402

HISTORY = {"mock-101", "mock-102", "mock-103", "mock-104", "mock-105"}
HANDOFF = "handoff"
LABELS = ROOT / "docs" / "rpa_comparison" / "scope3_reference_labels.json"
OUT = ROOT / "docs" / "rpa_comparison" / "scope3_results.json"
SYNONYMS = [(r"\bcall\b", "chat"), (r"\bmeeting\b", "sync"), (r"\bwalkthrough\b", "session"),
            (r"\bretro\b", "retrospective"), (r"\bkickoff\b", "kick-off"), (r"\bdeadline\b", "due date"),
            (r"\binvoice\b", "bill"), (r"\bweekly\b", "week's"), (r"\breminder\b", "heads-up")]


def load():
    raw = json.loads((IR / "data" / "mock_inbox.json").read_text(encoding="utf-8"))
    inbox = [EmailMessage(**m) for m in raw["inbox"]]
    sent = [EmailMessage(**m) for m in raw["sent"]]
    return inbox, sent


CHANGES = ("sender", "tag", "synonyms")          # each tested alone, then together as "changed"


def perturb(m: EmailMessage, changes=CHANGES) -> EmailMessage:
    """Same message, same meaning, different surface.

    sender    a different mailbox at the same organisation (and display name)
    tag       an "[EXTERNAL] " subject tag
    synonyms  common synonyms in the subject (call->chat, meeting->sync ...)"""
    p = copy.deepcopy(m)
    if "sender" in changes:
        local, domain = p.sender_email.split("@")
        parts = re.split(r"[.\-_]", local)
        new_local = (parts[0][0] + "." + parts[-1]) if len(parts) > 1 else (local + ".team")
        p.sender_email = f"{new_local}@{domain}"
        name = p.sender.split("<")[0].strip()
        p.sender = f"{name.upper()} <{p.sender_email}>"
    if "synonyms" in changes:
        for pat, rep in SYNONYMS:
            p.subject = re.sub(pat, rep, p.subject, flags=re.IGNORECASE)
    if "tag" in changes:
        p.subject = "[EXTERNAL] " + p.subject
    return p


# ── baseline: mail rules built from the same history ──────────────────────────
def build_baseline_rules(history_inbox, sent):
    by_thread = {m.thread_id: m for m in history_inbox}
    sender_rules = {}
    for s in sent:
        o = by_thread.get(s.thread_id)
        if o is None:
            continue
        fwd = "forwarded message" in (s.body_text or "").lower() or (s.to and s.to.split("@")[-1] != o.sender_email.split("@")[-1])
        sender_rules[o.sender_email] = "forward" if fwd else "reply"
    keyword_rules = [
        (r"\b(meeting|call|walkthrough|retro|kickoff|appointment)\b", "schedule"),
        (r"\b(newsletter|weekly|summary|unsubscribe)\b", "leave_alone"),
    ]
    return sender_rules, keyword_rules


def baseline_decide(m, rules):
    sender_rules, keyword_rules = rules
    if m.sender_email in sender_rules:
        return sender_rules[m.sender_email]
    for pat, action in keyword_rules:
        if re.search(pat, m.subject, flags=re.IGNORECASE):
            return action
    if m.sender_email.split("@")[0] in ("no-reply", "noreply", "newsletter"):
        return "leave_alone"
    return HANDOFF


# ── Intern ────────────────────────────────────────────────────────────────────
def build_intern(history_inbox, sent):
    from pattern_profile import PatternProfile
    from routing_rules import RuleLayer
    from llm_classifier import LLMClassifier
    from inbox_agent import InboxAgent
    tmp = Path(tempfile.mkdtemp()) / "pattern_profile.json"
    profile = PatternProfile(path=str(tmp))
    profile.observe_sent_history(sent, history_inbox)      # history only: no test leakage
    return InboxAgent(profile, RuleLayer(profile), LLMClassifier(provider="none"), mode="habits")


def intern_decide(agent, m):
    d = agent.decide(m)
    dec = d.decision if d.decision in {"reply", "forward", "schedule", "cold_email", "leave_alone"} else HANDOFF
    return dec, d.layer


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def metrics(decisions, labels):
    n = len(decisions)
    strict = sum(decisions[i] == labels[i]["primary"] for i in decisions)
    lenient = sum(decisions[i] in labels[i]["acceptable"] for i in decisions)
    hand = sum(decisions[i] == HANDOFF for i in decisions)
    wrong = sum(decisions[i] != HANDOFF and decisions[i] not in labels[i]["acceptable"] for i in decisions)
    return {"n": n, "strict": strict, "lenient": lenient, "handoff": hand, "wrong_action": wrong}


def main():
    labels = json.loads(LABELS.read_text(encoding="utf-8"))["labels"]
    inbox, sent = load()
    history = [m for m in inbox if m.id in HISTORY]
    test = [m for m in inbox if m.id not in HISTORY]
    assert {m.id for m in test} == set(labels), "labels and test set differ"
    rules = build_baseline_rules(history, sent)
    agent = build_intern(history, sent)
    res = {"conditions": {}, "per_message": {}}
    conds = ("original", "changed") + CHANGES               # "changed" = all three together
    dec = {s: {c: {} for c in conds} for s in ("baseline", "intern")}
    for m in test:
        mp = perturb(m)
        variants = {"original": m, "changed": mp, **{c: perturb(m, (c,)) for c in CHANGES}}
        for cond, msg in variants.items():
            dec["baseline"][cond][m.id] = baseline_decide(msg, rules)
            dec["intern"][cond][m.id], layer = intern_decide(agent, msg)
            res["per_message"].setdefault(m.id, {"label": labels[m.id]["primary"], "acceptable": labels[m.id]["acceptable"],
                                                 "changed_subject": mp.subject, "changed_sender": mp.sender_email})
            res["per_message"][m.id][f"baseline_{cond}"] = dec["baseline"][cond][m.id]
            res["per_message"][m.id][f"intern_{cond}"] = dec["intern"][cond][m.id]
            res["per_message"][m.id][f"intern_{cond}_layer"] = layer
    for s in dec:
        for cond in dec[s]:
            res["conditions"][f"{s}_{cond}"] = metrics(dec[s][cond], labels)
        same = sum(dec[s]["original"][i] == dec[s]["changed"][i] for i in labels)
        res["conditions"][f"{s}_stability"] = {"same": same, "n": len(labels)}
        for c in CHANGES:
            res["conditions"][f"{s}_stability_{c}"] = {"same": sum(dec[s]["original"][i] == dec[s][c][i] for i in labels), "n": len(labels)}
    for cond in conds:
        b = sum((dec["intern"][cond][i] in labels[i]["acceptable"]) and not (dec["baseline"][cond][i] in labels[i]["acceptable"]) for i in labels)
        c = sum(not (dec["intern"][cond][i] in labels[i]["acceptable"]) and (dec["baseline"][cond][i] in labels[i]["acceptable"]) for i in labels)
        res["conditions"][f"mcnemar_{cond}"] = {"intern_only_correct": b, "baseline_only_correct": c, "p": mcnemar_exact(b, c)}
    res["baseline_rules"] = {"sender_rules": rules[0], "keyword_rules": [list(r) for r in rules[1]]}
    OUT.write_text(json.dumps(res, indent=2), encoding="utf-8")
    for k, v in res["conditions"].items():
        print(f"{k:<22} {v}")
    print("written", OUT)


if __name__ == "__main__":
    main()
