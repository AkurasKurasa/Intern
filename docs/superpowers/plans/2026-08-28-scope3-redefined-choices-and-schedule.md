# Scope #3 Redefined Choices + Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change Scope #3's decision set from six choices (including Route to Scope #1/#2) to six *different* choices (Reply, Forward, Schedule, Cold email, Ignore, Flag), fully removing Scope #1/#2 routing, and build Schedule end-to-end.

**Architecture:** `DECISIONS_ORDER` changes from `["route_scope1", "route_scope2", "reply", "forward", "flag", "leave_alone"]` to `["reply", "forward", "schedule", "cold_email", "flag", "leave_alone"]`. Every place that produced or consumed a route decision gets that logic removed, not adapted — there is nothing left to route to. Schedule reuses the reply textarea's existing UI and recording machinery, but its Output step is simpler than Reply's: whatever text a human types gets written straight to a file, with no separate matching/reuse model, since a schedule note is new information every time.

**Tech Stack:** Python (pytest), no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-28-scope3-redefined-choices-and-schedule-design.md`

**Correction made while writing this plan, not in the spec:** the spec understated "remove routing" as touching only `inbox_agent.py`'s fast-fill verification. Direct inspection found it is woven into three more places: `routing_rules.py`'s `RuleLayer.classify()` (its *primary* keyword-matching branch directly produces `route_scope1`/`route_scope2` — this is not just a verification helper), `inbox_features.py`'s actual training/inference **input** feature vector (`rule_hit_scope1`/`rule_hit_scope2`, computed via `rule_layer.match_capsule()` — removing these drops `DIMS` by 2, a real shape change beyond just the output list), and `llm_classifier.py`'s `capsule_hints` parameter (tells the LLM what it could route to — meaningless once there's nothing to route to). Task 1 below covers the full, real scope.

## Global Constraints

- `DECISIONS_ORDER` becomes `["reply", "forward", "schedule", "cold_email", "flag", "leave_alone"]` — exactly these six, in this order.
- `cold_email` is a valid decision name in this list starting now, but nothing in this plan ever produces a confident `cold_email` prediction (zero training examples exist) — it behaves like any other decision with no data: cold-start-safe, never fast-filled, falls through to reasoning. Building `cold_email`'s own recording/learning is explicitly out of scope for this plan (next plan, after this ships).
- Schedule needs no `schedule_model.py`/`train_schedule_model.py`/`schedule_agent.py` — its Output step is "write the human's real text to a file," not "predict which past note to reuse." Do not build a matching model for it.
- `reply_recorder.py`, `reply_features.py`, `reply_model.py`, `train_reply_model.py`, `reply_agent.py`, `autonomous_watcher.py`'s reply/forward auto-draft branch, and `DemoRecorder`'s `trace_type="web"` machinery are all untouched by this plan.
- A blank/whitespace-only Schedule note saves nothing — same honesty guarantee `reply_recorder.py` already has. Never invent content.
- Full test suite (`pytest -q` from repo root) must show 0 failed before any task is considered done.

---

### Task 1: Remove Scope #1/#2 routing entirely, redefine `DECISIONS_ORDER`

**Files:**
- Modify: `components/inbox_router/inbox_features.py` (`DECISIONS_ORDER` line 36, `FEATURE_NAMES` lines 39-50, `extract()`'s capsule-matching block ~line 99)
- Modify: `components/inbox_router/routing_rules.py` (`DECISIONS` constant, `RuleLayer.classify()`'s branch 2, `match_capsule()`, `load_capsules()` if now unused)
- Modify: `components/inbox_router/inbox_agent.py` (`_try_fast_fill()` lines ~95-100, `_reason()` lines ~124-134)
- Modify: `components/inbox_router/llm_classifier.py` (`classify()`'s `capsule_hints` param, `_build_prompt()`)
- Modify: `components/inbox_router/autonomous_watcher.py` (remove `dispatch_scope2()` and the `route_scope1`/`route_scope2` branches of `handle_entry()` — leave the reply/forward auto-draft branch untouched)
- Modify: `components/inbox_router/local_ui/index.html` (override dropdown, lines 165-166)
- Test: `tests/test_inbox_features.py`, `tests/test_routing_rules.py` (or wherever `RuleLayer` is tested — check first), `tests/test_inbox_agent.py`, `tests/test_llm_classifier.py` (check first), `tests/test_autonomous_watcher.py`

**Interfaces:**
- Consumes: nothing from a prior task (this is Task 1).
- Produces: `DECISIONS_ORDER = ["reply", "forward", "schedule", "cold_email", "flag", "leave_alone"]` — every later task in this plan assumes this exact list, in this exact order. `inbox_features.DIMS` (derived from `len(FEATURE_NAMES)`) is now 2 less than before (the two `rule_hit_scope1`/`rule_hit_scope2` features are gone) — any later task touching `inbox_features.py` must not reintroduce them.

- [ ] **Step 1: Read the real current code before touching anything**

Run these and read the actual output — do not rely on the plan's summary of line numbers, they may have shifted:
```bash
grep -n "route_scope1\|route_scope2\|match_capsule\|rule_hit_scope\|capsule_hints" components/inbox_router/inbox_features.py components/inbox_router/routing_rules.py components/inbox_router/inbox_agent.py components/inbox_router/llm_classifier.py components/inbox_router/autonomous_watcher.py
```

- [ ] **Step 2: Update `inbox_features.py`**

Change:
```python
DECISIONS_ORDER = ["route_scope1", "route_scope2", "reply", "forward", "flag", "leave_alone"]
```
to:
```python
DECISIONS_ORDER = ["reply", "forward", "schedule", "cold_email", "flag", "leave_alone"]
```

Remove these two lines from `FEATURE_NAMES`:
```python
    "rule_hit_scope1",        # 10
    "rule_hit_scope2",        # 11
