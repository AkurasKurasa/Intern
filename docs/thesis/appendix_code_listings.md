# Appendix Code Listings

Excerpts from the Intern source code. Long development comments were shortened
and unrelated lines were left out; `...` marks every place where lines were cut.
Renumber the listings to fit the appendix (for example I.3, I.4, and so on).

---

## Listing 1. Division of decision-making between the transformer and the language model

Source: `components/agent/agent.py`, `LLMAgent.run()`

```python
# Transformer always runs: the learned behavioral engine
llm_action = None

t_pred = self._predict(state)
t_type = t_pred.get("action_type", "no_op")
t_conf = t_pred.get("confidence", max(t_pred.get("_scores", {}).values(), default=0.0))
logger.info("[TRANSFORMER] action=%-8s  conf=%.2f", t_type, t_conf)

# Routing: the LLM decides WHAT (action + value);
# the transformer supplies WHERE (click position) via _merge.
_decision_maker = "transformer"

# Transformer-navigation + LLM value-oracle:
#   - If a fillable field is focused and empty -> the LLM supplies its value,
#     then it is typed.
#   - Otherwise -> click the transformer's pointer target (navigate to the
#     next field). The transformer drives navigation.
if self._no_autohandlers:
    _fid2 = state.get("focused_element_id")
    _fe2  = next((e for e in state.get("elements", [])
                  if e.get("element_id") == _fid2), None)

    # The focused widget's type decides fill vs. navigate. Navigation
    # (which element, what order, when to switch tabs) stays with the
    # transformer.
    _fe2_ty  = (_fe2.get("type") or "").lower() if _fe2 else ""
    _fe2_val = (_fe2.get("value") or "").strip() if _fe2 else ""
    ...

    if _t_is_type and self._llm_client:
        # Transformer chose to FILL -> the LLM supplies the value (the WHAT).
        llm_action = self._ask_llm(state)
        ...
        if llm_action.get("action_type") == "done":
            logger.info("LLM: task complete."); break
        ...
        prediction = self._merge(t_pred, t_conf, llm_action, state)
        _decision_maker = "llm"
        logger.info("[OPT2] TRANSFORMER chose TYPE -> LLM value for '%s' -> %r",
                    _flabel, prediction.get("text", "")[:40])
```

At each step the transformer predicts where to act next. When the focused
field is empty and fillable, the language model is asked only for the value to
enter. Otherwise the transformer's predicted target is clicked.

---

## Listing 2. Behavioral cloning transformer model

Source: `components/intelligence/model/transformer.py`

