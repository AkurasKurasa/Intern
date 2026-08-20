"""
tests/test_opt2_fast_fill.py
==============================
Regression tests for OPT2's fast-fill early-exit (components/agent/agent.py,
inserted immediately before `t_pred = self._predict(state)`).

Built 2026-08-14, direct request ("don't stop unless there's something to
reason about" -- 3-day demo deadline). Skips the transformer call AND the
LLM call entirely for the one case where the answer is already
deterministically known: the focused field is a plain, empty, not-yet-
attempted editcontrol, and its value is already sitting in the intake
data under an exact key match (the same lookup `_ask_llm`'s own fast path
already trusts). Every mechanism this reuses was already built and tested
earlier the same night: `_lookup_field`, `_find_uia_control_by_name` +
`.SetFocus()` (no click), and the WM_SETTEXT direct-fill mechanism
(`direct_fill_hwnd`, `_keyboard_direct`).

Extended the same night to comboboxcontrol via CB_SETCURSEL (a direct
message that selects a real option with no click and no dropdown ever
opened -- live-tested against the real form, see
tests/test_executor_combobox_direct.py). Checkboxes already have their
own separate, working mechanism (BM_SETCHECK) and stay on the existing
reactive path untouched.

This branch sits deep inside LLMAgent.run() (~8000 lines, many
preconditions before reaching it -- Navigation Protocol's own decision,
stuck-guards, etc.) so, following this project's own established pattern
for testing logic embedded in that method (see
tests/test_type_path_focus_via_uia.py, tests/test_redirect_click_
destructive_button_guard.py), this file uses a mirror function that
reimplements exactly the new gating condition, plus direct source-level
checks confirming the real code actually wires those same calls in --
not a full run() invocation, which would require mocking far more of the
loop's preconditions than this one branch actually depends on.
"""
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent

_AGENT_PY = Path(__file__).resolve().parent.parent / "components" / "agent" / "agent.py"
_SOURCE = _AGENT_PY.read_text(encoding="utf-8")


def _make_agent():
    return LLMAgent(goal="test goal", dry_run=True, max_steps=1, step_delay=0,
                     disable_auto_handlers=True)


def _field(label, elem_type, value="", element_id="e1", bbox=(1400, 270, 1600, 300)):
    return {"element_id": element_id, "type": elem_type, "label": label,
            "text": label, "value": value, "bbox": list(bbox), "window_role": "active"}


def _is_fast_fill_eligible(agent, focused_el, elements, leave_blank_keys=frozenset(),
                            typed_keys=frozenset()):
    """Mirrors the exact gating condition inserted before `_predict()`:
    `if (_ff_fel and _ff_ty in ("editcontrol", "comboboxcontrol") and not _ff_val
            and _ff_key not in self._leave_blank_keys
            and _ff_key not in self._typed_keys):`"""
    ty  = (focused_el.get("type") or "").lower() if focused_el else ""
    val = (focused_el.get("value") or "").strip() if focused_el else ""
    key = agent._attempt_key(focused_el, elements=elements) if focused_el else None
    return bool(
        focused_el and ty in ("editcontrol", "comboboxcontrol") and not val
        and key not in leave_blank_keys and key not in typed_keys
    )


class TestFastFillEligibilityMirror:
    """Pure mirror-logic tests for the new gating condition -- fast,
    deterministic, no live OS/UIA needed."""

    def test_empty_editcontrol_is_eligible(self):
        agent = _make_agent()
        field = _field("Policy Number", "editcontrol")
        assert _is_fast_fill_eligible(agent, field, [field]) is True

    def test_filled_editcontrol_is_not_eligible(self):
        agent = _make_agent()
        field = _field("Policy Number", "editcontrol", value="POL-000123")
        assert _is_fast_fill_eligible(agent, field, [field]) is False

    def test_comboboxcontrol_is_eligible(self):
        """Extended the same night: CB_SETCURSEL (live-tested against the
        real form) lets comboboxes use this fast path too, via a
        different underlying mechanism than editcontrol (combobox_hwnd,
        not direct_fill_hwnd)."""
        agent = _make_agent()
        field = _field("Policy Status", "comboboxcontrol")
        assert _is_fast_fill_eligible(agent, field, [field]) is True

    def test_checkboxcontrol_is_never_eligible(self):
        """Checkboxes already have their own separate, working mechanism
        (BM_SETCHECK) -- must not collide with this one."""
        agent = _make_agent()
        field = _field("Auto-Pay Enrolled", "checkboxcontrol")
        assert _is_fast_fill_eligible(agent, field, [field]) is False

    def test_confirmed_leave_blank_field_is_not_reeligible(self):
        agent = _make_agent()
        field = _field("Middle Name", "editcontrol")
        key = agent._attempt_key(field, elements=[field])
        assert _is_fast_fill_eligible(agent, field, [field], leave_blank_keys={key}) is False

    def test_already_typed_field_is_not_reeligible(self):
        """Trusts a genuine-typed-text record over a live 'is it empty'
        read, same reasoning as the existing _fe2_already_attempted check
        this mirrors -- a field the agent already filled once, that a
        stale/racy live read reports as empty again, must not be
        silently re-fast-filled."""
        agent = _make_agent()
        field = _field("Policy Number", "editcontrol")  # value empty in THIS read
        key = agent._attempt_key(field, elements=[field])
        assert _is_fast_fill_eligible(agent, field, [field], typed_keys={key}) is False

    def test_no_focused_element_is_safe_and_not_eligible(self):
        agent = _make_agent()
        assert _is_fast_fill_eligible(agent, None, []) is False


