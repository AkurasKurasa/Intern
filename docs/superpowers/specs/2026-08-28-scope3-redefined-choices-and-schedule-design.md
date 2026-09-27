# Scope #3: Redefined Decision Set + Schedule — Design

## Context

Direct, extensively-refined user decision (2026-08-28, after a long
back-and-forth that rejected several earlier framings — see
`project_scope3_refined_definition` memory): Scope #3's final output
choices are **Reply, Forward, Schedule, Cold email, Ignore, Flag** — six
choices. **Route to Scope #1 and Route to Scope #2 are deliberately
removed**, confirmed via explicit yes/no. Scope #3 no longer connects to
Scope #1/#2 at all.

Locked-in defaults (stated to the user, not objected to, explicit
"stop asking, build it" instruction received after):
- The existing inbox-reactive system (an email arrives, Intern decides)
  stays exactly as-is in shape — this design only changes WHICH six
  decisions it picks from, not the mechanism.
- Schedule writes to a plain text file.
- Schedule is learned the same Record→Train→Output way replies already are.

**Scope of THIS spec: the decision-set change, plus Schedule, fully
end-to-end.** Cold Email (which is task-list-driven, not
inbox-reactive — composing to a brand-new contact, no existing thread
to react to) is real, wanted, and explicitly NOT abandoned — it is
sequenced as the next spec after this one ships and is proven, not
attempted in the same pass. Cramming both into one pass risked neither
landing solidly; this project's own final-review process today already
demonstrated what "ship everything at once, review finds it's broken"
costs.

## What already exists, checked directly

- `components/inbox_router/inbox_features.py:36` —
  `DECISIONS_ORDER = ["route_scope1", "route_scope2", "reply", "forward", "flag", "leave_alone"]`.
  Every downstream piece (`compute_centroids()`, `extract()`,
  `InboxDecisionNet`'s `num_decisions`) derives its shape from this one
  list generically — changing its contents is the single source of truth
  for what the trained model can predict. No other code needs to know
  the list's literal values, only its length and order.
- `components/inbox_router/inbox_agent.py:95-99,124,129-132` — the ONLY
  code that specifically special-cases `route_scope1`/`route_scope2`
  by name: `_try_fast_fill()`'s capsule-verification block (confirms a
  real capsule name via `RuleLayer.match_capsule()` before trusting a
  route decision) and `_reason()`'s LLM-branch capsule-name validation.
  This is the block that must be removed, not adapted — there is no
  capsule to verify once routing doesn't exist.
- `components/inbox_router/router.py:223` — a comment noting
  `route_scope1`/`route_scope2` do nothing Gmail-side (the real
  dispatch happens client-side via `renderer.js`). Once these decisions
  don't exist, this comment and its surrounding dead branch go too.
- `components/inbox_router/local_ui/index.html:165-166` — the override
  dropdown's two `route_scope1`/`route_scope2` `<option>`s.
- `components/inbox_router/autonomous_watcher.py` — `dispatch_scope2()`
  and `handle_entry()`'s `route_scope1`/`route_scope2` branches exist
  entirely to auto-dispatch Scope #2 or surface Scope #1 for a human.
  Once these decisions can never be produced, this is dead code with a
  real safety-relevant history (the hard "never auto-launch Scope #1"
  boundary lives here) — removed cleanly, not left as an unreachable
  landmine future code could accidentally resurrect.