```

In `extract()`, find this block (the exact surrounding code, e.g. how `capsule`/`is_scope1`/`is_scope2`/`rule_feats` are used, may differ slightly from this excerpt — read the real function body first):
```python
    capsule = rule_layer.match_capsule(message)
    is_scope2 = bool(capsule) and capsule.get("kind") == "script"
    is_scope1 = bool(capsule) and not is_scope2
    rule_feats = [1.0 if is_scope1 else 0.0, 1.0 if is_scope2 else 0.0]
```
Remove it, and remove `rule_feats` from wherever `extract()`'s final returned list concatenates it in (the return statement combines several feature groups — `rule_feats` should no longer be one of them). The `rule_layer` parameter to `extract()` may become entirely unused after this — if so, remove it from the function signature too, and update every call site (`inbox_agent.py`'s `_try_fast_fill()`, `train_inbox_agent.py`'s `build_dataset()`) to match the new signature.

- [ ] **Step 3: Update `routing_rules.py`**

Change:
```python
DECISIONS = {"route_scope1", "route_scope2", "reply", "forward", "flag", "leave_alone"}
```
to:
```python
DECISIONS = {"reply", "forward", "schedule", "cold_email", "flag", "leave_alone"}
```

In `RuleLayer.classify()`, remove branch 2 entirely (the `capsule = self.match_capsule(message)` block and its `if capsule is not None:` body that returns a `route_scope1`/`route_scope2` `RuleDecision`). Branch 1 (sender-pattern-based) and branch 3 (defer to LLM, `return RuleDecision()`) stay exactly as they are — renumber the comments if needed so they still read "1) ... 2) No confident signal -- defer to the LLM." (i.e. what was comment "3)" becomes "2)").

Remove `match_capsule()` and `load_capsules()` from the class **only if**, after Step 2's edit, nothing else in the codebase still calls them — re-run the Step 1 grep after this step to confirm. If `load_capsules()` is still referenced somewhere you haven't touched yet (e.g. by `inbox_agent.py`'s `_reason()`, addressed in Step 4 below), leave it for now and remove it once that caller is gone too.

`RuleDecision`'s `capsule_name` field: leave the dataclass field itself in place (removing a field that other code might still reference by name is riskier than leaving an always-empty string) — just confirm nothing sets it to a non-empty value anymore after this task.

- [ ] **Step 4: Update `inbox_agent.py`**

In `_try_fast_fill()`, remove:
```python
            if decision in ("route_scope1", "route_scope2"):
                capsule = self._rules.match_capsule(message)
                capsule_name = capsule.get("name", "") if capsule else ""
                if not capsule_name:
                    return None   # can't fast-fill a route with no verified capsule