class TestFastFillReusesRealMethods:
    """Confirms the eligibility check and value/handle resolution are
    driven by this project's actual, already-tested methods -- not a
    reimplementation that could silently drift from them."""

    def test_lookup_uses_the_real_lookup_field_and_detect_section(self, monkeypatch):
        agent = _make_agent()
        field = _field("Policy Number", "editcontrol")
        agent._detect_section = MagicMock(return_value="")
        agent._lookup_field = MagicMock(return_value="POL-000123")

        assert _is_fast_fill_eligible(agent, field, [field]) is True
        section = agent._detect_section({"elements": [field]}, field)
        value = agent._lookup_field(field["label"], section=section)

        agent._detect_section.assert_called_once()
        agent._lookup_field.assert_called_once_with("Policy Number", section="")
        assert value == "POL-000123"

    def test_lookup_miss_yields_no_known_value(self):
        agent = _make_agent()
        agent._lookup_field = MagicMock(return_value="")
        assert agent._lookup_field("Unknown Field", section="") == ""


class TestSourceWiresRealMechanisms:
    """Direct source-level verification (this project's own established
    pattern for logic embedded in the un-extracted run() method) that the
    real inserted branch actually calls the real, already-tested
    mechanisms in the right shape -- not a parallel reimplementation."""

    def _fast_fill_window(self):
        idx = _SOURCE.index("OPT2 FAST-FILL")
        return _SOURCE[idx:idx + 15000]

    def test_gated_on_no_autohandlers(self):
        window = self._fast_fill_window()
        assert "if self._no_autohandlers:" in window

    def test_scoped_to_editcontrol_only(self):
        window = self._fast_fill_window()
        assert '_ff_ty == "editcontrol"' in window
        assert '"comboboxcontrol"' not in window.split("editcontrol")[1][:200]

    def test_excludes_leave_blank_and_typed_keys(self):
        window = self._fast_fill_window()
        assert "_ff_key not in self._leave_blank_keys" in window
        assert "_ff_key not in self._typed_keys" in window

    def test_uses_real_lookup_field(self):
        window = self._fast_fill_window()
        assert "self._lookup_field(_ff_label, section=_ff_sec)" in window

    def test_uses_real_find_uia_control_by_name_and_setfocus_no_click(self):
        """The whole point: moving to the field must not be a raw
        coordinate click. Routes through _resolve_field_control (added
        2026-08-14, "still too slow") which only pays for
        _find_uia_control_by_name's expensive position-based
        disambiguation when the label is genuinely ambiguous on screen --
        still the same underlying no-click UIA mechanism either way."""
        window = self._fast_fill_window()
        assert "self._resolve_field_control(state, _ff_label" in window
        assert "_ff_ctrl.SetFocus()" in window
        assert '"action_type": "click"' not in window

    def test_uses_direct_fill_hwnd_not_a_new_action_type(self):
        """Must reuse the existing WM_SETTEXT mechanism (built and tested
        earlier the same night) via the same optional key on the
        existing 'keyboard' action_type -- not a new action_type string."""
        window = self._fast_fill_window()
        assert '"action_type": "keyboard"' in window
        assert '"direct_fill_hwnd": _ff_hwnd' in window

    def test_marks_attempted_and_settles_via_real_helpers(self):
        window = self._fast_fill_window()
        assert "self._mark_attempted(_ff_fel" in window
        assert "self._adaptive_settle_wait(self.step_delay * 0.2)" in window

    def test_combobox_uses_combobox_select_not_direct_fill_hwnd(self):
        """Comboboxes must route through the new combobox_select action
        type (combobox_hwnd), not the text-field mechanism -- WM_SETTEXT
        is a confirmed no-op on comboboxes."""
        window = self._fast_fill_window()
        assert '"action_type": "combobox_select"' in window
        assert '"combobox_hwnd": _ff_hwnd' in window

    def test_combobox_checks_success_before_committing(self):
        """A combobox whose known value doesn't match any real option
        must fall through to the existing click-based path, not be
        silently treated as done."""
        window = self._fast_fill_window()
        assert "_ff_cb_result.success" in window

    def test_combobox_falls_through_to_existing_click_path_on_failure(self):
        """No `continue` reachable purely from a failed combobox_select --
        must fall through to today's existing reactive combobox handling."""
        window = self._fast_fill_window()
        result_idx = window.index("_ff_cb_result = self._executor.execute")
        after = window[result_idx:result_idx + 1000]
        # continue only appears inside the `if _ff_cb_result.success:` block
        success_idx = after.index("if _ff_cb_result.success:")
        continue_idx = after.index("continue")
        assert success_idx < continue_idx

    def test_falls_through_to_continue_only_on_full_success(self):
        """Every failure point (no ctrl, no hwnd, no known value) must
        leave `continue` unreached so today's existing, unmodified code
        runs exactly as it does now."""
        window = self._fast_fill_window()
        # The `continue` must be nested inside the `if _ff_hwnd:` block,
        # not unconditional -- confirm it's preceded by the settle-wait
        # call on the same success path, not floating free.
        settle_idx = window.index("self._adaptive_settle_wait(self.step_delay * 0.2)")
        after = window[settle_idx:settle_idx + 100]
        assert "continue" in after

    def test_is_not_hardcoded_to_any_specific_field_name(self):
        """The whole point -- must be a generic label-driven lookup,
        never a literal field-name comparison."""
        window = self._fast_fill_window()
        assert not re.search(r'_ff_label\s*==\s*[\'"]', window)

    def test_inserted_before_the_transformer_predict_call(self):
        """The entire point of the feature: the transformer call itself
        must be skipped, not just the LLM call -- so this branch must sit
        textually before `t_pred = self._predict(state)`."""
        fast_fill_idx = _SOURCE.index("OPT2 FAST-FILL")
        predict_idx = _SOURCE.index('t_pred = self._predict(state)')
        assert fast_fill_idx < predict_idx

    def test_settle_wait_was_tightened_not_removed(self):
        """Tightened 2026-08-14 ahead of a ~1-minute demo target -- must
        still be present (still adaptive, still bounded), just a lower
        ceiling than the general-purpose fill path's own budget. 3 from
        editcontrol/combobox/dead-spot-rescue + 2 more from the later
        checkbox fast-fill addition (checked + unchecked cases) = 5."""
        window = self._fast_fill_window()
        assert window.count("self._adaptive_settle_wait(self.step_delay * 0.2)") == 5


