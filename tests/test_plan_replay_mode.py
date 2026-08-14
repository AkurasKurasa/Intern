"""
Tests for FormFillerPlugin's opt-in plan_replay mode
(components/agent/task_plugins/form_filler_plugin.py) and the plumbing
that wires it up (components/agent/agent.py's __init__).

Built 2026-08-14 alongside components/agent/field_planner.py. plan_replay
is off by default and purely additive: every existing branch in
handle_step() must be reachable exactly as before when it's False. This
file's first job is proving that off-by-default claim directly, since
FormFillerPlugin had zero direct unit tests before this change.

Design note carried over from field_planner.py: a PlannedField's bbox is
documented as a plan-time HINT only, never the click target. Consistent
with that, this new branch never falls back to a raw coordinate click on
its own -- if the real UIA focus-by-name fails, the field is treated as
diverged and control passes to the existing, already-tested reactive
cascade below (which has its own focus mechanisms), rather than guessing
at a stale coordinate. So unlike the other click sites hardened this
session, no NEW _find_destructive_button_at call site was needed here --
there's no new raw click for it to guard.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent import field_planner as fp_module
from agent.field_planner import PlannedField, Resolution
from agent.task_plugins import form_filler_plugin as ffp_module
from agent.task_plugins.form_filler_plugin import FormFillerPlugin


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol", **kw):
    e = {"element_id": label, "type": ftype, "label": label, "value": value,
         "bbox": list(bbox), "window_role": "active", "enabled": True}
    e.update(kw)
    return e


def _make_plugin(**kw):
    plugin = FormFillerPlugin(
        executor=MagicMock(), data_source=None, step_delay=0, **kw,
    )
    plugin._record_cache_loaded = True
    plugin._tab_just_switched = False
    return plugin


def _no_sleep(monkeypatch):
    monkeypatch.setattr(ffp_module.time, "sleep", lambda s: None)


class TestOffByDefault:
    def test_plan_visible_fields_is_never_called_when_plan_replay_is_false(self, monkeypatch):
        _no_sleep(monkeypatch)
        plugin = _make_plugin(plan_replay=False)
        spy = MagicMock(wraps=fp_module.plan_visible_fields)
        monkeypatch.setattr(ffp_module, "plan_visible_fields", spy)

        state = {"elements": [_field("First Name")], "focused_element_id": "First Name"}
        plugin._cached_record = {"First Name": "Alice"}
        monkeypatch.setattr(plugin, "_get_focused_value", lambda: "Alice")

        plugin.handle_step(state, 0)

        spy.assert_not_called()
        assert plugin._field_plan == []
        assert plugin._plan_idx == 0

    def test_default_constructor_arg_is_false(self):
        plugin = _make_plugin()
        assert plugin._plan_replay is False


class TestPlanBuildAndConsumption:
    def test_plan_is_built_from_empty_on_first_eligible_step(self, monkeypatch):
        _no_sleep(monkeypatch)
        plugin = _make_plugin(plan_replay=True)
        spy = MagicMock(wraps=fp_module.plan_visible_fields)
        monkeypatch.setattr(ffp_module, "plan_visible_fields", spy)

        state = {"elements": [_field("First Name")], "focused_element_id": "First Name"}
        plugin._cached_record = {"First Name": "Alice"}
        monkeypatch.setattr(plugin, "_focus_by_label", lambda label, bbox=None: False)

        plugin.handle_step(state, 0)

        spy.assert_called_once()

    def test_lookup_hit_editcontrol_executes_via_paste_and_verify_not_the_legacy_cascade(self, monkeypatch):
        _no_sleep(monkeypatch)
        plugin = _make_plugin(plan_replay=True)
        planned = PlannedField("first name", "First Name", "editcontrol",
                                [100, 100, 300, 130], "", "Alice", False, Resolution.LOOKUP_HIT)
        plugin._field_plan = [planned]
        plugin._plan_idx = 0

        monkeypatch.setattr(plugin, "_focus_by_label", lambda label, bbox=None: True)
        monkeypatch.setattr(plugin, "_get_focused_value", lambda: "Alice")
        auto_fill_spy = MagicMock(return_value=None)
        auto_skip_spy = MagicMock(return_value=False)
        monkeypatch.setattr(plugin, "_auto_fill", auto_fill_spy)
        monkeypatch.setattr(plugin, "_auto_skip", auto_skip_spy)

        state = {"elements": [_field("First Name")], "focused_element_id": "First Name"}
        handled, cont = plugin.handle_step(state, 0)

        assert (handled, cont) == (True, True)
        assert plugin._plan_idx == 1
        assert "First Name" in plugin._filled_this_tab
        auto_fill_spy.assert_not_called()
        auto_skip_spy.assert_not_called()

    def test_lookup_blank_tabs_past_without_typing(self, monkeypatch):
        _no_sleep(monkeypatch)
        plugin = _make_plugin(plan_replay=True)
        planned = PlannedField("middle name", "Middle Name", "editcontrol",
                                [100, 100, 300, 130], "", "", False, Resolution.LOOKUP_BLANK)
        plugin._field_plan = [planned]
        plugin._plan_idx = 0
        monkeypatch.setattr(plugin, "_focus_by_label", lambda label, bbox=None: True)

        state = {"elements": [_field("Middle Name")], "focused_element_id": "Middle Name"}
        handled, cont = plugin.handle_step(state, 0)

        assert (handled, cont) == (True, True)
        assert plugin._plan_idx == 1
        # Only a Tab keystroke, no paste keystrokes, was sent.
        calls = plugin._executor.execute.call_args_list
        assert all(c.args[0].get("keystrokes") == ["tab"] for c in calls
                   if c.args[0].get("action_type") == "keyboard")

    def test_needs_llm_defers_to_reactive_path_without_advancing(self, monkeypatch):
        _no_sleep(monkeypatch)
        plugin = _make_plugin(plan_replay=True)
        planned = PlannedField("mystery field", "Mystery Field", "editcontrol",
                                [100, 100, 300, 130], "", "", False, Resolution.NEEDS_LLM)
        plugin._field_plan = [planned]
        plugin._plan_idx = 0

        focus_spy = MagicMock(return_value=True)
        monkeypatch.setattr(plugin, "_focus_by_label", focus_spy)

        state = {"elements": [_field("Mystery Field")], "focused_element_id": "Mystery Field"}
        handled, cont = plugin.handle_step(state, 0)

        assert (handled, cont) == (False, False)
        assert plugin._plan_idx == 0          # not consumed -- still pending
        focus_spy.assert_called_once_with("Mystery Field", [100, 100, 300, 130])

    def test_combobox_is_never_fast_replayed_even_when_lookup_hit(self, monkeypatch):
        _no_sleep(monkeypatch)
        plugin = _make_plugin(plan_replay=True)
        planned = PlannedField("state", "State", "comboboxcontrol",
                                [100, 100, 300, 130], "", "CA", False, Resolution.LOOKUP_HIT)
        plugin._field_plan = [planned]
        plugin._plan_idx = 0
        monkeypatch.setattr(plugin, "_focus_by_label", lambda label, bbox=None: True)

        state = {"elements": [_field("State", ftype="comboboxcontrol")], "focused_element_id": "State"}
        handled, cont = plugin.handle_step(state, 0)

        assert (handled, cont) == (False, False)  # deferred to reactive combobox handling
        assert plugin._plan_idx == 0

    def test_diverged_field_is_consumed_and_falls_through(self, monkeypatch):
        _no_sleep(monkeypatch)
        plugin = _make_plugin(plan_replay=True)
        # Planned field no longer exists in the current observation at all.
        planned = PlannedField("vanished", "Vanished Field", "editcontrol",
                                [100, 100, 300, 130], "", "X", False, Resolution.LOOKUP_HIT)
        plugin._field_plan = [planned]
        plugin._plan_idx = 0

        state = {"elements": [_field("Something Else")], "focused_element_id": "Something Else"}
        plugin.handle_step(state, 0)

        assert plugin._plan_idx == 1   # stale entry consumed, not retried

    def test_satisfied_field_is_tabbed_past(self, monkeypatch):
        _no_sleep(monkeypatch)
        plugin = _make_plugin(plan_replay=True)
        planned = PlannedField("first name", "First Name", "editcontrol",
                                [100, 100, 300, 130], "", "Alice", False, Resolution.LOOKUP_HIT)
        plugin._field_plan = [planned]
        plugin._plan_idx = 0

        state = {"elements": [_field("First Name", value="Alice")],
                 "focused_element_id": "First Name"}
        handled, cont = plugin.handle_step(state, 0)

        assert (handled, cont) == (True, True)
        assert plugin._plan_idx == 1
        plugin._executor.execute.assert_called_with(
            {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})

    def test_plan_exhaustion_triggers_automatic_rebuild(self, monkeypatch):
        _no_sleep(monkeypatch)
        plugin = _make_plugin(plan_replay=True)
        plugin._field_plan = []
        plugin._plan_idx = 0
        spy = MagicMock(wraps=fp_module.plan_visible_fields)
        monkeypatch.setattr(ffp_module, "plan_visible_fields", spy)
        monkeypatch.setattr(plugin, "_focus_by_label", lambda label, bbox=None: False)

        state = {"elements": [_field("Anything")], "focused_element_id": "Anything"}
        plugin.handle_step(state, 0)

        spy.assert_called_once()


class TestFallbackHelpers:
    def test_unwired_focus_and_settle_fall_back_to_local_methods(self, monkeypatch):
        _no_sleep(monkeypatch)
        plugin = _make_plugin(plan_replay=True, focus_fn=None, settle_wait_fn=None)
        planned = PlannedField("first name", "First Name", "editcontrol",
                                [100, 100, 300, 130], "", "Alice", False, Resolution.LOOKUP_HIT)
        plugin._field_plan = [planned]
        plugin._plan_idx = 0

        local_focus_spy = MagicMock(return_value=True)
        monkeypatch.setattr(plugin, "_focus_by_label", local_focus_spy)
        monkeypatch.setattr(plugin, "_get_focused_value", lambda: "Alice")

        state = {"elements": [_field("First Name")], "focused_element_id": "First Name"}
        plugin.handle_step(state, 0)

        local_focus_spy.assert_called_once_with("First Name", [100, 100, 300, 130])

    def test_wired_focus_fn_is_used_instead_of_local_fallback(self, monkeypatch):
        _no_sleep(monkeypatch)
        wired_focus = MagicMock(return_value=True)
        plugin = _make_plugin(plan_replay=True, focus_fn=wired_focus)
        planned = PlannedField("first name", "First Name", "editcontrol",
                                [100, 100, 300, 130], "", "Alice", False, Resolution.LOOKUP_HIT)
        plugin._field_plan = [planned]
        plugin._plan_idx = 0
        monkeypatch.setattr(plugin, "_get_focused_value", lambda: "Alice")
        local_focus_spy = MagicMock(return_value=True)
        monkeypatch.setattr(plugin, "_focus_by_label", local_focus_spy)

        state = {"elements": [_field("First Name")], "focused_element_id": "First Name"}
        plugin.handle_step(state, 0)

        wired_focus.assert_called_once_with("First Name", [100, 100, 300, 130])
        local_focus_spy.assert_not_called()


class TestNotifyTabClickResetsThePlan:
    def test_notify_tab_click_clears_field_plan_and_idx(self):
        plugin = _make_plugin(plan_replay=True)
        planned = PlannedField("x", "X", "editcontrol", None, "", "v", False, Resolution.LOOKUP_HIT)
        plugin._field_plan = [planned]
        plugin._plan_idx = 1

        plugin.notify_tab_click(1, {"elements": []})

        assert plugin._field_plan == []
        assert plugin._plan_idx == 0


class TestFocusByLabelExtraction:
    """_focus_by_label was extracted from _focus_first_empty_field's inline
    UIA lookup so plan_replay could reuse it -- confirm both paths still
    resolve to the same method (no accidental behavioral fork)."""

    def test_focus_first_empty_field_delegates_to_focus_by_label(self, monkeypatch):
        plugin = _make_plugin()
        spy = MagicMock(return_value=True)
        monkeypatch.setattr(plugin, "_focus_by_label", spy)

        state = {"elements": [_field("Only Field")]}
        result = plugin._focus_first_empty_field(state)

        assert result is True
        spy.assert_called_once_with("Only Field")


class TestLLMAgentWiresPlanReplayHelpers:
    def test_focus_settle_and_viewport_fns_are_wired_onto_a_bare_plugin_stub(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
        from agent.agent import LLMAgent

        class _StubPlugin:
            _focus_fn = None
            _settle_wait_fn = None
            _viewport_bottom_fn = None

            def handle_step(self, state, step_idx):
                return (False, False)

        stub = _StubPlugin()
        agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1, step_delay=0,
                          task_plugin=stub)

        assert stub._focus_fn == agent._focus_element_via_uia
        assert stub._settle_wait_fn == agent._adaptive_settle_wait
        assert stub._viewport_bottom_fn == agent._form_viewport_bottom