```
The `capsule_name, forward_to = "", ""` line above it stays (still needed for the `forward_to` logic right below, and for constructing `InboxDecision`).

In `_reason()`, remove:
```python
        # Defensive guard against a hallucinated capsule name -- mirrors the
        # check router.py's old inline logic ran before this pipeline
        # existed. RuleLayer's own capsule_name always comes from a verified
        # match_capsule() call, so this only ever matters for the LLM branch.
        if decision in ("route_scope1", "route_scope2"):
            valid_names = {c.get("name") for c in self._rules.load_capsules()}
            if capsule_name not in valid_names:
                capsule_name = ""
                decision = "flag"
                rationale = (rationale + " (capsule name could not be verified — flagged instead)").strip()
```
Change the `self._llm.classify(message, pattern, rule_result, self._rules.load_capsules())` call to drop the now-removed `capsule_hints` argument once Step 5 changes `LLMClassifier.classify()`'s signature — do this step and Step 5 together, since they're two ends of the same call.

- [ ] **Step 5: Update `llm_classifier.py`**

Read `classify()`'s and `_build_prompt()`'s full current bodies first (`grep -n "def classify\|def _build_prompt" -A 30 components/inbox_router/llm_classifier.py`). Remove the `capsule_hints: List[dict]` parameter from `classify()`'s signature and from `_build_prompt()`'s signature, and remove whatever lines inside `_build_prompt()` format capsule hints into the prompt text (the `f"  - {c.get('name')}: ..."` block and its surrounding "here's what you could route to" framing). Update `inbox_agent.py`'s call site (from Step 4) to match the new 3-argument signature.

- [ ] **Step 6: Update `autonomous_watcher.py`**

Read the current file fully first. Remove `dispatch_scope2()` and the `route_scope1`/`route_scope2` branches inside `handle_entry()` — leave the `reply`/`forward` auto-draft branch (built earlier today, unrelated to routing) completely untouched. `handle_entry()`'s fallback ("left_pending" for anything not specifically handled) should now be reached by what used to be the routing branches too, which is correct — there's nothing left to route.

- [ ] **Step 7: Update `local_ui/index.html`**

Change:
```html
                <option value="route_scope1">Route to Scope #1</option>
                <option value="route_scope2">Route to Scope #2</option>
```
to:
```html
                <option value="schedule">Schedule</option>
                <option value="cold_email">Cold email</option>
