"""Tests for Scope #1's "Fast fill + reasoning" mode (--fast_fill_text_only).

The mode makes deliberate what the 2026-09-16 run did by accident: text
fields whose value is known for sure are fast-filled, while dropdowns and
checkboxes are left to the transformer (where to click) and the lookup or
LLM (what to choose). Today's fast paths write dropdowns and checkboxes
directly, which is why a normal run stopped showing the transformer at all.

The agent's step loop is far too large to drive in a unit test, so -- as
tests/test_agent_decision_feed.py does for the other fill-mode flags --
the gates are checked structurally, and the flag's construction and
plumbing are checked behaviourally.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "components"))

AGENT = open(os.path.join(ROOT, "components", "agent", "agent.py"), encoding="utf8").read()
RUN_TASK = open(os.path.join(ROOT, "run_task.py"), encoding="utf8").read()
RENDERER = open(os.path.join(ROOT, "app_electron", "renderer", "renderer.js"), encoding="utf8").read()


# -- behaviour: construction ---------------------------------------------------

def test_the_mode_is_off_unless_asked_for():
    from agent.agent import LLMAgent
    assert LLMAgent(goal="g", dry_run=True, max_steps=1)._fast_fill_text_only is False


def test_the_mode_can_be_switched_on():
    from agent.agent import LLMAgent
    assert LLMAgent(goal="g", dry_run=True, max_steps=1,
                    fast_fill_text_only=True)._fast_fill_text_only is True


# -- structure: every dropdown/checkbox fast path is gated ---------------------

def test_batch_fast_fill_leaves_dropdowns_to_the_transformer():
    assert 'elif _bf_ty == "comboboxcontrol" and not self._fast_fill_text_only:' in AGENT


def test_batch_fast_fill_leaves_checkboxes_to_the_transformer():
    assert ('elif _bf_ty in ("checkboxcontrol", "checkbox") and not self._fast_fill_text_only:'
            in AGENT)


def test_single_field_fast_fill_writes_text_only_in_this_mode():
    assert '_ff_types = (("editcontrol",) if self._fast_fill_text_only' in AGENT
    assert "if (_ff_fel and _ff_ty in _ff_types and not _ff_val" in AGENT


def test_single_field_checkbox_fast_fill_is_gated():
    m = re.search(r'elif \(_ff_fel and _ff_ty in \("checkboxcontrol", "checkbox"\)\s*\n\s*and not self\._fast_fill_text_only',
                  AGENT)
    assert m, "the single-field checkbox fast-fill must step aside in this mode"


def test_text_fields_are_still_fast_filled():
    # The batch text branch is NOT gated: text values known for sure still go
    # straight in, which is the "fast fill" half of the mode.
    assert '                    if _bf_ty == "editcontrol":\n' in AGENT
    assert 'if _bf_ty == "editcontrol" and not self._fast_fill_text_only' not in AGENT


def test_no_ungated_dropdown_or_checkbox_fast_fill_remains():
    # Every batch/single-field branch that writes a dropdown or a checkbox
    # directly must mention the flag on the same condition.
    for pattern in [r'elif _bf_ty == "comboboxcontrol"[^\n]*', r'elif _bf_ty in \("checkboxcontrol", "checkbox"\)[^\n]*']:
        for line in re.findall(pattern, AGENT):
            assert "_fast_fill_text_only" in line, line


# -- plumbing: run_task and the Play dialog ------------------------------------

def test_run_task_offers_the_flag_and_passes_it_through():
    assert '"--fast_fill_text_only", action="store_true"' in RUN_TASK
    assert "fast_fill_text_only   = _args.fast_fill_text_only," in RUN_TASK


def test_play_dialog_offers_three_fill_modes():
    block = RENDERER[RENDERER.index("form_filling: {"):RENDERER.index('"Sheet-to-Portal Matcher": {')]
    names = re.findall(r'name: "([^"]+)"', block)
    assert names == ["Direct write", "Fast fill + reasoning", "Transformer"], names


def test_the_combined_choice_launches_with_the_flag():
    block = RENDERER[RENDERER.index('name: "Fast fill + reasoning"'):]
    block = block[:block.index("},")]
    assert 'args: ["--fast_fill_text_only"]' in block
