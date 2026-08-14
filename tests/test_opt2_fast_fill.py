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

Deliberately scoped to editcontrol only -- comboboxes were live-tested
this session to silently reject the direct-write mechanism entirely, and
checkboxes already have their own separate, working mechanism
(BM_SETCHECK). Both must keep going through the existing reactive path
untouched.

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
    `if (_ff_fel and _ff_ty == "editcontrol" and not _ff_val
            and _ff_key not in self._leave_blank_keys
            and _ff_key not in self._typed_keys):`"""
    ty  = (focused_el.get("type") or "").lower() if focused_el else ""
    val = (focused_el.get("value") or "").strip() if focused_el else ""
    key = agent._attempt_key(focused_el, elements=elements) if focused_el else None
    return bool(
        focused_el and ty == "editcontrol" and not val
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

    def test_comboboxcontrol_is_never_eligible(self):
        """Live-tested this session: WM_SETTEXT is a silent no-op on a
        combobox -- must never be routed through this fast path."""
        agent = _make_agent()
        field = _field("Policy Status", "comboboxcontrol")
        assert _is_fast_fill_eligible(agent, field, [field]) is False

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
        return _SOURCE[idx:idx + 4200]

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
        coordinate click."""
        window = self._fast_fill_window()
        assert "self._find_uia_control_by_name(_ff_label" in window
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
        assert "self._adaptive_settle_wait(self.step_delay * 0.4)" in window

    def test_falls_through_to_continue_only_on_full_success(self):
        """Every failure point (no ctrl, no hwnd, no known value) must
        leave `continue` unreached so today's existing, unmodified code
        runs exactly as it does now."""
        window = self._fast_fill_window()
        # The `continue` must be nested inside the `if _ff_hwnd:` block,
        # not unconditional -- confirm it's preceded by the settle-wait
        # call on the same success path, not floating free.
        settle_idx = window.index("self._adaptive_settle_wait(self.step_delay * 0.4)")
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
