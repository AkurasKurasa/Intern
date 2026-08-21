# Scope #1 / Scope #2 Architecture Unification

Branch: `experiment/scope-unification`. Status: design approved in chat,
not yet built.

## Problem

Scope #1 (`components/agent/` — desktop car-insurance form filling,
Transformer=WHERE + LLM=WHAT) and Scope #2 (`components/scope2/` —
spreadsheet-to-web-portal filling, trained matcher + rule induction) are
two fully separate codebases that independently solve several of the
same supporting problems: recording whether a run succeeded, looking up
"record N's value for field X," and (about to be true) deciding when a
fast/cheap answer is confident enough versus when to escalate to
something slower and smarter. Duplicated solutions to the same problem
drift apart — a bug fixed in one copy silently stays broken in the
other. Confirmed concretely this session: Scope #1's metrics pipeline
(`scripts/eval_metrics.py` → `data/output/run_metrics.jsonl`) is
currently broken (real completed runs produce zero rows); Scope #2 has
its own, completely separate metrics pipeline that would never benefit
from that fix.

Direct request: unify the architecture so this class of bug stops being
possible, without changing what makes each scope hard in the first
place, and without regressing speed on either side.

## Hard constraints

These were established and re-confirmed multiple times in the design
conversation — they are not negotiable trade-offs, they are the point:

1. **Scope #1's decision-making and speed are byte-identical after this
   work.** Transformer=WHERE, LLM=WHAT, the OPT2 fast-fill shortcuts,
   and the confidence-gated reasoning ladder (`_MED_CONF`, `deep=True`,
   the struggle-streak triggers) keep their exact current behavior and
   timing. Nothing in this design touches `agent.py`'s click/type
   mechanics.
2. **Scope #2 must end up at least as fast as it is today**, even after
   gaining a new LLM-escalation capability. The escalation may only
   fire on cases that are *already* unresolved today (abstained
   matches) — never adding cost to a case that already succeeds.
3. **No task-specific hardcoding.** Neither scope may bake in a
   fixed manifest of "this form's fields" or "this portal's fields" —
   both must keep discovering their target live/fresh each run. This
   directly shaped the decision *not* to give Scope #1 Scope #2-style
   upfront planning (see "Rejected approaches" below).
4. **The two scopes solve genuinely different hard problems and must
   keep doing so.** Scope #1's hard problem is acting correctly in a
   live, physically uncertain environment (real clicks, hidden tabs,
   timing). Scope #2's hard problem is inferring meaning across
   naming/schema drift it has never seen before. Unifying shared
   infrastructure must not blur this distinction — it is the reason
   these are two separate thesis scopes rather than one.

## Rejected approaches (and why)

- **Give Scope #1 Scope #2-style upfront planning** (discover the
  whole form's fields before filling, the way Scope #2 knows its whole
  spreadsheet and portal schema before it starts). Rejected: a native
  multi-tab desktop app does not expose an inactive tab's fields
  through the accessibility tree until that tab is actually switched
  to (proven directly this session by the Driver-2-fields-invisible
  bug). Front-loading discovery would mean navigating every tab twice
  — once to plan, once to execute — costing real time. The alternative,
  a hardcoded field manifest, violates constraint 3.
