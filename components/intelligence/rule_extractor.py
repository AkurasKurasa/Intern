"""
rule_extractor.py
=================
Two modes:

1. extract(results, goal, task_name)
   After a completed agent run — derives generalized rules from the action trace.
   Saves a timestamped file per run (historical log).

2. correct(session_dir, goal, task_name)
   After a recording session — reads the persistent task spec, sends it + new
   human demo traces to the LLM, and overwrites the spec with a corrected version.
   The persistent file is loaded by the agent at startup as LLM instructions.

Persistent spec: data/output/rulesets/form_filling.md  (single truth file)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Single persistent task spec — overwritten (not appended) after each session
PERSISTENT_RULESET_NAME = "form_filling.md"

# ── system prompts ─────────────────────────────────────────────────────────────

_EXTRACT_SYSTEM_PROMPT = """You are an expert at analyzing AI agent behavior and extracting generalizable rules.

You will be given a trace of actions an AI agent took while completing a GUI automation task.
Your job is to derive a concise, generalized rule set that captures what the agent has learned.

Rules must be:
- ABSTRACT: applicable to similar tasks, not just this exact form or session
- ACTIONABLE: describe what to do, not what happened
- COMPLETE: cover navigation, data handling, error recovery, ordering constraints
- HONEST: if the agent made mistakes or seems confused, say so

Format each rule exactly as:
RULE [category] — rule statement

Categories: navigation | data | input | ordering | error | goal

After the rules, add a one-paragraph ASSESSMENT: does this rule set suggest the agent
genuinely understands the task, or is it pattern-matching without understanding? Be direct."""

_CORRECTIONAL_SYSTEM_PROMPT = """You are a task specification writer for a GUI automation agent.

You will be given:
  EXISTING SPEC — the current best understanding of the task (may be empty on first run)
  NEW DEMO TRACES — a human completing the task correctly

Your job: produce a CORRECTED, SHARPENED task specification.

Rules for correction:
- Do NOT accumulate. Replace wrong or vague rules with precise ones.
- Do NOT list every step — extract principles and constraints.
- If new traces contradict existing spec, the traces win. Update the spec.
- If new traces confirm existing spec, keep it (do not rewrite unnecessarily).
- Add edge cases observed in traces that the spec did not cover.
- Remove rules that are too vague to be actionable.

Output format (use exactly these sections):

## Inferred Goal
One sentence: what is this task, precisely?

## Navigation Rules
Ordered constraints on how to move through the UI.

## Field Rules
Per-field instructions: which tab, what data source, any conditions.

## Data Rules
How to read/use source data (Notepad, files, etc.).

## Edge Cases & Corrections
Conditions not covered by the general rules above.