- `components/inbox_router/routing_rules.py`'s `match_capsule()` —
  used only by the code being removed above (`inbox_agent.py`'s
  fast-fill verification, `autonomous_watcher.py`'s dispatch). Confirmed
  via grep it has no other caller — becomes dead code, removed with the
  rest rather than left orphaned.
- `components/inbox_router/reply_recorder.py`,
  `reply_features.py`, `reply_model.py`, `train_reply_model.py`,
  `reply_agent.py` (shipped earlier today) — the reply-learning
  pipeline. Schedule's design deliberately mirrors this shape wherever
  it fits, and deliberately diverges where reply's own logic doesn't
  apply (see "Why Schedule doesn't need a matching model" below).

## Design

### 1. Redefine `DECISIONS_ORDER`

```python
DECISIONS_ORDER = ["reply", "forward", "schedule", "cold_email", "flag", "leave_alone"]
```

`cold_email` is included in the list now (so the trained classifier's
shape is stable across this spec and the next one), but nothing in
this spec makes the classifier ever confidently predict it — no
`cold_email` examples exist yet, so it behaves exactly like any other
decision with zero training data: never fast-filled, always falls
through to reasoning, same cold-start-safe contract every other
decision already has. This avoids a second breaking change to the
model's shape (and a second checkpoint-format bump) when Cold Email's
own spec ships.

### 2. Remove the route_scope1/route_scope2 code, don't adapt it

- `inbox_agent.py`: delete the `if decision in ("route_scope1", "route_scope2"):` block in `_try_fast_fill()` (lines ~95-99) and its counterpart in `_reason()` (lines ~129-132). Fast-fill and reasoning both become simpler — a fast-filled or reasoned decision is trusted directly, no capsule-name verification step, since there's no capsule to verify.
- `router.py`: remove the now-inapplicable comment at line 223 and confirm no other code path assumes `route_scope1`/`route_scope2` can occur.
- `local_ui/index.html`: replace the two `route_scope1`/`route_scope2` `<option>` elements with `schedule` and `cold_email`.
- `autonomous_watcher.py`: remove `dispatch_scope2()` and the `route_scope1`/`route_scope2` branches of `handle_entry()`. The reply/forward auto-draft branch (shipped earlier today) is untouched.
- `routing_rules.py`: remove `match_capsule()` (confirmed no other caller) and any now-unused imports/fields it required.
- Every test file covering the removed code (`test_inbox_agent.py`'s route-decision tests, `test_autonomous_watcher.py`'s route_scope1/route_scope2 tests, `routing_rules.py`'s own tests) gets its now-inapplicable tests removed, not left asserting behavior that can no longer occur.

### 3. Schedule: recording, mirroring reply's shape

`local_ui/index.html`'s reply textarea (`#replyBody`) and its
show/hide logic (`refreshReplyBoxVisibility()` in `app.js`) already do
exactly what Schedule needs: a text box that appears when the relevant
decision is selected, tagged with the open message's id, submitted
through Confirm/Override. Schedule reuses this same box and machinery
— `isReplyLike()` in `app.js` becomes `isTextEntryDecision()` (or
similarly renamed) and includes `"schedule"` alongside
`"reply"`/`"forward"`.

**Why Schedule does NOT need a matching/reuse model the way replies
do.** A reply can legitimately reuse near-identical past wording — "confirmed,
thanks" fits many different confirmations. A schedule note is, by its
nature, new information every time (a new date, a new task, a new
person) — there is nothing to usefully "reuse" from a past schedule
note the way there is with a reply. So Schedule's Output step is not
"predict which past note best matches" — it is simply: **whatever the
human actually typed gets written to the schedule file, every time**,
same as the reply textarea already guarantees no AI-invented text ever
gets sent. The "learned" part of Schedule is entirely in the
*decision* — recognizing which emails are schedule-worthy — not in
generating the note's content. This is a real, deliberate scope
reduction from the reply pipeline's shape, not an oversight: it means
Schedule needs no `schedule_model.py`/`train_schedule_model.py`/
`schedule_agent.py` at all. `InboxDecisionNet` already learns "is this
schedule-worthy" as one of its six output categories, using the exact
same mechanism it already uses for reply/forward/flag/leave_alone.

### 4. Schedule: recording → real file output

New module `components/inbox_router/schedule_recorder.py`, a near-exact
mirror of `reply_recorder.py`'s shape (same honesty guarantee: blank
text saves nothing):

```python
DEFAULT_SCHEDULE_LOG_PATH = os.path.join(_THIS_DIR, "data", "schedule.txt")

def record_schedule_entry(message: EmailMessage, note: str,
                           path: str = DEFAULT_SCHEDULE_LOG_PATH) -> None:
    """Appends one real, human-written schedule note to a plain text
    file. Never invents content -- a blank/whitespace-only note saves
    nothing, exactly like reply_recorder.py's own guarantee."""
```

Wired into `router.py`'s `confirm_suggestion()`/`override_decision()`
exactly where `reply_recorder.record_reply_example()` is already
called for `"reply"`/`"forward"` — add a parallel branch for
`decision == "schedule"` that calls `record_schedule_entry()` instead,
using the same `reply_body`/text-box value already threaded through
(the local_ui text box is now shared by reply/forward/schedule, so
the existing plumbing carries schedule's text with zero new wiring
between the frontend and `router.py`).

This is the ENTIRE Schedule Output mechanism — no separate matching
model, no separate agent, no separate translator. The DemoRecorder →
`reply_trace_translator.py` path built earlier today for reply-recording
also needs one small extension: when the recorded textarea's decision
context is `"schedule"` rather than `"reply"`/`"forward"`, call
`record_schedule_entry()` instead of `reply_recorder.record_reply_example()`.
Since `reply_trace_translator.py` currently has no way to know which
*kind* of text-entry decision a given step represents (it assumes
"reply"), this needs the decision type resolved. **Decided: resolve it
from `routed_history.json`, not a new DOM attribute.** After a session
is recorded, `translate_session()` looks up each candidate
`message_id` in `routed_history.json` (already written by
`confirm_suggestion()`/`override_decision()` on every real confirm) and
reads that entry's real `decision` field — `"schedule"` routes to
`record_schedule_entry()`, `"reply"`/`"forward"` route to
`reply_recorder.record_reply_example()` as today, anything else is
skipped. This reuses data already being recorded with zero new
frontend wiring, and is more reliable than a DOM attribute besides:
the attribute would capture what was *selected* at record time, but
`routed_history.json`'s `decision` field is what was *actually
confirmed* — the true source of truth for what happened.

## What does NOT change

- The Record → Train → Output shape for the classifier itself
  (`inbox_features.py`, `inbox_model.py`, `train_inbox_agent.py`) is
  untouched beyond the one-line `DECISIONS_ORDER` edit — everything
  downstream already derives its shape from that list generically.
- `reply_recorder.py`, `reply_features.py`, `reply_model.py`,
  `train_reply_model.py`, `reply_agent.py` — untouched. Reply's own
  matching-model pipeline stays exactly as shipped earlier today.
- `DemoRecorder`'s `trace_type="web"` wiring (`recorder.py`,
  `web_observer.py`) — untouched. Schedule recording reuses the exact
  same recording mechanism, no new observer code.