```

- [ ] **Step 8: Update every affected test file**

For each test file found in Step 1's grep that references `route_scope1`, `route_scope2`, `match_capsule`, `rule_hit_scope`, or `capsule_hints`: read the test, decide whether it's testing behavior that no longer exists (delete the test) or testing something that still exists but needs updated fixture data (update it — e.g. a test asserting `DECISIONS_ORDER` has 6 specific old names needs the new 6 names). Do not leave any test asserting behavior this task removed. Common files to check: `tests/test_inbox_features.py`, `tests/test_inbox_agent.py`, `tests/test_autonomous_watcher.py`, and whatever test file covers `routing_rules.py`/`llm_classifier.py` (find with `grep -rln "RuleLayer\|LLMClassifier" tests/`).

- [ ] **Step 9: Run the focused tests, then the full suite**

```bash
python -m pytest tests/test_inbox_features.py tests/test_inbox_agent.py tests/test_autonomous_watcher.py -v
python -m pytest -q
```
Expected: 0 failed. Re-run the Step 1 grep one more time over the whole `components/inbox_router/` directory (not just the files listed) to confirm zero remaining references to `route_scope1`, `route_scope2`, `match_capsule`, `rule_hit_scope`, or `capsule_hints` outside of comments/docstrings explaining history.

- [ ] **Step 10: Commit**

```bash
git add components/inbox_router/inbox_features.py components/inbox_router/routing_rules.py components/inbox_router/inbox_agent.py components/inbox_router/llm_classifier.py components/inbox_router/autonomous_watcher.py components/inbox_router/local_ui/index.html tests/
git commit -m "Remove Scope #1/#2 routing, redefine DECISIONS_ORDER to reply/forward/schedule/cold_email/flag/leave_alone"
```

---

### Task 2: `schedule_recorder.py` — mirrors `reply_recorder.py`

**Files:**
- Create: `components/inbox_router/schedule_recorder.py`
- Test: `tests/test_schedule_recorder.py`

**Interfaces:**
- Consumes: `gmail_client.EmailMessage` (existing, unmodified).
- Produces: `DEFAULT_SCHEDULE_LOG_PATH: str`, `record_schedule_entry(message: EmailMessage, note: str, path: str = DEFAULT_SCHEDULE_LOG_PATH) -> None`. Task 3 calls this exact function with these exact parameter names.

First, read `components/inbox_router/reply_recorder.py` in full — this task is a near-exact mirror of its shape, adapted for a plain-text append instead of a JSONL append.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_schedule_recorder.py
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "components", "inbox_router"))

from gmail_client import EmailMessage
import schedule_recorder as sr


def _msg(mid="m1", sender_email="boss@work.com", subject="vendor call", body="Can we set up a call?"):
    return EmailMessage(
        id=mid, thread_id=mid, sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text=body, received_at="2026-08-28T00:00:00Z",
    )


class TestRecordScheduleEntry:
    def test_writes_a_real_line(self, tmp_path):
        path = str(tmp_path / "schedule.txt")
        sr.record_schedule_entry(_msg(), "Aug 30 -- vendor call re: pricing", path=path)

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Aug 30 -- vendor call re: pricing" in content

    def test_blank_note_saves_nothing(self, tmp_path):
        path = str(tmp_path / "schedule.txt")
        sr.record_schedule_entry(_msg(), "   ", path=path)

        assert not os.path.exists(path)

    def test_creates_parent_directory(self, tmp_path):
        path = str(tmp_path / "nested" / "dir" / "schedule.txt")
        sr.record_schedule_entry(_msg(), "real note", path=path)

        assert os.path.exists(path)

    def test_appends_multiple_entries(self, tmp_path):
        path = str(tmp_path / "schedule.txt")
        sr.record_schedule_entry(_msg(mid="m1"), "first note", path=path)
        sr.record_schedule_entry(_msg(mid="m2"), "second note", path=path)

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "first note" in content
        assert "second note" in content

    def test_default_path_is_under_data_dir(self):
        assert "data" in sr.DEFAULT_SCHEDULE_LOG_PATH
        assert sr.DEFAULT_SCHEDULE_LOG_PATH.endswith("schedule.txt")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_schedule_recorder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'schedule_recorder'`.

- [ ] **Step 3: Write `schedule_recorder.py`**