```python
class StateEncoder(nn.Module):
    """Project each UI element to d_model independently, returning
    per-element embeddings and a boolean padding mask."""
    def __init__(self, elem_features: int, d_model: int):
        super().__init__()
        self.proj = nn.Linear(elem_features, d_model)
        self.norm = nn.LayerNorm(d_model)

    def forward(self, state):
        mask = state[..., _REAL_ELEM_FLAG_IDX] > 0.5        # True for real elements
        elem_emb = self.norm(self.proj(state))
        elem_emb = elem_emb * mask.unsqueeze(-1).float()     # zero out padding
        return elem_emb, mask


class TransformerAgentNetwork(nn.Module):
    """Causal Transformer Agent. Interleaves state and action tokens
    [s1, a1, s2, a2, ..., sH] and uses causal self-attention to predict
    the next action from the last state."""

    def __init__(self, elem_features=ELEM_FEATURES, max_elements=128,
                 d_model=128, nhead=4, num_layers=4, dim_feedforward=256,
                 dropout=0.1, num_actions=NUM_ACTIONS, hist_len=4):
        super().__init__()
        ...
        self.state_enc  = StateEncoder(elem_features, d_model)
        self.action_enc = ActionEncoder(num_actions, d_model)
        self.pos_enc    = nn.Embedding(2 * hist_len, d_model)

        enc_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=dim_feedforward,
            dropout=dropout, batch_first=True, norm_first=True,
        )
        self.encoder    = nn.TransformerEncoder(enc_layer, num_layers=num_layers,
                                                enable_nested_tensor=False)
        self.cross_attn = nn.MultiheadAttention(
            embed_dim=d_model, num_heads=nhead, dropout=dropout, batch_first=True,
        )
        self.type_head = nn.Linear(d_model, num_actions)
        # Pointer head: which element to click
        self.click_q = nn.Linear(d_model, d_model)
        self.click_k = nn.Linear(d_model, d_model)
        ...

    def forward(self, states, past_types, past_cont):
        B, H, T, _ = states.shape
        elem_emb, elem_mask = self.state_enc(states.view(B * H, T, -1))
        elem_emb  = elem_emb.view(B, H, T, -1)
        elem_mask = elem_mask.view(B, H, T)

        # Pool each state into one sequence token (masked mean)
        denom = elem_mask.sum(-1, keepdim=True).clamp(min=1).float()
        s = elem_emb.sum(-2) / denom

        # Build sequence [s1, a1, s2, a2, ..., sH]
        seq_len = 2 * H - 1
        tokens  = torch.zeros(B, seq_len, self.d_model, device=states.device)
        tokens[:, 0::2] = s
        if H > 1:
            tokens[:, 1::2] = self.action_enc(past_types, past_cont)
        pos    = torch.arange(seq_len, device=states.device).unsqueeze(0)
        tokens = tokens + self.pos_enc(pos)

        # Causal attention over the demonstration history
        attn_mask = nn.Transformer.generate_square_subsequent_mask(seq_len, device=states.device)
        out  = self.encoder(tokens, mask=attn_mask, is_causal=True)
        last = self.out_norm(out[:, -1])

        # Cross-attention: the last token reads the elements of the current state
        cur_elem_emb, cur_elem_mask = elem_emb[:, -1], elem_mask[:, -1]
        ctx, _ = self.cross_attn(query=last.unsqueeze(1), key=cur_elem_emb,
                                 value=cur_elem_emb, key_padding_mask=~cur_elem_mask,
                                 need_weights=False)
        ctx = self.cross_norm(last + ctx.squeeze(1))

        # Pointer head: score every on-screen element as the next click target
        scale   = self.d_model ** 0.5
        click_q = self.click_q_norm(self.click_q(ctx))
        click_k = self.click_k_norm(self.click_k(cur_elem_emb))
        click_logits = torch.einsum('bd,btd->bt', click_q, click_k) / scale
        click_logits = click_logits.masked_fill(~cur_elem_mask, -1e9)
        ...
        return PolicyOutput(self.type_head(ctx), click_logits,
                            torch.sigmoid(self.key_head(ctx)), src_logits)
```

The model reads the last few screen states and actions in order and scores
every element on the current screen as the next one to act on. That is how
it reproduces the order the user demonstrated.

---

## Listing 3. Perception through the UI Automation accessibility tree

Source: `components/observers/ui_observer/ui_observer.py`, `UIAutomationObserver._walk()`

```python
def _walk(self, ctrl, out, depth, screen_w, screen_h, focused_rect,
          app, window_title, pid, window_role, elem_offset):
    if depth > self.max_depth or len(out) >= self.max_elements_per_window:
        return
    rect = ctrl.BoundingRectangle
    # Skip elements that are off-screen or too small
    if rect.right <= 0 or rect.bottom <= 0:
        return
    if rect.left >= screen_w or rect.top >= screen_h:
        return
    ...
    ctrl_type = ctrl.ControlTypeName or "Unknown"
    name      = (ctrl.Name or "").strip()
    is_offscreen = bool(ctrl.IsOffscreen)

    # Read the element's current value (text fields, comboboxes)
    value = ""
    vp = ctrl.GetPattern(_uia.PatternId.ValuePattern)
    if vp:
        value = (vp.Value or "").strip()
    # Checkboxes expose Toggle, not Value
    if not value:
        tgp = ctrl.GetPattern(_uia.PatternId.TogglePattern)
        if tgp:
            value = "checked" if tgp.ToggleState == _uia.ToggleState.On else ""
    ...
    simple_type = _CTRL_TYPE_MAP.get(ctrl_type, ctrl_type.lower())
    text = name or value
    is_focused = (focused_rect is not None
                  and rect.left == focused_rect.left and rect.top == focused_rect.top
                  and rect.right == focused_rect.right and rect.bottom == focused_rect.bottom)

    if bool(text) or simple_type in _ALWAYS_INCLUDE or is_focused:
        out.append({
            "element_id":   f"elem_{elem_offset + len(out)}",
            "type":         simple_type,
            "bbox":         [rect.left, rect.top, rect.right, rect.bottom],
            "text":         text,
            "value":        value,
            "label":        text,
            "enabled":      bool(ctrl.IsEnabled),
            "visible":      not is_offscreen,
            "focused":      is_focused,
            "source":       "uia",
            "app":          app,
            "window_title": window_title,
            "window_role":  window_role,
            ...
        })

    # Recurse into child controls
    for child in ctrl.GetChildren():
        self._walk(child, out, depth + 1, screen_w, screen_h, focused_rect,
                   app, window_title, pid, window_role, elem_offset)
```