- **Merge the two scopes' decision-making into one function** (one
  shared confidence check that IS both scopes' gate, not two gates
  behind one interface). Rejected: click-confidence (Scope #1) and
  match-score/margin (Scope #2) are different signals from different
  models solving different problems; forcing them through identical
  logic would blur constraint 4 for no real benefit over a shared
  *comparison* utility (Piece 5 below), which gets the same
  deduplication without conflating the two problems.

## Architecture

One Agent with two swappable halves and five shared services in the
middle.

```
                    ┌─────────────────────────────┐
                    │           Agent               │
                    │                               │
   ┌────────────┐   │  ┌─────────┐     ┌─────────┐  │   ┌────────────┐
   │ "hands"    │◄──┼──┤ shared  │     │ shared  │  ├──►│ "brain"    │
   │ (pluggable)│   │  │services │     │services │  │   │ (pluggable)│
   └────────────┘   │  └─────────┘     └─────────┘  │   └────────────┘
   UIA/pyautogui         5 pieces, below              Transformer+LLM
   -- or --                                           -- or --
   Playwright                                         matcher+rules
```

- **Hands**: Scope #1's real OS-level clicks/keystrokes (unchanged), or
  Scope #2's Playwright DOM control (unchanged). Neither hands
  implementation changes; they simply plug into the same loop shape
  instead of living inside two separate top-level programs.
- **Brain**: Scope #1's Transformer+LLM pairing (unchanged), or Scope
  #2's trained matcher + rule induction (unchanged). Same code, same
  weights, same behavior — just addressable through one interface.

### The five shared services

**1. Shared matching brain.** `components/scope2/features/extractor.py`
→ `model/matcher.py` → `resolver/assign.py` already operate on generic
`SourceColumn`/`FieldDescriptor` shapes (`descriptors.py`) with zero
form-specific logic anywhere in the core (confirmed by direct grep —
no field or portal names appear in the matching code). `descriptors.py`
already documents both dataclasses as intended for "our own Scope #1
use" — this seam was designed and never connected. The unification
writes one adapter: Scope #1's live-discovered UI elements become
`FieldDescriptor`s, intake-record fields become `SourceColumn`s, fed
into the existing pipeline unchanged. When the matcher is confident
(`STATUS_AUTO`), its answer is used directly — this is *faster* than
today's path for relabeled-field cases (e.g. "Policy Reference #" vs.
"Policy Number" from `data_entry_intake_FOREIGN_TEST.txt`), which
currently require a full LLM round-trip. When it abstains, Scope #1
falls through to the exact existing `_ask_llm(deep=True)` call,
unchanged.

**2. Shared success recording.** One recording path both scopes call
at the end of a run, replacing `scripts/eval_metrics.py` (Scope #1,
currently broken) and `components/scope2/eval/ground_truth.py` +
`run_variants.py` (Scope #2, separate). A fix to "why didn't this run
get recorded" fixes both scopes at once, forever.

**3. Shared value lookup.** One interface for "given record N, what's
the value for field X," with format-specific readers underneath:
`components/data_sources/notepad_source.py` (Scope #1, text) and
`components/scope2/executor/sheet_reader.py` (Scope #2, Excel) keep
their own parsing logic, but both implement the same lookup contract.
The record-boundary-scoping bug fixed in `notepad_source.py` this
session is exactly the class of bug `sheet_reader.py` could have,
undetected, on the Excel side — a shared contract makes that
detectable and testable once instead of twice.

**4. Shared launch mechanism.** Already done: `components/agent/
capsule.py`'s `WorkflowCapsule.kind` (`"agent"` vs `"script"`) already
lets both scopes launch through the same `recorder_bridge.py` Popen
path and the same Electron log stream (`tasks/registry.json` already
has both capsules registered this way). No further work needed here;
listed for completeness.

**5. Shared confidence gate.** Both scopes already ask "is this
confident enough, or should I escalate" — Scope #1 via `t_conf <
_MED_CONF or streak > 0`, Scope #2 (new, per constraint 2) via
`status == STATUS_ABSTAIN` (`score < tau or margin < delta` from
`resolver/assign.py`). The signal computation stays scope-specific;
only the "compare against threshold, return escalate y/n" step becomes
one shared, once-tested utility both scopes call.

## Data flow (Scope #1, after unification)

1. Live UIA discovery finds a visible empty field (unchanged).
2. Field label + intake record are adapted into `FieldDescriptor`/
   `SourceColumn` (new, Piece 1's adapter).
3. Shared matcher scores the match (new call into Piece 1).
4. Shared confidence gate (Piece 5) decides: confident → use the
   match's value directly (new, faster path); abstain → escalate.
5. Escalation is the *existing* `_ask_llm(deep=True)` call, completely
   unchanged.
6. Run completion is recorded through the shared service (Piece 2)
   instead of the current broken `eval_metrics.py` path.

Scope #2's flow gains the mirror-image step: after `resolver.assign`
produces abstained matches, escalate those (only those) to an LLM
before falling back to "unmapped," using the shared confidence gate to
decide, and the shared success-recording path at the end.

## Error handling

- Adapter (Piece 1) failures (malformed record field, no elements
  discovered) fail closed: fall through to today's existing behavior
  exactly as if the adapter didn't exist. Never block or crash a run
  Scope #1 would otherwise have completed.
- Shared value lookup (Piece 3) preserves each format reader's
  existing error/blank-handling semantics; the shared contract adds a
  common test surface, not new failure modes.
- Shared success recording (Piece 2) failing to write must never fail
  the run itself — matches the existing `finally:`-block intent in
  `run_task.py`, just fixed to actually reach that block on every real
  exit path (root cause not yet found — this is in-scope work, see
  Phasing).

## Testing strategy

TDD throughout, matching this project's established discipline. Each
piece gets its own test file before being wired in:
- Piece 1: reproduce the `FOREIGN_TEST` relabeling case through the
  adapter → matcher → resolver path; confirm `STATUS_AUTO` cases skip
  the LLM call entirely (speed claim, made testable) and `STATUS_
  ABSTAIN` cases still reach `_ask_llm(deep=True)` unchanged.
- Piece 2: a fake run's completion reaches the shared recorder under
  every exit path Scope #1 can take (normal return, hotkey stop, an
  exception) — the actual gap this session found and never root-caused.
- Piece 3: existing `notepad_source.py` and new `sheet_reader.py` tests
  both run against the same shared-contract test suite.
- Piece 5: pure unit tests on the threshold-comparison utility itself,
  independent of either scope.
- No existing Scope #1 test may change behavior — the full existing
  suite (1293 passed, 9 skipped as of this session) must stay green
  throughout, run before every commit.

## Phasing

Per the brainstorming decomposition guidance, this is too large for one
implementation pass. Suggested order, each its own plan/implementation
cycle:
1. **Piece 2 (shared success recording)** — smallest, already proven
   broken on Scope #1's side, no dependency on anything else here.
2. **Piece 5 (shared confidence gate)** — small, pure utility, unblocks
   Piece 1.
3. **Piece 1 (shared matching brain + adapter)** — the centerpiece;
   depends on Piece 5.
4. **Piece 3 (shared value lookup)** — independent of 1/2/5, can move
   in parallel if desired.

## Out of scope

- Deep-merging Scope #1's and Scope #2's actual decision-making into
  one function (rejected above).
- Giving Scope #1 upfront/pre-computed planning (rejected above).
- Any change to Scope #1's live click/type mechanics, timing, or the
  reasoning ladder's existing trigger conditions.
- Scope #3 (email/ticket triage) — not discussed, not touched.
