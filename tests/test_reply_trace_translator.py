import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gmail_client import EmailMessage, MockGmailClient
import reply_recorder as rr
import reply_trace_translator as translator


# The real placeholder prose local_ui/index.html puts on the reply textarea.
# WebObserver's display-label priority chain (aria-label > placeholder > title
# > name > inner_text) means this -- NOT the message id -- is what lands in
# text/label. Hard-coding the message id onto "label" (as these fixtures used
# to) invented an element the real observer never produces, which is exactly
# why the shadowed-name bug survived every one of these tests.
_REAL_PLACEHOLDER = "Type your reply -- this exact text is what gets sent, nothing is written for you."

# The literal snackbar strings local_ui/app.js shows, verbatim.
STATUS_CONFIRMED = "Confirmed."
STATUS_OVERRIDDEN = "Overridden."
STATUS_ARCHIVED = "Archived."


_UNSET = object()


def _status_element(text):
    """What WebObserver extracts from <div id="snackbar" role="status">."""
    return {
        "element_id": "web_9", "type": "custom", "control_type": "div",
        "text": text, "value": "", "label": text, "name": "",
        "enabled": True, "visible": True, "source": "web",
    }


def _step(step_idx, textarea_value="", message_id="",
          include_textarea_in_state=True, include_textarea_in_next_state=None,
          next_status_text=None, next_state=_UNSET):
    """Build a step with state and next_state that can differ.

    If include_textarea_in_next_state is None, defaults to include_textarea_in_state
    (i.e., state and next_state are the same). Set explicitly to test transitions.

    next_status_text puts a role="status" element with that exact text into
    next_state -- this is the POSITIVE evidence of a real submission the
    translator now requires. next_state=<dict/None> overrides next_state
    wholesale (used to test missing/empty next_state).
    """
    if include_textarea_in_next_state is None:
        include_textarea_in_next_state = include_textarea_in_state

    def _build_elements(include_textarea, status_text=None):
        elements = []
        if include_textarea:
            elements.append({
                "element_id": "web_0", "type": "input", "control_type": "textarea",
                # text/label carry the PLACEHOLDER, same as the real observer;
                # the message id lives on its own dedicated "name" key.
                "text": _REAL_PLACEHOLDER, "value": textarea_value,
                "label": _REAL_PLACEHOLDER, "name": message_id,
                "enabled": True, "visible": True, "source": "web",
            })
        if status_text is not None:
            elements.append(_status_element(status_text))
        return elements

    step = {
        "trace_id": f"live_step_{step_idx:04d}", "timestamp": "2026-08-28T00:00:00",
        "duration": 1.0, "type": "web",
        "state": {"application": "browser", "elements": _build_elements(include_textarea_in_state)},
        "mouse": {}, "keyboard": {},
        "next_state": {
            "application": "browser",
            "elements": _build_elements(include_textarea_in_next_state, next_status_text),
        },
    }
    if next_state is not _UNSET:
        if next_state is None:
            step.pop("next_state")
        else:
            step["next_state"] = next_state
    return step


def _write_session(tmp_path, steps):
    session_dir = tmp_path / "session_20260828_000000"
    session_dir.mkdir()
    for i, step in enumerate(steps):
        (session_dir / f"live_step_{i:04d}.json").write_text(json.dumps(step), encoding="utf-8")
    return str(session_dir)


def _build_gmail_client(tmp_path, inbox):
    data_dir = tmp_path / "data"
    os.makedirs(data_dir, exist_ok=True)
    (data_dir / "mock_inbox.json").write_text(json.dumps({"inbox": inbox, "sent": []}), encoding="utf-8")
    return MockGmailClient(data_dir=str(data_dir))


def _msg(mid, sender_email, subject, body="body text"):
    return {
        "id": mid, "thread_id": mid, "sender": f"Someone <{sender_email}>", "sender_email": sender_email,
        "subject": subject, "snippet": "", "body_text": body, "received_at": "2026-08-27T00:00:00Z",
        "labels": ["INBOX"],
    }


def _write_history(tmp_path, entries):
    """entries: list of dicts with at least message_id and decision.
    Writes in router.py's real format: {"messages": [...]}"""
    history_path = tmp_path / "data" / "routed_history.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump({"messages": entries}, f)
    return str(history_path)