Perception walks the accessibility tree that Windows already exposes for
every application, and records each visible element's type, position, label,
value, and focus state. It needs no screenshots or text recognition.

---

## Listing 4. Canonical element schema shared by all perception adapters

Source: `components/observers/schema.py`

```python
_STATE_REQUIRED   = ("elements", "screen_resolution")
_ELEM_REQUIRED    = ("type", "bbox")
_ELEM_RECOMMENDED = ("label", "value", "window_role")

def validate_state(state, sample=50):
    """Check a snapshot against the perception contract.
    Returns a list of issues (empty = conforms)."""
    issues = []
    for k in _STATE_REQUIRED:
        if k not in state:
            issues.append(f"ERROR: state missing required key '{k}'")

    elems = state.get("elements")
    if not elems:
        issues.append("WARN: state['elements'] is empty — agent sees a blank screen")
        return issues

    bad_types, missing_req = set(), set()
    for e in elems[:sample]:
        for k in _ELEM_REQUIRED:
            if k not in e:
                missing_req.add(k)
        t = e.get("type")
        if t is not None and t not in CONTROL_TYPES:
            bad_types.add(t)
        bb = e.get("bbox")
        if bb is not None and (not isinstance(bb, (list, tuple)) or len(bb) != 4):
            issues.append(f"ERROR: element bbox {bb!r} not a 4-tuple [x1,y1,x2,y2]")
    ...
    return issues
```

Every perception source (the accessibility tree, the browser page, or Excel)
must output elements in this one format. That is what lets the same learning
pipeline run on all three scopes without changes.

---

## Listing 5. Element key encoding with section disambiguation

Source: `components/agent/agent.py`, `LLMAgent._detect_section()` and `LLMAgent._attempt_key()`

```python
def _detect_section(self, state, focused_el):
    """Return the repeated-section name (e.g. 'Driver 2') the focused element
    belongs to, or '' if none. The section is the lowest section pane whose
    top edge is at or above the focused field's vertical centre."""
    if not focused_el or not focused_el.get("bbox"):
        return ""
    fx1, fy1, fx2, fy2 = focused_el["bbox"]
    fy_center = (fy1 + fy2) / 2

    current = ""
    for top, name in self._sorted_section_panes(state):
        if top <= fy_center:
            current = name
        else:
            break
    return current


def _attempt_key(self, elem, elements=None, section=""):
    """Stable key for an element: label first, disambiguated by section
    (e.g. 'Driver 2') or by rank among elements that share the same label."""
    lbl = (elem.get("label") or elem.get("text") or "").strip().lower()
    if not lbl:
        b = elem.get("bbox") or [0, 0, 0, 0]
        return ("@", round((b[0] + b[2]) / 2 / 20) * 20,
                     round((b[1] + b[3]) / 2 / 20) * 20)
    if section:
        return (section, lbl)
    if elements:
        same_label_ids = [id(e) for e in elements
                          if (e.get("label") or e.get("text") or "").strip().lower() == lbl]
        if len(same_label_ids) > 1:
            return (lbl, same_label_ids.index(id(elem)))
    return lbl
```

