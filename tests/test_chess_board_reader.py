import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHESS_DIR = os.path.join(_ROOT, "components", "chess")
for _p in (_ROOT, _CHESS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from board_reader import board_from_piece_classes, board_to_fen_ranks, diff_boards, STARTING_FEN_RANKS

# Real class names captured directly from Chess.com's public /analysis
# board (2026-08-27) -- not fabricated. If Chess.com ever changes this
# markup, this is the exact fixture that would need updating, and this
# test is what would catch the drift.
REAL_STARTING_POSITION_CLASSES = [
    "piece br square-88", "piece bn square-78", "piece bb square-68", "piece bk square-58",
    "piece bq square-48", "piece bb square-38", "piece bn square-28", "piece br square-18",
    "piece bp square-87", "piece bp square-77", "piece bp square-67", "piece bp square-57",
    "piece bp square-47", "piece bp square-37", "piece bp square-27", "piece bp square-17",
    "piece wp square-82", "piece wp square-72", "piece wp square-62", "piece wp square-52",
    "piece wp square-42", "piece wp square-32", "piece wp square-22", "piece wp square-12",
    "piece wr square-81", "piece wn square-71", "piece wb square-61", "piece wk square-51",
    "piece wq square-41", "piece wb square-31", "piece wn square-21", "piece wr square-11",
]


class TestBoardFromPieceClasses:
    def test_real_starting_position_has_32_pieces(self):
        board = board_from_piece_classes(REAL_STARTING_POSITION_CLASSES)
        assert len(board) == 32

    def test_white_king_is_on_e1(self):
        board = board_from_piece_classes(REAL_STARTING_POSITION_CLASSES)
        assert board["e1"] == ("w", "k")

    def test_black_king_is_on_e8(self):
        board = board_from_piece_classes(REAL_STARTING_POSITION_CLASSES)
        assert board["e8"] == ("b", "k")

    def test_white_rooks_on_a1_and_h1(self):
        board = board_from_piece_classes(REAL_STARTING_POSITION_CLASSES)
        assert board["a1"] == ("w", "r")
        assert board["h1"] == ("w", "r")

    def test_ignores_unrelated_classes(self):
        # A real page also has ".piece" elements that aren't board pieces
        # at all (e.g. "captured-pieces-cpiece captured-pieces-score",
        # seen in the real page) -- these must not produce a bogus square.
        board = board_from_piece_classes(["captured-pieces-cpiece captured-pieces-score"])
        assert board == {}


class TestBoardToFenRanks:
    def test_real_starting_position_produces_the_standard_fen(self):
        board = board_from_piece_classes(REAL_STARTING_POSITION_CLASSES)
        assert board_to_fen_ranks(board) == STARTING_FEN_RANKS


class TestDiffBoards:
    def test_detects_a_simple_pawn_move(self):
        before = board_from_piece_classes(REAL_STARTING_POSITION_CLASSES)
        after = dict(before)
        del after["e2"]
        after["e4"] = ("w", "p")

        result = diff_boards(before, after)
        assert result == ("e2", "e4", ("w", "p"))

    def test_detects_a_capture(self):
        before = {"e4": ("w", "p"), "d5": ("b", "p")}
        after = {"e4": ("w", "p")}
        del after["e4"]
        after["d5"] = ("w", "p")

        result = diff_boards(before, after)
        assert result == ("e4", "d5", ("w", "p"))

    def test_detects_kingside_castling(self):
        before = {"e1": ("w", "k"), "h1": ("w", "r")}
        after = {"g1": ("w", "k"), "f1": ("w", "r")}

        result = diff_boards(before, after)
        assert result == ("e1", "g1", ("w", "k"))

    def test_returns_none_when_nothing_changed(self):
        board = board_from_piece_classes(REAL_STARTING_POSITION_CLASSES)
        assert diff_boards(board, dict(board)) is None