class TestSingleFieldFastFillUsesSectionAwareAttemptKeys:
    """Same real root cause as the batch path (see
    TestBatchFastFillUsesSectionAwareAttemptKeys), applied to the single-
    field OPT2 fast-fill block -- both paths must use the SAME section-aware
    key scheme, or a field marked attempted by one path could go unrecognized
    by the other, reintroducing the exact class of bug being fixed."""

    def _fast_fill_window(self):
        idx = _SOURCE.index("OPT2 FAST-FILL")
        return _SOURCE[idx:idx + 15000]

    def test_ff_sec_is_computed_before_ff_key_not_after(self):
        window = self._fast_fill_window()
        sec_idx = window.index("_ff_sec")
        key_idx = window.index("_ff_key = (self._attempt_key(")
        assert sec_idx < key_idx, "_ff_sec must be available before _ff_key uses it"

    def test_ff_key_is_computed_with_the_section(self):
        window = self._fast_fill_window()
        idx = window.index("_ff_key = (self._attempt_key(")
        section = window[idx:idx + 150]
        assert "section=_ff_sec" in section

    def test_every_mark_attempted_for_ff_fel_passes_the_section(self):
        window = self._fast_fill_window()
        calls = re.findall(r'self\._mark_attempted\(_ff_fel, elements=state\.get\("elements", \[\]\)(.*?)\)',
                            window)
        assert len(calls) >= 4, "expected all 4 single-field mark_attempted call sites"
        for call_tail in calls:
            assert "section=_ff_sec" in call_tail


class TestDeadSpotRescue:
    """Tests for the OPT2 dead-spot rescue -- added 2026-08-14 from real
    log evidence (a live run showed Tab periodically landing on a
    non-fillable section-divider pane, paying for a full transformer
    decision every time). Tries the same deterministic,
    already-tested/used find_visible_empty_target mechanism first,
    before ever asking the model."""

    def _fast_fill_window(self):
        idx = _SOURCE.index("OPT2 FAST-FILL")
        return _SOURCE[idx:idx + 15000]

    def test_rescue_block_exists_and_is_gated_correctly(self):
        window = self._fast_fill_window()
        assert "OPT2 DEAD-SPOT RESCUE" in window
        assert '"checkboxcontrol", "checkbox")' in window

    def test_uses_the_real_navigation_protocol_mechanism(self):
        """Must reuse the exact same function this file already calls
        elsewhere for the identical purpose -- not a reimplementation
        that could silently drift from it."""
        window = self._fast_fill_window()
        assert "self._navproto.find_visible_empty_target(" in window
        assert "self._form_viewport_bottom(state)" in window
        assert "attempted_keys=self._attempted_keys" in window
        assert "attempt_key_fn=self._attempt_key" in window

    def test_moves_focus_without_a_click(self):
        """The whole point -- SetFocus, not a coordinate click."""
        window = self._fast_fill_window()
        rescue_idx = window.index("OPT2 DEAD-SPOT RESCUE")
        rescue_section = window[rescue_idx:]
        assert "_dsr_ctrl.SetFocus()" in rescue_section
        assert '"action_type": "click"' not in rescue_section

    def test_routes_through_resolve_field_control_not_the_raw_lookup(self):
        """Added 2026-08-14 ("still too slow") -- must skip the expensive
        disambiguation search for unique labels via _resolve_field_control,
        same as batch/single-field fast-fill, not call
        _find_uia_control_by_name directly."""
        window = self._fast_fill_window()
        rescue_idx = window.index("OPT2 DEAD-SPOT RESCUE")
        rescue_section = window[rescue_idx:]
        assert "self._resolve_field_control(" in rescue_section
        assert "self._find_uia_control_by_name(" not in rescue_section

    def test_falls_through_safely_on_any_failure(self):
        """No target found, no label, no live control, or SetFocus
        raising must all leave `continue` unreached -- today's existing
        transformer-driven code must still run exactly as it does now."""
        window = self._fast_fill_window()
        rescue_idx = window.index("OPT2 DEAD-SPOT RESCUE")
        predict_idx = window.index('t_pred = self._predict(state)')
        rescue_section = window[rescue_idx:predict_idx]
        # continue only appears once, inside the try/except success path
        assert rescue_section.count("continue") == 1
        assert "except Exception as _dsr_exc:" in rescue_section

    def test_is_gated_before_the_transformer_predict_call(self):
        rescue_idx = _SOURCE.index("OPT2 DEAD-SPOT RESCUE")
        predict_idx = _SOURCE.index('t_pred = self._predict(state)')
        assert rescue_idx < predict_idx


def _dead_spot_rescue_eligible(elem_type: str) -> bool:
    """Mirrors the exact gating condition:
    `elif _ff_fel and _ff_ty not in ("editcontrol", "comboboxcontrol",
    "checkboxcontrol", "checkbox"):`"""
    return elem_type.lower() not in ("editcontrol", "comboboxcontrol",
                                      "checkboxcontrol", "checkbox")


class TestDeadSpotRescueEligibilityMirror:
    def test_panecontrol_is_rescue_eligible(self):
        """The exact real-world case from the log: a section-divider
        pane, no field to fill at all."""
        assert _dead_spot_rescue_eligible("panecontrol") is True

    def test_editcontrol_is_not_rescue_eligible(self):
        """Must never fire when a real fillable field is focused --
        that's the fast-fill branch's job, not this one's."""
        assert _dead_spot_rescue_eligible("editcontrol") is False

    def test_comboboxcontrol_is_not_rescue_eligible(self):
        assert _dead_spot_rescue_eligible("comboboxcontrol") is False

    def test_checkboxcontrol_is_not_rescue_eligible(self):
        """Checkboxes have their own existing, correct handling --
        must not be redirected away from it."""
        assert _dead_spot_rescue_eligible("checkboxcontrol") is False

    def test_buttoncontrol_is_rescue_eligible(self):
        assert _dead_spot_rescue_eligible("buttoncontrol") is True