```python
"""
components/inbox_router/schedule_recorder.py
=================================================
Output step for the "schedule" decision. Unlike reply_recorder.py, this
has no matching/reuse concept -- a schedule note is new information
every time (a new date, a new task), nothing to usefully reuse from a
past note. So this is simply: whatever real text a human typed gets
appended to a plain text file, verbatim. Same honesty guarantee
reply_recorder.py already has -- a blank/whitespace-only note saves
nothing, never invents content.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from gmail_client import EmailMessage

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCHEDULE_LOG_PATH = os.path.join(_THIS_DIR, "data", "schedule.txt")


def record_schedule_entry(message: EmailMessage, note: str,
                           path: str = DEFAULT_SCHEDULE_LOG_PATH) -> None:
    note = (note or "").strip()
    if not note:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    recorded_at = datetime.now(timezone.utc).isoformat()
    line = f"[{recorded_at}] {message.subject!r} ({message.sender_email}): {note}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_schedule_recorder.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: 0 failed.

- [ ] **Step 6: Commit**

```bash
git add components/inbox_router/schedule_recorder.py tests/test_schedule_recorder.py
git commit -m "Add schedule_recorder.py: real schedule notes written straight to a text file"
```

---

### Task 3: Wire Schedule into `router.py` and `local_ui`'s text-entry logic

**Files:**
- Modify: `components/inbox_router/router.py` (`confirm_suggestion()`, `override_decision()`)
- Modify: `components/inbox_router/local_ui/app.js` (`isReplyLike()`)
- Test: `tests/test_inbox_router.py`

**Interfaces:**
- Consumes: `schedule_recorder.record_schedule_entry(message, note, path)` from Task 2.
- Produces: nothing new for later tasks — this is where the wiring lands.

- [ ] **Step 1: Read the real current code**

```bash
grep -n "def confirm_suggestion\|def override_decision" -A 45 components/inbox_router/router.py
```
Confirm the exact current shape of both methods' `reply`/`forward` branches before editing — the plan's excerpts below assume the shape as of today's earlier reply-recording work; read the real file first.

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_inbox_router.py` (mirror the existing `_build()` helper and `reply_examples_path`/`schedule_log_path` construction pattern already used for reply tests in that file — read a few nearby existing tests first to match the exact fixture style):

```python
class TestScheduleRecording:
    def test_confirm_schedule_with_real_text_records_a_schedule_entry(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "boss@work.com", "vendor call")])
        router.poll_once()

        router.confirm_suggestion("i1", "schedule", reply_body="Aug 30 -- vendor call re: pricing")

        schedule_path = str(tmp_path / "data" / "schedule.txt")
        with open(schedule_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Aug 30 -- vendor call re: pricing" in content

    def test_confirm_schedule_with_no_text_records_nothing(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "boss@work.com", "vendor call")])
        router.poll_once()

        router.confirm_suggestion("i1", "schedule", reply_body="")

        schedule_path = str(tmp_path / "data" / "schedule.txt")
        assert not os.path.exists(schedule_path)

    def test_confirm_schedule_does_not_touch_reply_examples(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "boss@work.com", "vendor call")])
        router.poll_once()

        router.confirm_suggestion("i1", "schedule", reply_body="a real note")

        reply_examples_path = str(tmp_path / "data" / "reply_examples.jsonl")
        assert not os.path.exists(reply_examples_path)

    def test_override_to_schedule_with_real_text_records_a_schedule_entry(self, tmp_path):
        router = self._build(tmp_path, inbox=[_msg("i1", "boss@work.com", "random subject")])
        router.poll_once()

        router.override_decision("i1", "schedule", "manual override", reply_body="Sept 2 -- follow up")

        schedule_path = str(tmp_path / "data" / "schedule.txt")
        with open(schedule_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Sept 2 -- follow up" in content
```

