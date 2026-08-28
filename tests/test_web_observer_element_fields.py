"""
REAL WebObserver tests -- a genuine Playwright browser against a genuine
HTML file on disk. No fakes, no hand-built element dicts.

Why this file exists
--------------------
Two Critical bugs shipped through a six-task plan because every test that
touched an "element dict" BUILT that dict by hand. Hand-built fixtures
encode what we believe the observer produces; they can never contradict
us. Both bugs lived in exactly that gap:

  C1  The reply textarea carries a real placeholder. WebObserver's display
      label is a priority chain (aria-label > placeholder > title > name >
      inner_text), so `name=` was UNREACHABLE on any element with a
      placeholder -- and the consumer silently read the placeholder prose
      as if it were a message id. Every fixture hard-coded the id onto
      "label", so no test could see it.

  I3  There was no way to tell "the user submitted" from "the user hit
      Back" -- both just hide the form. The page's role="status" snackbar
      is the positive evidence, and the observer wasn't collecting it.

These tests run the real extraction path end to end, which is the only
thing that could have caught either one.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

from observers.web_observer.web_observer import WebObserver

pytestmark = pytest.mark.skipif(
    not WebObserver().available,
    reason="playwright not installed -- real-browser observer tests skipped",
)

# Verbatim from components/inbox_router/local_ui/index.html.
_REAL_PLACEHOLDER = "Type your reply -- this exact text is what gets sent, nothing is written for you."

_PAGE = f"""<!doctype html>
<html><head><title>observer fixture</title></head><body>
  <textarea id="replyBody" name="m1"
            placeholder="{_REAL_PLACEHOLDER}"
            style="width:400px;height:120px"></textarea>
  <button id="confirmBtn" style="width:120px;height:32px">Confirm</button>
  <div id="snackbar" role="status" style="width:200px;height:40px">Confirmed.</div>
  <div id="hiddenSnackbar" role="status" style="width:200px;height:40px" hidden>Overridden.</div>
</body></html>
"""


@pytest.fixture(scope="module")
def elements(tmp_path_factory):
    """One real browser launch, one real snapshot, shared by every test."""
    page_path = tmp_path_factory.mktemp("web_observer") / "fixture.html"
    page_path.write_text(_PAGE, encoding="utf-8")

    obs = WebObserver(headless=True)
    assert obs.connect(url=page_path.as_uri()), "real browser failed to launch/connect"
    try:
        state = obs.snapshot()
    finally:
        obs.disconnect()

    assert state.get("elements"), "real snapshot came back with no elements at all"
    return state["elements"]


def _by_control_type(elements, control_type):
    return [e for e in elements if e.get("control_type") == control_type]


class TestNameAttributeIsNotShadowedByPlaceholder:
    def test_textarea_exposes_its_dom_name_attribute(self, elements):
        """C1, stated exactly: name= must survive even though placeholder is
        set and non-empty. This assertion fails against the pre-fix code --
        there was no "name" key on the element dict at all."""
        textareas = _by_control_type(elements, "textarea")
        assert len(textareas) == 1
        assert textareas[0]["name"] == "m1"

    def test_placeholder_still_wins_the_human_readable_label(self, elements):
        """The other half of the fix: this is real, deliberate UX prose and
        must keep being what a human-facing label shows. Proving both hold at
        once is the point -- a priority-chain reorder would break this."""
        textarea = _by_control_type(elements, "textarea")[0]
        assert textarea["label"] == _REAL_PLACEHOLDER
        assert textarea["text"] == _REAL_PLACEHOLDER

    def test_name_key_exists_on_every_element_even_without_the_attribute(self, elements):
        """A missing attribute must yield "" -- consumers use `.get("name") or
        ...` fallbacks, so None vs "" would change their behavior."""
        assert all("name" in e for e in elements)
        button = _by_control_type(elements, "button")[0]
        assert button["name"] == ""
        assert button["label"] == "Confirm"   # inner_text still reached


class TestStatusRoleIsExtracted:
    def test_visible_status_message_is_captured(self, elements):
        """I3: without [role='status'] in the selector this element is simply
        absent, and there is no positive evidence a submission happened."""
        texts = [e.get("text") for e in elements]
        assert "Confirmed." in texts

    def test_hidden_status_message_is_not_captured(self, elements):
        """A snackbar that hasn't fired must NOT look like it fired --
        otherwise the positive signal is worthless."""
        texts = [e.get("text") for e in elements]
        assert "Overridden." not in texts