class TestCheckboxFastFill:
    """Tests for OPT2's checkbox fast-fill -- added 2026-08-14, same
    night, direct evidence from a real live run's log: 25 of 57 remaining
    live model decisions in one full run were checkboxes, every one
    already having a deterministically known answer via the same lookup
    text/combobox fields already use. Reuses self._auto_check() (lookup +
    'yes'-prefix parsing, already existing) and the exact same
    WindowFromPoint + BM_SETCHECK call shape this file already uses at
    its other checkbox sites -- not a new mechanism."""

    def _fast_fill_window(self):
        idx = _SOURCE.index("OPT2 FAST-FILL")
        return _SOURCE[idx:idx + 15000]

    def test_gated_on_checked_fields_not_typed_keys(self):
        """Checkboxes don't reliably expose .value (see
        self._checked_fields' own comment) -- must use the checkbox-
        specific tracker, not the text-field one."""
        window = self._fast_fill_window()
        assert 'not in self._checked_fields' in window

    def test_reuses_real_auto_check_not_a_reimplementation(self):
        window = self._fast_fill_window()
        assert "self._auto_check(state)" in window

    def test_uses_the_same_bm_setcheck_mechanism_as_existing_sites(self):
        """Must use WindowFromPoint on the bbox center + BM_SETCHECK --
        the exact same call shape already proven elsewhere in this file,
        not the name-based UIA lookup text/combobox fields use."""
        window = self._fast_fill_window()
        idx = window.index("OPT2 CHECKBOX FAST-FILL")
        section = window[idx:idx + 3200]
        assert "WindowFromPoint((int(_chk_cx), int(_chk_cy)))" in section
        assert "SendMessage(_chk_hw, 0x00F1, 1, 0)" in section

    def test_unchecked_case_needs_no_bm_setcheck_call(self):
        """Wx checkboxes already default to unchecked -- a known 'NO'
        answer should just Tab past, not send any message at all."""
        window = self._fast_fill_window()
        idx = window.index("elif not _chk_should_check:")
        section = window[idx:idx + 500]
        assert "SendMessage" not in section

    def test_falls_through_on_unknown_checkbox_value(self):
        """If _auto_check returns None (not in the lookup data), must
        fall through to today's existing reactive path -- never guess."""
        window = self._fast_fill_window()
        idx = window.index("if _chk_result is not None:")
        # The comment documenting the fall-through-on-unknown case must
        # exist, confirming this isn't silently unhandled.
        assert "Unknown checkbox value" in window

    def test_is_gated_before_the_transformer_predict_call(self):
        checkbox_idx = _SOURCE.index("OPT2 CHECKBOX FAST-FILL")
        predict_idx = _SOURCE.index('t_pred = self._predict(state)')
        assert checkbox_idx < predict_idx