## Error handling

- A `"schedule"` decision with an empty/whitespace-only text box:
  `record_schedule_entry()` saves nothing, same as `reply_recorder.py`'s
  existing guarantee. No error, no crash — matches the established
  "never invent, silently do nothing on blank input" pattern.
- Removing `route_scope1`/`route_scope2` from `DECISIONS_ORDER` while a
  stale checkpoint trained against the old 6-item list still exists on
  disk: `inbox_model.py`'s existing `FeaturesMismatch`/dims-mismatch
  guard already covers this (a checkpoint's `dims`/`num_decisions` won't
  match the new list's length) — no new guard needed, this is exactly
  what that guard exists for. The model falls back to cold-start
  reasoning until retrained, same as any other stale-checkpoint case.

## Testing

- `test_inbox_features.py`: `DECISIONS_ORDER` now has 6 entries with
  the new names; existing tests referencing `route_scope1`/`route_scope2`
  by name get updated to `schedule`/`cold_email` or removed if the
  behavior they tested no longer exists (capsule-name verification).
- `test_inbox_agent.py`: the `TestRouteDecisionWithoutVerifiedCapsule`-
  style tests (capsule verification) are removed, not adapted — that
  code path no longer exists. New tests confirm a confident `schedule`
  prediction fast-fills directly, with no capsule-name check involved.
- `test_autonomous_watcher.py`: `dispatch_scope2()`/route-decision
  tests removed. The reply/forward auto-draft tests (shipped earlier
  today) stay untouched — confirm they still pass unmodified, proving
  the removal didn't touch that code path.
- `test_schedule_recorder.py` (new): mirrors `test_reply_recorder.py`
  exactly — correct shape, blank note saves nothing, creates parent
  dir, missing file returns `[]`/empty read.
- `router.py` tests: new tests for `confirm_suggestion()`/
  `override_decision()` with `decision="schedule"` — confirm
  `record_schedule_entry()` gets called with the real typed text, and
  that a `"schedule"` decision does NOT call `reply_recorder`'s
  functions (the two paths must stay genuinely separate, not silently
  cross-write into each other's files).
- `reply_trace_translator.py` tests: a new test proving a recorded
  session whose final decision was `"schedule"` (not `"reply"`) writes
  to `schedule.txt` via `record_schedule_entry()`, not to
  `reply_examples.jsonl`.
- Full suite must show 0 failed before this is considered done — same
  bar as every other piece of work today.