Check the `_build()`/`InboxRouter(...)` construction helper in this test file for whether it needs a new `schedule_log_path` parameter threaded through, matching how `reply_examples_path` was added earlier today — if `InboxRouter.__init__` needs a new parameter for this (see Step 3), update `_build()` here to pass `schedule_log_path=str(tmp_path / "data" / "schedule.txt")`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_inbox_router.py -k Schedule -v`
Expected: FAIL — `confirm_suggestion()`/`override_decision()` don't yet do anything with `decision == "schedule"`.

- [ ] **Step 4: Add a `schedule_log_path` parameter to `InboxRouter.__init__`**

Mirror exactly how `reply_examples_path: str = DEFAULT_REPLY_EXAMPLES_PATH` was added earlier today: add `schedule_log_path: str = DEFAULT_SCHEDULE_LOG_PATH` as a new `__init__` parameter, store it as `self._schedule_log_path = schedule_log_path`, and add the import `from schedule_recorder import DEFAULT_SCHEDULE_LOG_PATH, record_schedule_entry` at the top of `router.py` alongside the existing `from reply_recorder import ...` line.

- [ ] **Step 5: Wire the `schedule` branch into `confirm_suggestion()` and `override_decision()`**

In both methods, find the existing block that handles `decision in ("reply", "forward")` (creates a draft, records a reply example). Add a parallel `elif decision == "schedule":` branch right after it — read the real surrounding code first so the new branch fits the actual control flow (whether it's an `if/elif` chain or two separate `if` statements matters for how to insert this correctly):

```python
        elif decision == "schedule":
            if reply_body.strip():
                try:
                    record_schedule_entry(message, reply_body, path=self._schedule_log_path)
                except Exception as exc:
                    emit("inbox_log", line=f"Failed to record schedule entry: {exc}", level="err")
```

No draft gets created for `schedule` (there's nothing to send — a schedule entry isn't an email action). The existing `entry["status"] = ...`/`entry["decision"] = ...`/history-update code below the reply/forward/schedule branches applies to `schedule` the same as every other decision — don't special-case that part.

- [ ] **Step 6: Update `local_ui/app.js`'s `isReplyLike()`**

Find:
```javascript
function isReplyLike(decision) {
  return decision === "reply" || decision === "forward";
}
```
Change to:
```javascript
function isReplyLike(decision) {
  return decision === "reply" || decision === "forward" || decision === "schedule";
}
```
(Renaming the function to something like `isTextEntryDecision` is optional polish, not required — if you rename it, update every call site in the same file, don't leave a stale name in some places.)

- [ ] **Step 7: Run the focused tests, then the full suite**

```bash
python -m pytest tests/test_inbox_router.py -v
python -m pytest -q
```
Expected: 0 failed.

- [ ] **Step 8: Commit**

```bash
git add components/inbox_router/router.py components/inbox_router/local_ui/app.js tests/test_inbox_router.py
git commit -m "Wire Schedule into confirm_suggestion/override_decision and the reply-box show/hide logic"
```

---

### Task 4: Extend `reply_trace_translator.py` to route Schedule recordings correctly

**Files:**
- Modify: `components/inbox_router/reply_trace_translator.py`
- Test: `tests/test_reply_trace_translator.py`

**Interfaces:**
- Consumes: `schedule_recorder.record_schedule_entry(message, note, path)` from Task 2. `router.py`'s `routed_history.json` format (existing, unmodified) — one JSON object per line, each with a `message_id` and `decision` field; read the real file shape from `router.py`'s `_append_history()`/`_update_history_entry()` before writing code that parses it.
- Produces: nothing new for later tasks — this is the last task in this plan.

- [ ] **Step 1: Read the real current `translate_session()` and `routed_history.json`'s real shape**

```bash
grep -n "_append_history\|_update_history_entry\|routed_history" components/inbox_router/router.py
```
Read the full function bodies this finds, to know the exact real field names and format before writing a parser for them.

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_reply_trace_translator.py` (reuse the existing `_step()`, `_write_session()`, `_build_gmail_client()`, `_msg()` helpers already in that file):