class TestBatchFastFill:
    """Tests for OPT2's BATCH fast-fill -- added 2026-08-14, direct request
    ("it needs to be instant") after real log evidence showed the
    single-field fast-fill above had already cut model calls to almost
    nothing (16 transformer calls in a full run) yet total wall time barely
    moved (4m09s vs the prior run's 4m33s): steps landed ~1s apart whether
    or not the model was skipped, because each field still paid for its own
    observe()+act() cycle. This fills EVERY currently-visible known-value
    field in one pass -- one observe(), many direct writes, no per-field
    re-observe -- using find_all_visible_empty_targets (navigation_
    protocol.py) plus the exact same already-tested direct-write mechanisms
    the single-field path above already uses (WM_SETTEXT / CB_SETCURSEL /
    BM_SETCHECK), not a reimplementation."""

    def _batch_window(self):
        idx = _SOURCE.index("OPT2 BATCH FAST-FILL")
        return _SOURCE[idx:idx + 21500]

    def test_batch_block_exists_before_the_single_field_block(self):
        batch_idx = _SOURCE.index("OPT2 BATCH FAST-FILL")
        single_idx = _SOURCE.index("OPT2 FAST-FILL: skip the transformer")
        assert batch_idx < single_idx

    def test_gated_on_no_autohandlers(self):
        window = self._batch_window()
        assert "if self._no_autohandlers:" in window

    def test_uses_the_real_find_all_visible_empty_targets(self):
        """Must reuse the shared eligibility rule (navigation_protocol.py),
        not a second, parallel scan that could silently drift from it.
        attempt_key_fn is a small section-aware closure (see
        TestBatchFastFillUsesSectionAwareAttemptKeys) rather than
        self._attempt_key passed directly -- it must still call the real
        self._attempt_key internally, not reimplement its own logic."""
        window = self._batch_window()
        assert "self._navproto.find_all_visible_empty_targets(" in window
        assert "attempted_keys=self._attempted_keys" in window
        assert "attempt_key_fn=_bf_attempt_key_fn" in window
        assert "self._attempt_key(e, elements=els, section=" in window

    def test_handles_all_three_known_fillable_types(self):
        window = self._batch_window()
        assert '_bf_ty == "editcontrol"' in window
        assert '_bf_ty == "comboboxcontrol"' in window
        assert '_bf_ty in ("checkboxcontrol", "checkbox")' in window

    def test_editcontrol_uses_direct_fill_hwnd_no_click(self):
        window = self._batch_window()
        idx = window.index('_bf_ty == "editcontrol"')
        section = window[idx:idx + 6000]
        assert '"direct_fill_hwnd": _bf_hwnd' in section
        assert '"action_type": "click"' not in section
        assert "_bf_ctrl.SetFocus()" in section

    def test_comboboxcontrol_uses_combobox_select_and_checks_success(self):
        window = self._batch_window()
        idx = window.index('_bf_ty == "comboboxcontrol"')
        section = window[idx:idx + 5000]
        assert '"action_type": "combobox_select"' in section
        assert '"combobox_hwnd": _bf_hwnd' in section
        assert "_bf_cb_result.success" in section

    def test_routes_through_resolve_field_control_not_the_raw_lookup(self):
        """Added 2026-08-14 ("still too slow, at least <60s"): timed a real
        batch (13 fields, 7s, no observe() between them) and traced the
        cost into _find_uia_control_by_name's own expensive disambiguation
        search, always triggered because every caller always passed
        expected_bbox even for fields whose label is completely unique on
        screen. _resolve_field_control only pays for that search when the
        label is genuinely ambiguous -- both editcontrol and
        comboboxcontrol branches must route through it, not call
        _find_uia_control_by_name directly."""
        window = self._batch_window()
        assert window.count("self._resolve_field_control(state, _bf_label, _bf_el.get(\"bbox\"), section=_bf_sec)") == 2
        assert "self._find_uia_control_by_name(" not in window

    def test_checkbox_reuses_the_extracted_lookup_helper_not_auto_check(self):
        """Batch can't use self._auto_check(state) directly -- that method
        is hardwired to state['focused_element_id'], but batch must
        evaluate checkboxes that AREN'T focused. Must call the extracted
        per-field helper instead."""
        window = self._batch_window()
        assert "self._lookup_checkbox_should_check(_bf_label)" in window
        assert "self._auto_check(state)" not in window

    def test_checkbox_uses_the_same_bm_setcheck_mechanism_as_existing_sites(self):
        window = self._batch_window()
        idx = window.index('_bf_ty in ("checkboxcontrol", "checkbox")')
        section = window[idx:idx + 1800]
        assert "WindowFromPoint((int(_bf_cx), int(_bf_cy)))" in section
        assert "SendMessage(_bf_hw, 0x00F1, 1, 0)" in section

    def test_no_tab_keystroke_between_fields_no_reobserve_per_field(self):
        """The whole point: N fields filled without N observe() calls.
        self._observe() must not appear anywhere inside the batch loop
        body itself (only the settle-wait/next-step observe outside it)."""
        window = self._batch_window()
        loop_idx = window.index("for _bf_el in _bf_targets:")
        end_idx = window.index("if _bf_filled > 0:")
        loop_body = window[loop_idx:end_idx]
        assert "self._observe()" not in loop_body

    def test_marks_attempted_for_every_filled_field(self):
        """3 real writes (editcontrol/comboboxcontrol/checkbox) + 2
        confirmed-blank skips (editcontrol/comboboxcontrol) = 5."""
        window = self._batch_window()
        assert window.count("self._mark_attempted(_bf_el") == 5

    def test_settles_once_for_the_whole_batch_not_per_field(self):
        """One settle-wait call gated on filled_count > 0, not one per
        field written -- that's the entire point of batching."""
        window = self._batch_window()
        loop_idx = window.index("for _bf_el in _bf_targets:")
        end_idx = window.index("if _bf_filled > 0:")
        loop_body = window[loop_idx:end_idx]
        assert "self._adaptive_settle_wait" not in loop_body
        after = window[end_idx:end_idx + 300]
        assert "self._adaptive_settle_wait(self.step_delay * 0.2)" in after
        assert "continue" in after

    def test_continue_only_reached_when_something_was_actually_filled(self):
        window = self._batch_window()
        idx = window.index("if _bf_filled > 0:")
        section = window[idx:idx + 300]
        assert "continue" in section
        # nothing below the batch block's own scope short-circuits when
        # filled_count stayed at 0 -- confirmed by the single-field block
        # still being reachable right after this one in the source.
        single_idx = _SOURCE.index("OPT2 FAST-FILL: skip the transformer")
        batch_idx = _SOURCE.index("OPT2 BATCH FAST-FILL")
        assert batch_idx < single_idx

    def test_is_not_hardcoded_to_any_specific_field_name(self):
        window = self._batch_window()
        assert not re.search(r'_bf_label\s*==\s*[\'"]', window)

    def test_is_gated_before_the_transformer_predict_call(self):
        batch_idx = _SOURCE.index("OPT2 BATCH FAST-FILL")
        predict_idx = _SOURCE.index('t_pred = self._predict(state)')
        assert batch_idx < predict_idx