Each element gets a key built from its label and, when the form repeats a
section (such as Driver 1, 2, and 3), the section it sits in. That way fields
that share a label are not confused with each other.

---

## Listing 6. Converting demonstrations into state-action pairs

Source: `components/trace_translator/trace_translator/trace_translator.py`, `TraceTranslator`

```python
def states_to_trace(self, state_before, state_after, action=None, trace_id=None):
    """Convert two observer states into a single trace step.
    If no action is provided, infers mouse/keyboard from the state diff."""
    diff = self._diff_states(state_before, state_after)

    if action is None:
        mouse, keyboard = self._infer_action_from_diff(diff, state_before, state_after)
    else:
        mouse    = action.get("mouse", {})
        keyboard = action.get("keyboard", {})

    return {
        "trace_id":     trace_id,
        "state_before": state_before,
        "state_after":  state_after,
        "mouse":        mouse,
        "keyboard":     keyboard,
        "diff":         diff,
        ...
    }


def _infer_action_from_diff(self, diff, state_before, state_after):
    """Infer the action from a state diff.
    Priority:
      1. Text grew in an element    -> keyboard (type new characters)
      2. Button/link disappeared    -> click at its centre
      3. Button/link appeared       -> click at its centre
      4. No change                  -> no_op"""
    for change in diff.get("changed", []):
        text_delta = change.get("changes", {}).get("text")
        if text_delta is None:
            continue
        before_text = text_delta.get("before") or ""
        after_text  = text_delta.get("after")  or ""
        if len(after_text) > len(before_text):
            typed = after_text[len(before_text):]
            bbox  = change["after"].get("bbox", [0, 0, 0, 0])
            cx, cy = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
            return ({"action": "click", "x": cx, "y": cy},
                    {"text": typed, "key_count": len(typed)})

    for elem in diff.get("removed", []):
        if elem.get("type") in ("button", "link"):
            bbox = elem.get("bbox", [0, 0, 0, 0])
            cx, cy = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
            return ({"action": "click", "x": cx, "y": cy}, {})
    ...
    return ({}, {})
```

Each recorded step is stored as the screen before, the screen after, and the
action that happened in between. When the action was not captured directly,
it is worked out from what changed on screen.

---

## Listing 7. Executing predicted actions as mouse and keyboard input

Source: `components/agent/executor.py`, `ActionExecutor.execute()` and `ActionExecutor._click()`

```python
def execute(self, prediction):
    action_type = prediction.get("action_type", "no_op")
    ts = datetime.now().isoformat()
    try:
        if action_type == "click":
            pos  = prediction.get("click_position", [0, 0])
            x, y = int(round(pos[0])), int(round(pos[1]))
            # Skip invalid (0,0) targets from a padding element
            if x <= 1 and y <= 1:
                return ExecutionResult("click", None, 0, [], ts, self.dry_run, False, "invalid_coords")
            self._click(x, y)
            return ExecutionResult("click", (x, y), 0, [], ts, self.dry_run, True, "")

        elif action_type == "keyboard":
            key_count  = max(1, int(prediction.get("key_count", 1)))
            keystrokes = list(prediction.get("keystrokes", []))
            text       = prediction.get("text", "")
            issued = self._keyboard(key_count, keystrokes, text,
                                    prediction.get("direct_fill_hwnd"))
            return ExecutionResult("keyboard", None, len(issued), issued, ts, self.dry_run, True, "")

        elif action_type == "scroll":
            ...
        else:
            return ExecutionResult("no_op", None, 0, [], ts, self.dry_run, True, "")
    except Exception as exc:
        return ExecutionResult(action_type, None, 0, [], ts, self.dry_run, False, str(exc))


def _click(self, x, y):
    time.sleep(self.pre_click_delay)
    # Try a direct UI Automation action first, then a semantic Win32 click,
    # and only fall back to moving the real mouse.
    if not self._try_uia_invoke(x, y) and not self._try_semantic_click(x, y):
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.click(x, y)
    time.sleep(self.post_click_delay)
```