class TestTranslateSession:
    def test_submitted_reply_is_recorded(self, tmp_path):
        # One step: state has textarea with real text, next_state shows the
        # real "Confirmed." status message -- positive proof the Confirm click
        # actually submitted within this step's own before/after pair.
        steps = [
            _step(0, textarea_value="Thanks, that works for me.", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=False,
                  next_status_text=STATUS_CONFIRMED),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        history_path = _write_history(tmp_path, [{"message_id": "m1", "decision": "reply"}])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path, history_path=history_path)

        assert count == 1
        examples = rr.load_reply_examples(examples_path)
        assert len(examples) == 1
        assert examples[0]["message_id"] == "m1"
        assert examples[0]["reply_body"] == "Thanks, that works for me."
        assert examples[0]["source"] == "live"

    def test_single_step_session_with_submitted_reply(self, tmp_path):
        # Single-file session where the action submits a reply: state has
        # textarea, next_state carries the success status. Confirms the
        # algorithm works with just one step file (no "last step" logic needed).
        steps = [
            _step(0, textarea_value="Sure, call me in 10.", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=False,
                  next_status_text=STATUS_OVERRIDDEN),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "quick question")])
        history_path = _write_history(tmp_path, [{"message_id": "m1", "decision": "reply"}])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path, history_path=history_path)

        assert count == 1

    def test_textarea_still_open_in_next_state_is_not_counted(self, tmp_path):
        # One step: state has textarea, next_state still has the same textarea
        # (value may have changed, but the detail view stayed open).
        # This action was a keystroke, not a submit.
        steps = [
            _step(0, textarea_value="typing...", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=True),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 0
        assert rr.load_reply_examples(examples_path) == []

    def test_empty_textarea_produces_no_example(self, tmp_path):
        # Even though next_state carries a real success status (a genuine
        # submission), an empty reply_body is skipped (nothing real to save).
        steps = [
            _step(0, textarea_value="", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=False,
                  next_status_text=STATUS_CONFIRMED),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 0

    def test_unresolvable_message_id_is_skipped_without_raising(self, tmp_path):
        # A genuinely submitted reply (success status present in next_state)
        # whose message_id does not resolve in the inbox -- skipped silently
        # with logging.
        steps = [
            _step(0, textarea_value="Thanks!", message_id="does-not-exist",
                  include_textarea_in_state=True, include_textarea_in_next_state=False,
                  next_status_text=STATUS_CONFIRMED),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[])  # empty inbox -- nothing resolves
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)  # must not raise

        assert count == 0

    def test_message_id_comes_from_name_not_from_placeholder_label(self, tmp_path):
        """C1 regression. The real WebObserver puts the textarea's PLACEHOLDER
        prose in text/label (aria-label > placeholder > title > name), so the
        message id is only ever reachable via the dedicated `name` key. Reading
        label/text here yields the placeholder sentence, gmail_client resolves
        nothing, and the translator writes zero examples -- forever, silently."""
        steps = [
            _step(0, textarea_value="On it, thanks.", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=False,
                  next_status_text=STATUS_CONFIRMED),
        ]
        # Sanity-check the fixture itself matches the real observer's output.
        textarea = steps[0]["state"]["elements"][0]
        assert textarea["label"] == _REAL_PLACEHOLDER
        assert textarea["text"] == _REAL_PLACEHOLDER
        assert textarea["name"] == "m1"

        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        history_path = _write_history(tmp_path, [{"message_id": "m1", "decision": "reply"}])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path, history_path=history_path)

        assert count == 1
        assert rr.load_reply_examples(examples_path)[0]["message_id"] == "m1"

    def test_abandoned_draft_closed_by_back_is_not_recorded(self, tmp_path):
        """I3 regression. Back hides the textarea exactly like Confirm does but
        shows NO status message. Under the old absence-based rule this typed,
        deliberately-abandoned draft was recorded as a sent reply."""
        steps = [
            _step(0, textarea_value="draft I decided not to send", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=False,
                  next_status_text=None),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 0
        assert rr.load_reply_examples(examples_path) == []

    def test_abandoned_draft_closed_by_archive_is_not_recorded(self, tmp_path):
        """I3, the other half. Archive DOES show a status message -- but it's
        "Archived.", not a submit. Matching on "any status message appeared"
        would still record this; only the two real submit strings count."""
        steps = [
            _step(0, textarea_value="draft I decided not to send", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=False,
                  next_status_text=STATUS_ARCHIVED),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 0

    def test_missing_next_state_is_not_recorded(self, tmp_path):
        """I4 regression. No next_state key at all = no evidence. The old rule
        failed OPEN here ("no textarea in next_state" is trivially true of an
        absent next_state) and recorded the draft as submitted."""
        steps = [
            _step(0, textarea_value="typed but we have no idea what happened", message_id="m1",
                  next_state=None),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 0

    def test_empty_next_state_is_not_recorded(self, tmp_path):
        """I4, the shape the C2 Playwright thread bug actually produced: a
        present-but-empty next_state. Must also count as no evidence."""
        steps = [
            _step(0, textarea_value="typed but the snapshot came back blank", message_id="m1",
                  next_state={"application": "browser", "elements": []}),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(session_dir, gmail_client, examples_path)

        assert count == 0

    def test_no_step_files_returns_zero(self, tmp_path):
        session_dir = tmp_path / "empty_session"
        session_dir.mkdir()
        gmail_client = _build_gmail_client(tmp_path, inbox=[])
        examples_path = str(tmp_path / "reply_examples.jsonl")

        count = translator.translate_session(str(session_dir), gmail_client, examples_path)

        assert count == 0


class TestScheduleRouting:
    def test_schedule_decision_writes_to_schedule_file_not_reply_examples(self, tmp_path):
        steps = [
            _step(0, textarea_value="Aug 30 -- vendor call", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=False,
                  next_status_text=STATUS_CONFIRMED),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "vendor call")])
        history_path = _write_history(tmp_path, [{"message_id": "m1", "decision": "schedule"}])
        reply_examples_path = str(tmp_path / "reply_examples.jsonl")
        schedule_log_path = str(tmp_path / "schedule.txt")

        count = translator.translate_session(
            session_dir, gmail_client, reply_examples_path,
            history_path=history_path, schedule_log_path=schedule_log_path)

        assert count == 1
        assert not os.path.exists(reply_examples_path)
        with open(schedule_log_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Aug 30 -- vendor call" in content

    def test_reply_decision_still_writes_to_reply_examples_not_schedule(self, tmp_path):
        steps = [
            _step(0, textarea_value="Thanks, that works.", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=False,
                  next_status_text=STATUS_CONFIRMED),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "status update")])
        history_path = _write_history(tmp_path, [{"message_id": "m1", "decision": "reply"}])
        reply_examples_path = str(tmp_path / "reply_examples.jsonl")
        schedule_log_path = str(tmp_path / "schedule.txt")

        count = translator.translate_session(
            session_dir, gmail_client, reply_examples_path,
            history_path=history_path, schedule_log_path=schedule_log_path)

        assert count == 1
        assert not os.path.exists(schedule_log_path)
        examples = rr.load_reply_examples(reply_examples_path)
        assert len(examples) == 1

    def test_message_id_with_no_history_entry_is_skipped(self, tmp_path):
        steps = [
            _step(0, textarea_value="typed something", message_id="m1",
                  include_textarea_in_state=True, include_textarea_in_next_state=False,
                  next_status_text=STATUS_CONFIRMED),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("m1", "boss@work.com", "subject")])
        history_path = _write_history(tmp_path, [])  # no entry for m1 at all
        reply_examples_path = str(tmp_path / "reply_examples.jsonl")
        schedule_log_path = str(tmp_path / "schedule.txt")

        count = translator.translate_session(
            session_dir, gmail_client, reply_examples_path,
            history_path=history_path, schedule_log_path=schedule_log_path)

        assert count == 0

    def test_message_with_no_decision_when_history_has_other_entries_is_skipped(self, tmp_path):
        """Bug fix: when history file exists with entries for OTHER messages
        but not this one, the message must be SKIPPED, not silently recorded
        as reply (fabricating training data). This was the regression bug."""
        steps = [
            _step(0, textarea_value="typed something", message_id="unknown",
                  include_textarea_in_state=True, include_textarea_in_next_state=False,
                  next_status_text=STATUS_CONFIRMED),
        ]
        session_dir = _write_session(tmp_path, steps)
        gmail_client = _build_gmail_client(tmp_path, inbox=[_msg("unknown", "boss@work.com", "subject")])
        # History has entries for OTHER messages, but not for "unknown"
        history_path = _write_history(tmp_path, [{"message_id": "m1", "decision": "reply"}])
        reply_examples_path = str(tmp_path / "reply_examples.jsonl")
        schedule_log_path = str(tmp_path / "schedule.txt")

        count = translator.translate_session(
            session_dir, gmail_client, reply_examples_path,
            history_path=history_path, schedule_log_path=schedule_log_path)

        # Must be skipped, not recorded as reply (which was the bug)
        assert count == 0
        assert not os.path.exists(reply_examples_path)