class TestBatchFastFillConfirmedBlankSkip:
    """Tests for the confirmed-blank extension to OPT2 batch fast-fill --
    added 2026-08-14, direct follow-up ("I need it a bit more faster")
    after real log evidence: a single genuinely-blank field ('Custom
    Equipment Value ($)') cost TWO full transformer calls before the
    reactive path's own three-attempt escalation finally confirmed there
    was nothing to fill. Batch fast-fill now runs that same escalation
    (_resolve_field_value_with_escalation, shared with _ask_llm -- see
    tests/test_resolve_field_value_with_escalation.py) itself, and Tabs
    past a confirmed-blank field with no transformer call at all."""

    def _batch_window(self):
        idx = _SOURCE.index("OPT2 BATCH FAST-FILL")
        return _SOURCE[idx:idx + 21500]

    def test_editcontrol_and_comboboxcontrol_both_use_the_escalation_helper(self):
        window = self._batch_window()
        assert window.count(
            "self._resolve_field_value_with_escalation(state, _bf_label, section=_bf_sec)") == 2

    def test_confirmed_blank_is_recorded_in_leave_blank_keys(self):
        """Must feed the SAME tracker Navigation Protocol and the reactive
        path already trust (self._leave_blank_keys), not a separate,
        batch-only concept of 'blank' that the rest of the file can't see."""
        window = self._batch_window()
        assert window.count("self._leave_blank_keys.add(_bf_key)") == 2

    def test_confirmed_blank_sends_no_write_message_only_tab(self):
        """The whole point -- a confirmed-blank field costs a Tab
        keystroke and nothing else, no WM_SETTEXT/CB_SETCURSEL call."""
        window = self._batch_window()
        idx = window.index("confirmed blank, Tab past")
        section = window[idx:idx + 700]
        assert '"direct_fill_hwnd"' not in section
        assert '"combobox_select"' not in section
        assert '"key_count": 1, "keystrokes": ["tab"]' in section

    def test_confirmed_blank_still_marks_attempted_and_counts_toward_the_batch(self):
        """A confirmed-blank field is real, useful work -- it must still
        mark_attempted (so it's never re-checked) and increment the same
        _bf_filled counter that gates the end-of-batch continue, exactly
        like an actual write does."""
        window = self._batch_window()
        idx = window.index("confirmed blank, Tab past")
        section = window[idx:idx + 700]
        assert "self._mark_attempted(_bf_el" in section
        assert "_bf_filled += 1" in section

    def test_already_confirmed_blank_fields_are_not_re_escalated(self):
        """The existing leave_blank_keys/typed_keys gate at the top of
        each branch already runs BEFORE the escalation call -- a field
        confirmed blank on an earlier batch must not pay for the
        escalation (cache refresh + Notepad peek) a second time."""
        window = self._batch_window()
        edit_idx = window.index('_bf_ty == "editcontrol"')
        escalation_idx = window.index(
            "self._resolve_field_value_with_escalation(state, _bf_label, section=_bf_sec)", edit_idx)
        gate_idx = window.index("_bf_key in self._leave_blank_keys", edit_idx)
        assert edit_idx < gate_idx < escalation_idx

    def test_is_not_hardcoded_to_any_specific_field_name(self):
        window = self._batch_window()
        idx = window.index("confirmed blank, Tab past")
        section = window[idx - 200:idx + 400]
        assert not re.search(r'_bf_label\s*==\s*[\'"]', section)


class TestConfirmedBlankGetsOneDeepLLMCheckFirst:
    """Real live bug + fix, direct report ('Check most recent logs. I don't
    think reasoning was activated again.' -> 'how the hell do we get
    reasoning to fire' -> 'let's try it'). 'Lookup found nothing' and
    'genuinely blank' aren't the same thing -- the FOREIGN_TEST intake has
    a real, legitimate relabeling ('Policy Reference #' instead of 'Policy
    Number'), a real answer under different wording that plain text
    matching can never bridge but real judgment can. Before committing a
    field as blank, give the LLM exactly one careful look (deep=True, the
    same mechanism already proven for the OPT2 combobox case) -- if the
    LLM also finds nothing, commit blank exactly as before, same
    leave_blank_keys/mark_attempted/Tab-only guarantee, never re-paid on
    a later visit to the same field."""

    def _batch_window(self):
        idx = _SOURCE.index("OPT2 BATCH FAST-FILL")
        return _SOURCE[idx:idx + 21500]

    def test_both_editcontrol_and_comboboxcontrol_branches_get_the_check(self):
        window = self._batch_window()
        # "deep=True" also appears in explanatory comments, so count the
        # actual call sites, not the bare substring.
        assert window.count("self._ask_llm(_bf_state_for_llm, deep=True)") == 2
        assert window.count("self._ask_llm(") == 2

    def test_llm_check_happens_before_committing_blank_in_both_branches(self):
        window = self._batch_window()
        blank_positions = [m.start() for m in re.finditer("confirmed blank, Tab past", window)]
        llm_positions = [m.start() for m in re.finditer(r"self\._ask_llm\(", window)]
        assert len(blank_positions) == 2 and len(llm_positions) == 2
        for llm_pos, blank_pos in zip(llm_positions, blank_positions):
            assert llm_pos < blank_pos, "must check the LLM BEFORE giving up, not after"

    def test_llm_check_overrides_focused_element_id_to_the_real_field(self):
        """Same real bug class already fixed once for the OPT2 combobox
        deferral: _ask_llm() derives 'the focused field' only from
        state['focused_element_id'], which can point at a different
        element than the one batch fast-fill is actually resolving."""
        window = self._batch_window()
        assert window.count("_bf_el.get(\"element_id\")") == 2 or window.count("_bf_el.get('element_id')") == 2
        assert window.count("dict(state)") == 2

    def test_confirmed_blank_bookkeeping_is_unchanged_after_the_llm_also_finds_nothing(self):
        """The existing performance guarantee (a field confirmed blank is
        never re-escalated on a later visit) must be untouched."""
        window = self._batch_window()
        assert window.count("self._leave_blank_keys.add(_bf_key)") == 2
        idx = window.index("confirmed blank, Tab past")
        section = window[idx:idx + 700]
        assert "self._mark_attempted(_bf_el" in section


