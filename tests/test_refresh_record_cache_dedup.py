"""
Regression tests for _refresh_record_cache()'s dedup guard.

Found live 2026-08-14, direct request ("any more improvements we could
do"): a single-record run logged 31 "Record cache refreshed" lines, every
one showing the IDENTICAL 176-field sample already sitting in
self._cached_record. 13 of _refresh_record_cache's 14 call sites in
run() are tab-advance handlers (7 tab-advances per record) -- each pays
for a real Win32 Notepad text read + full regex parse on every single
tab switch, even though the intake record is a static per-record
snapshot, not a live-edited file: the data can't have changed since the
last successful read of the SAME record.

self._attempted_record_num already tracks, reliably, which record_num
the cache last completed a genuine structured parse for (existing
invariant, set only after _parse_records() actually succeeds -- not
something new invented for this fix). The guard reuses it: skip the
whole read+parse when self._cached_record is already populated AND
self._record_num == self._attempted_record_num. A record advance changes
self._record_num BEFORE this is called for the new record, so the guard
naturally doesn't match then -- the reset logic (attempted-keys,
tab-idx, etc.) still runs exactly as it always has.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
import agent.agent as agent_module
from agent.agent import LLMAgent


def _make_agent(record_num=1):
    fake_source = MagicMock()
    fake_source.read_full_text = MagicMock(return_value="Policy Number: PAI-2026-00441")
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1,
                      data_source=fake_source, record_num=record_num)
    return agent, fake_source


def _parsed(record_num, fields):
    return {record_num: dict(fields)}


class TestDedupGuardSkipsRedundantReReads:
    def test_second_call_same_record_skips_the_real_read(self, monkeypatch):
        agent, fake_source = _make_agent(record_num=1)
        monkeypatch.setattr(agent_module, "_parse_records",
                             MagicMock(return_value=_parsed(1, {"Policy Number": "PAI-2026-00441"})))

        agent._refresh_record_cache({})
        assert fake_source.read_full_text.call_count == 1
        assert agent._cached_record == {"Policy Number": "PAI-2026-00441"}

        agent._refresh_record_cache({})
        # No second real read -- the cache is already confirmed populated
        # for this exact record_num.
        assert fake_source.read_full_text.call_count == 1
        assert agent._cached_record == {"Policy Number": "PAI-2026-00441"}

    def test_tab_advance_pattern_seven_calls_only_one_real_read(self, monkeypatch):
        """Mirrors the real live pattern -- 7 tab-advances per record,
        each calling this once. Only the first should ever actually read
        and parse; direct evidence against the log's 31-refreshes-for-
        176-fields-every-time finding."""
        agent, fake_source = _make_agent(record_num=1)
        monkeypatch.setattr(agent_module, "_parse_records",
                             MagicMock(return_value=_parsed(1, {"Policy Number": "PAI-2026-00441"})))

        for _ in range(7):
            agent._refresh_record_cache({})

        assert fake_source.read_full_text.call_count == 1


class TestDedupGuardNeverBlocksARealRecordChange:
    def test_record_advance_forces_a_real_refresh(self, monkeypatch):
        agent, fake_source = _make_agent(record_num=1)
        parse_mock = MagicMock(side_effect=[
            _parsed(1, {"Policy Number": "PAI-2026-00441"}),
            _parsed(2, {"Policy Number": "PAI-2026-00442"}),
        ])
        monkeypatch.setattr(agent_module, "_parse_records", parse_mock)

        agent._refresh_record_cache({})
        assert agent._cached_record == {"Policy Number": "PAI-2026-00441"}

        agent._record_num = 2
        agent._refresh_record_cache({})

        assert fake_source.read_full_text.call_count == 2
        assert agent._cached_record == {"Policy Number": "PAI-2026-00442"}

    def test_reset_logic_still_fires_on_a_real_record_change(self, monkeypatch):
        """The whole reason self._attempted_record_num exists -- clearing
        attempted/typed/leave-blank/checked state on a genuine record
        boundary -- must be completely unaffected by this fix."""
        agent, fake_source = _make_agent(record_num=1)
        monkeypatch.setattr(agent_module, "_parse_records",
                             MagicMock(side_effect=[
                                 _parsed(1, {"Policy Number": "PAI-2026-00441"}),
                                 _parsed(2, {"Policy Number": "PAI-2026-00442"}),
                             ]))
        agent._refresh_record_cache({})
        agent._attempted_keys.add("policy number")
        agent._typed_keys.add("policy number")
        agent._current_tab_idx = 5

        agent._record_num = 2
        agent._refresh_record_cache({})

        assert agent._attempted_keys == set()
        assert agent._typed_keys == set()
        assert agent._current_tab_idx == 0


class TestDedupGuardFirstCallAlwaysReads:
    def test_empty_cache_never_skips_even_with_matching_record_num(self, monkeypatch):
        """self._attempted_record_num starts equal to self._record_num at
        construction time -- the guard must not be fooled by that into
        skipping the very first, genuinely-needed read."""
        agent, fake_source = _make_agent(record_num=1)
        monkeypatch.setattr(agent_module, "_parse_records",
                             MagicMock(return_value=_parsed(1, {"Policy Number": "PAI-2026-00441"})))
        assert agent._cached_record == {}
        assert agent._record_num == agent._attempted_record_num

        agent._refresh_record_cache({})

        fake_source.read_full_text.assert_called_once()
        assert agent._cached_record == {"Policy Number": "PAI-2026-00441"}


class TestDedupGuardExcludesVisualReaderMode:
    def test_visual_reader_mode_is_never_skipped(self, monkeypatch):
        """visual_reader's own branch never updates
        self._attempted_record_num, so the 'confirmed populated'
        invariant the guard relies on was never established for it --
        must keep refreshing from self._visual_cache every call, exactly
        as it already does today."""
        agent, fake_source = _make_agent(record_num=1)
        agent._visual_reader = True
        agent._visual_cache = {"Policy Number": "PAI-2026-00441"}

        agent._refresh_record_cache({})
        agent._visual_cache = {"Policy Number": "PAI-2026-00442"}
        agent._refresh_record_cache({})

        assert agent._cached_record == {"Policy Number": "PAI-2026-00442"}
        fake_source.read_full_text.assert_not_called()
