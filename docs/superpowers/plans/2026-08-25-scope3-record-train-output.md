# Scope #3 Record -> Train -> Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Scope #3 (email triage) a trained decision model, following the same Record -> Train -> Output shape Scope #1 uses, so `InboxRouter` fast-fills confident decisions and falls back to reasoning (today's `RuleLayer` + `LLMClassifier`, now internal to the new output) when unsure.

**Architecture:** A Transformer-based sentence embedding (reusing `components/scope2/features/encoders.py`, already a project dependency) plus hand-engineered signals feed a small trained classifier (`InboxDecisionNet`, mirroring `components/scope2/model/matcher.py`'s shape). `InboxAgent` wraps that model as Scope #3's single decision-maker, replacing `InboxRouter`'s current inline rule-then-LLM branching with one `decide()` call.

**Tech Stack:** Python, PyTorch, `sentence-transformers` (via Scope #2's `features/encoders.py`), pytest.

**Spec:** `docs/superpowers/specs/2026-08-25-scope3-record-train-output-design.md`

## Global Constraints

- Branch: `feature/scope3-record-train-output` (already created).
- Navigation is explicitly out of scope for this plan (deferred per direct instruction).
- No real Gmail account wiring — validate against `MockGmailClient` / `mock_inbox.json`, same as existing Scope #3 work.
- No UI surface for model type/thresholds — these are fixed constructor defaults, never exposed settings.
- Every new module follows this codebase's established bare-import convention (`from gmail_client import ...`, not relative imports), with the same defensive `sys.path` bootstrap pattern already used in `pattern_profile.py`/`routing_rules.py`.
- Full project test suite must stay green after every task (currently 1306 passed, 9 skipped, 0 failed).
- Commit after every task, per this project's standing rule to commit every change, not just at the end of a session.

---

### Task 1: Make `RuleLayer.match_capsule` public; feature extraction — `inbox_features.py`

**Files:**
- Modify: `components/inbox_router/routing_rules.py`
- Create: `components/inbox_router/inbox_features.py`
- Test: `tests/test_inbox_features.py`

**Interfaces:**
- Consumes: `gmail_client.EmailMessage`, `pattern_profile.SenderPattern`, `routing_rules.RuleLayer.match_capsule(message) -> Optional[dict]`.
- Produces: `RuleLayer.match_capsule` (public, renamed from `_match_capsule`), `DIMS: int`, `FEATURE_NAMES: List[str]`, `VERSION: str`, `DECISIONS_ORDER: List[str]`, `extract(message, pattern, centroids, rule_layer) -> List[float]`, `compute_centroids(examples: List[dict]) -> Dict[str, List[float]]`. These names are imported by Tasks 2, 5, 6, and 7 exactly as spelled here.

- [ ] **Step 0: Rename `RuleLayer._match_capsule` to `match_capsule`**

Feature extraction needs to call this from outside `routing_rules.py`, so it becomes a real public capability now rather than staying private and growing a workaround elsewhere. In `components/inbox_router/routing_rules.py`, rename the method (drop the leading underscore) and update its one internal call site:

```python
    def match_capsule(self, message: EmailMessage) -> Optional[dict]:
        haystack = f"{message.subject}\n{message.body_text}".lower()
        for capsule in self.load_capsules():
            keywords = capsule.get("trigger_keywords") or []
            apps = capsule.get("trigger_apps") or []
            if any(kw.lower() in haystack for kw in keywords) or \
               any(app.lower() in haystack for app in apps):
                return capsule
        return None
```

And in `classify()`, change:
```python
        capsule = self._match_capsule(message)
```
to:
```python
        capsule = self.match_capsule(message)
```

Run: `pytest tests/test_inbox_router.py -v`
Expected: PASS (all existing tests — nothing outside `routing_rules.py` referenced the private name, confirmed by grep before this plan was written)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_inbox_features.py
import math
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage
from pattern_profile import PatternProfile
from routing_rules import RuleLayer
import inbox_features as feats


def _msg(sender_email="alice@vendor.com", subject="Hello", body="Just checking in."):
    return EmailMessage(
        id="m1", thread_id="t1", sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text=body, received_at="2026-08-25T00:00:00Z",
    )


def _rule_layer(tmp_path, profile, capsules=None):
    import json
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"capsules": capsules or []}), encoding="utf-8")
    return RuleLayer(profile, registry_path=str(registry_path))