Each predicted action becomes a click, keystrokes, or a scroll. Clicks go
through up to three tiers, from the most precise (a direct accessibility
action) down to a real mouse click. Every outcome is logged so the execution
error rate can be measured.

---

## Listing 8. Scope 2 field resolution with abstention

Source: `components/scope2/resolver/assign.py`, `resolve()`

```python
TAU   = 0.6    # minimum score to accept a pair
DELTA = 0.15   # minimum margin over the runner-up

def resolve(columns, fields, matrix, derived_labels=(), tau=TAU, delta=DELTA):
    """Assign columns to mappable fields one-to-one, abstaining when unclear."""
    buckets  = partition(fields, derived_labels)
    mappable = buckets[BUCKET_MAPPABLE]
    mappable_indices = [fields.index(f) for f in mappable]
    ...
    scores = np.array([[matrix[ci][fi] for fi in mappable_indices]
                       for ci in range(len(columns))], dtype=float)

    # Hungarian minimises, so cost is -log(score)
    cost = -np.log(scores + EPSILON)

    # Pad so unequal counts are handled
    rows, cols = cost.shape
    size = max(rows, cols)
    padded = np.full((size, size), -np.log(EPSILON))
    padded[:rows, :cols] = cost

    row_index, col_index = linear_sum_assignment(padded)
    column_margins = margins(scores.tolist())

    for ci, fj in zip(row_index, col_index):
        if ci >= rows or fj >= cols:
            continue  # padding, not a real pair
        score  = float(scores[ci][fj])
        margin = float(column_margins[ci])
        # Accept only if the score is high AND it clearly beats the runner-up
        status = STATUS_AUTO if (score >= tau and margin >= delta) else STATUS_ABSTAIN
        mapping.assignments.append(Assignment(
            source_header=columns[ci].header, target_label=mappable[fj].label,
            score=score, margin=margin, status=status,
        ))
    ...
    return mapping
```

Spreadsheet columns are matched one-to-one to web-form fields. A match is
accepted only when its score is high and it clearly beats the second-best
option. Otherwise the system abstains instead of guessing, which is how it
handles changed portal layouts safely.

---

## Listing 9. Scope 3 inbox decision layers

Source: `components/inbox_router/inbox_agent.py`, `InboxAgent.decide()`

```python
def decide(self, message):
    if self._mode == "reasoning":
        return self._llm_decision(message, pattern=None, rule_hint=RuleDecision())

    # 1. Trained model: accept only when highly confident
    fast = self._try_fast_fill(message)
    if fast is not None:
        return fast

    # 2. Learned sender and content rules
    rule_result = self._rules.classify(message)
    if rule_result.decision:
        return InboxDecision(
            decision=rule_result.decision, confidence=rule_result.confidence,
            rationale=rule_result.rationale, layer="rule",
            capsule_name=rule_result.capsule_name, forward_to=rule_result.forward_to,
        )

    # 3. Habits-only mode: hand the message to the person
    if self._mode == "habits":
        return InboxDecision(
            decision=UNDECIDED, confidence=0.0,
            rationale="No confident habit for this sender yet, so it is left for you to decide.",
            layer="none",
        )

    # 4. Language model reasons over the message and the sender's pattern
    return self._llm_decision(message, self._profile.pattern_for(message.sender_email), rule_result)


def _try_fast_fill(self, message):
    pattern = self._profile.pattern_for(message.sender_email)
    feats = extract(message, pattern, self._centroids)
    probs = self._model.probabilities(torch.tensor([feats], dtype=torch.float32))[0]
    top_idx  = int(torch.argmax(probs))
    top_conf = float(probs[top_idx])
    if top_conf < self._high_confidence:
        return None                       # not confident enough: fall through
    decision = DECISIONS_ORDER[top_idx]
    ...
    return InboxDecision(
        decision=decision, confidence=top_conf,
        rationale=f"Trained model is {top_conf:.0%} confident, based on similar past emails.",
        layer="fast_fill", ...
    )
```

Each email passes through up to four layers, in order: the trained model
(used only when it is very confident), learned sender rules, a handoff to the
person, or the language model. The layer that decided is recorded with every
decision.
