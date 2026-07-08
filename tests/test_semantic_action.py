"""
tests/test_semantic_action.py
==============================
Round-trip and shape tests for the Universal Semantic Action Space vocabulary.

Run from the repo root:
    python -m pytest tests/test_semantic_action.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from components.agent.semantic_action import SemanticAction, Verb, VALUE_VERBS, TARGETED_VERBS


class TestToLegacyDict:

    def test_invoke_becomes_click(self):
        d = SemanticAction(verb=Verb.INVOKE, position=(1, 2)).to_legacy_dict()
        assert d == {"action_type": "click", "click_position": [1, 2]}

    def test_focus_becomes_click(self):
        d = SemanticAction(verb=Verb.FOCUS, position=(3, 4)).to_legacy_dict()
        assert d["action_type"] == "click"

    def test_toggle_becomes_click(self):
        d = SemanticAction(verb=Verb.TOGGLE, position=(3, 4)).to_legacy_dict()
        assert d["action_type"] == "click"

    def test_select_option_becomes_click(self):
        d = SemanticAction(verb=Verb.SELECT_OPTION, position=(3, 4), value="X").to_legacy_dict()
        assert d["action_type"] == "click"   # opening/choosing is a click; value carried separately

    def test_set_value_becomes_keyboard_with_text(self):
        d = SemanticAction(verb=Verb.SET_VALUE, value="hello").to_legacy_dict()
        assert d == {"action_type": "keyboard", "text": "hello", "key_count": 5}

    def test_hotkey_becomes_keyboard_with_keystrokes(self):
        d = SemanticAction(verb=Verb.HOTKEY, keystrokes=["ctrl", "a"]).to_legacy_dict()
        assert d == {"action_type": "keyboard", "keystrokes": ["ctrl", "a"], "key_count": 2}

    def test_scroll_to_becomes_scroll(self):
        d = SemanticAction(verb=Verb.SCROLL_TO, position=(9, 9),
                           direction="up", clicks=2).to_legacy_dict()
        assert d == {"action_type": "scroll", "click_position": [9, 9],
                     "direction": "up", "clicks": 2}

    def test_wait_verify_done_become_noop(self):
        for verb in (Verb.WAIT, Verb.VERIFY, Verb.DONE):
            assert SemanticAction(verb=verb).to_legacy_dict() == {"action_type": "no_op"}

    def test_missing_position_defaults_to_origin(self):
        d = SemanticAction(verb=Verb.INVOKE).to_legacy_dict()
        assert d["click_position"] == [0.0, 0.0]


class TestFromLegacyDict:

    def test_click_becomes_invoke(self):
        a = SemanticAction.from_legacy_dict({"action_type": "click", "click_position": [1, 2]})
        assert a.verb == Verb.INVOKE
        assert a.position == (1, 2)

    def test_keyboard_with_text_becomes_set_value(self):
        a = SemanticAction.from_legacy_dict({"action_type": "keyboard", "text": "hi"})
        assert a.verb == Verb.SET_VALUE
        assert a.value == "hi"

    def test_keyboard_without_text_becomes_hotkey(self):
        a = SemanticAction.from_legacy_dict({"action_type": "keyboard", "keystrokes": ["tab"]})
        assert a.verb == Verb.HOTKEY
        assert a.keystrokes == ["tab"]

    def test_scroll_becomes_scroll_to(self):
        a = SemanticAction.from_legacy_dict({"action_type": "scroll", "click_position": [1, 1],
                                             "direction": "up", "clicks": 4})
        assert a.verb == Verb.SCROLL_TO
        assert a.direction == "up"
        assert a.clicks == 4

    def test_noop_becomes_wait(self):
        a = SemanticAction.from_legacy_dict({"action_type": "no_op"})
        assert a.verb == Verb.WAIT

    def test_unknown_action_type_becomes_wait(self):
        a = SemanticAction.from_legacy_dict({"action_type": "something_unknown"})
        assert a.verb == Verb.WAIT


class TestVerbSets:

    def test_value_verbs_are_set_value_and_select_option(self):
        assert VALUE_VERBS == {Verb.SET_VALUE, Verb.SELECT_OPTION}

    def test_targeted_verbs_exclude_hotkey_wait_done(self):
        assert Verb.HOTKEY not in TARGETED_VERBS
        assert Verb.WAIT not in TARGETED_VERBS
        assert Verb.DONE not in TARGETED_VERBS

    def test_targeted_verbs_include_focus_and_invoke(self):
        assert Verb.FOCUS in TARGETED_VERBS
        assert Verb.INVOKE in TARGETED_VERBS
