# Scope #3: Record → Train → Output pipeline

Branch: `feature/scope3-record-train-output`
Date: 2026-08-25

## Context

Direct request: "Remember we have architecture and pipeline to follow, develop Scope #3 further in a new branch. Record -> Train -> Output. That output must have that reasoning, fast fill, and navigation."

Scope #3 (`components/inbox_router/`) currently has no trained model at all. Every decision goes through two hand-written layers: `RuleLayer` (deterministic keyword/pattern rules) and `LLMClassifier` (a real LLM call, used only when `RuleLayer` has no answer). `PatternProfile` learns sender-level statistics passively from the Gmail Sent folder, but nothing is ever *trained* — there is no Scope #3 equivalent of `tasks/form_filling/model.pt` or `components/scope2/data/models/matcher.pt`.

Scope #1's own shape is: a person deliberately records themselves doing the task (`scripts/demo_recorder.py` → `data/demos/...`), `scripts/train.py` fits `TransformerAgentNetwork` on those recordings, and the output is `LLMAgent` — one system whose own behavior includes a confidence-gated fast path (acts alone when sure) and a reasoning path (consults the LLM when unsure). This spec gives Scope #3 the same three-stage shape, reusing the existing `RuleLayer`/`LLMClassifier` code as the *reasoning* tier of one new unified output, not as separate legacy plumbing.

**Navigation is explicitly out of scope for this pass**, per direct instruction ("let's not mind it right now"). It stays a named property of the eventual output; its concrete shape is deferred to a follow-up.

## Goals

- A dedicated **Record** step: someone can deliberately work through realistic inbox scenarios and have every decision (confirm / override / reply / forward / flag / leave-alone) saved as a labeled training example — the Scope #3 analog of a demo-recording session.
- A **Train** step: a standalone script, same shape as `scripts/train.py`, that reads recorded sessions and fits a small trained model, saving a checkpoint.
- An **Output**: one unified agent (`InboxAgent`) that replaces `InboxRouter`'s current classify-only logic. It loads the checkpoint and is the decision-maker — fast-fill when confident, reasoning (existing `RuleLayer` + `LLMClassifier`, now internal to `InboxAgent`) when not.
- Reuse existing, proven code wherever the shape already matches (Scope #2's `Matcher`/`features/extractor.py` pattern for the trained model; `sentence_transformers`, already a project dependency, for the semantic signal).

## Non-goals

- Navigation (deferred, see above).
- Any change to Scope #1/#2's own pipelines.
- Wiring a real Gmail account (`RealGmailClient`) — this pipeline is validated against `MockGmailClient` and the existing `mock_inbox.json` fixture, same as all of Scope #3's Phase A work so far.
- Any UI surface for choosing model type/thresholds — those are fixed engineering decisions, not exposed settings (see project memory `feedback_no_ml_choices_for_end_users`).

## Architecture

```
Record                         Train                          Output
------                         -----                          ------
Inbox Dispatch mockup    -->   components/inbox_router/  -->   InboxAgent
  Confirm/Override click       train_inbox_agent.py            (replaces InboxRouter's
  (+ one-time bootstrap          reads recorded examples         classify-only logic)
   from Sent-folder history)     builds features via              loads checkpoint
                                 inbox_features.py                 confident -> fast-fill
                                 fits InboxDecisionNet              unsure    -> reasoning
                                 saves checkpoint                    (RuleLayer + LLMClassifier,
                                 (inbox_model.pt)                    now internal calls)
```

## Components

### 1. `components/inbox_router/decision_recorder.py` (new) — Record

A small, focused module — not a new subprocess, just a function `record_example(message, decision, source)` called from two places:

- `InboxRouter.confirm_suggestion()` / `override_decision()` (existing methods in `router.py`) — every real Confirm/Override becomes one recorded example. `source="live"`.
- A new one-time bootstrap script, `components/inbox_router/bootstrap_from_sent.py`, that replays `PatternProfile`'s existing Sent-folder correlation (`observe_sent_history`'s own logic, called directly against `MockGmailClient`/`RealGmailClient`) and emits one recorded example per correlated thread it finds (reply/forward only — Sent history structurally cannot teach `route_scope1`/`route_scope2`, since those never produce a sent message). `source="bootstrap"`.