```python
def _write_history(tmp_path, entries):
    """entries: list of dicts with at least message_id and decision."""
    history_path = tmp_path / "data" / "routed_history.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return str(history_path)


class TestScheduleRouting:
    def test_schedule_decision_writes_to_schedule_file_not_reply_examples(self, tmp_path):
        steps = [
            _step(0, textarea_value="Aug 30 -- vendor call", message_id="m1"),
            _step(1, include_textarea=False),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "vendor call")])
        history_path = _write_history(tmp_path, [{"message_id": "m1", "decision": "schedule"}])
        reply_examples_path = str(tmp_path / "reply_examples.jsonl")
        schedule_log_path = str(tmp_path / "schedule.txt")

        count = translator.translate_session(
            session_dir, gmail_client, reply_examples_path,
            history_path=history_path, schedule_log_path=schedule_log_path)

        assert count == 1
        assert not os.path.exists(reply_examples_path)
        with open(schedule_log_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Aug 30 -- vendor call" in content

    def test_reply_decision_still_writes_to_reply_examples_not_schedule(self, tmp_path):
        steps = [
            _step(0, textarea_value="Thanks, that works.", message_id="m1"),
            _step(1, include_textarea=False),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        history_path = _write_history(tmp_path, [{"message_id": "m1", "decision": "reply"}])
        reply_examples_path = str(tmp_path / "reply_examples.jsonl")
        schedule_log_path = str(tmp_path / "schedule.txt")

        count = translator.translate_session(
            session_dir, gmail_client, reply_examples_path,
            history_path=history_path, schedule_log_path=schedule_log_path)

        assert count == 1
        assert not os.path.exists(schedule_log_path)
        examples = rr.load_reply_examples(reply_examples_path)
        assert len(examples) == 1

    def test_message_id_with_no_history_entry_is_skipped(self, tmp_path):
        steps = [
            _step(0, textarea_value="typed something", message_id="m1"),
            _step(1, include_textarea=False),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "subject")])
        history_path = _write_history(tmp_path, [])  # no entry for m1 at all
        reply_examples_path = str(tmp_path / "reply_examples.jsonl")
        schedule_log_path = str(tmp_path / "schedule.txt")

        count = translator.translate_session(
            session_dir, gmail_client, reply_examples_path,
            history_path=history_path, schedule_log_path=schedule_log_path)

        assert count == 0
```

Add `import json` and `import os` at the top of the test file if not already present, and confirm `rr` (the `reply_recorder` module alias) is already imported in this file — it should be, from earlier today's work.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_reply_trace_translator.py -k Schedule -v`
Expected: FAIL — `translate_session()` doesn't yet accept `history_path`/`schedule_log_path` keyword arguments.

- [ ] **Step 4: Update `reply_trace_translator.py`**

Read the current full `translate_session()` body first (it was written and reviewed earlier today — the exact current shape matters). Add two new parameters with sensible defaults, a helper to read `routed_history.json` into a `{message_id: decision}` lookup, and branch on the resolved decision instead of unconditionally calling `record_reply_example()`:

```python
from schedule_recorder import DEFAULT_SCHEDULE_LOG_PATH, record_schedule_entry

DEFAULT_HISTORY_PATH = os.path.join(_THIS_DIR, "data", "routed_history.json")


def _load_decision_by_message_id(history_path: str) -> dict:
    decisions = {}
    if not os.path.exists(history_path):
        return decisions
    with open(history_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid = entry.get("message_id")
            if mid:
                decisions[mid] = entry.get("decision", "")
    return decisions


def translate_session(session_dir: str, gmail_client, reply_examples_path: str = DEFAULT_REPLY_EXAMPLES_PATH,
                       history_path: str = DEFAULT_HISTORY_PATH,
                       schedule_log_path: str = DEFAULT_SCHEDULE_LOG_PATH) -> int:
    steps = _load_steps(session_dir)
    decisions_by_id = _load_decision_by_message_id(history_path)
    written = 0

    for i, step in enumerate(steps):
        state = step.get("state", {})
        textarea = _find_reply_textarea(state)
        if textarea is None:
            continue
        text = (textarea.get("value") or "").strip()
        if not text:
            continue
        message_id = textarea.get("name") or textarea.get("label") or textarea.get("text") or ""
        if not message_id:
            continue

        next_state = step.get("next_state", {})
        next_textarea = _find_reply_textarea(next_state)
        still_open = (
            next_textarea is not None
            and (next_textarea.get("name") or next_textarea.get("label") or next_textarea.get("text")) == message_id
        )
        if still_open:
            continue

        message = gmail_client.get_message(message_id)
        if message is None:
            print(f"  [skip] step {i}: message_id {message_id!r} did not resolve to a real message")
            continue

        decision = decisions_by_id.get(message_id, "")
        if decision == "schedule":
            record_schedule_entry(message, text, path=schedule_log_path)
        elif decision in ("reply", "forward"):
            record_reply_example(message, text, source="live", path=reply_examples_path)
        else:
            print(f"  [skip] step {i}: message_id {message_id!r} has no reply/forward/schedule decision recorded ({decision!r})")
            continue

        written += 1
        print(f"  [recorded] {message_id} ({decision}): {text[:60]!r}")

    return written
```

Note this replaces the module's earlier `name`-lookup fallback chain (`label`/`text`) consistent with whatever Task 1 of *today's earlier* plan already established — read the real current function to confirm the exact fallback order already in place and keep it as-is, only adding the `decision`-branching logic shown above.

Also update `main()`'s argparse to accept `--history-path` and `--schedule-log-path` CLI flags mirroring `--examples-path`, defaulting to `DEFAULT_HISTORY_PATH`/`DEFAULT_SCHEDULE_LOG_PATH`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_reply_trace_translator.py -v`
Expected: PASS (all tests, including the pre-existing ones from earlier today — confirm none regressed).

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: 0 failed.

- [ ] **Step 7: Commit**

```bash
git add components/inbox_router/reply_trace_translator.py tests/test_reply_trace_translator.py
git commit -m "Route recorded sessions to schedule.txt or reply_examples.jsonl based on the real recorded decision"
```

---

### Task 5: Update DEVELOPERS.md and the Task Tree

**Files:**
- Modify: `DEVELOPERS.md`
- Modify: `treetask/index.html`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Find the existing Scope #3 entry area**

```bash
grep -n "scope3_learned_autonomous_reply\|Scope #3" DEVELOPERS.md | head -5
```

- [ ] **Step 2: Add a new, dated sub-entry** covering: the final redefined 6-choice list (Reply, Forward, Schedule, Cold email, Ignore, Flag), the explicit removal of Route to Scope #1/#2 and why (direct user decision, confirmed via explicit yes/no after a long refinement conversation), the real extra scope found while planning (routing was woven into `RuleLayer.classify()`'s primary path, `inbox_features.py`'s actual input feature dimensions, and `LLMClassifier`'s prompt — not just a verification helper), and that Schedule needed no matching model (a schedule note is new information every time, nothing to reuse) — a genuine, deliberate scope reduction from Reply's own pipeline shape. Note Cold Email is sequenced as a separate, not-yet-started follow-up.

- [ ] **Step 3: Mirror the same content into `treetask/index.html`**'s matching node.

- [ ] **Step 4: Verify the script block still parses**

```bash
node -e "const fs=require('fs');const html=fs.readFileSync('treetask/index.html','utf8');const m=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];m.forEach((x,i)=>{try{new Function(x[1]);console.log(i,'OK');}catch(e){console.log(i,'ERR',e.message);}});"
```
Expected: `0 OK`.

- [ ] **Step 5: Commit and push**

```bash
git add DEVELOPERS.md treetask/index.html
git commit -m "Sync Task Tree and DEVELOPERS.md with the redefined 6-choice decision set and Schedule"
git push
```
