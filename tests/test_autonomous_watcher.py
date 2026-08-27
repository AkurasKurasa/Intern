import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMP = os.path.join(_ROOT, "components")
_INBOX_DIR = os.path.join(_COMP, "inbox_router")
for _p in (_ROOT, _COMP, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.capsule import CapsuleRegistry
import autonomous_watcher as watcher


class FakePopen:
    """Stands in for subprocess.Popen -- records every call instead of
    actually spawning a real process, so tests can verify exactly what
    WOULD have been launched (e.g. real Scope #2 automation) without any
    real automation ever running during a test."""

    def __init__(self):
        self.calls = []
        self._next_pid = 1000

    def __call__(self, argv, cwd=None):
        self._next_pid += 1
        self.calls.append({"argv": argv, "cwd": cwd})
        return _FakeProcess(self._next_pid)


class _FakeProcess:
    def __init__(self, pid):
        self.pid = pid


class FakeRouter:
    """Returns a scripted sequence of pending-entry dicts from
    process_next_unprocessed(), then None once the script is exhausted
    -- mirrors InboxRouter's real contract without needing a real
    classification pipeline or real mail."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def process_next_unprocessed(self):
        self.calls += 1
        if self._script:
            return self._script.pop(0)
        return None


def _entry(decision, capsule_name="", message_id="m1", subject="Test email"):
    return {
        "message_id": message_id, "subject": subject, "decision": decision,
        "capsule_name": capsule_name, "rationale": "x", "confidence": 0.9,
    }


def _build_registry(tmp_path, script_entrypoint=None):
    registry_path = tmp_path / "registry.json"
    capsules = [{
        "name": "Sheet-to-Portal Matcher", "description": "", "model_path": "",
        "trigger_keywords": [], "trigger_apps": [], "kind": "script",
        "entrypoint": script_entrypoint or "components/scope2/automate.py",
        "args": ["--variant", "v0_base", "--commit"], "cwd": "",
    }]
    registry_path.write_text(json.dumps({"capsules": capsules}), encoding="utf-8")
    return CapsuleRegistry(registry_path=str(registry_path))


class TestDispatchScope2:
    def test_launches_the_real_matched_capsule(self, tmp_path):
        registry = _build_registry(tmp_path)
        popen = FakePopen()
        entry = _entry("route_scope2", capsule_name="Sheet-to-Portal Matcher")

        result = watcher.dispatch_scope2(entry, registry, _ROOT, popen=popen)

        assert result["ok"] is True
        assert result["pid"] == 1001
        assert len(popen.calls) == 1
        assert popen.calls[0]["argv"][-3:] == ["--variant", "v0_base", "--commit"]
        assert "automate.py" in popen.calls[0]["argv"][2]

    def test_unknown_capsule_name_does_not_launch_anything(self, tmp_path):
        registry = _build_registry(tmp_path)
        popen = FakePopen()
        entry = _entry("route_scope2", capsule_name="Some Capsule That Doesn't Exist")

        result = watcher.dispatch_scope2(entry, registry, _ROOT, popen=popen)

        assert result["ok"] is False
        assert "not found" in result["reason"]
        assert popen.calls == []

    def test_missing_entrypoint_file_does_not_launch_anything(self, tmp_path):
        registry = _build_registry(tmp_path, script_entrypoint="components/scope2/does_not_exist.py")
        popen = FakePopen()
        entry = _entry("route_scope2", capsule_name="Sheet-to-Portal Matcher")

        result = watcher.dispatch_scope2(entry, registry, _ROOT, popen=popen)

        assert result["ok"] is False
        assert "does_not_exist.py" in result["reason"]
        assert popen.calls == []


class TestHandleEntry:
    def test_route_scope2_dispatches_and_logs(self, tmp_path):
        registry = _build_registry(tmp_path)
        popen = FakePopen()
        entry = _entry("route_scope2", capsule_name="Sheet-to-Portal Matcher", subject="Grades ready")
        dispatch_log = tmp_path / "dispatch_log.jsonl"
        needs_attention = tmp_path / "needs_attention.jsonl"

        outcome = watcher.handle_entry(entry, registry, _ROOT, popen=popen,
                                        dispatch_log_path=dispatch_log,
                                        needs_attention_path=needs_attention)

        assert outcome["action"] == "dispatched_scope2"
        assert len(popen.calls) == 1
        logged = json.loads(dispatch_log.read_text().splitlines()[0])
        assert logged["subject"] == "Grades ready"
        assert logged["dispatch_result"]["ok"] is True
        assert not needs_attention.exists()

    def test_route_scope1_surfaces_but_never_launches_anything(self, tmp_path):
        registry = _build_registry(tmp_path)
        popen = FakePopen()
        entry = _entry("route_scope1", capsule_name="form_filling", subject="Car insurance intake")
        dispatch_log = tmp_path / "dispatch_log.jsonl"
        needs_attention = tmp_path / "needs_attention.jsonl"

        outcome = watcher.handle_entry(entry, registry, _ROOT, popen=popen,
                                        dispatch_log_path=dispatch_log,
                                        needs_attention_path=needs_attention)

        assert outcome["action"] == "needs_attention"
        # The load-bearing safety property: route_scope1 must NEVER spawn
        # a process by itself -- that's real mouse/keyboard on the real
        # screen, and only a human pressing Play gets to trigger that.
        assert popen.calls == []
        logged = json.loads(needs_attention.read_text().splitlines()[0])
        assert logged["subject"] == "Car insurance intake"
        assert not dispatch_log.exists()

    def test_other_decisions_are_left_pending_untouched(self, tmp_path):
        registry = _build_registry(tmp_path)
        popen = FakePopen()
        dispatch_log = tmp_path / "dispatch_log.jsonl"
        needs_attention = tmp_path / "needs_attention.jsonl"

        for decision in ("reply", "forward", "flag", "leave_alone"):
            outcome = watcher.handle_entry(_entry(decision), registry, _ROOT, popen=popen,
                                            dispatch_log_path=dispatch_log,
                                            needs_attention_path=needs_attention)
            assert outcome["action"] == "left_pending"

        assert popen.calls == []
        assert not dispatch_log.exists()
        assert not needs_attention.exists()

    def test_route_scope2_with_no_capsule_name_is_left_pending_not_dispatched(self, tmp_path):
        # A rule/model match without a resolvable capsule_name must not
        # attempt a dispatch it can't actually carry out.
        registry = _build_registry(tmp_path)
        popen = FakePopen()

        outcome = watcher.handle_entry(_entry("route_scope2", capsule_name=""), registry, _ROOT, popen=popen)

        assert outcome["action"] == "left_pending"
        assert popen.calls == []


class TestWatch:
    def test_stop_when_idle_exits_without_sleeping(self, tmp_path):
        router = FakeRouter([
            _entry("flag", message_id="m1"),
            _entry("route_scope2", capsule_name="Sheet-to-Portal Matcher", message_id="m2"),
        ])
        registry = _build_registry(tmp_path)
        popen = FakePopen()
        slept = []

        outcomes = watcher.watch(router, registry, _ROOT, stop_when_idle=True,
                                  popen=popen, sleep=lambda s: slept.append(s),
                                  dispatch_log_path=tmp_path / "d.jsonl",
                                  needs_attention_path=tmp_path / "n.jsonl")

        assert len(outcomes) == 2
        assert outcomes[0]["action"] == "left_pending"
        assert outcomes[1]["action"] == "dispatched_scope2"
        assert slept == []

    def test_continuous_mode_sleeps_when_idle_then_keeps_watching(self, tmp_path):
        router = FakeRouter([_entry("flag")])  # one entry, then None forever
        registry = _build_registry(tmp_path)
        popen = FakePopen()
        slept = []

        def fake_sleep(seconds):
            slept.append(seconds)
            if len(slept) >= 3:
                raise StopIteration  # ends the test's own patience, not the loop's

        try:
            watcher.watch(router, registry, _ROOT, poll_interval=5, stop_when_idle=False,
                          popen=popen, sleep=fake_sleep,
                          dispatch_log_path=tmp_path / "d.jsonl",
                          needs_attention_path=tmp_path / "n.jsonl")
        except StopIteration:
            pass

        assert slept == [5, 5, 5]

    def test_max_iterations_caps_even_with_more_pending(self, tmp_path):
        router = FakeRouter([_entry("flag", message_id=f"m{i}") for i in range(5)])
        registry = _build_registry(tmp_path)
        popen = FakePopen()

        outcomes = watcher.watch(router, registry, _ROOT, stop_when_idle=True,
                                  max_iterations=2, popen=popen, sleep=lambda s: None,
                                  dispatch_log_path=tmp_path / "d.jsonl",
                                  needs_attention_path=tmp_path / "n.jsonl")

        assert len(outcomes) == 2