Both write to the same append-only file, `components/inbox_router/data/training_examples.jsonl` (gitignored, same category as `pattern_profile.json` and `mock_state.json`), one JSON object per line:

```json
{"message_id": "...", "subject": "...", "sender_email": "...", "body_text": "...",
 "decision": "reply", "source": "live"|"bootstrap", "recorded_at": "..."}
```

This deliberately mirrors a demo trace file's role: dumb, append-only, replayable, and the single source of truth Train reads from. No labeling UI is needed beyond the Confirm/Override buttons that already exist.

### 2. `components/inbox_router/inbox_features.py` (new) — shared feature extraction

Mirrors `components/scope2/features/extractor.py`'s role exactly: one function, `extract(message, pattern) -> torch.Tensor`, called identically at training time (over recorded examples) and at inference time (over a live message) — the same "train/inference skew is a bug" rule Scope #2's extractor.py states explicitly, copied here on purpose.

Fixed-length feature vector (small, on purpose — same reasoning as Scope #2's 17-dim vector, sized for tens-to-low-hundreds of examples, not thousands):

- **Semantic** (via `sentence_transformers`, the same model Scope #2 already loads): cosine similarity between this email's text embedding and the *centroid* embedding of past recorded examples for each of the 6 decisions. This keeps the network's input small (6 scalars, one per decision) instead of feeding a raw 384-dim embedding in directly — same reason Scope #2's `Matcher` docstring gives for not concatenating raw embeddings: too few examples, would just memorize.
- **Pattern** (from the existing `PatternProfile`): `reply_count`, `forward_count`, `ignore_count` ratios for this sender's domain.
- **Rule-signal** (from the existing `RuleLayer`): whether a capsule keyword match fired, and which kind (`route_scope1` vs `route_scope2`), as a one-hot pair.
- **Structural**: body length (log-scaled, clipped), has-prior-thread-history (bool).

`DIMS`/`FEATURE_NAMES`/`VERSION` constants, same convention as `extractor.py`, so a trained checkpoint records what it was trained against and a mismatched load fails loudly (`ExtractorMismatch`-equivalent) rather than silently.

### 3. `components/inbox_router/inbox_model.py` (new) — the trained model

Directly mirrors `components/scope2/model/matcher.py`'s `Matcher` class shape:

```python
class InboxDecisionNet(nn.Module):
    def __init__(self, dims=DIMS, num_decisions=6):
        self.net = nn.Sequential(
            nn.Linear(dims, 16), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(16, num_decisions),
        )
    def forward(self, x): return self.net(x)          # logits
    def probabilities(self, x): return softmax(self(x), dim=-1)
```

Same `save()`/`load()` pair as `matcher.py`, recording `extractor_version` and `feature_names` in the checkpoint. Six-way softmax (not sigmoid — this is a single-label choice among 6 decisions, not Scope #2's independent-pair-scoring problem), confidence = `max(probabilities)`.

### 4. `components/inbox_router/train_inbox_agent.py` (new) — Train

Standalone script, same shape/conventions as `scripts/train.py`: argparse (`--examples_path`, `--save_path`, `--epochs`, `--val_split`), resolves repo root the same way, reads `training_examples.jsonl`, runs each example through `inbox_features.extract()`, trains `InboxDecisionNet` with cross-entropy loss, reports train/val accuracy, saves to `components/inbox_router/data/inbox_model.pt` by default.

### 5. `components/inbox_router/inbox_agent.py` (new) — Output, replaces `InboxRouter`'s classify logic

`InboxAgent` is the new decision-maker. `router.py`'s `InboxRouter._classify_and_record()` (currently: try `RuleLayer`, else `LLMClassifier`) is replaced with a call into `InboxAgent.decide(message)`:

1. Extract features via `inbox_features.extract()`.
2. If a checkpoint is loaded and its top prediction confidence ≥ `high_confidence` (constructor param, default `0.75` — the same default and reasoning `RuleLayer` already uses for its own dominant-pattern gate, not `agent.py`'s deliberately-disabled `1.01`) → **fast-fill**: return that decision directly, `layer="fast_fill"`, no LLM call.
3. Otherwise → **reasoning**: call `RuleLayer.classify()` first (still cheap and deterministic — folded in as a guard inside `InboxAgent`'s own decision flow, the same way `agent.py` has built-in guards rather than a separate pre-model pass), then `LLMClassifier.classify()` if that's also empty. `layer="rule"` or `"llm"`, exactly as today.
4. No checkpoint loaded yet (cold start — no training file exists, or `train_inbox_agent.py` hasn't been run) → step 2 is skipped entirely, every message goes straight to step 3. This is the natural, non-special-cased consequence of "no checkpoint" rather than an explicit branch.

`InboxRouter` keeps its Gmail-polling/confirm/override/history/metrics responsibilities unchanged — it now holds an `InboxAgent` instance instead of separate `RuleLayer`/`LLMClassifier` instances, and `_classify_and_record()` becomes a thin call to `agent.decide(message)`. Every Confirm/Override still calls `decision_recorder.record_example()` before/alongside its existing `PatternProfile` update — training data accumulates automatically from normal use, not just from the dedicated recording sessions.

`_pick_provider()`/LLM construction stays in `router.py`'s `main()`, passed into `InboxAgent`'s constructor — no change to how a provider gets chosen.

## Data flow

1. **Record**: dedicated session (someone runs the mockup, works through realistic emails, confirms/overrides on purpose) → `training_examples.jsonl` grows. One-time: `bootstrap_from_sent.py` seeds it from Sent-folder history.
2. **Train**: `python components/inbox_router/train_inbox_agent.py` → reads the file → `inbox_model.pt`.
3. **Output**: `router.py` constructs `InboxAgent(checkpoint_path="components/inbox_router/data/inbox_model.pt", rule_layer=..., llm_classifier=...)`. If the checkpoint file doesn't exist, `InboxAgent` loads with `_model = None` (cold start, per above).
4. Every live decision — fast-fill or reasoning — still ends at the same Confirm/Override human check that exists today. Nothing this pipeline adds can act without that step, same as now.

## Error handling

- Missing/corrupt checkpoint at `InboxAgent` construction → log via `emit("inbox_log", ..., level="dim")` and fall back to cold-start behavior (never crash the router process over a bad checkpoint — same "never raises on write failure" spirit as `_record_session_metrics`).
- `extract()` on a malformed message (missing body, etc.) → same defensive defaults `RuleLayer`/`LLMClassifier` already use elsewhere (empty string fallbacks), never raises.
- `train_inbox_agent.py` on fewer than N examples (too small to split train/val meaningfully) → clear CLI error message, refuses to save a checkpoint trained on noise, same spirit as this project's other "don't overclaim confidence from insufficient data" precedent (`PatternProfile.dominant_action`'s `total() >= 2` gate).

## Testing

TDD throughout, per project standard:

- `inbox_features.py`: unit tests for each feature computing the right value on constructed fixtures; a version-bump/mismatch test mirroring Scope #2's `ExtractorMismatch` coverage.
- `inbox_model.py`: save/load round-trip, shape checks, `probabilities()` sums to 1.
- `decision_recorder.py`: appends correct JSONL shape; `bootstrap_from_sent.py` against a small fixture produces the expected reply/forward-only examples.
- `train_inbox_agent.py`: trains on a tiny fixture set, produces a loadable checkpoint, refuses on too-few examples.
- `inbox_agent.py`: fast-fill fires only above threshold; falls through to `RuleLayer` then `LLMClassifier` when unsure; cold-start (no checkpoint) always defers; confidence/layer fields are correct in the returned result.
- `router.py`: existing `tests/test_inbox_router.py` (32 tests) updated for the `InboxAgent`-backed `_classify_and_record()`, still asserting the same public event/history/metrics contracts.
- Full project suite must stay green (currently 1306 passed, 9 skipped, 0 failed).

## File layout summary

```
components/inbox_router/
  decision_recorder.py       (new)
  bootstrap_from_sent.py     (new)
  inbox_features.py          (new)
  inbox_model.py             (new)
  train_inbox_agent.py       (new)
  inbox_agent.py             (new)
  router.py                  (modified: InboxRouter uses InboxAgent)
  data/
    training_examples.jsonl  (new, gitignored)
    inbox_model.pt           (new — not committed until a real trained
                              checkpoint exists; matcher.pt is the precedent
                              for committing a real one once it does)
tests/
  test_inbox_features.py     (new)
  test_inbox_model.py        (new)
  test_decision_recorder.py  (new)
  test_train_inbox_agent.py  (new)
  test_inbox_agent.py        (new)
  test_inbox_router.py       (modified)
```
