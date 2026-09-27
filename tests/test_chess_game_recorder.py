import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHESS_DIR = os.path.join(_ROOT, "components", "chess")
for _p in (_ROOT, _CHESS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from board_reader import board_from_piece_classes
import game_recorder

STARTING = [
    "piece br square-88", "piece bn square-78", "piece bb square-68", "piece bk square-58",
    "piece bq square-48", "piece bb square-38", "piece bn square-28", "piece br square-18",
    "piece bp square-87", "piece bp square-77", "piece bp square-67", "piece bp square-57",
    "piece bp square-47", "piece bp square-37", "piece bp square-27", "piece bp square-17",
    "piece wp square-82", "piece wp square-72", "piece wp square-62", "piece wp square-52",
    "piece wp square-42", "piece wp square-32", "piece wp square-22", "piece wp square-12",
    "piece wr square-81", "piece wn square-71", "piece wb square-61", "piece wk square-51",
    "piece wq square-41", "piece wb square-31", "piece wn square-21", "piece wr square-11",
]
# STARTING with the white e-pawn (square-52, i.e. e2) moved to e4 (square-54).
AFTER_E4 = [c.replace("square-52", "square-54") if c.startswith("piece wp square-52") else c
            for c in STARTING]
# AFTER_E4 with the black e-pawn (square-57, i.e. e7) moved to e5 (square-55).
AFTER_E5 = [c.replace("square-57", "square-55") if c.startswith("piece bp square-57") else c
            for c in AFTER_E4]


class FakePage:
    """Stands in for a real Playwright page: returns a scripted sequence
    of board states, one per poll, so record_session()'s loop and
    control-flow logic can be tested without a real browser or the
    threading a live page would require alongside real clicks."""

    def __init__(self, board_sequence):
        self._sequence = list(board_sequence)
        self._index = 0

    def wait_for_timeout(self, _ms):
        pass

    def eval_on_selector_all(self, _selector, _js):
        board = self._sequence[min(self._index, len(self._sequence) - 1)]
        self._index += 1
        return board


class TestRecordSession:
    def test_records_two_real_moves_to_the_jsonl_file(self, tmp_path):
        page = FakePage([STARTING, AFTER_E4, AFTER_E5])
        out_path = tmp_path / "game.jsonl"

        count = game_recorder.record_session(page, out_path, poll_interval=0, max_moves=2)

        assert count == 2
        lines = [json.loads(line) for line in out_path.read_text().splitlines()]
        assert len(lines) == 2
        assert lines[0]["from_square"] == "e2"
        assert lines[0]["to_square"] == "e4"
        assert lines[0]["color"] == "w"
        assert lines[0]["piece_type"] == "p"
        assert lines[1]["from_square"] == "e7"
        assert lines[1]["to_square"] == "e5"
        assert lines[1]["color"] == "b"

    def test_fen_before_and_after_are_correct(self, tmp_path):
        page = FakePage([STARTING, AFTER_E4])
        out_path = tmp_path / "game.jsonl"

        game_recorder.record_session(page, out_path, poll_interval=0, max_moves=1)

        entry = json.loads(out_path.read_text().splitlines()[0])
        assert entry["fen_before"] == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
        assert entry["fen_after"] == "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR"

    def test_no_move_detected_when_board_is_unchanged(self, tmp_path):
        # Two identical reads in a row (a poll landing between moves,
        # nothing happened yet) must not produce a false move entry.
        page = FakePage([STARTING, STARTING, AFTER_E4])
        out_path = tmp_path / "game.jsonl"

        count = game_recorder.record_session(page, out_path, poll_interval=0, max_moves=1)

        assert count == 1
        lines = out_path.read_text().splitlines()
        assert len(lines) == 1

    def test_appends_to_an_existing_file_rather_than_overwriting(self, tmp_path):
        out_path = tmp_path / "game.jsonl"
        out_path.write_text(json.dumps({"move_number": 0, "note": "pre-existing"}) + "\n")

        page = FakePage([STARTING, AFTER_E4])
        game_recorder.record_session(page, out_path, poll_interval=0, max_moves=1)

        lines = out_path.read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["note"] == "pre-existing"