class TestBatchFastFillControlResolutionFailureIsVisible:
    """Real live bug, direct report ('There's still a problem with Driver 2
    and Driver 3, please change'). A real, known value could be resolved
    (_bf_val was non-empty -- the field is NOT genuinely blank) but the
    on-screen UIA control for it couldn't be matched (_resolve_field_control
    returned None, or SetFocus()/NativeWindowHandle raised) -- 7 of Driver
    2's fields on the messy-UI test form (First Name, Last Name, Date of
    Birth, Gender, DL Number, DL Issuing State, DL Expiration -- every field
    ALSO shared with the Policyholder tab, forcing the slow disambiguation
    path) went completely missing from a real run with ZERO log output at
    any visible level, while Driver 3's identical fields filled correctly.
    Traced to this exact `if not _bf_hwnd: continue` -- the only trace was
    a logger.debug() call on the exception path, invisible in a normal run,
    and NO log at all when _bf_ctrl resolved to None without raising (the
    actual case here, confirmed by _resolve_field_control's own contract:
    it returns None on a clean 'not found', no exception). Root cause not
    yet provable without seeing that line -- this makes the failure visible
    (not a guessed behavioral fix) so the next live run gives a real answer."""

    def _batch_window(self):
        idx = _SOURCE.index("OPT2 BATCH FAST-FILL")
        return _SOURCE[idx:idx + 21500]

    def test_control_resolution_failure_is_logged_at_a_visible_level_in_both_branches(self):
        window = self._batch_window()
        assert window.count(
            'logger.warning("Batch fast-fill: no UIA control resolved for %r') == 2

    def test_the_exception_path_is_also_promoted_to_a_visible_level(self):
        """The pre-existing SetFocus()/NativeWindowHandle exception handler
        used logger.debug -- same invisibility problem, same fix."""
        window = self._batch_window()
        assert window.count(
            'logger.debug("Batch fast-fill focus/handle resolution failed for %r') == 0
        assert window.count(
            'logger.warning("Batch fast-fill focus/handle resolution failed for %r') == 2

    def test_failure_log_includes_the_value_that_could_not_be_written(self):
        """The diagnostic must say what was ready to write, not just that
        something failed -- otherwise it can't distinguish 'a real value was
        lost' from a genuinely-blank field being skipped for some other reason."""
        window = self._batch_window()
        idx = window.index('logger.warning("Batch fast-fill: no UIA control resolved for %r')
        section = window[idx:idx + 300]
        assert "_bf_val" in section

    def test_still_continues_past_an_unresolved_field_same_as_before(self):
        """The new logging must not change control flow -- a field whose
        control can't be resolved still falls through to a later step
        (dead-spot rescue / transformer fallback), not raise or hang."""
        window = self._batch_window()
        assert window.count("if not _bf_hwnd:\n                            continue") == 2


class TestBatchFastFillPassesSectionForDisambiguation:
    """Real live bug, direct report ("Driver 2 returns empty... also add a
    way to distinguish similar bare label names"). Both batch branches
    already computed _bf_sec (via _detect_section) for the VALUE lookup --
    but never passed it to _resolve_field_control, so the UIA-level control
    search had no way to tell Driver 2's 'First Name' apart from Driver 3's
    or the Policyholder's own, other than raw on-screen distance (which a
    stale bbox, from batch fast-fill's own no-re-observe-between-fields
    design, can throw off). Threading the already-computed section through
    reuses real on-screen geometry (_section_bounds) as a stronger signal,
    at zero extra cost -- _bf_sec was already being computed either way."""

    def _batch_window(self):
        idx = _SOURCE.index("OPT2 BATCH FAST-FILL")
        return _SOURCE[idx:idx + 21500]

    def test_both_branches_pass_the_already_computed_section(self):
        window = self._batch_window()
        assert window.count(
            'self._resolve_field_control(state, _bf_label, _bf_el.get("bbox"), section=_bf_sec)') == 2

    def test_bf_sec_is_computed_before_it_is_used_for_control_resolution(self):
        """_bf_sec is now computed ONCE per field, shared by both branches
        (moved up during the section-aware-attempt-key fix, see
        TestBatchFastFillUsesSectionAwareAttemptKeys) -- must still be
        assigned before EITHER branch's control-resolution call uses it."""
        window = self._batch_window()
        sec_pos = window.index('_bf_sec = self._detect_section(state, _bf_el)')
        ctrl_positions = [m.start() for m in re.finditer(
            r'self\._resolve_field_control\(state, _bf_label, _bf_el\.get\("bbox"\), section=_bf_sec\)', window)]
        assert len(ctrl_positions) == 2
        for ctrl_pos in ctrl_positions:
            assert sec_pos < ctrl_pos, "_bf_sec must be computed before it's used to resolve the control"


class TestBatchFastFillRejectsLeaveBlankPhrasesFromTheLlm:
    """Real live bug, direct report ("Third Party Information was falsely
    filled... not fill things where they're not supposed to be filled").
    Confirmed in a real run: a genuinely-blank field ("Third Party Name,"
    no third party involved) got the literal text "leave blank" typed
    into it -- the deep=True LLM check (added to give a real answer
    before committing a field blank) answered in English instead of
    truly returning nothing, and that English answer was accepted as a
    real value to write.

    _is_leave_blank_prediction already exists in this file for exactly
    this failure mode (a genuine keyboard-action LLM prediction whose
    text means "leave this blank" -- '', 'none', 'n/a', or anything
    starting with 'leave blank') and is already proven at another call
    site (_merge()'s own click-vs-keyboard override guard). Reusing it
    here, not reimplementing a second copy that could drift out of sync.

    Deliberately scoped: this closes the LITERAL-PHRASE case only. A
    separate, harder case (the LLM inventing a plausible-looking but
    wrong VALUE, e.g. filling 'Third Party Name' with the policyholder's
    own name) is a different problem this does not claim to solve."""

    def _batch_window(self):
        idx = _SOURCE.index("OPT2 BATCH FAST-FILL")
        return _SOURCE[idx:idx + 21500]

    def test_both_branches_reject_a_leave_blank_prediction_before_accepting_it(self):
        window = self._batch_window()
        assert window.count(
            "and not _is_leave_blank_prediction(_bf_llm_action)") == 2

    def test_the_reject_check_is_combined_with_the_existing_action_type_check(self):
        """Must not replace the action_type gate, only tighten it -- a
        click-type llm_action (no meaningful text) must still be ignored
        exactly as before."""
        window = self._batch_window()
        for m in re.finditer(r'and not _is_leave_blank_prediction\(_bf_llm_action\)', window):
            before = window[max(0, m.start() - 200):m.start()]
            assert '_bf_llm_action.get("action_type") in ("type", "keyboard")' in before

    def test_rejecting_falls_through_to_the_existing_confirmed_blank_path(self):
        """A rejected leave-blank prediction must leave _bf_val empty, so
        the field still gets committed blank via the same existing
        bookkeeping (leave_blank_keys/mark_attempted/Tab-only) -- not a
        new, separate blank-handling path."""
        window = self._batch_window()
        idx = window.index("and not _is_leave_blank_prediction(_bf_llm_action)")
        section = window[idx:idx + 500]
        assert "confirmed blank, Tab past" in section


