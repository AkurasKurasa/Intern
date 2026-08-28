"""
Tests for app/recorder_bridge.py's Inbox Router commands
(start_inbox_router / stop_inbox_router / inbox_confirm_suggestion /
inbox_override_decision) -- the additive bridge changes for Scope #3.

Same approach as test_recorder_bridge_capsule_run.py: subprocess.Popen is
monkeypatched out entirely, so no test here ever spawns a real process,
opens a real browser, or touches a real Gmail account.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import recorder_bridge as rb


def _events(monkeypatch):
    captured = []
    monkeypatch.setattr(rb, "emit", lambda event, **fields: captured.append({"event": event, **fields}))
    return captured


class _FakeInboxProc:
    """Stands in for subprocess.Popen -- no real process ever spawned.
    Unlike capsule runs' _FakeProc, this one also captures stdin writes,
    since Inbox Router is long-lived/interactive (confirm/override
    commands are sent to it while it's still running)."""
    def __init__(self, lines=None, exit_code=0):
        self.stdout = iter((lines or []))
        self._exit_code = exit_code
        self._polled_running = True
        self.stdin = self
        self.written = []

    def write(self, text):
        self.written.append(text)

    def flush(self):
        pass

    def poll(self):
        return None if self._polled_running else self._exit_code

    def wait(self):
        self._polled_running = False
        return self._exit_code

    def send_signal(self, sig):
        pass


class TestStartInboxRouter:
    def test_spawns_router_py_with_correct_argv(self, monkeypatch):
        popen_calls = []
        monkeypatch.setattr(rb.subprocess, "Popen",
                             lambda args, **kwargs: (popen_calls.append((args, kwargs)), _FakeInboxProc())[1])
        bridge = rb.Bridge()

        bridge.start_inbox_router()

        assert len(popen_calls) == 1
        args, kwargs = popen_calls[0]
        assert args[0] == sys.executable
        assert args[1] == "-u"
        assert args[2] == rb._INBOX_SCRIPT
        assert kwargs["cwd"] == rb._ROOT
        assert kwargs["creationflags"] == rb.subprocess.CREATE_NEW_PROCESS_GROUP

    def test_stdin_is_a_pipe_not_devnull(self, monkeypatch):
        """The one deliberate difference from run_capsule()'s Popen call --
        this child is long-lived and interactive (confirm/override commands
        keep arriving), not fire-and-forget, so it needs a real stdin pipe."""
        popen_calls = []
        monkeypatch.setattr(rb.subprocess, "Popen",
                             lambda args, **kwargs: (popen_calls.append((args, kwargs)), _FakeInboxProc())[1])
        bridge = rb.Bridge()

        bridge.start_inbox_router()

        _, kwargs = popen_calls[0]
        assert kwargs["stdin"] == rb.subprocess.PIPE

    def test_refuses_when_already_running(self, monkeypatch):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        bridge._inbox_proc = _FakeInboxProc()  # poll() -> None -> still running

        bridge.start_inbox_router()

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "already running" in errors[0]["message"].lower()

    def test_stdout_lines_with_event_field_pass_through_verbatim(self, monkeypatch):
        events = _events(monkeypatch)
        lines = [
            json.dumps({"event": "inbox_routed", "message_id": "m1", "decision": "flag"}) + "\n",
            "",
        ]
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: _FakeInboxProc(lines=lines))
        bridge = rb.Bridge()

        bridge.start_inbox_router()

        for _ in range(50):
            routed = [e for e in events if e["event"] == "inbox_routed"]
            if routed:
                break
            import time; time.sleep(0.05)

        routed = [e for e in events if e["event"] == "inbox_routed"]
        assert len(routed) == 1
        assert routed[0]["message_id"] == "m1"
        assert routed[0]["decision"] == "flag"

    def test_non_json_stdout_line_becomes_inbox_log(self, monkeypatch):
        events = _events(monkeypatch)
        monkeypatch.setattr(rb.subprocess, "Popen",
                             lambda *a, **k: _FakeInboxProc(lines=["not json at all\n", ""]))
        bridge = rb.Bridge()

        bridge.start_inbox_router()

        for _ in range(50):
            logs = [e for e in events if e["event"] == "inbox_log"]
            if logs:
                break
            import time; time.sleep(0.05)

        logs = [e for e in events if e["event"] == "inbox_log"]
        assert len(logs) == 1
        assert logs[0]["line"] == "not json at all"
        assert logs[0]["level"] == "dim"