class TestExtract:
    def test_returns_correct_length(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile)
        result = feats.extract(_msg(), None, {}, rules)
        assert len(result) == feats.DIMS
        assert all(isinstance(v, float) for v in result)

    def test_pattern_ratios_reflect_sender_history(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        pattern = profile._get_or_create("vendor.com")
        pattern.reply_count, pattern.forward_count, pattern.ignore_count = 3, 1, 0
        rules = _rule_layer(tmp_path, profile)
        result = feats.extract(_msg(), pattern, {}, rules)
        idx = feats.FEATURE_NAMES.index("pattern_reply_ratio")
        assert result[idx] == pytest.approx(0.75)

    def test_no_pattern_gives_zero_ratios(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile)
        result = feats.extract(_msg(), None, {}, rules)
        idx = feats.FEATURE_NAMES.index("pattern_reply_ratio")
        assert result[idx] == 0.0

    def test_rule_hit_scope1_sets_correct_feature(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile, capsules=[
            {"name": "form_filling", "description": "", "model_path": "x.pt",
             "trigger_keywords": ["insurance"], "trigger_apps": []},
        ])
        result = feats.extract(_msg(subject="insurance intake"), None, {}, rules)
        idx1 = feats.FEATURE_NAMES.index("rule_hit_scope1")
        idx2 = feats.FEATURE_NAMES.index("rule_hit_scope2")
        assert result[idx1] == 1.0
        assert result[idx2] == 0.0

    def test_rule_hit_scope2_sets_correct_feature(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile, capsules=[
            {"name": "Sheet-to-Portal Matcher", "description": "", "kind": "script",
             "model_path": "", "trigger_keywords": ["grades"], "trigger_apps": []},
        ])
        result = feats.extract(_msg(subject="grades roster"), None, {}, rules)
        idx1 = feats.FEATURE_NAMES.index("rule_hit_scope1")
        idx2 = feats.FEATURE_NAMES.index("rule_hit_scope2")
        assert result[idx1] == 0.0
        assert result[idx2] == 1.0

    def test_no_rule_match_sets_both_zero(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile)
        result = feats.extract(_msg(subject="totally unrelated"), None, {}, rules)
        idx1 = feats.FEATURE_NAMES.index("rule_hit_scope1")
        idx2 = feats.FEATURE_NAMES.index("rule_hit_scope2")
        assert result[idx1] == 0.0
        assert result[idx2] == 0.0

    def test_body_length_scaled_is_bounded(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile)
        short = feats.extract(_msg(body="hi"), None, {}, rules)
        long = feats.extract(_msg(body="word " * 5000), None, {}, rules)
        idx = feats.FEATURE_NAMES.index("body_length_scaled")
        assert 0.0 <= short[idx] <= 1.0
        assert 0.0 <= long[idx] <= 1.0
        assert long[idx] > short[idx]

    def test_semantic_features_favor_matching_centroid(self, tmp_path):
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = _rule_layer(tmp_path, profile)
        centroids = feats.compute_centroids([
            {"subject": "please reply soon", "body_text": "Can you get back to me?", "decision": "reply"},
            {"subject": "fwd this along", "body_text": "Please pass this to the team.", "decision": "forward"},
        ])
        result = feats.extract(_msg(subject="please reply soon", body="Can you get back to me?"),
                                None, centroids, rules)
        reply_idx = feats.FEATURE_NAMES.index("sem_sim_reply")
        forward_idx = feats.FEATURE_NAMES.index("sem_sim_forward")
        assert result[reply_idx] > result[forward_idx]


class TestComputeCentroids:
    def test_empty_examples_returns_empty_dict(self):
        assert feats.compute_centroids([]) == {}

    def test_groups_by_decision(self):
        examples = [
            {"subject": "a", "body_text": "reply text one", "decision": "reply"},
            {"subject": "b", "body_text": "reply text two", "decision": "reply"},
            {"subject": "c", "body_text": "forward text", "decision": "forward"},
        ]
        centroids = feats.compute_centroids(examples)
        assert set(centroids.keys()) == {"reply", "forward"}
        assert len(centroids["reply"]) == len(centroids["forward"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inbox_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'inbox_features'`

- [ ] **Step 3: Write the implementation**

```python
# components/inbox_router/inbox_features.py
"""
components/inbox_router/inbox_features.py
=============================================
Shared feature extraction for Scope #3's Record -> Train -> Output pipeline.
Mirrors components/scope2/features/extractor.py's role exactly: one
function, extract(), called identically at training time (over recorded
examples) and at inference time (over a live message) -- a feature that
exists only during training is a train/inference skew no accuracy number
will reveal, the same rule extractor.py states explicitly.

Reuses components/scope2/features/encoders.py (a pretrained
sentence-transformer, already a project dependency and already loaded
lazily/cached there) rather than loading a second copy of the model.
"""
from __future__ import annotations

import math
import os
import sys
from typing import Dict, List, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
_SCOPE2_DIR = os.path.join(_ROOT, "components", "scope2")
for _p in (_THIS_DIR, _SCOPE2_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from features import encoders  # noqa: E402
from gmail_client import EmailMessage  # noqa: E402
from pattern_profile import SenderPattern  # noqa: E402
from routing_rules import RuleLayer  # noqa: E402

VERSION = "inbox-features-v1-13d"

DECISIONS_ORDER = ["route_scope1", "route_scope2", "reply", "forward", "flag", "leave_alone"]

FEATURE_NAMES = [
    "sem_sim_route_scope1",   # 1
    "sem_sim_route_scope2",   # 2
    "sem_sim_reply",          # 3
    "sem_sim_forward",        # 4
    "sem_sim_flag",           # 5
    "sem_sim_leave_alone",    # 6
    "pattern_reply_ratio",    # 7
    "pattern_forward_ratio",  # 8
    "pattern_ignore_ratio",   # 9
    "rule_hit_scope1",        # 10
    "rule_hit_scope2",        # 11
    "body_length_scaled",     # 12
    "has_sender_history",     # 13
]

DIMS = len(FEATURE_NAMES)

_BODY_LENGTH_CAP = 5000


def compute_centroids(examples: List[dict]) -> Dict[str, List[float]]:
    """Averages the text embedding of every recorded example, grouped by
    decision. Computed once at train time and persisted in the checkpoint
    (see inbox_model.py) so inference uses the exact same centroids the
    model was trained against -- the same "skew is a bug" reasoning as
    FEATURES_VERSION."""
    buckets: Dict[str, List[List[float]]] = {}
    for ex in examples:
        text = f"{ex.get('subject', '')}\n{ex.get('body_text', '')}".strip()
        if not text:
            continue
        vec = encoders.encode(text)
        buckets.setdefault(ex["decision"], []).append(vec)
    centroids: Dict[str, List[float]] = {}
    for decision, vecs in buckets.items():
        dims = len(vecs[0])
        centroids[decision] = [sum(v[i] for v in vecs) / len(vecs) for i in range(dims)]
    return centroids


def extract(message: EmailMessage, pattern: Optional[SenderPattern],
            centroids: Dict[str, List[float]], rule_layer: RuleLayer) -> List[float]:
    text = f"{message.subject}\n{message.body_text}".strip()
    email_vec = encoders.encode(text) if text else [0.0] * encoders.DIMS

    sem_feats = [
        encoders.cosine(email_vec, centroids[decision]) if decision in centroids else 0.0
        for decision in DECISIONS_ORDER
    ]

    total = pattern.total() if pattern is not None else 0
    if total > 0:
        pattern_feats = [
            pattern.reply_count / total,
            pattern.forward_count / total,
            pattern.ignore_count / total,
        ]
    else:
        pattern_feats = [0.0, 0.0, 0.0]

    capsule = rule_layer.match_capsule(message)
    is_scope2 = bool(capsule) and capsule.get("kind") == "script"
    is_scope1 = bool(capsule) and not is_scope2
    rule_feats = [1.0 if is_scope1 else 0.0, 1.0 if is_scope2 else 0.0]

    body_len = len(message.body_text or "")
    body_length_scaled = min(1.0, math.log1p(body_len) / math.log1p(_BODY_LENGTH_CAP))
    has_sender_history = 1.0 if total > 0 else 0.0

    return sem_feats + pattern_feats + rule_feats + [body_length_scaled, has_sender_history]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_inbox_features.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 5: Commit**

```bash
git add components/inbox_router/routing_rules.py components/inbox_router/inbox_features.py tests/test_inbox_features.py
git commit -m "Make RuleLayer.match_capsule public; add Scope #3 feature extraction (inbox_features.py)"
```

---

### Task 2: The trained model — `inbox_model.py`

**Files:**
- Create: `components/inbox_router/inbox_model.py`
- Test: `tests/test_inbox_model.py`

**Interfaces:**
- Consumes: `inbox_features.DIMS`, `inbox_features.FEATURE_NAMES`, `inbox_features.VERSION`, `inbox_features.DECISIONS_ORDER` (Task 1).
- Produces: `InboxDecisionNet` (class, `.forward(x)`, `.probabilities(x)`, `.parameter_count()`, `.dims`, `.num_decisions`), `FeaturesMismatch` (exception), `save(model, path, centroids, metadata=None) -> str`, `load(path, allow_mismatch=False) -> (InboxDecisionNet, dict)`. These exact names are imported by Tasks 5 and 6.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_inbox_model.py
import os
import sys

import pytest
import torch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import inbox_model as im


class TestInboxDecisionNet:
    def test_forward_shape(self):
        model = im.InboxDecisionNet(dims=4, num_decisions=3)
        x = torch.zeros(2, 4)
        logits = model(x)
        assert logits.shape == (2, 3)

    def test_probabilities_sum_to_one(self):
        model = im.InboxDecisionNet(dims=4, num_decisions=3)
        x = torch.randn(5, 4)
        probs = model.probabilities(x)
        sums = probs.sum(dim=-1)
        assert torch.allclose(sums, torch.ones(5), atol=1e-5)

    def test_parameter_count_positive(self):
        model = im.InboxDecisionNet(dims=4, num_decisions=3)
        assert model.parameter_count() > 0


class TestSaveLoad:
    def test_round_trip_preserves_predictions(self, tmp_path):
        model = im.InboxDecisionNet(dims=im.DIMS, num_decisions=len(im.DECISIONS_ORDER))
        x = torch.randn(3, im.DIMS)
        before = model.probabilities(x)

        path = str(tmp_path / "ckpt.pt")
        im.save(model, path, centroids={"reply": [0.1, 0.2]})
        loaded, artifact = im.load(path)

        after = loaded.probabilities(x)
        assert torch.allclose(before, after, atol=1e-6)
        assert artifact["centroids"] == {"reply": [0.1, 0.2]}

    def test_features_version_mismatch_raises(self, tmp_path):
        model = im.InboxDecisionNet(dims=im.DIMS, num_decisions=len(im.DECISIONS_ORDER))
        path = str(tmp_path / "ckpt.pt")
        im.save(model, path, centroids={})
        artifact = torch.load(path, weights_only=False)
        artifact["features_version"] = "some-old-version"
        torch.save(artifact, path)
        with pytest.raises(im.FeaturesMismatch):
            im.load(path)

    def test_dims_mismatch_raises(self, tmp_path):
        model = im.InboxDecisionNet(dims=im.DIMS, num_decisions=len(im.DECISIONS_ORDER))
        path = str(tmp_path / "ckpt.pt")
        im.save(model, path, centroids={})
        artifact = torch.load(path, weights_only=False)
        artifact["dims"] = artifact["dims"] + 1
        torch.save(artifact, path)
        with pytest.raises(im.FeaturesMismatch):
            im.load(path)

    def test_allow_mismatch_bypasses_guard(self, tmp_path):
        model = im.InboxDecisionNet(dims=im.DIMS, num_decisions=len(im.DECISIONS_ORDER))
        path = str(tmp_path / "ckpt.pt")
        im.save(model, path, centroids={})
        artifact = torch.load(path, weights_only=False)
        artifact["features_version"] = "some-old-version"
        torch.save(artifact, path)
        loaded, _artifact = im.load(path, allow_mismatch=True)
        assert isinstance(loaded, im.InboxDecisionNet)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inbox_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'inbox_model'`

- [ ] **Step 3: Write the implementation**

```python
# components/inbox_router/inbox_model.py
"""
components/inbox_router/inbox_model.py
==========================================
InboxDecisionNet -- the trained model in Scope #3's Record -> Train ->
Output pipeline. Directly mirrors components/scope2/model/matcher.py's
Matcher: small on purpose (few training examples expected -- a large
network would just memorize them), same save()/load() shape with a
version+dims mismatch guard so a stale checkpoint fails loudly instead of
silently scoring nonsense.
"""
from __future__ import annotations

import os
import sys
from typing import Optional, Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import torch
from torch import nn
from torch.nn import functional as F

from inbox_features import DECISIONS_ORDER, DIMS, FEATURE_NAMES, VERSION as FEATURES_VERSION

HIDDEN = 16
DROPOUT = 0.2


class InboxDecisionNet(nn.Module):
    def __init__(self, dims: int = DIMS, num_decisions: int = len(DECISIONS_ORDER)) -> None:
        super().__init__()
        self.dims = dims
        self.num_decisions = num_decisions
        self.net = nn.Sequential(
            nn.Linear(dims, HIDDEN),
            nn.ReLU(),
            nn.Dropout(DROPOUT),
            nn.Linear(HIDDEN, num_decisions),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Logits over DECISIONS_ORDER."""
        return self.net(x)

    @torch.no_grad()
    def probabilities(self, x: torch.Tensor) -> torch.Tensor:
        self.eval()
        return F.softmax(self(x), dim=-1)

    def parameter_count(self) -> int:
        return sum(p.numel() for p in self.parameters())


class FeaturesMismatch(RuntimeError):
    """The checkpoint was trained against a different feature layout than is loaded."""


def save(model: InboxDecisionNet, path: str, centroids: dict, metadata: Optional[dict] = None) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    torch.save({
        "state_dict": model.state_dict(),
        "dims": model.dims,
        "num_decisions": model.num_decisions,
        "features_version": FEATURES_VERSION,
        "feature_names": FEATURE_NAMES,
        "centroids": centroids,
        "metadata": metadata or {},
    }, path)
    return path


def load(path: str, allow_mismatch: bool = False) -> Tuple[InboxDecisionNet, dict]:
    artifact = torch.load(path, weights_only=False)
    if artifact["features_version"] != FEATURES_VERSION and not allow_mismatch:
        raise FeaturesMismatch(
            f"checkpoint was trained with {artifact['features_version']!r} but "
            f"{FEATURES_VERSION!r} is loaded; retrain before scoring"
        )
    if artifact["dims"] != DIMS and not allow_mismatch:
        raise FeaturesMismatch(
            f"checkpoint expects {artifact['dims']} features, extractor produces {DIMS}"
        )
    model = InboxDecisionNet(artifact["dims"], artifact["num_decisions"])
    model.load_state_dict(artifact["state_dict"])
    model.eval()
    return model, artifact
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_inbox_model.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add components/inbox_router/inbox_model.py tests/test_inbox_model.py
git commit -m "Add Scope #3 trained model (inbox_model.py, mirrors Scope #2's matcher shape)"
```

---

### Task 3: Recording — `decision_recorder.py`

**Files:**
- Create: `components/inbox_router/decision_recorder.py`
- Test: `tests/test_decision_recorder.py`

**Interfaces:**
- Consumes: `gmail_client.EmailMessage`.
- Produces: `DEFAULT_EXAMPLES_PATH: str`, `record_example(message, decision, source, path=DEFAULT_EXAMPLES_PATH) -> None`, `load_examples(path=DEFAULT_EXAMPLES_PATH) -> List[dict]`. Imported by Tasks 4, 5, and 7.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_decision_recorder.py
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage
import decision_recorder as rec


def _msg():
    return EmailMessage(
        id="m1", thread_id="t1", sender="Alice <alice@vendor.com>", sender_email="alice@vendor.com",
        subject="Hello", snippet="", body_text="Just checking in.", received_at="2026-08-25T00:00:00Z",
    )


class TestRecordExample:
    def test_appends_correct_shape(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        rec.record_example(_msg(), "reply", source="live", path=path)
        lines = open(path, encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["message_id"] == "m1"
        assert row["subject"] == "Hello"
        assert row["sender_email"] == "alice@vendor.com"
        assert row["body_text"] == "Just checking in."
        assert row["decision"] == "reply"
        assert row["source"] == "live"
        assert "recorded_at" in row

    def test_appends_multiple_lines(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        rec.record_example(_msg(), "reply", source="live", path=path)
        rec.record_example(_msg(), "forward", source="bootstrap", path=path)
        lines = open(path, encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 2

    def test_invalid_source_raises(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        with pytest.raises(ValueError):
            rec.record_example(_msg(), "reply", source="bogus", path=path)

    def test_creates_parent_directory(self, tmp_path):
        path = str(tmp_path / "nested" / "dir" / "examples.jsonl")
        rec.record_example(_msg(), "reply", source="live", path=path)
        assert os.path.exists(path)


class TestLoadExamples:
    def test_missing_file_returns_empty_list(self, tmp_path):
        assert rec.load_examples(str(tmp_path / "nope.jsonl")) == []

    def test_reads_back_recorded_examples(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        rec.record_example(_msg(), "reply", source="live", path=path)
        rec.record_example(_msg(), "forward", source="bootstrap", path=path)
        examples = rec.load_examples(path)
        assert len(examples) == 2
        assert examples[0]["decision"] == "reply"
        assert examples[1]["decision"] == "forward"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_decision_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'decision_recorder'`

- [ ] **Step 3: Write the implementation**

```python
# components/inbox_router/decision_recorder.py
"""
components/inbox_router/decision_recorder.py
================================================
Record step of Scope #3's Record -> Train -> Output pipeline. Every real
Confirm/Override in the Inbox Dispatch mockup (via router.py's
InboxRouter) and every bootstrap example from Sent-folder history (via
bootstrap_from_sent.py) becomes one labeled example appended here -- the
Scope #3 analog of a demo trace file: dumb, append-only, replayable, the
single source of truth train_inbox_agent.py reads from.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from gmail_client import EmailMessage

DEFAULT_EXAMPLES_PATH = os.path.join(_THIS_DIR, "data", "training_examples.jsonl")

VALID_SOURCES = {"live", "bootstrap"}


def record_example(message: EmailMessage, decision: str, source: str,
                    path: str = DEFAULT_EXAMPLES_PATH) -> None:
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {VALID_SOURCES}, got {source!r}")
    row = {
        "message_id": message.id,
        "subject": message.subject,
        "sender_email": message.sender_email,
        "body_text": message.body_text,
        "decision": decision,
        "source": source,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def load_examples(path: str = DEFAULT_EXAMPLES_PATH) -> List[dict]:
    if not os.path.exists(path):
        return []
    examples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    return examples
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_decision_recorder.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add components/inbox_router/decision_recorder.py tests/test_decision_recorder.py
git commit -m "Add Scope #3 Record step (decision_recorder.py)"
```

---

### Task 4: Bootstrap from Sent history — `bootstrap_from_sent.py`

**Files:**
- Create: `components/inbox_router/bootstrap_from_sent.py`
- Test: `tests/test_bootstrap_from_sent.py`

**Interfaces:**
- Consumes: `decision_recorder.record_example` (Task 3), `gmail_client.EmailMessage`/`get_gmail_client`, `pattern_profile.FORWARD_MARKERS`/`_domain`.
- Produces: `bootstrap_examples(sent, inbox, path=DEFAULT_EXAMPLES_PATH) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bootstrap_from_sent.py
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage
import bootstrap_from_sent as boot
import decision_recorder as rec


def _inbox_msg(mid, thread_id, sender_email):
    return EmailMessage(
        id=mid, thread_id=thread_id, sender=sender_email, sender_email=sender_email,
        subject="Original", snippet="", body_text="Original body.",
        received_at="2026-08-20T00:00:00Z",
    )


def _sent_msg(thread_id, to, body="Thanks, got it.", ):
    return EmailMessage(
        id="s-" + thread_id, thread_id=thread_id, sender="me@company.com",
        sender_email="me@company.com", subject="Re: Original", snippet="",
        body_text=body, received_at="2026-08-21T00:00:00Z", to=to,
    )


class TestBootstrapExamples:
    def test_reply_correlated_by_thread(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        inbox = [_inbox_msg("i1", "t1", "alice@vendor.com")]
        sent = [_sent_msg("t1", to="alice@vendor.com")]
        count = boot.bootstrap_examples(sent, inbox, path=path)
        assert count == 1
        examples = rec.load_examples(path)
        assert examples[0]["decision"] == "reply"

    def test_forward_detected_by_marker(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        inbox = [_inbox_msg("i1", "t1", "alice@vendor.com")]
        sent = [_sent_msg("t1", to="bob@other.com", body="---- Forwarded message ----\nSee below.")]
        count = boot.bootstrap_examples(sent, inbox, path=path)
        assert count == 1
        examples = rec.load_examples(path)
        assert examples[0]["decision"] == "forward"

    def test_forward_detected_by_third_party_recipient(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        inbox = [_inbox_msg("i1", "t1", "alice@vendor.com")]
        sent = [_sent_msg("t1", to="bob@other.com", body="Passing this along.")]
        count = boot.bootstrap_examples(sent, inbox, path=path)
        examples = rec.load_examples(path)
        assert examples[0]["decision"] == "forward"

    def test_sent_with_no_matching_thread_produces_nothing(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        inbox = [_inbox_msg("i1", "t1", "alice@vendor.com")]
        sent = [_sent_msg("t999", to="alice@vendor.com")]
        count = boot.bootstrap_examples(sent, inbox, path=path)
        assert count == 0
        assert rec.load_examples(path) == []

    def test_multiple_correlated_threads(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        inbox = [_inbox_msg("i1", "t1", "alice@vendor.com"), _inbox_msg("i2", "t2", "carol@vendor.com")]
        sent = [_sent_msg("t1", to="alice@vendor.com"), _sent_msg("t2", to="carol@vendor.com")]
        count = boot.bootstrap_examples(sent, inbox, path=path)
        assert count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bootstrap_from_sent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootstrap_from_sent'`

- [ ] **Step 3: Write the implementation**

```python
# components/inbox_router/bootstrap_from_sent.py
"""
components/inbox_router/bootstrap_from_sent.py
==================================================
One-time Record bootstrap: replays the same Sent-folder correlation
PatternProfile.observe_sent_history() already does, but emits one recorded
training example per correlated thread instead of (only) updating pattern
counters. Reply/forward only -- Sent history structurally cannot teach
route_scope1/route_scope2, since those decisions never produce a sent
message.

Usage:
    python components/inbox_router/bootstrap_from_sent.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from decision_recorder import DEFAULT_EXAMPLES_PATH, record_example
from gmail_client import EmailMessage, get_gmail_client
from pattern_profile import FORWARD_MARKERS, _domain

SENT_LOOKBACK_DAYS = 90


def bootstrap_examples(sent: List[EmailMessage], inbox: List[EmailMessage],
                        path: str = DEFAULT_EXAMPLES_PATH) -> int:
    by_thread = {m.thread_id: m for m in inbox}
    count = 0
    for s in sent:
        origin = by_thread.get(s.thread_id)
        if origin is None:
            continue
        domain = _domain(origin.sender_email)
        body_lower = (s.body_text or "").lower()
        is_forward = any(marker in body_lower for marker in FORWARD_MARKERS) or (
            s.to and _domain(s.to) != domain
        )
        decision = "forward" if is_forward else "reply"
        record_example(origin, decision, source="bootstrap", path=path)
        count += 1
    return count


def main() -> None:
    client = get_gmail_client()
    since_iso = (datetime.now(timezone.utc) - timedelta(days=SENT_LOOKBACK_DAYS)).isoformat()
    sent = client.list_sent(since_iso)
    inbox = client.list_recent_inbox(since_iso)
    count = bootstrap_examples(sent, inbox)
    print(f"Bootstrapped {count} training examples from {len(sent)} sent + {len(inbox)} inbox messages.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bootstrap_from_sent.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add components/inbox_router/bootstrap_from_sent.py tests/test_bootstrap_from_sent.py
git commit -m "Add Scope #3 Sent-folder bootstrap (bootstrap_from_sent.py)"
```

---

### Task 5: Training — `train_inbox_agent.py`

**Files:**
- Create: `components/inbox_router/train_inbox_agent.py`
- Test: `tests/test_train_inbox_agent.py`

**Interfaces:**
- Consumes: `decision_recorder.{DEFAULT_EXAMPLES_PATH, load_examples}` (Task 3), `inbox_features.{DECISIONS_ORDER, DIMS, compute_centroids, extract}` (Task 1), `inbox_model.{InboxDecisionNet, save}` (Task 2), `gmail_client.EmailMessage`, `pattern_profile.PatternProfile`, `routing_rules.RuleLayer`.
- Produces: `TooFewExamplesError` (exception), `MIN_EXAMPLES: int`, `build_dataset(examples, profile, rule_layer) -> (Tensor, Tensor, dict)`, `train(examples_path, save_path, epochs, lr, val_split) -> dict`. `train()` is imported by Task 6's tests to build a real checkpoint fixture.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_train_inbox_agent.py
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import decision_recorder as rec
import inbox_model as im
import train_inbox_agent as trainer
from gmail_client import EmailMessage


def _msg(mid, sender_email, subject, body):
    return EmailMessage(
        id=mid, thread_id="", sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text=body, received_at="2026-08-25T00:00:00Z",
    )


def _seed_examples(path):
    examples = [
        ("m1", "boss@work.com", "status update", "Here's where things stand.", "reply"),
        ("m2", "boss@work.com", "quick question", "Do you have a minute?", "reply"),
        ("m3", "boss@work.com", "fyi", "Thought you'd want to see this.", "reply"),
        ("m4", "newsletter@vendor.com", "weekly digest", "This week's roundup.", "leave_alone"),
        ("m5", "newsletter@vendor.com", "weekly digest", "This week's roundup part 2.", "leave_alone"),
        ("m6", "team@work.com", "fwd: doc", "Passing this along to you.", "forward"),
    ]
    for mid, sender, subject, body, decision in examples:
        rec.record_example(_msg(mid, sender, subject, body), decision, source="live", path=path)
    return len(examples)


class TestBuildDataset:
    def test_produces_matching_tensor_and_label_shapes(self, tmp_path):
        from pattern_profile import PatternProfile
        from routing_rules import RuleLayer
        path = str(tmp_path / "examples.jsonl")
        n = _seed_examples(path)
        examples = rec.load_examples(path)
        profile = PatternProfile(path=str(tmp_path / "profile.json"))
        rules = RuleLayer(profile, registry_path=str(tmp_path / "registry.json"))
        x, y, centroids = trainer.build_dataset(examples, profile, rules)
        assert x.shape == (n, trainer.DIMS)
        assert y.shape == (n,)
        assert set(centroids.keys()) == {"reply", "leave_alone", "forward"}


class TestTrain:
    def test_too_few_examples_raises(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        rec.record_example(_msg("m1", "a@b.com", "hi", "hi"), "reply", source="live", path=path)
        save_path = str(tmp_path / "model.pt")
        with pytest.raises(trainer.TooFewExamplesError):
            trainer.train(path, save_path, epochs=5, lr=1e-2, val_split=0.15)

    def test_trains_and_saves_loadable_checkpoint(self, tmp_path):
        path = str(tmp_path / "examples.jsonl")
        _seed_examples(path)
        save_path = str(tmp_path / "model.pt")
        result = trainer.train(path, save_path, epochs=20, lr=1e-2, val_split=0.15)
        assert os.path.exists(save_path)
        assert 0.0 <= result["train_acc"] <= 1.0
        assert result["num_examples"] == 6
        model, artifact = im.load(save_path)
        assert isinstance(model, im.InboxDecisionNet)
        assert artifact["centroids"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_train_inbox_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'train_inbox_agent'`

- [ ] **Step 3: Write the implementation**

```python
# components/inbox_router/train_inbox_agent.py
"""
components/inbox_router/train_inbox_agent.py
================================================
Train step of Scope #3's Record -> Train -> Output pipeline. Reads recorded
examples (components/inbox_router/data/training_examples.jsonl by default),
builds features via inbox_features.extract(), fits InboxDecisionNet, saves
a checkpoint. Same CLI shape as scripts/train.py.

Usage:
    python components/inbox_router/train_inbox_agent.py
    python components/inbox_router/train_inbox_agent.py --epochs 300 --save_path components/inbox_router/data/inbox_model.pt
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
for _p in (_ROOT, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import torch
from torch import nn

from decision_recorder import DEFAULT_EXAMPLES_PATH, load_examples
from gmail_client import EmailMessage
from inbox_features import DECISIONS_ORDER, DIMS, compute_centroids, extract
from inbox_model import InboxDecisionNet, save as save_model
from pattern_profile import PatternProfile
from routing_rules import RuleLayer

MIN_EXAMPLES = 6   # at least one per decision class, in spirit


class TooFewExamplesError(ValueError):
    pass


def _example_to_message(ex: dict) -> EmailMessage:
    return EmailMessage(
        id=ex.get("message_id", ""), thread_id="", sender="",
        sender_email=ex.get("sender_email", ""), subject=ex.get("subject", ""),
        snippet="", body_text=ex.get("body_text", ""), received_at="",
    )


def build_dataset(examples: list, profile: PatternProfile,
                   rule_layer: RuleLayer) -> Tuple[torch.Tensor, torch.Tensor, dict]:
    centroids = compute_centroids(examples)
    xs, ys = [], []
    for ex in examples:
        message = _example_to_message(ex)
        pattern = profile.pattern_for(message.sender_email)
        feats = extract(message, pattern, centroids, rule_layer)
        xs.append(feats)
        ys.append(DECISIONS_ORDER.index(ex["decision"]))
    return torch.tensor(xs, dtype=torch.float32), torch.tensor(ys, dtype=torch.long), centroids


@torch.no_grad()
def _accuracy(model: InboxDecisionNet, x: torch.Tensor, y: torch.Tensor):
    if len(x) == 0:
        return None
    model.eval()
    preds = model(x).argmax(dim=-1)
    return float((preds == y).float().mean())


def train(examples_path: str, save_path: str, epochs: int, lr: float, val_split: float) -> dict:
    examples = load_examples(examples_path)
    if len(examples) < MIN_EXAMPLES:
        raise TooFewExamplesError(
            f"Only {len(examples)} recorded examples found at {examples_path} -- "
            f"need at least {MIN_EXAMPLES} to train a checkpoint that isn't noise."
        )
    profile = PatternProfile()
    rule_layer = RuleLayer(profile)
    x, y, centroids = build_dataset(examples, profile, rule_layer)

    n_val = max(1, int(len(examples) * val_split)) if len(examples) >= 10 else 0
    n_train = len(examples) - n_val
    x_train, y_train = x[:n_train], y[:n_train]
    x_val, y_val = x[n_train:], y[n_train:]

    model = InboxDecisionNet(dims=DIMS)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.CrossEntropyLoss()

    for _epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        logits = model(x_train)
        loss = loss_fn(logits, y_train)
        loss.backward()
        optimizer.step()

    train_acc = _accuracy(model, x_train, y_train)
    val_acc = _accuracy(model, x_val, y_val) if n_val else None

    save_model(model, save_path, centroids, metadata={
        "num_examples": len(examples), "train_acc": train_acc, "val_acc": val_acc,
    })
    return {"train_acc": train_acc, "val_acc": val_acc, "num_examples": len(examples)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Scope #3's InboxDecisionNet.")
    parser.add_argument("--examples_path", default=DEFAULT_EXAMPLES_PATH)
    parser.add_argument("--save_path", default=os.path.join(_THIS_DIR, "data", "inbox_model.pt"))
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--lr", type=float, default=1e-2)
    parser.add_argument("--val_split", type=float, default=0.15)
    args = parser.parse_args()

    result = train(args.examples_path, args.save_path, args.epochs, args.lr, args.val_split)
    val_acc_str = "n/a" if result["val_acc"] is None else f"{result['val_acc']:.2%}"
    print(f"Trained on {result['num_examples']} examples. "
          f"train_acc={result['train_acc']:.2%} val_acc={val_acc_str}")
    print(f"Saved checkpoint to {args.save_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_train_inbox_agent.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Commit**

```bash
git add components/inbox_router/train_inbox_agent.py tests/test_train_inbox_agent.py
git commit -m "Add Scope #3 Train step (train_inbox_agent.py)"
```

---

### Task 6: Output — `inbox_agent.py`

**Files:**
- Create: `components/inbox_router/inbox_agent.py`
- Test: `tests/test_inbox_agent.py`

**Interfaces:**
- Consumes: `inbox_features.{DECISIONS_ORDER, extract}` (Task 1), `inbox_model.{FeaturesMismatch, InboxDecisionNet, load}` (Task 2), `train_inbox_agent.train` (Task 5, test-only, to build a real checkpoint fixture), `llm_classifier.LLMClassifier`, `pattern_profile.PatternProfile`, `routing_rules.RuleLayer`, `gmail_client.EmailMessage`.
- Produces: `InboxDecision` (dataclass: `decision, confidence, rationale, layer, capsule_name, forward_to`), `InboxAgent` (class: `__init__(profile, rule_layer, llm_classifier, checkpoint_path=DEFAULT_CHECKPOINT_PATH, high_confidence=0.75)`, `.decide(message) -> InboxDecision`), `DEFAULT_CHECKPOINT_PATH: str`. `InboxAgent` and `DEFAULT_CHECKPOINT_PATH` are imported by Task 7's `router.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_inbox_agent.py
import json
import os
import sys

import torch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage
from llm_classifier import LLMClassifier
from pattern_profile import PatternProfile
from routing_rules import RuleLayer
import decision_recorder as rec
import inbox_agent as ia
import inbox_model as im
import train_inbox_agent as trainer


def _msg(sender_email="alice@vendor.com", subject="Hello", body="Just checking in."):
    return EmailMessage(
        id="m1", thread_id="t1", sender=sender_email, sender_email=sender_email,
        subject=subject, snippet="", body_text=body, received_at="2026-08-25T00:00:00Z",
    )


def _agent(tmp_path, checkpoint_path, high_confidence=0.75, capsules=None):
    profile = PatternProfile(path=str(tmp_path / "profile.json"))
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"capsules": capsules or []}), encoding="utf-8")
    rules = RuleLayer(profile, registry_path=str(registry_path))
    classifier = LLMClassifier(provider="none")
    return ia.InboxAgent(profile, rules, classifier, checkpoint_path=checkpoint_path,
                          high_confidence=high_confidence)


class TestColdStart:
    def test_no_checkpoint_always_reasons(self, tmp_path):
        agent = _agent(tmp_path, checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"))
        result = agent.decide(_msg())
        assert result.layer in ("rule", "llm")

    def test_no_checkpoint_flags_unresolved_email(self, tmp_path):
        agent = _agent(tmp_path, checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"))
        result = agent.decide(_msg(sender_email="stranger@nowhere.com", subject="totally unrelated"))
        assert result.decision == "flag"
        assert result.layer == "llm"


class TestFastFillWithTrainedCheckpoint:
    def _build_checkpoint(self, tmp_path):
        examples_path = str(tmp_path / "examples.jsonl")
        for i in range(3):
            rec.record_example(
                _msg(sender_email="boss@work.com", subject=f"status {i}", body="Please reply when you can."),
                "reply", source="live", path=examples_path,
            )
        for i in range(3):
            rec.record_example(
                _msg(sender_email="newsletter@vendor.com", subject=f"digest {i}", body="This week's roundup."),
                "leave_alone", source="live", path=examples_path,
            )
        save_path = str(tmp_path / "model.pt")
        trainer.train(examples_path, save_path, epochs=300, lr=5e-2, val_split=0.0)
        return save_path

    def test_confident_prediction_fast_fills(self, tmp_path):
        checkpoint_path = self._build_checkpoint(tmp_path)
        agent = _agent(tmp_path, checkpoint_path=checkpoint_path, high_confidence=0.0)
        result = agent.decide(_msg(sender_email="boss@work.com", subject="status 99",
                                    body="Please reply when you can."))
        assert result.layer == "fast_fill"

    def test_high_threshold_forces_reasoning(self, tmp_path):
        checkpoint_path = self._build_checkpoint(tmp_path)
        agent = _agent(tmp_path, checkpoint_path=checkpoint_path, high_confidence=0.999999)
        result = agent.decide(_msg(sender_email="boss@work.com", subject="status 99",
                                    body="Please reply when you can."))
        assert result.layer in ("rule", "llm")

    def test_route_decision_without_verified_capsule_falls_through(self, tmp_path):
        examples_path = str(tmp_path / "examples.jsonl")
        for i in range(3):
            rec.record_example(
                _msg(sender_email="broker@insure.com", subject=f"intake {i}", body="Please process this form."),
                "route_scope1", source="live", path=examples_path,
            )
        for i in range(3):
            rec.record_example(
                _msg(sender_email="newsletter@vendor.com", subject=f"digest {i}", body="This week's roundup."),
                "leave_alone", source="live", path=examples_path,
            )
        save_path = str(tmp_path / "model.pt")
        trainer.train(examples_path, save_path, epochs=300, lr=5e-2, val_split=0.0)
        # No capsules registered -- match_capsule() can never verify a name,
        # so even a confident route_scope1 prediction must fall through.
        agent = _agent(tmp_path, checkpoint_path=save_path, high_confidence=0.0, capsules=[])
        result = agent.decide(_msg(sender_email="broker@insure.com", subject="intake 99",
                                    body="Please process this form."))
        assert result.layer in ("rule", "llm")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inbox_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'inbox_agent'`

- [ ] **Step 3: Write the implementation**

```python
# components/inbox_router/inbox_agent.py
"""
components/inbox_router/inbox_agent.py
==========================================
Output of Scope #3's Record -> Train -> Output pipeline. InboxAgent is the
single decision-maker: loads a trained InboxDecisionNet checkpoint (if one
exists) and fast-fills when confident, otherwise falls through to the
existing RuleLayer -> LLMClassifier chain as its own internal reasoning
step -- not as separate legacy plumbing sitting behind it.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import torch

from gmail_client import EmailMessage
from inbox_features import DECISIONS_ORDER, extract
from inbox_model import FeaturesMismatch, InboxDecisionNet, load as load_model
from llm_classifier import LLMClassifier
from pattern_profile import PatternProfile
from routing_rules import RuleLayer

DEFAULT_CHECKPOINT_PATH = os.path.join(_THIS_DIR, "data", "inbox_model.pt")


@dataclass
class InboxDecision:
    decision: str
    confidence: float
    rationale: str
    layer: str            # "fast_fill" | "rule" | "llm"
    capsule_name: str = ""
    forward_to: str = ""


class InboxAgent:
    def __init__(self, profile: PatternProfile, rule_layer: RuleLayer,
                 llm_classifier: LLMClassifier,
                 checkpoint_path: str = DEFAULT_CHECKPOINT_PATH,
                 high_confidence: float = 0.75) -> None:
        self._profile = profile
        self._rules = rule_layer
        self._llm = llm_classifier
        self._high_confidence = high_confidence
        self._model: Optional[InboxDecisionNet] = None
        self._centroids: dict = {}
        self._load_checkpoint(checkpoint_path)

    def _load_checkpoint(self, checkpoint_path: str) -> None:
        if not os.path.exists(checkpoint_path):
            return   # cold start -- no checkpoint yet, every decision reasons
        try:
            model, artifact = load_model(checkpoint_path)
        except (FeaturesMismatch, Exception):
            # FeaturesMismatch is itself an Exception subclass; both are
            # listed for readability -- a stale/corrupt checkpoint must
            # never crash startup, only fall back to cold-start reasoning.
            self._model = None
            return
        self._model = model
        self._centroids = artifact.get("centroids", {})

    def decide(self, message: EmailMessage) -> InboxDecision:
        fast = self._try_fast_fill(message)
        if fast is not None:
            return fast
        return self._reason(message)

    def _try_fast_fill(self, message: EmailMessage) -> Optional[InboxDecision]:
        if self._model is None:
            return None
        pattern = self._profile.pattern_for(message.sender_email)
        feats = extract(message, pattern, self._centroids, self._rules)
        x = torch.tensor([feats], dtype=torch.float32)
        probs = self._model.probabilities(x)[0]
        top_idx = int(torch.argmax(probs))
        top_conf = float(probs[top_idx])
        if top_conf < self._high_confidence:
            return None
        decision = DECISIONS_ORDER[top_idx]
        capsule_name, forward_to = "", ""
        if decision in ("route_scope1", "route_scope2"):
            capsule = self._rules.match_capsule(message)
            capsule_name = capsule.get("name", "") if capsule else ""
            if not capsule_name:
                return None   # can't fast-fill a route with no verified capsule
        return InboxDecision(
            decision=decision, confidence=top_conf,
            rationale=f"Trained model is {top_conf:.0%} confident, based on similar past emails.",
            layer="fast_fill", capsule_name=capsule_name, forward_to=forward_to,
        )

    def _reason(self, message: EmailMessage) -> InboxDecision:
        rule_result = self._rules.classify(message)
        if rule_result.decision:
            return InboxDecision(
                decision=rule_result.decision, confidence=rule_result.confidence,
                rationale=rule_result.rationale, layer="rule",
                capsule_name=rule_result.capsule_name, forward_to=rule_result.forward_to,
            )
        pattern = self._profile.pattern_for(message.sender_email)
        llm_result = self._llm.classify(message, pattern, rule_result, self._rules.load_capsules())
        return InboxDecision(
            decision=llm_result.decision, confidence=llm_result.confidence,
            rationale=llm_result.rationale, layer="llm",
            capsule_name=llm_result.capsule_name, forward_to=llm_result.forward_to,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_inbox_agent.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add components/inbox_router/inbox_agent.py tests/test_inbox_agent.py
git commit -m "Add Scope #3 Output step (inbox_agent.py): fast-fill + reasoning"
```

---

### Task 7: Wire `InboxAgent` into `router.py`; record examples on Confirm/Override

**Files:**
- Modify: `components/inbox_router/router.py`
- Modify: `tests/test_inbox_router.py`

**Interfaces:**
- Consumes: `inbox_agent.{InboxAgent, DEFAULT_CHECKPOINT_PATH}` (Task 6), `decision_recorder.record_example` (Task 3), `routing_rules.RuleLayer.match_capsule` (Task 1).
- Produces: `InboxRouter.__init__(..., inbox_checkpoint_path=...)` (new optional param).

- [ ] **Step 1: Wire `InboxAgent` into `InboxRouter`**

In `components/inbox_router/router.py`, add the import (near the existing `from routing_rules import RuleLayer` line):

```python
from inbox_agent import DEFAULT_CHECKPOINT_PATH, InboxAgent
from decision_recorder import record_example
```

Change `InboxRouter.__init__`'s signature and body from:

```python
    def __init__(self, gmail_client: GmailClientBase, profile: PatternProfile,
                 rule_layer: RuleLayer, llm_classifier: LLMClassifier,
                 history_path: str = HISTORY_PATH,
                 poll_interval_s: float = DEFAULT_POLL_INTERVAL_S) -> None:
        self._gmail = gmail_client
        self._profile = profile
        self._rules = rule_layer
        self._llm = llm_classifier
        self._history_path = history_path
```

to:

```python
    def __init__(self, gmail_client: GmailClientBase, profile: PatternProfile,
                 rule_layer: RuleLayer, llm_classifier: LLMClassifier,
                 history_path: str = HISTORY_PATH,
                 poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
                 inbox_checkpoint_path: str = DEFAULT_CHECKPOINT_PATH) -> None:
        self._gmail = gmail_client
        self._profile = profile
        self._rules = rule_layer
        self._llm = llm_classifier
        self._agent = InboxAgent(profile, rule_layer, llm_classifier,
                                  checkpoint_path=inbox_checkpoint_path)
        self._history_path = history_path
```

(everything else in `__init__` after `self._history_path = history_path` stays unchanged.)

Change `_classify_and_record()` from:

```python
    def _classify_and_record(self, message: EmailMessage) -> dict:
        rule_result = self._rules.classify(message)
        if rule_result.decision:
            decision, confidence, rationale = rule_result.decision, rule_result.confidence, rule_result.rationale
            capsule_name, forward_to, layer = rule_result.capsule_name, rule_result.forward_to, "rule"
        else:
            pattern = self._profile.pattern_for(message.sender_email)
            llm_result = self._llm.classify(message, pattern, rule_result, self._rules.load_capsules())
            decision, confidence, rationale = llm_result.decision, llm_result.confidence, llm_result.rationale
            capsule_name, forward_to, layer = llm_result.capsule_name, llm_result.forward_to, "llm"
```

to:

```python
    def _classify_and_record(self, message: EmailMessage) -> dict:
        result = self._agent.decide(message)
        decision, confidence, rationale = result.decision, result.confidence, result.rationale
        capsule_name, forward_to, layer = result.capsule_name, result.forward_to, result.layer
```

(the rest of the method — the hallucinated-capsule guard, building `entry`, updating counters — stays unchanged; it already reads `decision`/`confidence`/`rationale`/`capsule_name`/`forward_to`/`layer` from local variables, which now come from `result` instead of the two branches.)

Update the layer-counts initializer in `__init__` from:
```python
        self._layer_counts = {"rule": 0, "llm": 0}
```
to:
```python
        self._layer_counts = {"rule": 0, "llm": 0, "fast_fill": 0}
```

- [ ] **Step 2: Record examples on Confirm/Override**

In `confirm_suggestion()`, change:
```python
        if message is not None:
            self._profile.record_confirmed_decision(message, decision)
        self._confirmed_count += 1
```
to:
```python
        if message is not None:
            self._profile.record_confirmed_decision(message, decision)
            record_example(message, decision, source="live")
        self._confirmed_count += 1
```

In `override_decision()`, change:
```python
        if message is not None:
            self._profile.record_override(message, old_decision, new_decision)
        self._overridden_count += 1
```
to:
```python
        if message is not None:
            self._profile.record_override(message, old_decision, new_decision)
            record_example(message, new_decision, source="live")
        self._overridden_count += 1
```

- [ ] **Step 3: Update `main()` to thread the checkpoint path through (no behavior change — `DEFAULT_CHECKPOINT_PATH` is already the default)**

No edit needed — `main()`'s existing `InboxRouter(gmail_client, profile, rule_layer, classifier)` call already picks up the new `inbox_checkpoint_path` parameter's default value.

- [ ] **Step 4: Make existing tests deterministic against real on-disk checkpoints**

In `tests/test_inbox_router.py`, both `_build()` helper methods currently end with:

```python
        return InboxRouter(client, profile, rules, classifier, history_path=history_path)
```

Change both occurrences (`replace_all`) to:

```python
        return InboxRouter(client, profile, rules, classifier, history_path=history_path,
                            inbox_checkpoint_path=str(tmp_path / "no_such_checkpoint.pt"))
```

This keeps every existing test deterministically cold-start (no fast-fill), regardless of whether a real trained checkpoint happens to exist on disk at `components/inbox_router/data/inbox_model.pt` when the suite runs.

- [ ] **Step 5: Run the full Scope #3 test suite**

Run: `pytest tests/test_inbox_router.py tests/test_inbox_features.py tests/test_inbox_model.py tests/test_decision_recorder.py tests/test_bootstrap_from_sent.py tests/test_train_inbox_agent.py tests/test_inbox_agent.py -v`
Expected: PASS (all tests — the existing 32 `test_inbox_router.py` tests must show identical `decision`/`layer` results to before this task, since cold-start `InboxAgent` behavior is byte-for-byte the same logic that used to be inline)

- [ ] **Step 6: Commit**

```bash
git add components/inbox_router/router.py tests/test_inbox_router.py
git commit -m "Wire InboxAgent into InboxRouter; record examples on Confirm/Override"
```

---

### Task 8: Full suite verification, docs sync, push

**Files:**
- Modify: `DEVELOPERS.md`
- Modify: `treetask/index.html`
- Create: `components/inbox_router/.gitignore` entries (if a repo-root or component-level `.gitignore` already covers `data/` — check first, see Step 1)

- [ ] **Step 1: Check whether `training_examples.jsonl`/`inbox_model.pt` are already covered by an existing `.gitignore`**

Run: `git check-ignore -v components/inbox_router/data/training_examples.jsonl components/inbox_router/data/inbox_model.pt`

If both are already ignored (likely — `components/inbox_router/data/` already holds `pattern_profile.json`/`mock_state.json`, both gitignored per the spec's own precedent), no `.gitignore` edit is needed. If either is NOT ignored, add the two filenames to the nearest existing `.gitignore` that already lists `pattern_profile.json`.

- [ ] **Step 2: Run the full project test suite**

Run: `pytest -q`
Expected: PASS, 0 failed (baseline before this plan: 1306 passed, 9 skipped; expect that plus every new test file's count added, 9 skipped unchanged)

- [ ] **Step 3: Update `DEVELOPERS.md`**

Add a new bullet under the Scope #3 section, immediately before the existing `scope3_mockup_workflow_launcher` entry (matching that entry's style — bold opener with date/direct-request, technical detail, test-count evidence):

```markdown
- [x] `scope3_record_train_output_pipeline` — **Added 2026-08-25, direct request** ("develop Scope #3 further in a new branch. Record -> Train -> Output. That output must have that reasoning, fast fill, and navigation"), on branch `feature/scope3-record-train-output`. Scope #3 previously had no trained model at all -- decisions came entirely from `RuleLayer` (deterministic) and `LLMClassifier` (LLM fallback), with `PatternProfile` learning passively from Sent-folder history. This gives it the same three-stage shape Scope #1 uses: **Record** (`decision_recorder.py` appends one labeled example per real Confirm/Override click, plus a one-time `bootstrap_from_sent.py` seed from existing Sent-folder correlation), **Train** (`train_inbox_agent.py`, same CLI shape as `scripts/train.py`, fits a small classifier on recorded examples), **Output** (`inbox_agent.py`'s `InboxAgent`, which replaces `InboxRouter`'s inline rule-then-LLM branching with one `decide()` call).

  Feature extraction (`inbox_features.py`) directly mirrors `components/scope2/features/extractor.py`'s role: a Transformer-based sentence embedding (reused from `components/scope2/features/encoders.py`, not a second copy of the model) produces per-decision centroid-similarity scores, combined with `PatternProfile` ratios, `RuleLayer` keyword-match signals, and structural features into one small fixed-length vector. The trained model (`inbox_model.py`'s `InboxDecisionNet`) directly mirrors `components/scope2/model/matcher.py`'s `Matcher` shape and reasoning -- small on purpose, since a large network would memorize the handful of examples expected early on rather than learn from them.

  `InboxAgent` fast-fills (acts alone, no LLM call) when the trained model's top prediction is >=75% confident (`RuleLayer`'s own existing default, not `agent.py`'s deliberately-disabled `1.01`); otherwise it reasons via the same `RuleLayer` -> `LLMClassifier` chain that already existed, now internal to `InboxAgent` rather than separate legacy plumbing sitting behind a new model. A `route_scope1`/`route_scope2` fast-fill additionally requires a keyword-verified capsule name -- a confident-but-unverifiable route prediction falls through to reasoning rather than guessing.

  Navigation deliberately deferred, direct instruction ("let's not mind it right now") -- stays a named property of the eventual output, concrete shape left to a follow-up.

  RuleLayer gained one public rename (`_match_capsule` -> `match_capsule`) since `inbox_features.py` and `inbox_agent.py` both now call it directly -- checked first that nothing outside `routing_rules.py` referenced the private name.

  TDD throughout: `test_inbox_features.py` (11), `test_inbox_model.py` (7), `test_decision_recorder.py` (6), `test_bootstrap_from_sent.py` (5), `test_train_inbox_agent.py` (3), `test_inbox_agent.py` (5) -- 37 new tests, plus `test_inbox_router.py`'s existing 32 updated to pin cold-start determinism against a guaranteed-nonexistent checkpoint path rather than whatever might exist on disk. Full suite: verify and record the real number here after Task 8 Step 2 runs, not assumed.
```

(Replace the final sentence's instruction with the actual passed/skipped/failed numbers once Step 2's real output is known — do not write a guessed number.)

- [ ] **Step 4: Mirror the same content into `treetask/index.html`**

Find the `scope3` hub's `items` array (search for `scope3_mockup_workflow_launcher`) and insert a new node object immediately before it, following the exact same `{id, t, done, desc}` shape already used by neighboring nodes, with `desc` condensed from Step 3's `DEVELOPERS.md` entry (same content, shorter prose — match the compression level of `scope3_mock_pattern_fixture_fix`'s existing node, not a verbatim copy of the full `DEVELOPERS.md` paragraph).

- [ ] **Step 5: Validate the Task Tree's JS still parses**

Run:
```bash
node -e "
const fs = require('fs');
const html = fs.readFileSync('treetask/index.html', 'utf8');
const m = html.match(/<script>([\s\S]*)<\/script>/);
if (!m) { console.error('NO SCRIPT BLOCK FOUND'); process.exit(1); }
new Function(m[1]);
console.log('OK - script block parses cleanly');
"
```
Expected: `OK - script block parses cleanly`

- [ ] **Step 6: Commit and push the branch**

```bash
git add DEVELOPERS.md treetask/index.html
git commit -m "Sync Task Tree and DEVELOPERS.md with Scope #3's Record -> Train -> Output pipeline"
git push -u origin feature/scope3-record-train-output
```

(This pushes the feature branch, not `master` — merging to `master` is a separate, later decision, not part of this plan.)