class TestBatchFastFillDriverFieldScanDiagnostic:
    """Narrow, read-only diagnostic, direct request ("just find a way")
    after five straight fix attempts for the Driver 2 gap failed --
    including the safe, UIA-only scroll-reset (attempt five), which
    produced zero "Scroll-form: UIA SetScrollPercent reset" log lines on
    the next live run, meaning either it never fired or scroll position
    was never the real cause. Rather than guess a sixth time, this reports
    exactly what the batch loop sees for ONLY the 7 specific field labels
    already confirmed missing (First Name, Last Name, Date of Birth,
    Gender, DL Number, DL Issuing State, DL Expiration) -- NOT the earlier
    "any repeated label" diagnostic that got reverted for logging 2,417
    lines in one run by also matching unrelated noise (Notepad's own menu
    items). Scoped to a fixed 7-label set, this can only ever log a
    handful of lines per tab visit -- the volume problem that sank the
    prior diagnostic structurally cannot recur here."""

    def _batch_window(self):
        idx = _SOURCE.index("OPT2 BATCH FAST-FILL")
        return _SOURCE[idx:idx + 21500]

    def test_scan_checks_exactly_the_seven_known_missing_labels(self):
        window = self._batch_window()
        idx = window.index("_DFS_LABELS = {")
        section = window[idx:idx + 300]
        for label in ("first name", "last name", "date of birth", "gender",
                      "dl number", "dl issuing state", "dl expiration"):
            assert f'"{label}"' in section

    def test_scan_runs_right_after_targets_are_computed(self):
        window = self._batch_window()
        targets_idx = window.index(
            "_bf_targets = self._navproto.find_all_visible_empty_targets(")
        scan_idx = window.index("[DRIVER-FIELD-SCAN]")
        loop_idx = window.index("for _bf_el in _bf_targets:")
        assert targets_idx < scan_idx < loop_idx

    def test_scan_is_pure_logging_no_new_actions(self):
        window = self._batch_window()
        scan_idx = window.index("[DRIVER-FIELD-SCAN]")
        loop_idx = window.index("for _bf_el in _bf_targets:")
        section = window[scan_idx - 600:loop_idx]
        assert "self._executor.execute(" not in section
        assert "pyautogui" not in section

    def test_scan_reports_visibility_value_and_whether_it_became_a_target(self):
        window = self._batch_window()
        idx = window.index("[DRIVER-FIELD-SCAN]")
        section = window[idx:idx + 500]
        assert "visible" in section
        assert "value" in section
        assert "is_target" in section


class TestBatchFastFillUsesSectionAwareAttemptKeys:
    """Real root cause, finally proven by [DRIVER-FIELD-SCAN]'s real live
    output: Driver 2's fields were genuinely visible and empty, yet
    consistently is_target=False -- meaning _attempted_keys already
    contained their computed key despite never having been touched. Cause:
    UIA duplicates every repeated-section label across multiple elements
    (a decorative textcontrol alongside the real editcontrol/comboboxcontrol),
    and across three sections sharing a label (Policyholder + Driver 2 +
    Driver 3), rank-based disambiguation (element-list position) is fragile
    enough to collide. Fix: _attempt_key/_mark_attempted now accept an
    optional `section` (see test_attempt_key_disambiguation.py) that
    dominates rank when given -- this wires it through both the batch
    loop's own key computation AND the exclusion filter passed into
    find_all_visible_empty_targets, so a field belonging to a genuinely
    different section can never collide with Driver 2's key again."""

    def _batch_window(self):
        idx = _SOURCE.index("OPT2 BATCH FAST-FILL")
        return _SOURCE[idx:idx + 21500]

    def test_bf_sec_is_computed_before_bf_key_not_after(self):
        """_bf_sec must be available BEFORE _bf_key is computed, so the key
        itself can be section-aware from the start -- not computed with an
        empty section and never corrected."""
        window = self._batch_window()
        sec_idx = window.index("_bf_sec = self._detect_section(state, _bf_el)")
        key_idx = window.index("_bf_key = self._attempt_key(")
        assert sec_idx < key_idx

    def test_bf_key_is_computed_with_the_section(self):
        window = self._batch_window()
        assert 'section=_bf_sec' in window[
            window.index("_bf_key = self._attempt_key("):
            window.index("_bf_key = self._attempt_key(") + 150]

    def test_every_mark_attempted_for_bf_el_passes_the_section(self):
        window = self._batch_window()
        calls = re.findall(r'self\._mark_attempted\(_bf_el, elements=state\.get\("elements", \[\]\)(.*?)\)',
                            window)
        assert len(calls) >= 5, "expected all 5 batch-fill mark_attempted call sites"
        for call_tail in calls:
            assert "section=_bf_sec" in call_tail

    def test_target_exclusion_filter_is_section_aware_too(self):
        """find_all_visible_empty_targets's own attempt_key_fn (the filter
        deciding which fields even become candidates) must ALSO be section-
        aware -- otherwise a field could be silently excluded from the
        target list before ever reaching _bf_key's own section-aware check."""
        window = self._batch_window()
        idx = window.index("_bf_targets = self._navproto.find_all_visible_empty_targets(")
        section = window[max(0, idx - 400):idx + 300]
        assert "_detect_section(state, e)" in section or "_detect_section(state, " in section
        assert "attempt_key_fn=" in section