## Confidence
One sentence: how confident are you this spec is complete and correct?"""


# ── LLM callers ───────────────────────────────────────────────────────────────

def _call_lmstudio(prompt: str, system: str, url: str = "http://localhost:1234/v1") -> str:
    try:
        import openai
        client = openai.OpenAI(base_url=url, api_key="lm-studio")
        resp = client.chat.completions.create(
            model="local-model",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=1500,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("RuleExtractor LM Studio call failed: %s", e)
        return ""


def _call_anthropic(prompt: str, system: str, api_key: str) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.warning("RuleExtractor Anthropic call failed: %s", e)
        return ""


def _call_groq(prompt: str, system: str, api_key: str) -> str:
    try:
        import openai
        client = openai.OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=1500,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("RuleExtractor Groq call failed: %s", e)
        return ""


# ── trace compression ──────────────────────────────────────────────────────────

def _compress_trace(results: List[Dict[str, Any]]) -> str:
    """Compress agent run results into compact readable trace."""
    lines = []
    for r in results:
        step   = r.get("step", "?")
        state  = r.get("state", {})
        action = r.get("action", {})
        a_type = action.get("action_type", "noop")

        if a_type in ("noop", "wait", "no_op"):
            continue

        tab = state.get("active_tab", "") or ""
        focused = ""
        focused_id = state.get("focused_element_id")
        for elem in state.get("elements", []):
            if elem.get("element_id") == focused_id:
                focused = elem.get("label") or elem.get("text") or ""
                break

        if a_type == "click":
            pos = action.get("click_position", [])
            lines.append(f"Step {step:>3}: CLICK  tab={tab!r}  target={focused!r}  pos={pos}")
        elif a_type == "keyboard":
            text = action.get("text", "") or "".join(action.get("keystrokes", []))
            lines.append(f"Step {step:>3}: TYPE   tab={tab!r}  field={focused!r}  value={text!r}")
        elif a_type == "scroll":
            lines.append(f"Step {step:>3}: SCROLL tab={tab!r}  dir={action.get('direction','?')}")
        else:
            lines.append(f"Step {step:>3}: {a_type.upper()}  tab={tab!r}")

    return "\n".join(lines) if lines else "(empty trace)"


def _compress_session(session_dir: str | Path) -> str:
    """Compress raw trace JSONs from a recording session into readable text."""
    session_path = Path(session_dir)
    trace_files  = sorted(session_path.glob("*.json"))
    trace_files  = [f for f in trace_files if f.name != "session_manifest.json"]

    if not trace_files:
        return "(no traces found in session)"

    lines = []
    for i, fpath in enumerate(trace_files):
        try:
            with fpath.open(encoding="utf-8") as f:
                t = json.load(f)
        except Exception:
            continue

        action = t.get("action", {})
        a_type = action.get("action_type", "")
        if not a_type or a_type == "noop":
            continue

        state  = t.get("state", {})
        elems  = state.get("elements", [])
        fid    = state.get("focused_element_id", "")

        # Active tab — find selected tabitem
        tab = ""
        for e in elems:
            if e.get("type", "").lower() in ("tabitem", "tab") and e.get("selected"):
                tab = e.get("label") or e.get("text") or ""
                break

        # Focused element label
        focused = ""
        for e in elems:
            if e.get("element_id") == fid:
                focused = e.get("label") or e.get("text") or ""
                break

        if a_type == "click":
            pos = action.get("click_position", [])
            # Try to find element at click position by bbox proximity
            target = focused
            if not target:
                cx, cy = (pos[0], pos[1]) if len(pos) >= 2 else (0, 0)
                best, best_d = None, 9999
                for e in elems:
                    bb = e.get("bbox", {})
                    ex = bb.get("x", 0) + bb.get("width", 0) / 2
                    ey = bb.get("y", 0) + bb.get("height", 0) / 2
                    d = abs(ex - cx) + abs(ey - cy)
                    if d < best_d:
                        best_d = d
                        best = e.get("label") or e.get("text") or ""
                target = best or ""
            lines.append(f"Step {i:>4}: CLICK  tab={tab!r}  target={target!r}  pos={pos}")

        elif a_type == "keyboard":
            text = action.get("text", "") or ""
            keys = action.get("keystrokes", [])
            if text.strip():
                lines.append(f"Step {i:>4}: TYPE   tab={tab!r}  field={focused!r}  value={text!r}")
            elif keys:
                readable = [k for k in keys if not k.startswith("Key.shift")]
                if readable:
                    lines.append(f"Step {i:>4}: KEY    tab={tab!r}  keys={readable}")

    if not lines:
        return "(no meaningful actions in session)"

    # Deduplicate consecutive identical lines (held keys, repeated clicks)
    deduped = [lines[0]]
    for line in lines[1:]:
        if line != deduped[-1]:
            deduped.append(line)

    # Cap at 300 lines to stay within LLM context
    if len(deduped) > 300:
        deduped = deduped[:150] + ["... (truncated) ..."] + deduped[-150:]

    return "\n".join(deduped)


# ── main class ─────────────────────────────────────────────────────────────────

class RuleExtractor:
    """
    Extracts / corrects task specifications using an LLM.

    Parameters
    ----------
    provider   : "lmstudio" | "anthropic" | "groq"
    api_key    : API key (empty for lmstudio)
    output_dir : Where ruleset files are saved
    """

    def __init__(
        self,
        provider:   str = "lmstudio",
        api_key:    str = "",
        output_dir: str = "data/output/rulesets",
    ):
        self.provider   = provider
        self.api_key    = api_key
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._persistent_path = self.output_dir / PERSISTENT_RULESET_NAME

    # ── public API ─────────────────────────────────────────────────────────────

    def extract(
        self,
        results:   List[Dict[str, Any]],
        goal:      str = "",
        task_name: str = "task",
    ) -> str:
        """Extract rules from a completed agent run. Saves a timestamped log file."""
        if not results:
            logger.warning("RuleExtractor: empty results.")
            return ""

        trace   = _compress_trace(results)
        n_steps = len(results)
        prompt  = (
            f"TASK GOAL: {goal}\n"
            f"TOTAL STEPS: {n_steps}\n\n"
            f"ACTION TRACE:\n{trace}\n\n"
            "Derive the generalized rule set."
        )

        logger.info("RuleExtractor: sending %d-step trace to %s …", n_steps, self.provider)
        ruleset = self._call(prompt, _EXTRACT_SYSTEM_PROMPT)

        if not ruleset:
            ruleset = "(Rule extraction failed — LLM unavailable or returned empty.)"

        # Save timestamped log
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.output_dir / f"{ts}_{task_name}.md"
        path.write_text(
            f"# Rule Set — {task_name}\n"
            f"**Goal:** {goal}  \n**Steps:** {n_steps}  \n"
            f"**Generated:** {datetime.now().isoformat()}\n\n---\n\n{ruleset}\n",
            encoding="utf-8",
        )
        logger.info("RuleExtractor: saved log → %s", path)
        self._print(ruleset, task_name)
        return ruleset

    def correct(
        self,
        session_dir: str | Path,
        goal:        str = "",
        task_name:   str = "form_filling",
    ) -> str:
        """
        Correct the persistent task spec using a new human demo session.

        Reads existing spec (if any), compresses new session traces, asks the LLM
        to produce a corrected spec, and overwrites the persistent file.
        Returns the corrected spec text.
        """
        session_dir = Path(session_dir)
        existing_spec = self._load_persistent()
        new_traces    = _compress_session(session_dir)
        n_files       = len(list(session_dir.glob("*.json")))

        prompt = (
            f"STATED GOAL: {goal}\n\n"
            f"=== EXISTING SPEC ===\n"
            f"{existing_spec or '(none — this is the first session)'}\n\n"
            f"=== NEW HUMAN DEMO ({n_files} traces from {session_dir.name}) ===\n"
            f"{new_traces}\n\n"
            "Produce the corrected task specification."
        )

        logger.info(
            "RuleExtractor.correct: session=%s  traces=%d  provider=%s",
            session_dir.name, n_files, self.provider,
        )
        corrected = self._call(prompt, _CORRECTIONAL_SYSTEM_PROMPT)

        if not corrected:
            logger.warning("RuleExtractor.correct: LLM returned empty — spec unchanged.")
            return existing_spec or ""

        # Overwrite persistent spec
        content = (
            f"# Task Specification — {task_name}\n"
            f"**Goal:** {goal}  \n"
            f"**Last updated:** {datetime.now().isoformat()}  \n"
            f"**Sessions processed:** {session_dir.name}\n\n"
            f"---\n\n"
            f"{corrected}\n"
        )
        self._persistent_path.write_text(content, encoding="utf-8")
        logger.info("RuleExtractor.correct: spec updated → %s", self._persistent_path)
        self._print(corrected, f"{task_name} [corrected]")
        return corrected

    def load_spec(self) -> str:
        """Return the current persistent task spec, or empty string if none."""
        return self._load_persistent()

    # ── internal ───────────────────────────────────────────────────────────────

    def _load_persistent(self) -> str:
        if not self._persistent_path.exists():
            return ""
        try:
            return self._persistent_path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _call(self, prompt: str, system: str) -> str:
        if self.provider == "anthropic":
            return _call_anthropic(prompt, system, self.api_key)
        if self.provider == "groq":
            return _call_groq(prompt, system, self.api_key)
        return _call_lmstudio(prompt, system)

    def _print(self, text: str, label: str) -> None:
        border = "=" * 60
        print(f"\n{border}")
        print(f"  TASK SPEC — {label.upper()}")
        print(border)
        print(text)
        print(f"{border}\n")