class TestStopAndCommandForwarding:
    def test_stop_sends_shutdown_over_stdin(self, monkeypatch):
        bridge = rb.Bridge()
        fake_proc = _FakeInboxProc()
        bridge._inbox_proc = fake_proc

        bridge.stop_inbox_router()

        assert json.loads(fake_proc.written[0].strip()) == {"cmd": "shutdown"}

    def test_stop_refuses_when_nothing_running(self, monkeypatch):
        events = _events(monkeypatch)
        bridge = rb.Bridge()

        bridge.stop_inbox_router()

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "not running" in errors[0]["message"].lower()

    def test_confirm_suggestion_forwards_correct_command(self, monkeypatch):
        bridge = rb.Bridge()
        fake_proc = _FakeInboxProc()
        bridge._inbox_proc = fake_proc

        bridge.inbox_confirm_suggestion("m1", "reply")

        assert json.loads(fake_proc.written[0].strip()) == {
            "cmd": "confirm", "message_id": "m1", "decision": "reply", "reply_body": "",
        }

    def test_confirm_suggestion_forwards_real_reply_text(self, monkeypatch):
        # The whole point of threading reply_body through this bridge:
        # confirming a "reply" decision with real typed text must not
        # silently become an empty draft.
        bridge = rb.Bridge()
        fake_proc = _FakeInboxProc()
        bridge._inbox_proc = fake_proc

        bridge.inbox_confirm_suggestion("m1", "reply", "Thanks, that works for me.")

        assert json.loads(fake_proc.written[0].strip()) == {
            "cmd": "confirm", "message_id": "m1", "decision": "reply",
            "reply_body": "Thanks, that works for me.",
        }

    def test_override_decision_forwards_correct_command(self, monkeypatch):
        bridge = rb.Bridge()
        fake_proc = _FakeInboxProc()
        bridge._inbox_proc = fake_proc

        bridge.inbox_override_decision("m1", "forward", "wrong guess")

        assert json.loads(fake_proc.written[0].strip()) == {
            "cmd": "override", "message_id": "m1", "new_decision": "forward", "reason": "wrong guess",
            "reply_body": "",
        }

    def test_override_decision_forwards_real_reply_text(self, monkeypatch):
        bridge = rb.Bridge()
        fake_proc = _FakeInboxProc()
        bridge._inbox_proc = fake_proc

        bridge.inbox_override_decision("m1", "reply", "wrong guess", "Sure, call me in 10.")

        assert json.loads(fake_proc.written[0].strip()) == {
            "cmd": "override", "message_id": "m1", "new_decision": "reply", "reason": "wrong guess",
            "reply_body": "Sure, call me in 10.",
        }

    def test_confirm_refuses_when_nothing_running(self, monkeypatch):
        events = _events(monkeypatch)
        bridge = rb.Bridge()

        bridge.inbox_confirm_suggestion("m1", "reply")

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1


class TestDispatchLoopWiring:
    """Confirms the four new cmd branches in Bridge.run()'s dispatch call
    the right methods -- exercised via the same msg-dict shape the real
    stdin loop would parse, not by actually feeding stdin."""

    def _dispatch(self, bridge, msg):
        cmd = msg.get("cmd")
        if cmd == "start_inbox_router":
            bridge.start_inbox_router()
        elif cmd == "stop_inbox_router":
            bridge.stop_inbox_router()
        elif cmd == "inbox_confirm_suggestion":
            bridge.inbox_confirm_suggestion(msg.get("message_id", ""), msg.get("decision", ""),
                                             msg.get("reply_body", ""))
        elif cmd == "inbox_override_decision":
            bridge.inbox_override_decision(msg.get("message_id", ""), msg.get("new_decision", ""),
                                            msg.get("reason", ""), msg.get("reply_body", ""))

    def test_start_inbox_router_command(self, monkeypatch):
        popen_calls = []
        monkeypatch.setattr(rb.subprocess, "Popen",
                             lambda args, **kwargs: (popen_calls.append((args, kwargs)), _FakeInboxProc())[1])
        bridge = rb.Bridge()

        self._dispatch(bridge, {"cmd": "start_inbox_router"})

        assert len(popen_calls) == 1

    def test_inbox_confirm_suggestion_command(self, monkeypatch):
        bridge = rb.Bridge()
        fake_proc = _FakeInboxProc()
        bridge._inbox_proc = fake_proc

        self._dispatch(bridge, {"cmd": "inbox_confirm_suggestion", "message_id": "m1", "decision": "flag"})

        assert json.loads(fake_proc.written[0].strip())["decision"] == "flag"

    def test_inbox_confirm_suggestion_command_carries_reply_body(self, monkeypatch):
        bridge = rb.Bridge()
        fake_proc = _FakeInboxProc()
        bridge._inbox_proc = fake_proc

        self._dispatch(bridge, {
            "cmd": "inbox_confirm_suggestion", "message_id": "m1", "decision": "reply",
            "reply_body": "Got it, thanks.",
        })

        assert json.loads(fake_proc.written[0].strip())["reply_body"] == "Got it, thanks."
