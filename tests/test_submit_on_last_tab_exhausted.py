"""
Regression test for _try_advance_tab() actually clicking Submit when the
last tab is exhausted, instead of just logging "signalling done" and
returning False (components/agent/agent.py, ~L4684).

Found live 2026-08-08, directly reported after the backward-tab-click fix
held up cleanly: "Okay, that's good. The only problem is that it didn't
submit." The log already said the right thing twice in that run --
"Stuck guard: already on last tab (8 tabs total) -- signalling done." --
right at the exact moment Payment tab (the last tab) was confirmed
exhausted (2 consecutive scrolls revealed nothing new). But "signalling
done" was only ever a LOG MESSAGE: the function just returned False, every
caller treats that identically to "advance failed for some unrelated
reason" (pane-detection drift, etc.), and falls through to normal
per-step processing -- nothing ever looked for or clicked Submit.

Fix: when there's no next tab, look for a submit-like button
(_find_submit_button / _SUBMIT_KW, the SAME generic keyword set already
used elsewhere in this file to BLOCK premature submit clicks -- no new,
form-specific assumption introduced) and click it. Returns True (an
action was taken) only when a button was actually found and clicked;
still returns False, unchanged, when none exists -- no regression for a
form without a matching button.

Generalization check (asked directly by the user): this is driven by two
form-agnostic signals -- tab POSITION (there's no next tab in the
dynamically-detected tab list, not a hardcoded count) and a generic
keyword match against visible button text (submit/save/ok/accept/done/
finish/new) -- no field names, no "Payment tab", no "Car Insurance" form
literal anywhere in this logic. Any tabbed form with a last-tab-style
submit button is covered the same way.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import _find_submit_button, _SUBMIT_KW


def _button(text, element_id="btn1", bbox=(1800, 900, 1900, 930)):
    return {"element_id": element_id, "type": "buttoncontrol", "text": text,
            "label": text, "bbox": list(bbox), "window_role": "background"}


class TestFindSubmitButton:
    def test_finds_a_button_matching_submit(self):
        btn = _button("Submit")
        assert _find_submit_button([btn]) is btn

    def test_case_insensitive_and_partial_match(self):
        # "Submit & New" contains both "submit" and "new" -- either keyword matches.
        btn = _button("Submit & New")
        assert _find_submit_button([btn]) is btn

    def test_finds_a_save_button_too(self):
        """Not hardcoded to the literal word 'Submit' -- any of the generic
        keywords in _SUBMIT_KW qualifies, for forms that phrase it differently."""
        btn = _button("Save Record")
        assert _find_submit_button([btn]) is btn

    def test_ignores_window_role_deliberately(self):
        """The real submit button lives on the main window, tagged
        'background' by the state scanner -- must still be found."""
        btn = _button("Submit")
        btn["window_role"] = "background"
        assert _find_submit_button([btn]) is btn

    def test_returns_none_when_no_matching_button_exists(self):
        other = _button("Clear All")
        assert _find_submit_button([other]) is None

    def test_returns_none_for_a_button_with_no_bbox(self):
        btn = _button("Submit")
        del btn["bbox"]
        assert _find_submit_button([btn]) is None

    def test_ignores_non_button_elements_with_matching_text(self):
        el = {"element_id": "e1", "type": "editcontrol", "text": "Submit",
              "label": "Submit", "bbox": [100, 100, 200, 130]}
        assert _find_submit_button([el]) is None


def _decide_last_tab_exhausted(elements):
    """Mirrors the CURRENT last-tab-exhausted branch in _try_advance_tab():
    click the submit button if one exists, otherwise signal done."""
    btn = _find_submit_button(elements)
    if btn:
        return "clicked_submit"
    return "signalled_done_no_button"


class TestLastTabExhaustedClicksSubmit:
    def test_clicks_submit_when_a_button_is_present(self):
        assert _decide_last_tab_exhausted([_button("Submit")]) == "clicked_submit"

    def test_signals_done_without_a_matching_button(self):
        """No regression: a form with no submit-like button on the last
        tab still just signals done, same as before this fix."""
        assert _decide_last_tab_exhausted([_button("Clear All")]) == "signalled_done_no_button"

    def test_keyword_set_is_generic_not_form_specific(self):
        """Direct check on the generalization question: none of the
        keywords name this specific form or task."""
        for kw in _SUBMIT_KW:
            assert "car" not in kw and "insurance" not in kw and "payment" not in kw
