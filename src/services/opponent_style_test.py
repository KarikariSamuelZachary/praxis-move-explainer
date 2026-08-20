"""
Live test harness for opponent_style.py.

Three genetically distinct opponent datasets, served through a fake pool
that mimics `database.connection_pool` exactly enough for
`compute_opponent_style` to consume it. We deliberately do NOT use a
real Postgres for these tests — the same trick the module's contract
invites (it talks to the pool, not the DB).

We learn from the earlier repertoire-sampler fixture bug by using three
distinct datasets that exercise genuinely different code paths:

  A. THIN        -> only 2 games, varied openings, small sacrifice count.
                    Expect: sufficient=False (floor blocks us).

  B. ESTABLISHED -> 12 games, balanced openings across 4 families, low
                    natural sacrifice rate. No recency tilt.
                    Expect: sufficient=True, family_lean spread across
                    the 4 families, sacrifice_frequency in the v1 noise
                    band (a few percent or zero).

  C. RECENT-SHIFT -> 12 games = 6 OLD low-sac positional games (end_time
                    four years ago) + 6 RECENT high-sac games (end_time
                    yesterday). The games are deliberately engineered so
                    the recent sub-pile has many piece-drops and the old
                    sub-pile has none.
                    Expect: recency-weighted sacrifice_frequency is much
                    higher than the unweighted baseline (raw events /
                    raw moves), demonstrating that the recency weighting
                    correctly tilts the signal toward recent behaviour.

Sacrifice-fixtures: each party-sac game uses a simple "hang a piece,
recapture to recoup never arrives" pattern that is verified to trip the
v1 heuristic (threshold=3, window=3 plies, tolerance=0.5).

D. SIGNALS (added with the three new v1 signals — average game length,
   castling side, queen-trade timing): 6 games engineered to provide
   distinct patterns for each signal — K-side / Q-side / never castling,
   early / mid / no queen trade, varying game lengths. All end_time=0 so
   the recency weight is neutral (1.0) and the closed-form expected
   values are simple arithmetic over 6 games. Asserts the three new
   signal keys match the engineered patterns exactly.

Run:
    cd src && ../venv/bin/python services/opponent_style_test.py
"""
import sys
import os
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
import chess.pgn

import services.opponent_style as style_mod
from core import database


# ---------------------------------------------------------------------------
# Fake pool mimicking psycopg2's connection_pool contract.
# ---------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self._params = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self._params = params

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self._rows)


class _FakePool:
    def __init__(self, rows):
        self._rows = rows
        self.putconn_calls = 0
        self.getconn_calls = 0

    def getconn(self):
        self.getconn_calls += 1
        return _FakeConn(self._rows)

    def putconn(self, conn):
        self.putconn_calls += 1


# ---------------------------------------------------------------------------
# PGN construction helpers.
# ---------------------------------------------------------------------------
OPP_NAME = "TestOpponent"
OTHER_NAME = "TestRival"


def _pgn(moves_san, *, opponent_plays_white, eco, opening, end_time,
         site="https://example.test", result="*", time_class=""):
    game = chess.pgn.Game()
    game.headers["White"] = OPP_NAME if opponent_plays_white else OTHER_NAME
    game.headers["Black"] = OTHER_NAME if opponent_plays_white else OPP_NAME
    game.headers["Event"] = "TestGame"
    game.headers["Site"] = site
    game.headers["Date"] = "2024.01.01"
    game.headers["Round"] = "-"
    game.headers["Result"] = result
    if eco:
        game.headers["ECO"] = eco
    if opening:
        game.headers["Opening"] = opening

    board = game.board()
    node = game
    for san in moves_san:
        move = board.parse_san(san)
        node = node.add_main_variation(move)
        board.push(move)

    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
    pgn_text = game.accept(exporter)
    return {
        "pgn": pgn_text,
        "end_time": end_time,
        "opponent_username": OPP_NAME,
        "time_class": time_class,
    }


# A quiet Italian Game mainline (16 plies), no captures, leaves plenty of
# opponent moves for the sac detector's denominator without ever tripping
# the threshold. Verified legal via python-chess before being committed.
_QUIET_ITALIAN = [
    "e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O", "Nf6", "d3", "d6",
    "Nc3", "a6", "a3", "Be6", "Bb3", "O-O",
]


def _quiet_game(end_time, eco, opening, opp_white):
    return _pgn(
        _QUIET_ITALIAN,
        opponent_plays_white=opp_white,
        eco=eco, opening=opening, end_time=end_time,
    )


# Sacrifice fix-by-WHITE (opponent=white). The classic "3.Nxe5?! Nxe5"
# hang-a-knight pattern from the Scotch:
#   1.e4 e5 2.Nf3 Nc6 3.Nxe5?! Nxe5 4.d4 Nc6 ...
# White move 3 (Nxe5) is the "hang": m_before (white) = 39, captured black
# e5 pawn (black material unchanged for white). Black recaptures with 3...Nxe5
# on ply 5: white material drops 39 -> 36. White moves 4.d4 attacking the
# knight, black retreats 4...Nc6 — no recoup within the 3-ply window. The
# heuristic fires on white's move 3 (Nxe5).
_SCOTCH_SAC_WHITE = [
    "e4", "e5", "Nf3", "Nc6",
    "Nxe5", "Nxe5",     # (ply 4) white hangs knight, (ply 5) black recaptures
    "d4", "Nc6",        # ply 6, 7 — white attacks knight, it retreats (no recoup)
    "Bc4", "Bb4+",
    "c3", "Ba5",
]


def _sac_white(end_time, eco, opening):
    return _pgn(_SCOTCH_SAC_WHITE, opponent_plays_white=True,
                eco=eco, opening=opening, end_time=end_time)


# Sacrifice fix-by-BLACK (opponent=black). Symmetric hang-a-bishop pattern
# via the "Bb4-then-Bxc3-sac" pseudo-Legal-style manouvre:
#   1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5 4.O-O Nf6 5.d3 d6 6.Bg5 Bb4 7.c3 Bxc3
#   8.bxc3 ...
# Black move 7 (Bxc3) is the "hang": m_before (black) = 39, black bishop
# captures white c3 pawn (black material unchanged, white loses 1).
# White recaptures 8.bxc3 on ply 14: black material drops 39 -> 36. The
# 3-ply window from move 7 covers plies 14, 15, 16 (8.bxc3, 8...Qd7, 9.Qe2)
# — never recoups. Heuristic fires on black's move 7 (Bxc3).
_SAC_BLACK = [
    "e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O", "Nf6",
    "d3", "d6", "Bg5", "Bb4", "c3", "Bxc3", "bxc3", "Qd7",
    "Qe2", "Qe6", "Rd1", "Ne7",
]


def _sac_black(end_time, eco, opening):
    return _pgn(_SAC_BLACK, opponent_plays_white=False,
                eco=eco, opening=opening, end_time=end_time)


# ---------------------------------------------------------------------------
# New v1 signal fixtures: engineered distinct patterns for average game
# length, castling side, and queen-trade timing. All 6 games use
# end_time=0 (neutral recency weight = 1.0) so the closed-form expected
# values are simple arithmetic over 6 games — the recency mechanism
# itself is already exercised by fixture C (RECENT-SHIFT), so this fixture
# isolates the per-signal computation, not the recency tilt.
#
# Each game was verified legal via python-chess before being committed:
# every move parses, every castle is castling-detected correctly, every
# queen capture is recorded at the right ply, and the queens-on-board
# flag at game end matches the engineered intent.
# ---------------------------------------------------------------------------

# GAME KSIDE_EARLY_Q (opp=white): white K-side castle at ply 13; queen
# trade at ply 8 (the last queen capture). 13 plies total.
#   1.e4 e5 2.Qh5 Nf6 3.Qxe5+ Qe7 4.Qxe7+ Bxe7 5.Nf3 Nc6 6.Bc4 O-O O-O
# Plies:  1   2   3     4      5       6      7        8      9    10   11     12   13
# White's queen captures black's e5 pawn at ply 5 (Qxe5+, capturing PAWN).
# White's queen captures BLACK's QUEEN at ply 7 (Qxe7+).
# BLACK's bishop captures WHITE's QUEEN at ply 8 (Bxe7).
# White K-side castles at ply 13 (O-O). Black K-side castles at ply 12.
# At game end: white_q=0, black_q=0 -> queens_on_at_end=False, queens_off_at_end=True.
# last_queen_capture_ply = max([7, 8]) = 8. opp_castled = "kingside".
_KSIDE_EARLY_Q = [
    "e4", "e5", "Qh5", "Nf6", "Qxe5+", "Qe7", "Qxe7+", "Bxe7",
    "Nf3", "Nc6", "Bc4", "O-O", "O-O",
]


# GAME KSIDE_MID_Q (opp=white): white K-side castle at ply 17; queen
# trade at ply 12 (the last queen capture). 17 plies total. Same opening
# idea as _KSIDE_EARLY_Q but with 4 quiet silent b3/b6/Bb2/Bb7 moves
# prepended, so the queen trade lands at ply 12 (vs ply 8 in EARLY_Q).
#   1.b3 b6 2.Bb2 Bb7 3.e4 e5 4.Qh5 Nf6 5.Qxe5+ Qe7 6.Qxe7+ Bxe7
#   7.Nf3 Nc6 8.Bc4 O-O O-O
_KSIDE_MID_Q = [
    "b3", "b6", "Bb2", "Bb7", "e4", "e5", "Qh5", "Nf6",
    "Qxe5+", "Qe7", "Qxe7+", "Bxe7", "Nf3", "Nc6", "Bc4", "O-O", "O-O",
]


# GAME QSIDE_NO_Q (opp=white): white Q-SIDE castle at ply 11; NO queen
# trade (both queens stay on at game end). 12 plies total.
#   1.d4 Nf6 2.Nc3 g6 3.Bf4 Bg7 4.e3 O-O 5.Qd3 Nc6 6.O-O-O d6
# White castles Q-side at ply 11 (O-O-O). Black K-side castles at ply 8.
# No queen captures at all. queens_on_at_end=True, queens_off_at_end=False.
_QSIDE_NO_Q = [
    "d4", "Nf6", "Nc3", "g6", "Bf4", "Bg7", "e3", "O-O",
    "Qd3", "Nc6", "O-O-O", "d6",
]


# GAME NEVER_NO_Q_A (opp=white): no castling at all; no queen trade.
# 12 plies total. A "patient d3/e3/g3 setup" that never mobilises the king.
#   1.Nc3 Nc6 2.d3 d6 3.e3 e6 4.g3 g6 5.Bg2 Bg7 6.d4 d5
_NEVER_NO_Q_A = [
    "Nc3", "Nc6", "d3", "d6", "e3", "e6", "g3", "g6", "Bg2", "Bg7", "d4", "d5",
]


# GAME KSIDE_NO_Q_FOR_BLACK (opp=BLACK): black K-side castle at ply 10; no
# queen trade. 10 plies total. The shortest game in the fixture.
#   1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5 4.c3 Nf6 5.d3 O-O
# Black's O-O is at ply 10. No queen captures. opp=black so opp_castled
# tracks BLACK's castle -> "kingside". queens_on_at_end=True.
_KSIDE_NO_Q_BLACK = [
    "e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Nf6", "d3", "O-O",
]


# GAME NEVER_NO_Q_B (opp=white): no castling at all; no queen trade.
# 12 plies total. A second "never castle" game to pair with
# _NEVER_NO_Q_A so the 'never' bin has 2 contributors (vs 1 for
# queenside, 3 for kingside).
_NEVER_NO_Q_B = [
    "Nc3", "Nc6", "d3", "d6", "e3", "e6", "g3", "g6", "Bg2", "Bg7", "d4", "d5",
]


def _signals_game(moves_san, *, opp_white, eco, opening):
    """Wrap an engineered signals-game with a neutral end_time (weight 1.0)."""
    return _pgn(
        moves_san,
        opponent_plays_white=opp_white,
        eco=eco, opening=opening, end_time=0,
    )


def _fixture_signals():
    """6 games engineered for distinct patterns on each new signal.

    Game labels summarise their engineered pattern (opp-castling / queen
    trade timing / plies): all empirically verified before being
    committed by replaying each mainline and confirming the detected
    castling side, last-queen-capture ply, and queens-on-at-end flag.

      KSIDE_EARLY_Q   : opp=white, K-side castle,  queen trade ply 8,  13 plies
      KSIDE_MID_Q     : opp=white, K-side castle,  queen trade ply 12, 17 plies
      QSIDE_NO_Q      : opp=white, Q-side castle,  no queen trade,     12 plies
      NEVER_NO_Q_A    : opp=white, never castle,   no queen trade,     12 plies
      KSIDE_NO_Q_BLACK: opp=black, K-side castle,  no queen trade,     10 plies
      NEVER_NO_Q_B    : opp=white, never castle,   no queen trade,     12 plies

    Closed-form expected values at neutral weighting (weight=1.0 each, so
    weighted_game_count=6.0 and every weighted mean is the simple arithmetic
    mean over the 6 games):

      average_game_length = (13 + 17 + 12 + 12 + 10 + 12) / 6
                          = 76 / 6 = 12.6667

      castling_side_distribution:
        kingside : 3 games (KSIDE_EARLY_Q, KSIDE_MID_Q, KSIDE_NO_Q_BLACK) -> 3/6 = 0.5000
        queenside: 1 game  (KSIDE_NO_Q      )                          -> 1/6 = 0.1667
        never    : 2 games (NEVER_NO_Q_A,    NEVER_NO_Q_B)             -> 2/6 = 0.3333
        sorted desc: kingside=0.5, never=0.3333, queenside=0.1667

      queen_trade_move_number (only games with both queens off at end
      contribute — KSIDE_EARLY_Q at ply 8, KSIDE_MID_Q at ply 12):
        = (8 + 12) / 2 = 10.0000

      queens_stay_on_rate (games with both queens on at end):
        = 4 / 6 = 0.6667
        (qualifying games: QSIDE_NO_Q, NEVER_NO_Q_A, KSIDE_NO_Q_BLACK, NEVER_NO_Q_B)
    """
    return [
        _signals_game(_KSIDE_EARLY_Q,    opp_white=True,  eco="C20", opening="King's Pawn Game"),
        _signals_game(_KSIDE_MID_Q,      opp_white=True,  eco="C20", opening="King's Pawn Game"),
        _signals_game(_QSIDE_NO_Q,       opp_white=True,  eco="D00", opening="Queen's Pawn Game"),
        _signals_game(_NEVER_NO_Q_A,     opp_white=True,  eco="A04", opening="Reti Opening"),
        _signals_game(_KSIDE_NO_Q_BLACK, opp_white=False, eco="C50", opening="Italian Game"),
        _signals_game(_NEVER_NO_Q_B,     opp_white=True,  eco="A04", opening="Reti Opening"),
    ]


# ---------------------------------------------------------------------------
# Time-control fixtures for `compute_time_control_distribution`.
# ---------------------------------------------------------------------------
# Helper builds a PGN-shaped row with the opponent as White, a fixed quiet
# move list, and the requested [TimeControl] header set. The moves are
# deliberately minimal — the time-control signal reads ONLY the PGN header
# block (regex over `[Key "Value"]` lines, no mainline replay) so the move
# list's exact contents do not affect the bucket; we keep just enough for
# the row to be valid PGN.
_TC_QUIET_MOVES = ["e4", "e5", "Nf3", "Nc6"]


def _tc_game(time_control, end_time):
    """One opponent-game row with a chosen [TimeControl] header and end_time."""
    game = chess.pgn.Game()
    game.headers["White"] = OPP_NAME
    game.headers["Black"] = OTHER_NAME
    game.headers["Event"] = "TestGame"
    game.headers["Site"] = "https://example.test"
    game.headers["Date"] = "2024.01.01"
    game.headers["Round"] = "-"
    game.headers["Result"] = "*"
    game.headers["TimeControl"] = time_control

    board = game.board()
    node = game
    for san in _TC_QUIET_MOVES:
        move = board.parse_san(san)
        node = node.add_main_variation(move)
        board.push(move)

    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
    pgn_text = game.accept(exporter)
    return {"pgn": pgn_text, "end_time": end_time, "opponent_username": OPP_NAME}


def _fixture_time_controls():
    """6 games spanning 3 time-control buckets, neutral weighting.

      3 games at [TimeControl "600+0"] -> bucket "10+0"
      2 games at [TimeControl "180+2"] -> bucket "3+2"
      1 game  at [TimeControl "60+0"]  -> bucket "1+0"

    All end_time=0 (weight=1.0 each) so the closed-form expected values are
    simple arithmetic over 6 games, and the recency mechanism itself is
    exercised separately by fixture G. Asserts the percentages AND the most-
    common pick (the spec: "verifying both the percentages and the most-
    common pick").
    """
    return [
        _tc_game("600+0", 0),
        _tc_game("600+0", 0),
        _tc_game("600+0", 0),
        _tc_game("180+2", 0),
        _tc_game("180+2", 0),
        _tc_game("60+0", 0),
    ]


def _fixture_time_controls_recency_tilt():
    """7 games engineered so raw count and recency-weight disagree.

      3 RECENT "10+0" games at end_time = NOW          -> weight 1.0 each
      4 OLD    "3+2"  games at end_time = NOW - 4yrs    -> weight ~0.018 each

    Raw count tilts toward "3+2" (4 > 3); recency-weighted share tilts
    toward "10+0" (3.0 > 4 * 0.018 ~= 0.072). If
    compute_time_control_distribution were a raw tally it would pick
    "3+2"; the test asserts it picks "10+0", proving the SAME decay
    constant the other signals use (STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR)
    is reused here, not a new decay rate.
    """
    four_years_ago = NOW - (4 * YEAR_SEC)
    return [
        _tc_game("600+0", NOW),
        _tc_game("600+0", NOW),
        _tc_game("600+0", NOW),
        _tc_game("180+2", four_years_ago),
        _tc_game("180+2", four_years_ago),
        _tc_game("180+2", four_years_ago),
        _tc_game("180+2", four_years_ago),
    ]


# ---------------------------------------------------------------------------
# Opening-results fixtures for `compute_opening_results`.
# ---------------------------------------------------------------------------
# 7 games engineered across 3 openings with a mix of wins/losses/draws and
# the opponent playing BOTH colors — exercising every code path the W/L/D
# math walks: white-win, white-loss, white-draw, black-win, black-loss,
# black-draw, and a single-game "openings he lost against" bucket.
#
# The `_pgn` helper writes the [Result] header verbatim; `compute_opening_
# results` reads it via `_analyze_game`'s new `result` field, which flips
# the POV per the opponent's resolved color. So:
#   opp=white, Result="1-0"  -> "win"
#   opp=white, Result="0-1"  -> "loss"
#   opp=white, Result="1/2-1/2" -> "draw"
#   opp=black, Result="0-1"  -> "win"
#   opp=black, Result="1-0"  -> "loss"
#   opp=black, Result="1/2-1/2" -> "draw"
#
# All end_time=0 (neutral weighting = 1.0 each) so the closed-form expected
# values are simple arithmetic over 7 games.
#
# Engineered layout (3 buckets; weighted_count = number of games, since
# weight is 1.0 per game at neutral end_time):
#
#   "Italian Game":      3 games (1 win as white, 1 loss as white,
#                                1 draw as black)
#     weighted_wins=1, weighted_losses=1, weighted_draws=1
#     win_rate = 1 / (1+1+1) = 0.3333
#
#   "Sicilian Defense":  3 games (1 win as black, 1 loss as black,
#                                 1 win as white)
#     weighted_wins=2, weighted_losses=1, weighted_draws=0
#     win_rate = 2 / (2+1+0) = 0.6667
#
#   "Scotch Game":       1 game (1 loss as white) — the single-game
#                        "Openings He Lost Against" bucket the spec
#                        explicitly wants surfaced (NO floor).
#     weighted_wins=0, weighted_losses=1, weighted_draws=0
#     win_rate = 0 / (0+1+0) = 0.0
#
# All three buckets hit the MIN_STYLE_GAMES floor as a group (7 games >
# 3, though compute_opening_results deliberately doesn't gate on this —
# the no-floor contract is the spec's explicit ask).
def _fixture_opening_results():
    """7 games across 3 openings, both colors, mix of W/L/D.

    Neutral weighting (end_time=0). Closed-form expected by_opening is
    documented in the fixture's header comment above.
    """
    games = []

    # --- Italian Game (3 games) -----------------------------------------
    # opp=white, win
    games.append(_pgn(
        _QUIET_ITALIAN, opponent_plays_white=True,
        eco="C50", opening="Italian Game", end_time=0, result="1-0",
    ))
    # opp=white, loss
    games.append(_pgn(
        _QUIET_ITALIAN, opponent_plays_white=True,
        eco="C50", opening="Italian Game", end_time=0, result="0-1",
    ))
    # opp=black, draw
    games.append(_pgn(
        _QUIET_ITALIAN, opponent_plays_white=False,
        eco="C50", opening="Italian Game", end_time=0, result="1/2-1/2",
    ))

    # --- Sicilian Defense (3 games) ------------------------------------
    # opp=black, win  (Result "0-1" -> black POV "win")
    games.append(_pgn(
        _QUIET_ITALIAN, opponent_plays_white=False,
        eco="B20", opening="Sicilian Defense", end_time=0, result="0-1",
    ))
    # opp=black, loss (Result "1-0" -> black POV "loss")
    games.append(_pgn(
        _QUIET_ITALIAN, opponent_plays_white=False,
        eco="B20", opening="Sicilian Defense", end_time=0, result="1-0",
    ))
    # opp=white, win
    games.append(_pgn(
        _QUIET_ITALIAN, opponent_plays_white=True,
        eco="B20", opening="Sicilian Defense", end_time=0, result="1-0",
    ))

    # --- Scotch Game (1 game) — the single-game "lost against" bucket ---
    # opp=white, loss
    games.append(_pgn(
        _QUIET_ITALIAN, opponent_plays_white=True,
        eco="C45", opening="Scotch Game", end_time=0, result="0-1",
    ))

    return games


# ---------------------------------------------------------------------------
# A second opening-results fixture adding a "*" (unfinished/aborted) game
# to verify the contract that "*" games contribute to weighted_count but
# to NONE of the W/L/D numerators and are excluded from win_rate's
# denominator (so an all-aborted bucket has win_rate=None, not 0.0).
# ---------------------------------------------------------------------------
def _fixture_opening_results_with_aborted():
    """The 7 games from _fixture_opening_results() PLUS 1 aborted game in a
    NEW single-game bucket and 1 aborted game added to an EXISTING bucket.

      row 8: opp=white, Result="*", opening "Caro-Kann Defense"
             -> NEW bucket "Caro-Kann Defense" with weighted_count=1 and
                win_rate=None (every game in it is "*").

      row 9: opp=white, Result="*", opening "Italian Game"
             -> ADDED to the existing Italian bucket. Its weighted_count
                goes 3 -> 4, but its W/L/D numerators and win_rate stay
                IDENTICAL (the "*" game contributes to the bucket's
                weighted_count only). This is the decisive test of the
                "*" denominator-exclusion contract.
    """
    good = _fixture_opening_results()
    aborted = [
        _pgn(
            _QUIET_ITALIAN, opponent_plays_white=True,
            eco="B10", opening="Caro-Kann Defense", end_time=0, result="*",
        ),
        _pgn(
            _QUIET_ITALIAN, opponent_plays_white=True,
            eco="C50", opening="Italian Game", end_time=0, result="*",
        ),
    ]
    return good + aborted


def _fixture_signals_with_bad_pgns():
    """Same 6 parseable games as _fixture_signals() PLUS 2 deliberately
    unparseable PGN rows, to verify the denominator-consistency fix:

      row 7: {"pgn": "", "end_time": 0}
             An empty string — chess.pgn.read_game returns None, so
             _analyze_game returns None via the `game is None` path.

      row 8: {"pgn": '[Event "Test"]\\n[White "TestOpponent"]\\n[Black "TestRival"]\\n\\n',
              "end_time": 0}
             A PGN with valid headers (including the correct opponent name)
             but NO move data — read_game returns a Game with headers but
             an empty mainline. _analyze_game resolves opponent_color
             successfully (White matches "testopponent") but then hits the
             `if not mainline: return None` path. This exercises a
             DIFFERENT failure branch than row 7 (the game IS recognized
             as belonging to this opponent, the PGN just has no moves).

    Both bad rows have end_time=0 (weight=1.0) for clean arithmetic. Under
    the denominator-consistency contract (exclude unparseable from all
    five signals), every signal computed from this 8-row fixture should be
    IDENTICAL to the same signal computed from the 6-row clean fixture
    (fixture D / _fixture_signals), because the 2 bad rows contribute zero
    to every numerator AND every signal-specific denominator. The only
    fields that should differ are:
      - game_count:               6 -> 8
      - weighted_game_count:      6.0 -> 8.0 (bad rows still add their weight)
      - weighted_parseable_game_count stays at 6.0 (bad rows excluded)
    """
    good_games = _fixture_signals()
    bad_rows = [
        {"pgn": "", "end_time": 0},
        {"pgn": '[Event "Test"]\n[White "TestOpponent"]\n[Black "TestRival"]\n\n',
         "end_time": 0},
    ]
    return good_games + bad_rows


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------
NOW = int(time.time())
YEAR_SEC = int(365.25 * 86400)


def _fixture_thin():
    """2 games — below MIN_STYLE_EFF_SAMPLES=5.0 (effective_sample_size ~2.0)."""
    return [
        _quiet_game(NOW, "C50", "Italian Game", opp_white=True),
        _sac_white(NOW, "C44", "Scotch Game"),
    ]


def _fixture_established():
    """12 games across 4 families; 3 of them are sac games."""
    families = [
        ("C50", "Italian Game", True),
        ("B20", "Sicilian Defense", False),
        ("B10", "Caro-Kann Defense", False),
        ("D06", "Queen's Gambit Declined", True),
    ]
    games = []
    for i in range(12):
        eco, opening, opp_white = families[i % 4]
        end_time = NOW - (30 * 24 * 3600)  # 1 month ago
        if i in (1, 7, 10):
            g = _sac_white(end_time, eco, opening) if opp_white else _sac_black(end_time, eco, opening)
        else:
            g = _quiet_game(end_time, eco, opening, opp_white)
        games.append(g)
    return games


def _fixture_recent_shift():
    """12 games = 6 OLD quiet + 6 RECENT high-sac."""
    games = []
    old_end = NOW - (4 * YEAR_SEC)
    # 6 old quiet games — at lambda=1.0 these decay to ~0.018 weight each.
    for eco, opening, opp_white in [
        ("C50", "Italian Game", True),
        ("B20", "Sicilian Defense", False),
        ("B10", "Caro-Kann Defense", False),
        ("D06", "Queen's Gambit Declined", True),
        ("C50", "Italian Game", False),
        ("B20", "Sicilian Defense", True),
    ]:
        games.append(_quiet_game(old_end, eco, opening, opp_white))
    recent_end = NOW - (24 * 3600)  # yesterday
    for eco, opening, opp_white in [
        ("B20", "Sicilian Defense", True),
        ("B20", "Sicilian Defense", True),
        ("B20", "Sicilian Defense", False),
        ("B33", "Sicilian Defense: Sveshnikov", False),
        ("C44", "Scotch Game", True),
        ("C44", "Scotch Game", True),
    ]:
        if opp_white:
            games.append(_sac_white(recent_end, eco, opening))
        else:
            games.append(_sac_black(recent_end, eco, opening))
    return games


def _fixture_stale_only_sac():
    """12 games = 6 RECENT quiet + 6 OLD high-sac.

    The MIRROR of _fixture_recent_shift: the sacrifice pattern appears
    ONLY in the old games. The recent games are all quiet (zero sacs).
    Under recency weighting, the old sac pattern should still produce a
    SOFT signal (weighted sac freq > 0, because old games still carry
    ~0.018 weight each), but it should be MUCH weaker than the unweighted
    rate (which treats old and recent equally).

    This is the spec's test case: "Pattern that appears only in older
    games → still produces a soft signal."
    """
    games = []
    recent_end = NOW - (24 * 3600)  # yesterday
    # 6 recent quiet games — weight ~1.0 each, zero sacrifices.
    for eco, opening, opp_white in [
        ("C50", "Italian Game", True),
        ("B20", "Sicilian Defense", False),
        ("B10", "Caro-Kann Defense", False),
        ("D06", "Queen's Gambit Declined", True),
        ("C50", "Italian Game", False),
        ("B20", "Sicilian Defense", True),
    ]:
        games.append(_quiet_game(recent_end, eco, opening, opp_white))
    old_end = NOW - (4 * YEAR_SEC)
    # 6 old sac games — weight ~0.018 each, but each carries sacrifices.
    for eco, opening, opp_white in [
        ("B20", "Sicilian Defense", True),
        ("B20", "Sicilian Defense", True),
        ("B20", "Sicilian Defense", False),
        ("B33", "Sicilian Defense: Sveshnikov", False),
        ("C44", "Scotch Game", True),
        ("C44", "Scotch Game", True),
    ]:
        if opp_white:
            games.append(_sac_white(old_end, eco, opening))
        else:
            games.append(_sac_black(old_end, eco, opening))
    return games


# ---------------------------------------------------------------------------
# Test runner.
# ---------------------------------------------------------------------------
def _run_fixture(label, games, sparring_tc=None):
    fake_pool = _FakePool(games)
    database.connection_pool = fake_pool
    try:
        result = style_mod.compute_opponent_style(
            requested_by_user_id="user_test_001",
            provider="lichess",
            opponent_username=OPP_NAME,
            sparring_time_control=sparring_tc,
        )
    finally:
        database.connection_pool = None
    return result, fake_pool


def _print(label, result, pool):
    print(f"\n=== {label} ===")
    print(f"  getconn/putconn balance: {pool.getconn_calls}/{pool.putconn_calls}")
    print(json.dumps(result, indent=2, default=str))


def _assert_thin(result):
    assert result["sufficient"] is False, f"thin: expected sufficient=False, got {result['sufficient']}"
    assert result["sacrifice_frequency"] is None
    assert result["opening_family_lean"] is None
    assert result["game_count"] == 2, f"thin: expected game_count=2, got {result['game_count']}"
    # New transparency fields: effective_sample_size and recency_decay_lambda
    # are always present, even in the insufficient path.
    assert "effective_sample_size" in result, (
        f"thin: effective_sample_size should be in the return dict even below floor"
    )
    assert "recency_decay_lambda" in result, (
        f"thin: recency_decay_lambda should be in the return dict even below floor"
    )
    assert abs(result["effective_sample_size"] - 2.0) < 0.01, (
        f"thin: effective_sample_size should be ~2.0 (2 recent games), "
        f"got {result['effective_sample_size']}"
    )
    assert result["recency_decay_lambda"] == 1.0, (
        f"thin: recency_decay_lambda should be 1.0, got {result['recency_decay_lambda']}"
    )
    # All five signals (incl. the three new ones) must also be None below the floor.
    assert result["average_game_length"] is None, (
        f"thin: average_game_length should be None below floor, got {result['average_game_length']}"
    )
    assert result["castling_side_distribution"] is None, (
        f"thin: castling_side_distribution should be None below floor"
    )
    assert result["queen_trade_move_number"] is None, (
        f"thin: queen_trade_move_number should be None below floor"
    )
    assert result["queens_stay_on_rate"] is None, (
        f"thin: queens_stay_on_rate should be None below floor"
    )
    # weighted_parseable_game_count is 0.0 in the insufficient path.
    assert result["weighted_parseable_game_count"] == 0.0, (
        f"thin: weighted_parseable_game_count should be 0.0 below floor, "
        f"got {result['weighted_parseable_game_count']}"
    )
    print("  [PASS] sufficient=False, all signals (incl. new) suppressed below floor.")


def _assert_established(result):
    assert result["sufficient"] is True, f"est: expected sufficient=True, got {result['sufficient']}"
    assert result["game_count"] == 12, f"est: expected game_count=12, got {result['game_count']}"
    # Effective sample size: 12 games at ~30 days old (lambda=1.0) ->
    # weight = exp(-1.0 * 30/365.25) ~= 0.92 -> eff_sample ~= 11.04.
    # Well above MIN_STYLE_EFF_SAMPLES=5.0.
    assert "effective_sample_size" in result, "est: effective_sample_size should be in dict"
    assert result["effective_sample_size"] > 5.0, (
        f"est: effective_sample_size should clear MIN_STYLE_EFF_SAMPLES=5.0, "
        f"got {result['effective_sample_size']}"
    )
    assert result["recency_decay_lambda"] == 1.0, (
        f"est: recency_decay_lambda should be 1.0, got {result['recency_decay_lambda']}"
    )
    assert result["opening_family_lean"] is not None, "est: family lean should be populated"
    s = sum(result["opening_family_lean"].values())
    assert abs(s - 1.0) < 1e-3, f"est: family lean should sum to ~1.0, got {s}"
    assert result["sacrifice_frequency"] is not None, "est: sac freq should be set"
    # The three new signals must be populated (not None) for sufficient=True.
    assert result["average_game_length"] is not None, "est: average_game_length should be set"
    assert result["castling_side_distribution"] is not None, "est: castling_side_distribution should be set"
    assert result["queens_stay_on_rate"] is not None, "est: queens_stay_on_rate should be set"
    # All 12 games in this fixture are parseable, so weighted_parseable_game_count
    # should equal weighted_game_count (no data-quality gap).
    assert abs(result["weighted_parseable_game_count"] - result["weighted_game_count"]) < 1e-9, (
        f"est: all 12 games parseable, so weighted_parseable_game_count should equal "
        f"weighted_game_count, got {result['weighted_parseable_game_count']} vs "
        f"{result['weighted_game_count']}"
    )
    print(f"  [PASS] weighted_parseable_game_count == weighted_game_count "
          f"({result['weighted_parseable_game_count']}) — no unparseable PGNs in fixture")
    # queen_trade_move_number may be None if no game traded queens. The
    # established fixture's games (Italian + Scotch sac patterns) DO have
    # queens on at end (no queen captures in any of them), so we EXPECT
    # None here. Assert the contract holds: the signal correctly reports
    # None when no qualifying game exists.
    assert result["queen_trade_move_number"] is None, (
        f"est: established-fixture games have no queen trades; "
        f"queen_trade_move_number should be None, got {result['queen_trade_move_number']}"
    )
    # Castling distribution sanity: all 12 games have the opponent castle
    # K-side OR never (the sac-game opponents never castle in v1 fixtures).
    # _QUIET_ITALIAN has both sides castle K-side, so opp-castle = "kingside"
    # for the 9 quiet games. Sac-white (SCOTCH_SAC_WHITE) has no castling
    # at all -> "never"; sac-black (SAC_BLACK) has WHITE O-O on ply 7 but
    # black (opp) never castles -> "never". So 9 kingside, 3 never, 0 qs.
    castling = result["castling_side_distribution"]
    assert "kingside" in castling and "queenside" in castling and "never" in castling, (
        f"est: castling_side_distribution should have all 3 bins, got {castling}"
    )
    assert abs(castling["kingside"] - 9 / 12) < 1e-3, (
        f"est: kingside should be 9/12, got {castling['kingside']}"
    )
    assert abs(castling["never"] - 3 / 12) < 1e-3, (
        f"est: never should be 3/12, got {castling['never']}"
    )
    assert castling["queenside"] == 0.0, (
        f"est: queenside should be 0.0, got {castling['queenside']}"
    )
    # Queens-stay-on: every game in this fixture ends with both queens on
    # (no queen captures in any fixture game), so the rate should be 1.0.
    assert abs(result["queens_stay_on_rate"] - 1.0) < 1e-3, (
        f"est: queens_stay_on_rate should be 1.0 (no queen trades), "
        f"got {result['queens_stay_on_rate']}"
    )
    print(f"  [PASS] sufficient=True; family lean sums to {s:.4f}; sac freq = {result['sacrifice_frequency']}")
    print(f"        raw sacs={result['sacrifice_events']} over {result['opponent_moves']} opp-moves")
    print(f"        new signals: avg_len={result['average_game_length']} "
          f"castling={result['castling_side_distribution']} "
          f"q_trade={result['queen_trade_move_number']} "
          f"q_stay={result['queens_stay_on_rate']}")


def _assert_recent_shift(result):
    assert result["sufficient"] is True
    assert result["game_count"] == 12, f"shift: expected 12, got {result['game_count']}"
    weighted = result["sacrifice_frequency"]
    if result["opponent_moves"] > 0:
        unweighted = result["sacrifice_events"] / result["opponent_moves"]
    else:
        unweighted = 0.0
    print(f"  recency-weighted sacrifice_frequency = {weighted}")
    print(f"  raw/unweighted sacrificial rate     = {round(unweighted,4)}")
    assert weighted > unweighted * 1.5, (
        f"shift: recency should tilt the sac rate up; "
        f"weighted={weighted} unweighted={unweighted}"
    )
    print(f"  [PASS] recency tilt verified: weighted > 1.5 * unweighted.")
    # Effective sample size under decay: 12 games, 6 old (4yr, weight
    # ~0.018 each) + 6 recent (1day, weight ~1.0 each). eff_sample ~ 6.1,
    # well below the raw 12 — decay is operative.
    assert "effective_sample_size" in result, (
        f"shift: effective_sample_size should be in the return dict"
    )
    assert result["effective_sample_size"] < float(result["game_count"]), (
        f"shift: effective_sample_size ({result['effective_sample_size']}) should "
        f"be < game_count ({result['game_count']}) under decay"
    )
    assert result["effective_sample_size"] >= 5.0, (
        f"shift: effective_sample_size ({result['effective_sample_size']}) should "
        f"clear MIN_STYLE_EFF_SAMPLES=5.0 (6 recent games at ~1.0 each dominate)"
    )
    print(f"  [PASS] effective_sample_size = {result['effective_sample_size']} "
          f"< game_count = {result['game_count']} (decay operative)")
    lean = result["opening_family_lean"]
    print(f"  family_lean = {lean}")
    sicilian_share = lean.get("Sicilian Defense", 0.0)
    assert sicilian_share > 0.30, (
        f"shift: expected Sicilian Defense dominant; got {sicilian_share}"
    )
    print(f"  [PASS] Sicilian Defense leans dominant: share={sicilian_share}")


def _assert_stale_only_soft_signal(result):
    """Assert the STALE-ONLY fixture: sac pattern only in old games.

    The 6 recent games are quiet (zero sacs); the 6 old games (4yr ago)
    are all sac games. Under recency weighting (lambda=1.0):

      - Weighted sac freq: dominated by the recent quiet games, so the
        weighted rate should be MUCH lower than the unweighted rate.
      - BUT the old sac pattern still produces a SOFT signal: the
        weighted rate should be > 0 (old games carry ~0.018 weight each,
        so their sacs still contribute to the numerator and denominator).

    This is the spec's test case: "Pattern that appears only in older
    games → still produces a soft signal."
    """
    assert result["sufficient"] is True, (
        f"stale-only: 12 games with ~6.1 effective_sample_size should be sufficient"
    )
    assert result["game_count"] == 12, (
        f"stale-only: expected 12, got {result['game_count']}"
    )
    weighted = result["sacrifice_frequency"]
    if result["opponent_moves"] > 0:
        unweighted = result["sacrifice_events"] / result["opponent_moves"]
    else:
        unweighted = 0.0
    print(f"  recency-weighted sacrifice_frequency = {weighted}")
    print(f"  raw/unweighted sacrificial rate     = {round(unweighted, 4)}")
    # SOFT SIGNAL: weighted rate is > 0 (old sacs still contribute).
    assert weighted > 0.0, (
        f"stale-only: weighted sac freq should be > 0 (old sacs still "
        f"produce a soft signal under decay), got {weighted}"
    )
    # DECAY SUPPRESSED: weighted rate is much lower than unweighted
    # (old games are decayed, recent games have zero sacs).
    assert weighted < unweighted, (
        f"stale-only: weighted sac freq should be < unweighted (decay "
        f"suppresses the old-only pattern), got weighted={weighted} "
        f"unweighted={unweighted}"
    )
    print(f"  [PASS] soft signal verified: 0 < weighted ({weighted}) < "
          f"unweighted ({round(unweighted, 4)}) — old pattern is a soft "
          f"signal, not silenced.")


def _assert_uniform_age_sanity(result):
    """Assert the UNIFORM-AGE sanity check: when all games are the same
    age (here: all end_time=0, so all weights = 1.0), the weighted
    aggregates should collapse to the unweighted aggregates.

    This confirms the math doesn't distort results when there's nothing
    to distort: if every game carries the same weight, the weighted mean
    IS the unweighted mean, and the weighted frequency IS the raw
    frequency. A bug in the weighting (e.g., a systematic bias toward
    older or newer games) would show up here as a divergence between
    weighted and unweighted values.

    Uses the existing SIGNALS fixture (D) which has 6 games all at
    end_time=0. The _assert_signals function already checks the
    closed-form expected values; here we additionally assert that
    effective_sample_size == game_count (the defining property of
    uniform weighting).
    """
    assert result["sufficient"] is True
    assert result["game_count"] == 6
    # Under uniform age (all end_time=0 -> weight 1.0), effective_sample_size
    # equals game_count exactly. This is the sanity check: decay doesn't
    # distort when all weights are equal.
    assert abs(result["effective_sample_size"] - float(result["game_count"])) < 1e-9, (
        f"uniform-age: effective_sample_size ({result['effective_sample_size']}) "
        f"should equal game_count ({result['game_count']}) when all games "
        f"are the same age (neutral weighting)"
    )
    # weighted_game_count should also equal game_count under uniform
    # weighting (no data-quality gap in this fixture).
    assert abs(result["weighted_game_count"] - float(result["game_count"])) < 1e-3, (
        f"uniform-age: weighted_game_count ({result['weighted_game_count']}) "
        f"should equal game_count ({result['game_count']}) under neutral weighting"
    )
    # The weighted aggregates should equal the unweighted aggregates.
    # For sacrifice_frequency: weighted = raw_sacs / raw_opp_moves (since
    # all weights are 1.0, the weighted sums reduce to raw counts).
    if result["opponent_moves"] > 0:
        unweighted_sac_freq = result["sacrifice_events"] / result["opponent_moves"]
        assert abs(result["sacrifice_frequency"] - unweighted_sac_freq) < 1e-3, (
            f"uniform-age: weighted sac_freq ({result['sacrifice_frequency']}) "
            f"should equal unweighted ({round(unweighted_sac_freq, 4)}) under "
            f"neutral weighting"
        )
    print(f"  [PASS] uniform-age sanity: effective_sample_size == game_count "
          f"({result['effective_sample_size']}), weighted aggregates == "
          f"unweighted aggregates")


# ---------------------------------------------------------------------------
# Time-control similarity weighting tests (L-P).
# All use end_time=0 (recency-neutral, weight 1.0) unless noted, so the
# closed-form expected values are pure arithmetic over the TC similarity
# matrix (_TC_SIMILARITY) -- no exp() involved, so the assertions are exact.
# ---------------------------------------------------------------------------

# 4 rapid (sac) + 4 blitz (quiet) + 4 bullet (quiet), all recent (end_time=0).
# Under a RAPID sparring session, rapid games dominate the aggregates.
def _fixture_tc_same_dominance():
    games = []
    # 4 rapid Scotch-sac games (opponent=white, high sac count).
    for _ in range(4):
        games.append(_pgn(_SCOTCH_SAC_WHITE, opponent_plays_white=True,
                          eco="C44", opening="Scotch Game",
                          end_time=0, time_class="rapid"))
    # 4 blitz quiet Italian games.
    for _ in range(4):
        games.append(_pgn(_QUIET_ITALIAN, opponent_plays_white=True,
                          eco="C50", opening="Italian Game",
                          end_time=0, time_class="blitz"))
    # 4 bullet quiet Italian games.
    for _ in range(4):
        games.append(_pgn(_QUIET_ITALIAN, opponent_plays_white=True,
                          eco="C50", opening="Italian Game",
                          end_time=0, time_class="bullet"))
    return games


def _assert_tc_same_dominance(result, result_no_tc):
    """Under a rapid sparring session, rapid games dominate.

    Closed-form (all recent, recency=1.0): eff_sample = 4*1.0(rapid<->rapid)
    + 4*0.6(blitz<->rapid) + 4*0.2(bullet<->rapid) = 4 + 2.4 + 0.8 = 7.2.
    The per-bucket transparency mass should show rapid as dominant (== the
    sparring bucket). And the sacrifice frequency should be HIGHER under the
    rapid sparring session than under no-TC weighting, because the rapid
    (sac) games get full weight while the quiet blitz/bullet games are
    down-weighted -- so the sac rate is no longer diluted by 8 quiet games.
    """
    assert result["sufficient"] is True
    assert result["sparring_time_control_bucket"] == "rapid"
    assert result["time_control_unclassified_count"] == 0
    # Closed-form effective sample size.
    assert abs(result["effective_sample_size"] - 7.2) < 1e-6, (
        f"tc-same-dominance: eff_sample should be 7.2 "
        f"(4*1.0+4*0.6+4*0.2), got {result['effective_sample_size']}"
    )
    # Per-bucket transparency: rapid dominant, blitz next, bullet last.
    by_bucket = result["time_control_weighted_by_bucket"]
    assert by_bucket is not None
    assert abs(by_bucket["rapid"] - 4.0) < 1e-6, f"rapid mass: {by_bucket['rapid']}"
    assert abs(by_bucket["blitz"] - 2.4) < 1e-6, f"blitz mass: {by_bucket['blitz']}"
    assert abs(by_bucket["bullet"] - 0.8) < 1e-6, f"bullet mass: {by_bucket['bullet']}"
    # First key (sorted desc) must be 'rapid' == sparring bucket.
    assert list(by_bucket.keys())[0] == "rapid"
    # Same-TC games dominate the sac signal: rapid-sparring sac freq >
    # no-TC sac freq (quiet games no longer dilute the sac rate).
    sf_tc = result["sacrifice_frequency"]
    sf_no = result_no_tc["sacrifice_frequency"]
    assert sf_tc > sf_no, (
        f"tc-same-dominance: sac freq under rapid sparring ({sf_tc}) "
        f"should be > no-TC sac freq ({sf_no}) -- rapid sac games dominate"
    )
    print(f"  [PASS] rapid sparring: eff_sample=7.2, rapid bucket dominant "
          f"(4.0>2.4>0.8), sac freq {sf_tc} > no-TC {sf_no}")


def _assert_tc_cross_down_weight(result):
    """10 blitz games under a RAPID sparring session are down-weighted x0.6.

    Closed-form (all recent): eff_sample = 10 * 1.0 * 0.6 = 6.0, vs the
    recency-only 10.0. Confirms cross-TC games are down-weighted (NOT
    purged -- 6.0 > 0, and 10 games still clears the floor under the
    down-weighting so the sufficient path + per-bucket mass are visible).
    """
    assert result["sufficient"] is True
    assert result["sparring_time_control_bucket"] == "rapid"
    assert abs(result["effective_sample_size"] - 6.0) < 1e-6, (
        f"tc-cross: eff_sample should be 6.0 (10*0.6), "
        f"got {result['effective_sample_size']}"
    )
    # Not purged: the blitz mass is still present (6.0), not 0.
    by_bucket = result["time_control_weighted_by_bucket"]
    assert by_bucket is not None
    assert abs(by_bucket["blitz"] - 6.0) < 1e-6
    print(f"  [PASS] blitz games under rapid sparring: eff_sample=6.0 "
          f"(x0.6 down-weight, not purged)")


def _assert_tc_unknown_fallback(result):
    """No sparring TC -> TC factor collapses to 1.0 (recency-only).

    No crash, uniform weights. sparring_time_control_bucket is None (the
    "unknown" bucket is surfaced as None to the caller). The 10 blitz games
    each get weight 1.0 (recency) so eff_sample = 10.0 == recency-only.
    """
    assert result["sufficient"] is True
    assert result["sparring_time_control_bucket"] is None
    # Recency-only: 10 recent games -> eff_sample 10.0.
    assert abs(result["effective_sample_size"] - 10.0) < 1e-6, (
        f"tc-unknown: eff_sample should be 10.0 (recency-only), "
        f"got {result['effective_sample_size']}"
    )
    # No-TC path still surfaces the per-bucket mass (recency-only weights).
    by_bucket = result["time_control_weighted_by_bucket"]
    assert by_bucket is not None
    assert abs(by_bucket["blitz"] - 10.0) < 1e-6
    print(f"  [PASS] unknown sparring TC: no crash, eff_sample=10.0 "
          f"(recency-only fallback), sparring_bucket=None")


def _assert_tc_combined_product(result_tc, result_no_tc):
    """Combined weight = recency x TC (product, not just one factor).

    4 blitz games (2 recent + 2 old) under rapid sparring. The recency-only
    run (result_no_tc) gives eff_sample_noTC = 2*1.0 + 2*recency(age). The
    TC run should give eff_sample_TC = 0.6 * eff_sample_noTC exactly --
    every game's weight is the recency factor TIMES the 0.6 TC factor, so
    the TC run is a uniform 0.6 scaling of the recency-only run. This
    verifies the two factors compose multiplicatively without needing to
    compute exp() in the test (the recency-only run is the ground truth).
    """
    ratio = result_tc["effective_sample_size"] / result_no_tc["effective_sample_size"]
    # 1e-3 tolerance: the two runs anchor on slightly different time.time()
    # calls (a few ms apart), so the 1-year-old games' recency factor drifts
    # by a relative ~1e-8 between runs -- negligible, but the RATIO of two
    # slightly-different eff_samples amplifies it to ~1e-5. 1e-3 still
    # catches a gross bug (e.g. a 0.5 instead of 0.6 factor would be off by
    # 0.1 >> 1e-3) while tolerating the wall-clock drift.
    assert abs(ratio - 0.6) < 1e-3, (
        f"tc-combined: eff_sample ratio (TC/no-TC) should be 0.6 "
        f"(blitz<->rapid similarity), got {ratio} -- "
        f"TC={result_tc['effective_sample_size']} "
        f"no-TC={result_no_tc['effective_sample_size']}"
    )
    # And it's strictly less than the recency-only number (TC bites) but
    # strictly greater than 0 (old cross-TC games still contribute a soft
    # prior -- neither factor zeroes them out).
    assert result_tc["effective_sample_size"] < result_no_tc["effective_sample_size"]
    assert result_tc["effective_sample_size"] > 0.0
    print(f"  [PASS] combined recency x TC: eff_sample_TC "
          f"({result_tc['effective_sample_size']}) = 0.6 x "
          f"eff_sample_noTC ({result_no_tc['effective_sample_size']}) "
          f"-- multiplicative composition verified")


def _assert_tc_uniform_sanity(result_tc, result_no_tc):
    """Uniform-TC sanity: all games match the sparring bucket -> TC factor
    1.0 -> eff_sample == recency-only eff_sample. TC doesn't distort when
    there's nothing to down-weight."""
    assert result_tc["sparring_time_control_bucket"] == "rapid"
    assert abs(result_tc["effective_sample_size"]
               - result_no_tc["effective_sample_size"]) < 1e-6, (
        f"tc-uniform: eff_sample should match recency-only when all games "
        f"are same-TC, got TC={result_tc['effective_sample_size']} "
        f"no-TC={result_no_tc['effective_sample_size']}"
    )
    print(f"  [PASS] uniform-TC sanity: all-rapid games under rapid "
          f"sparring -> eff_sample {result_tc['effective_sample_size']} == "
          f"recency-only (TC factor 1.0, no distortion)")


def _assert_signals(result):
    """Assert the three new v1 signals match the engineered fixture values.

    Closed-form values for _fixture_signals at neutral weighting
    (all end_time=0, so weight=1.0 each) are documented in the
    `_fixture_signals` docstring. The arithmetic is reproduced here for
    the assertion messages so a regression is debuggable from the error
    alone.
    """
    print(f"\n  Engineered fixture results:")
    print(f"    average_game_length       = {result['average_game_length']}  (expected 12.6667)")
    print(f"    castling_side_distribution = {result['castling_side_distribution']}")
    print(f"    queen_trade_move_number    = {result['queen_trade_move_number']}  (expected 10.0)")
    print(f"    queens_stay_on_rate        = {result['queens_stay_on_rate']}  (expected 0.6667)")

    # --- sufficient -------------------------------------------------------
    assert result["sufficient"] is True, (
        f"signals: 6 games at neutral weight give effective_sample_size=6.0 "
        f"(above MIN_STYLE_EFF_SAMPLES=5.0); "
        f"got sufficient={result['sufficient']}"
    )
    assert result["game_count"] == 6, (
        f"signals: expected 6 games, got {result['game_count']}"
    )
    # Effective sample size: 6 games at end_time=0 (neutral weight 1.0)
    # -> effective_sample_size = 6.0. This is the UNIFORM-AGE SANITY CHECK:
    # when all games are the same age (here: all end_time=0, weight 1.0),
    # the weighted aggregates should collapse to the unweighted aggregates
    # (decay is uniform so it doesn't distort). effective_sample_size
    # equals the raw game count.
    assert "effective_sample_size" in result, (
        f"signals: effective_sample_size should be in the return dict"
    )
    assert abs(result["effective_sample_size"] - 6.0) < 1e-3, (
        f"signals: effective_sample_size should be 6.0 (6 games at neutral "
        f"weight 1.0 each), got {result['effective_sample_size']}"
    )
    assert result["recency_decay_lambda"] == 1.0, (
        f"signals: recency_decay_lambda should be 1.0, "
        f"got {result['recency_decay_lambda']}"
    )
    print(f"  [PASS] effective_sample_size = {result['effective_sample_size']} "
          f"(== game_count under neutral weighting — uniform-age sanity)")

    # --- average game length ---------------------------------------------
    # (13 + 17 + 12 + 12 + 10 + 12) / 6 = 76 / 6 = 12.6667
    expected_avg_len = 76 / 6
    assert abs(result["average_game_length"] - expected_avg_len) < 1e-3, (
        f"signals: average_game_length should be {expected_avg_len:.4f}, "
        f"got {result['average_game_length']}"
    )
    print(f"  [PASS] average_game_length = {result['average_game_length']} "
          f"(expected {expected_avg_len:.4f})")

    # --- castling side distribution --------------------------------------
    castling = result["castling_side_distribution"]
    assert castling is not None, "signals: castling_side_distribution must be non-None"
    # All 3 bins are always present (castling is a fixed 3-way domain).
    assert "kingside" in castling, f"signals: missing 'kingside' bin: {castling}"
    assert "queenside" in castling, f"signals: missing 'queenside' bin: {castling}"
    assert "never" in castling, f"signals: missing 'never' bin: {castling}"
    # 3 kingside / 1 queenside / 2 never.
    assert abs(castling["kingside"] - 0.5) < 1e-3, (
        f"signals: kingside should be 0.5 (3/6 games), got {castling['kingside']}"
    )
    assert abs(castling["queenside"] - (1 / 6)) < 1e-3, (
        f"signals: queenside should be 1/6 (1/6 games), got {castling['queenside']}"
    )
    assert abs(castling["never"] - (2 / 6)) < 1e-3, (
        f"signals: never should be 2/6 (2/6 games), got {castling['never']}"
    )
    # Sums to ~1.0 (the stat is a probability distribution).
    total = sum(castling.values())
    assert abs(total - 1.0) < 1e-3, (
        f"signals: castling_side_distribution should sum to ~1.0, got {total}"
    )
    # Sorted by descending weight: kingside=0.5, never=0.3333, queenside=0.1667.
    keys_in_order = list(castling.keys())
    assert keys_in_order == ["kingside", "never", "queenside"], (
        f"signals: castling_side_distribution should be sorted desc "
        f"(kingside, never, queenside), got order {keys_in_order}"
    )
    print(f"  [PASS] castling_side_distribution = {castling}")
    print(f"         (all 3 bins present, sums to {total:.4f}, sorted desc)")

    # --- queen trade timing (a): last-queen-capture ply ------------------
    # Only KSIDE_EARLY_Q (ply 8) and KSIDE_MID_Q (ply 12) have both queens
    # off at end. Simple mean: (8 + 12) / 2 = 10.0.
    expected_q_trade = 10.0
    assert result["queen_trade_move_number"] is not None, (
        f"signals: queen_trade_move_number should be 10.0 (2 games qualify), "
        f"got None"
    )
    assert abs(result["queen_trade_move_number"] - expected_q_trade) < 1e-3, (
        f"signals: queen_trade_move_number should be {expected_q_trade}, "
        f"got {result['queen_trade_move_number']}"
    )
    print(f"  [PASS] queen_trade_move_number = {result['queen_trade_move_number']} "
          f"(expected {expected_q_trade}; from 2 qualifying games: ply 8 + ply 12)")

    # --- queen trade timing (b): queens-stay-on rate ---------------------
    # 4 of 6 games have both queens on at end (QSIDE_NO_Q, NEVER_NO_Q_A,
    # KSIDE_NO_Q_BLACK, NEVER_NO_Q_B) -> 4/6 = 0.6667.
    expected_q_stay = 4 / 6
    assert abs(result["queens_stay_on_rate"] - expected_q_stay) < 1e-3, (
        f"signals: queens_stay_on_rate should be {expected_q_stay:.4f} (4/6 games), "
        f"got {result['queens_stay_on_rate']}"
    )
    print(f"  [PASS] queens_stay_on_rate = {result['queens_stay_on_rate']} "
          f"(expected {expected_q_stay:.4f}; 4 of 6 games have both queens on)")

    # --- cross-signal cross-check: (a) eligibility + (b) numerator + the
    # asymmetric middle case (none in this fixture) should account for all
    # 6 games. (a) covers 2 games (both queens off); (b) covers 4 games
    # (both queens on); 0 games in the asymmetric middle case. 2 + 0 + 4 = 6.
    # We don't have direct access to the per-game flags from the result
    # dict, but the 2/4 partition implies: weighted(q-trade denom)=2,
    # weighted(q-stay numerator)=4, and weighted_game_count=6. We can
    # verify weighted_game_count=6 by the neutral-weighting setup.
    assert abs(result["weighted_game_count"] - 6.0) < 1e-3, (
        f"signals: weighted_game_count should be 6.0 under neutral weighting, "
        f"got {result['weighted_game_count']}"
    )
    # All 6 games in this fixture are parseable, so weighted_parseable_game_count
    # should equal weighted_game_count (no data-quality gap).
    assert abs(result["weighted_parseable_game_count"] - 6.0) < 1e-3, (
        f"signals: weighted_parseable_game_count should be 6.0 (all parseable), "
        f"got {result['weighted_parseable_game_count']}"
    )
    assert abs(result["weighted_parseable_game_count"] - result["weighted_game_count"]) < 1e-9, (
        f"signals: weighted_parseable_game_count should equal weighted_game_count "
        f"(no unparseable PGNs), got {result['weighted_parseable_game_count']} vs "
        f"{result['weighted_game_count']}"
    )
    print(f"  [PASS] weighted_game_count = {result['weighted_game_count']} "
          f"(neutral weighting -> all 6 games at weight 1.0)")
    print(f"  [PASS] weighted_parseable_game_count = {result['weighted_parseable_game_count']} "
          f"== weighted_game_count (no unparseable PGNs)")
    # And the q-stay rate of 4/6 implies the q-trade denominator was 2
    # (minus the asymmetric middle case, which is 0 in this fixture).

    # --- regression: existing signals still compute on this fixture -----
    # Make sure the new code paths didn't break sac freq / family lean.
    # Don't assert exact values (the engineered games trigger the
    # documented v1 false-positive "queen-trade-as-sac" issue for the two
    # queen-trading games; sac freq is non-trivial to closed-form). Just
    # confirm the keys are present, the family distribution has at least
    # the engineered openings, and sacrifice events raw count is an int.
    assert result["opening_family_lean"] is not None, (
        "signals: opening_family_lean should be populated on sufficient=True"
    )
    assert "King's Pawn Game" in result["opening_family_lean"], (
        f"signals: expected 'King's Pawn Game' in family lean, "
        f"got {result['opening_family_lean']}"
    )
    family_total = sum(result["opening_family_lean"].values())
    assert abs(family_total - 1.0) < 1e-3, (
        f"signals: family_lean should sum to ~1.0, got {family_total}"
    )
    assert isinstance(result["sacrifice_events"], int), (
        f"signals: sacrifice_events should be int, got {type(result['sacrifice_events'])}"
    )
    assert isinstance(result["opponent_moves"], int), (
        f"signals: opponent_moves should be int, got {type(result['opponent_moves'])}"
    )
    # The two queen-trading games (KSIDE_EARLY_Q at ply 7, KSIDE_MID_Q at
    # ply 11) trigger the documented v1 limitation where a queen trade
    # false-positives as a sacrifice (each side's material drops by 9
    # without "recoup" within the 3-ply window). So sacrifice_events
    # should be >= 2 (one for the 7...Qxe7+ by white in each game). Don't
    # assert the exact count — the documented quirk is enough signal.
    if result["opponent_moves"] > 0:
        assert result["sacrifice_events"] >= 2, (
            f"signals: expected >= 2 queen-trade false-positive sacs (one per "
            f"queen-trading game), got {result['sacrifice_events']} over "
            f"{result['opponent_moves']} opp-moves"
        )
        print(f"  [PASS] existing signals still compute on new fixture: "
              f"sac_freq={result['sacrifice_frequency']} "
              f"(>=2 queen-trade false-positive sacs as documented v1 limit), "
              f"family lean sums to {family_total:.4f}")


def _assert_unparseable_excluded(result_clean, result_dirty):
    """Assert all five signals are identical between the clean 6-game
    fixture (D) and the 8-row fixture (E) that adds 2 unparseable PGNs.

    Under the denominator-consistency contract, unparseable PGNs are
    excluded from every signal's denominator, so they should have ZERO
    effect on any of the five signal values — only the transparency
    counts (game_count, weighted_game_count, weighted_parseable_game_count)
    should differ between clean and dirty.

    This test shows the numbers side-by-side so the effect (or deliberate
    lack of effect) is visible, as the spec requires.
    """
    print(f"\n  Side-by-side comparison (clean 6 games vs dirty 6+2 bad):")
    print(f"    {'field':>35}  {'clean':>12}  {'dirty':>12}  {'delta':>12}")
    print(f"    {'-'*35}  {'-'*12}  {'-'*12}  {'-'*12}")

    # --- transparency counts that SHOULD differ -----------------------------
    # game_count: 6 -> 8 (we have 8 rows on file in the dirty fixture).
    print(f"    {'game_count':>35}  {result_clean['game_count']:>12}  "
          f"{result_dirty['game_count']:>12}  "
          f"{result_dirty['game_count'] - result_clean['game_count']:>+12}")
    assert result_dirty["game_count"] == result_clean["game_count"] + 2, (
        f"unparseable: dirty game_count should be clean+2, "
        f"got {result_dirty['game_count']} vs {result_clean['game_count']}"
    )

    # weighted_game_count: 6.0 -> 8.0 (bad rows still contribute their weight).
    print(f"    {'weighted_game_count':>35}  {result_clean['weighted_game_count']:>12.4f}  "
          f"{result_dirty['weighted_game_count']:>12.4f}  "
          f"{result_dirty['weighted_game_count'] - result_clean['weighted_game_count']:>+12.4f}")
    assert abs(result_dirty["weighted_game_count"] - 8.0) < 1e-3, (
        f"unparseable: weighted_game_count should be 8.0 (6 good + 2 bad at weight 1.0), "
        f"got {result_dirty['weighted_game_count']}"
    )
    assert abs(result_clean["weighted_game_count"] - 6.0) < 1e-3, (
        f"unparseable: clean weighted_game_count should be 6.0, "
        f"got {result_clean['weighted_game_count']}"
    )

    # weighted_parseable_game_count: 6.0 -> 6.0 (bad rows excluded).
    print(f"    {'weighted_parseable_game_count':>35}  "
          f"{result_clean['weighted_parseable_game_count']:>12.4f}  "
          f"{result_dirty['weighted_parseable_game_count']:>12.4f}  "
          f"{result_dirty['weighted_parseable_game_count'] - result_clean['weighted_parseable_game_count']:>+12.4f}")
    assert abs(result_dirty["weighted_parseable_game_count"] - 6.0) < 1e-3, (
        f"unparseable: weighted_parseable_game_count should be 6.0 (bad rows excluded), "
        f"got {result_dirty['weighted_parseable_game_count']}"
    )
    assert abs(result_dirty["weighted_parseable_game_count"]
               - result_clean["weighted_parseable_game_count"]) < 1e-9, (
        f"unparseable: weighted_parseable_game_count should be identical "
        f"clean vs dirty (bad rows excluded from both), got "
        f"{result_clean['weighted_parseable_game_count']} vs "
        f"{result_dirty['weighted_parseable_game_count']}"
    )
    print(f"  [PASS] transparency counts behave correctly: game_count and "
          f"weighted_game_count increase by 2; weighted_parseable_game_count "
          f"stays at 6.0 (bad rows excluded)")

    # --- all five signals that should be IDENTICAL ---------------------------
    # Under the denominator-consistency fix, unparseable PGNs contribute
    # zero to every numerator AND every signal-specific denominator, so
    # every signal value is unchanged between clean and dirty.
    signal_keys = [
        "sacrifice_frequency",
        "average_game_length",
        "queens_stay_on_rate",
    ]
    for key in signal_keys:
        clean_val = result_clean[key]
        dirty_val = result_dirty[key]
        print(f"    {key:>35}  {clean_val!s:>12}  {dirty_val!s:>12}  "
              f"{'(identical)' if clean_val == dirty_val else '(DIFFERS!)':>12}")
        assert clean_val == dirty_val, (
            f"unparseable: signal '{key}' should be identical clean vs dirty "
            f"(unparseable PGNs excluded from denominator), got "
            f"{clean_val} vs {dirty_val}"
        )

    # average_game_length deserves a special public spotlights: it was the
    # signal MOST affected by the BUG (under the old code, 2 unparseable
    # rows with weight 1.0 each would drag the mean from 12.6667 to
    # 76 / 8 = 9.5 — a 25% lie). The fix keeps it at 12.6667 because the
    # denominator is now weighted_parseable_game_count (6.0), not
    # weighted_game_count (which would be 8.0 with the bad rows). Show this
    # explicitly so the effect of the fix is visible.
    print(f"\n  average_game_length in detail:")
    print(f"    clean:                  {result_clean['average_game_length']}")
    print(f"    dirty (with fix):       {result_dirty['average_game_length']}")
    old_buggy_dirty = 76.0 / 8.0
    print(f"    dirty (old buggy code): {round(old_buggy_dirty, 4)}  <-- would have been {round(old_buggy_dirty, 4)}")
    print(f"    (old code divided by weighted_game_count=8.0 instead of "
          f"weighted_parseable_game_count=6.0)")
    assert abs(result_dirty["average_game_length"] - result_clean["average_game_length"]) < 1e-9, (
        f"unparseable: average_game_length should be identical clean vs dirty; "
        f"the fix changed the denominator from weighted_game_count to "
        f"weighted_parseable_game_count so unparseable PGNs don't drag the mean"
    )

    # queens_stay_on_rate was also affected by the bug: old code divided
    # weighted_queens_on_games (4.0) by weighted_game_count (8.0) = 0.5
    # instead of the correct 4.0 / 6.0 = 0.6667.
    print(f"\n  queens_stay_on_rate in detail:")
    print(f"    clean:                  {result_clean['queens_stay_on_rate']}")
    print(f"    dirty (with fix):       {result_dirty['queens_stay_on_rate']}")
    old_buggy_dirty_rate = 4.0 / 8.0
    print(f"    dirty (old buggy code): {round(old_buggy_dirty_rate, 4)}  <-- would have been {round(old_buggy_dirty_rate, 4)}")
    print(f"    (old code divided by weighted_game_count=8.0 instead of "
          f"weighted_parseable_game_count=6.0)")
    assert abs(result_dirty["queens_stay_on_rate"] - result_clean["queens_stay_on_rate"]) < 1e-9, (
        f"unparseable: queens_stay_on_rate should be identical clean vs dirty"
    )

    # Dict-valued signals: compare element-wise.
    for dict_key in ["opening_family_lean", "castling_side_distribution"]:
        clean_d = result_clean[dict_key] or {}
        dirty_d = result_dirty[dict_key] or {}
        print(f"    {dict_key:>35}  {json.dumps(clean_d, sort_keys=True):>12}  "
              f"{json.dumps(dirty_d, sort_keys=True):>12}")
        assert clean_d == dirty_d, (
            f"unparseable: signal '{dict_key}' should be identical clean vs dirty, "
            f"got {clean_d} vs {dirty_d}"
        )
    print(f"  [PASS] opening_family_lean and castling_side_distribution are "
          f"identical clean vs dirty")

    # queen_trade_move_number: None for both (same qualifying games in both).
    print(f"    {'queen_trade_move_number':>35}  "
          f"{result_clean['queen_trade_move_number']!s:>12}  "
          f"{result_dirty['queen_trade_move_number']!s:>12}")
    assert result_clean["queen_trade_move_number"] == result_dirty["queen_trade_move_number"], (
        f"unparseable: queen_trade_move_number should be identical, "
        f"got {result_clean['queen_trade_move_number']} vs "
        f"{result_dirty['queen_trade_move_number']}"
    )
    print(f"  [PASS] queen_trade_move_number identical clean vs dirty")

    # raw transparency ints that SHOULD differ:
    # sacrifice_events and opponent_moves are raw (unweighted) counts from
    # parseable games only — bad rows contribute 0. So they should be
    # IDENTICAL clean vs dirty.
    for raw_key in ["sacrifice_events", "opponent_moves"]:
        print(f"    {raw_key:>35}  {result_clean[raw_key]:>12}  "
              f"{result_dirty[raw_key]:>12}")
        assert result_clean[raw_key] == result_dirty[raw_key], (
            f"unparseable: {raw_key} should be identical (raw counts from "
            f"parseable games only), got {result_clean[raw_key]} vs "
            f"{result_dirty[raw_key]}"
        )
    print(f"  [PASS] sacrifice_events and opponent_moves are identical "
          f"(raw counts from parseable games only — bad rows contribute 0)")

    print(f"\n  [PASS] ALL FIVE signals are identical between clean (6 games) "
          f"and dirty (6+2 bad) fixtures — denominator consistency verified.")


def _assert_time_controls(result):
    """Assert `compute_time_control_distribution` on the 6-game fixture F.

    Fixture F (neutral weighting, end_time=0 so weight=1.0 each):
      3 games at [TimeControl "600+0"]  -> bucket "10+0"
      2 games at [TimeControl "180+2"]  -> bucket "3+2"
      1 game  at [TimeControl "60+0"]   -> bucket "1+0"

    Closed-form expected values (weight=1.0 each, simple arithmetic over
    6 games; only 3 buckets exist so all are within the top-N kept and
    no "Other" bucket is created):

      weighted_game_count   = 6.0
      weighted_with_tc      = 6.0      (every game has a TC header)
      distribution = {
        "10+0": 3/6 = 0.5,
        "3+2":  2/6 = 0.3333,
        "1+0":  1/6 = 0.1667,
      }   (sorted desc; no "Other" key)
      most_common = "10+0"
    """
    print(f"\n  Time-control fixture results:")
    print(f"    game_count           = {result['game_count']}  (expected 6)")
    print(f"    weighted_game_count  = {result['weighted_game_count']}  (expected 6.0)")
    print(f"    weighted_with_tc      = {result['weighted_with_tc']}  (expected 6.0)")
    print(f"    most_common          = {result['most_common']}  (expected '10+0')")
    print(f"    distribution         = {result['distribution']}")

    assert result["sufficient"] is True, (
        f"time-controls: 6 games is above MIN_STYLE_GAMES=3, "
        f"got sufficient={result['sufficient']}"
    )
    assert result["game_count"] == 6, (
        f"time-controls: expected 6 games, got {result['game_count']}"
    )
    assert abs(result["weighted_game_count"] - 6.0) < 1e-3, (
        f"time-controls: weighted_game_count should be 6.0 under neutral "
        f"weighting, got {result['weighted_game_count']}"
    )
    # Every game has a parseable [TimeControl], so the TC-bearing weight
    # equals total weight (no data-quality gap).
    assert abs(result["weighted_with_tc"] - 6.0) < 1e-3, (
        f"time-controls: weighted_with_tc should equal weighted_game_count "
        f"(every game has a TC header), got {result['weighted_with_tc']} "
        f"vs {result['weighted_game_count']}"
    )

    distribution = result["distribution"]
    assert distribution is not None, (
        "time-controls: distribution should be populated on sufficient=True "
        "with all games carrying a TC header"
    )
    # Exactly the 3 engineered buckets — no "Other" (only 3 buckets, well
    # below _TOP_TIME_CONTROL_BUCKETS).
    assert "Other" not in distribution, (
        f"time-controls: with only 3 buckets no 'Other' should be created, "
        f"got {distribution}"
    )
    assert set(distribution.keys()) == {"10+0", "3+2", "1+0"}, (
        f"time-controls: expected buckets {{10+0, 3+2, 1+0}}, got "
        f"{set(distribution.keys())}"
    )
    # Percentages (stored as fractions in [0, 1]).
    assert abs(distribution["10+0"] - 0.5) < 1e-3, (
        f"time-controls: '10+0' should be 3/6 = 0.5, got {distribution['10+0']}"
    )
    assert abs(distribution["3+2"] - (2 / 6)) < 1e-3, (
        f"time-controls: '3+2' should be 2/6 = 0.3333, got {distribution['3+2']}"
    )
    assert abs(distribution["1+0"] - (1 / 6)) < 1e-3, (
        f"time-controls: '1+0' should be 1/6 = 0.1667, got {distribution['1+0']}"
    )
    # Distribution sums to ~1.0 (it's a probability distribution).
    total = sum(distribution.values())
    assert abs(total - 1.0) < 1e-3, (
        f"time-controls: distribution should sum to ~1.0, got {total}"
    )
    # Sorted by descending weight (matches the existing signal sorts).
    keys_in_order = list(distribution.keys())
    assert keys_in_order == ["10+0", "3+2", "1+0"], (
        f"time-controls: distribution should be sorted desc, got {keys_in_order}"
    )

    # The single most-common bucket.
    assert result["most_common"] == "10+0", (
        f"time-controls: most_common should be '10+0' (3 of 6 games), "
        f"got {result['most_common']}"
    )
    print(f"  [PASS] time-control distribution (neutral): 3 buckets, "
          f"percentages match, most_common='10+0'")


def _assert_time_control_recency_tilt(games, result):
    """Assert the recency decay is OPERATIVE in compute_time_control_distribution.

    Fixture G (designed so raw count and recency-weight disagree):
      3 recent "10+0" games at end_time NOW            (weight 1.0 each)
      4 OLD    "3+2" games at end_time NOW - 4 years    (weight ~0.018 each)

    Raw count tilts toward "3+2" (4 > 3), so if this function were a raw
    tally it would pick "3+2". With the recency decay reused from the rest
    of the module (lambda=1.0/yr -> 4yr weight ~0.018), the weighted share
    of "10+0" (3.0) dominates "3+2" (4 * 0.018 ~= 0.072), so the
    most_common bucket flips to "10+0". This is the test that
    distinguishes "reuse of the existing decay" from "raw count" — a
    raw-tally implementation would FAIL this assertion.
    """
    expected_4yr_weight = 2.71828 ** (-1.0 * 4)
    expected_3plus2_weight = 4 * expected_4yr_weight
    expected_10plus0_weight = 3.0
    expected_total = expected_10plus0_weight + expected_3plus2_weight
    expected_10plus0_share = expected_10plus0_weight / expected_total
    expected_3plus2_share = expected_3plus2_weight / expected_total

    print(f"\n  Recency-tilt fixture closed-form:")
    print(f"    4-yr-decay weight per game = {round(expected_4yr_weight, 4)}")
    print(f"    weighted 10+0 = 3.0        weighted 3+2 = {round(expected_3plus2_weight, 4)}")
    print(f"    expected share 10+0 = {round(expected_10plus0_share, 4)}")
    print(f"    expected share 3+2  = {round(expected_3plus2_share, 4)}")
    print(f"    raw counts: 10+0=3, 3+2=4  (raw tally would pick 3+2)")
    print(f"    result most_common = {result['most_common']}")
    print(f"    result distribution = {result['distribution']}")

    assert result["sufficient"] is True, (
        f"time-control recency: 7 games is above MIN_STYLE_GAMES, "
        f"got sufficient={result['sufficient']}"
    )
    assert result["game_count"] == 7, (
        f"time-control recency: expected 7 games, got {result['game_count']}"
    )
    distribution = result["distribution"]
    assert distribution is not None, (
        "time-control recency: distribution should be populated"
    )
    # The decisive assertion: recency tilts most_common to "10+0" even
    # though raw counts favour "3+2".
    assert result["most_common"] == "10+0", (
        f"time-control recency: most_common should be '10+0' under recency "
        f"weighting (3 recent > 4 four-year-old), got "
        f"{result['most_common']} -- the function looks like a raw tally "
        f"instead of reusing STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR"
    )
    assert abs(distribution["10+0"] - expected_10plus0_share) < 1e-3, (
        f"time-control recency: '10+0' share should be "
        f"{expected_10plus0_share:.4f} under decay, got "
        f"{distribution['10+0']}"
    )
    assert abs(distribution["3+2"] - expected_3plus2_share) < 1e-3, (
        f"time-control recency: '3+2' share should be "
        f"{expected_3plus2_share:.4f} under decay, got "
        f"{distribution['3+2']}"
    )
    print(f"  [PASS] time-control recency tilt verified: most_common flips "
          f"to the recent bucket ('10+0') despite losing raw-count "
          f"(3 vs 4) — the existing decay constant is reused, not "
          f"redefined.")


def _assert_time_control_floor(result):
    """Assert the MIN_STYLE_GAMES floor suppresses the time-control signal.

    2 games (fixture F's first 2 rows) is below MIN_STYLE_GAMES=3, so
    both `distribution` and `most_common` must be None — the sparring page
    must NOT prefill a Time Control field off a 2-game opponent. This
    matches the existing fall-through contract `compute_opponent_style`
    uses via the same floor constant.
    """
    assert result["sufficient"] is False, (
        f"time-control floor: 2 games is below MIN_STYLE_GAMES=3, "
        f"got sufficient={result['sufficient']}"
    )
    assert result["game_count"] == 2, (
        f"time-control floor: expected 2 games, got {result['game_count']}"
    )
    assert result["distribution"] is None, (
        f"time-control floor: distribution should be None below floor, "
        f"got {result['distribution']}"
    )
    assert result["most_common"] is None, (
        f"time-control floor: most_common should be None below floor, "
        f"got {result['most_common']}"
    )
    # transparency counts: the floor is on raw count, never weighted, so
    # weighted_game_count is reported 0.0 in the insufficient path (matches
    # compute_opponent_style below-floor response shape).
    assert result["weighted_game_count"] == 0.0, (
        f"time-control floor: weighted_game_count should be 0.0 below floor, "
        f"got {result['weighted_game_count']}"
    )
    print(f"  [PASS] time-control floor: below MIN_STYLE_GAMES -> "
          f"distribution=None, most_common=None (sparring page will not "
          f"prefill).")


def _assert_opening_results(result):
    """Assert `compute_opening_results` on the 7-game fixture (mix of W/L/D).

    Closed-form expected by_opening values (weight=1.0 each under neutral
    end_time=0 — simple arithmetic over the games in each bucket):

      "Italian Game":      3 games -> weighted_count=3.0
        weighted_wins=1.0   weighted_losses=1.0   weighted_draws=1.0
        win_rate = 1 / (1+1+1) = 0.3333

      "Sicilian Defense":  3 games -> weighted_count=3.0
        weighted_wins=2.0   weighted_losses=1.0   weighted_draws=0.0
        win_rate = 2 / (2+1+0) = 0.6667

      "Scotch Game":       1 game  -> weighted_count=1.0
        weighted_wins=0.0   weighted_losses=1.0   weighted_draws=0.0
        win_rate = 0 / (0+1+0) = 0.0
        (the spec: "show every bucket with at least one game, however
         small" — the NO-FLOOR contract is the decisive difference from
         compute_time_control_distribution / compute_opponent_style.)

    by_opening must be sorted by descending weighted_count. Italian and
    Sicilian tie at 3.0 — a stable sort preserves insertion order, which
    is the order the games were appended (Italian first, Sicilian second,
    Scotch last). This is the order the spec wants ("most-played-first
    ordering matches the frequency panel's").
    """
    print(f"\n  Opening-results fixture results:")
    print(f"    game_count                          = {result['game_count']}  (expected 7)")
    print(f"    weighted_game_count                 = {result['weighted_game_count']}  (expected 7.0)")
    print(f"    weighted_parseable_game_count       = {result['weighted_parseable_game_count']}  (expected 7.0)")
    print(f"    by_opening                          = {json.dumps(result['by_opening'], sort_keys=True)}")

    assert result["game_count"] == 7, (
        f"opening-results: expected 7 games, got {result['game_count']}"
    )
    # NO floor on this signal: sufficient is not in the response shape at
    # all (unlike compute_time_control_distribution which returns
    # sufficient=False below MIN_STYLE_GAMES). Asserting the key's
    # ABSENCE is the regression guard for "don't add filtering".
    assert "sufficient" not in result, (
        f"opening-results: 'sufficient' must NOT be in the response "
        f"(no floor per spec); got sufficient={result.get('sufficient')!r}"
    )
    assert abs(result["weighted_game_count"] - 7.0) < 1e-3, (
        f"opening-results: weighted_game_count should be 7.0, got "
        f"{result['weighted_game_count']}"
    )
    # Every game is parseable (all built via _pgn with a valid move list),
    # so weighted_parseable_game_count == weighted_game_count.
    assert abs(result["weighted_parseable_game_count"] - 7.0) < 1e-3, (
        f"opening-results: weighted_parseable_game_count should equal "
        f"weighted_game_count (all games parseable), got "
        f"{result['weighted_parseable_game_count']}"
    )

    by_opening = result["by_opening"]
    assert set(by_opening.keys()) == {
        "Italian Game", "Sicilian Defense", "Scotch Game",
    }, (
        f"opening-results: expected 3 buckets {{Italian, Sicilian, Scotch}}, "
        f"got {set(by_opening.keys())}"
    )

    # --- Italian Game: 1 W / 1 L / 1 D -> win_rate 1/3 ------------------
    italian = by_opening["Italian Game"]
    assert abs(italian["weighted_count"] - 3.0) < 1e-3, (
        f"opening-results: Italian weighted_count should be 3.0, got "
        f"{italian['weighted_count']}"
    )
    assert abs(italian["weighted_wins"] - 1.0) < 1e-3, (
        f"opening-results: Italian weighted_wins should be 1.0, got "
        f"{italian['weighted_wins']}"
    )
    assert abs(italian["weighted_losses"] - 1.0) < 1e-3, (
        f"opening-results: Italian weighted_losses should be 1.0, got "
        f"{italian['weighted_losses']}"
    )
    assert abs(italian["weighted_draws"] - 1.0) < 1e-3, (
        f"opening-results: Italian weighted_draws should be 1.0, got "
        f"{italian['weighted_draws']}"
    )
    assert abs(italian["win_rate"] - (1 / 3)) < 1e-3, (
        f"opening-results: Italian win_rate should be 1/3 = 0.3333, got "
        f"{italian['win_rate']}"
    )
    print(f"  [PASS] Italian Game: 1W/1L/1D -> win_rate={italian['win_rate']}")

    # --- Sicilian Defense: 2 W / 1 L / 0 D -> win_rate 2/3 -------------
    sicilian = by_opening["Sicilian Defense"]
    assert abs(sicilian["weighted_count"] - 3.0) < 1e-3, (
        f"opening-results: Sicilian weighted_count should be 3.0, got "
        f"{sicilian['weighted_count']}"
    )
    assert abs(sicilian["weighted_wins"] - 2.0) < 1e-3, (
        f"opening-results: Sicilian weighted_wins should be 2.0 (1 as "
        f"black, 1 as white), got {sicilian['weighted_wins']}"
    )
    assert abs(sicilian["weighted_losses"] - 1.0) < 1e-3, (
        f"opening-results: Sicilian weighted_losses should be 1.0 "
        f"(as black), got {sicilian['weighted_losses']}"
    )
    assert abs(sicilian["weighted_draws"] - 0.0) < 1e-3, (
        f"opening-results: Sicilian weighted_draws should be 0.0, got "
        f"{sicilian['weighted_draws']}"
    )
    assert abs(sicilian["win_rate"] - (2 / 3)) < 1e-3, (
        f"opening-results: Sicilian win_rate should be 2/3 = 0.6667, got "
        f"{sicilian['win_rate']}"
    )
    print(f"  [PASS] Sicilian Defense: 2W/1L/0D (both colors) -> "
          f"win_rate={sicilian['win_rate']}")

    # --- Scotch Game: 0 W / 1 L / 0 D -> win_rate 0.0 (NO FLOOR) -------
    # This is the spec's decisive no-floor assertion: a 1-game bucket
    # MUST appear. If compute_opening_results had inherited
    # MIN_STYLE_GAMES filtering, this bucket would be absent and the
    # assertion would fail.
    scotch = by_opening["Scotch Game"]
    assert abs(scotch["weighted_count"] - 1.0) < 1e-3, (
        f"opening-results: Scotch weighted_count should be 1.0, got "
        f"{scotch['weighted_count']}"
    )
    assert abs(scotch["weighted_wins"] - 0.0) < 1e-3, (
        f"opening-results: Scotch weighted_wins should be 0.0, got "
        f"{scotch['weighted_wins']}"
    )
    assert abs(scotch["weighted_losses"] - 1.0) < 1e-3, (
        f"opening-results: Scotch weighted_losses should be 1.0, got "
        f"{scotch['weighted_losses']}"
    )
    assert abs(scotch["weighted_draws"] - 0.0) < 1e-3, (
        f"opening-results: Scotch weighted_draws should be 0.0, got "
        f"{scotch['weighted_draws']}"
    )
    assert scotch["win_rate"] == 0.0, (
        f"opening-results: Scotch win_rate should be 0.0 (1 loss, 0 wins), "
        f"got {scotch['win_rate']}"
    )
    print(f"  [PASS] Scotch Game: 0W/1L/0D (single-game bucket, NO floor) "
          f"-> win_rate={scotch['win_rate']}")

    # --- sort order: by descending weighted_count, stable on ties -----
    # Italian (3.0) and Sicilian (3.0) tie; Scotch (1.0) last. A stable
    # sort preserves insertion order (Italian before Sicilian, since the
    # first Italian game was appended before the first Sicilian).
    keys_in_order = list(by_opening.keys())
    assert keys_in_order[0] in ("Italian Game", "Sicilian Defense"), (
        f"opening-results: first bucket should be a 3-game bucket, got "
        f"{keys_in_order[0]}"
    )
    assert keys_in_order[-1] == "Scotch Game", (
        f"opening-results: last bucket should be Scotch (1 game, lowest "
        f"weighted_count), got {keys_in_order[-1]} -- sort by descending "
        f"weighted_count is broken"
    )
    print(f"  [PASS] by_opening sorted by descending weighted_count "
          f"(order: {keys_in_order})")


def _assert_opening_results_with_aborted(result, result_clean):
    """Assert "*" (unfinished/aborted) games are handled per the contract.

    Fixture = 7 clean games + 2 aborted games:
      row 8: opp=white, Result="*", "Caro-Kann Defense"  -> NEW bucket
      row 9: opp=white, Result="*", "Italian Game"        -> existing bucket

    Contract checks (from compute_opening_results' docstring):
      A. "*" contributes to the bucket's weighted_count only.
      B. "*" contributes to NONE of the W/L/D numerators.
      C. "*" is excluded from win_rate's denominator.
      D. An ALL-"*" bucket has win_rate=None (not 0.0) — the honest
         "absent", not "loses every game".

    Concretely:
      - "Caro-Kann Defense" is a new single-game bucket with
        weighted_count=1.0, all W/L/D = 0.0, win_rate=None.
      - "Italian Game" weighted_count goes 3.0 -> 4.0 (the aborted game
        IS counted toward the bucket's mass), but its W/L/D numerators
        and win_rate stay IDENTICAL to the clean fixture's Italian
        bucket (1.0 / 1.0 / 1.0 / 0.3333) — the aborted game contributes
        to weighted_count only and is excluded from win_rate's
        denominator. This is the decisive test of (A)+(B)+(C).
      - The other two buckets (Sicilian, Scotch) are untouched.
    """
    print(f"\n  Aborted-game fixture results:")
    print(f"    game_count (dirty)        = {result['game_count']}  (expected 9)")
    print(f"    weighted_game_count (dirty) = {result['weighted_game_count']}  (expected 9.0)")
    print(f"    by_opening (dirty)         = {json.dumps(result['by_opening'], sort_keys=True)}")

    assert result["game_count"] == 9, (
        f"opening-results aborted: expected 9 games (7 clean + 2 aborted), "
        f"got {result['game_count']}"
    )
    assert abs(result["weighted_game_count"] - 9.0) < 1e-3, (
        f"opening-results aborted: weighted_game_count should be 9.0, "
        f"got {result['weighted_game_count']}"
    )
    assert abs(result["weighted_parseable_game_count"] - 9.0) < 1e-3, (
        f"opening-results aborted: weighted_parseable_game_count should be "
        f"9.0 (aborted games are still parseable — they have valid PGNs, "
        f"just result='*'), got {result['weighted_parseable_game_count']}"
    )

    by_opening = result["by_opening"]
    # All four buckets present (the original 3 + the new Caro-Kann).
    assert set(by_opening.keys()) == {
        "Italian Game", "Sicilian Defense", "Scotch Game", "Caro-Kann Defense",
    }, (
        f"opening-results aborted: expected 4 buckets (Caro-Kann added), "
        f"got {set(by_opening.keys())}"
    )

    # --- D: all-"*" bucket has win_rate=None ---------------------------
    caro = by_opening["Caro-Kann Defense"]
    assert abs(caro["weighted_count"] - 1.0) < 1e-3, (
        f"opening-results aborted: Caro-Kann weighted_count should be 1.0, "
        f"got {caro['weighted_count']}"
    )
    assert abs(caro["weighted_wins"] - 0.0) < 1e-3, (
        f"opening-results aborted: Caro-Kann weighted_wins should be 0.0, "
        f"got {caro['weighted_wins']}"
    )
    assert abs(caro["weighted_losses"] - 0.0) < 1e-3, (
        f"opening-results aborted: Caro-Kann weighted_losses should be 0.0, "
        f"got {caro['weighted_losses']}"
    )
    assert abs(caro["weighted_draws"] - 0.0) < 1e-3, (
        f"opening-results aborted: Caro-Kann weighted_draws should be 0.0, "
        f"got {caro['weighted_draws']}"
    )
    assert caro["win_rate"] is None, (
        f"opening-results aborted: Caro-Kann win_rate should be None "
        f"(every game in it is '*' -- no result signal), got "
        f"{caro['win_rate']!r} -- the contract is None (absent), not 0.0 "
        f"(would imply 'loses every game')"
    )
    print(f"  [PASS] Caro-Kann (all-'*') bucket: win_rate=None (honest "
          f"'absent', not 0.0-as-signal)")

    # --- A+B+C: "*" contributes to weighted_count only, not W/L/D, not
    #     win_rate's denominator. Italian in the dirty fixture has the
    #     aborted game added (weighted_count 3.0 -> 4.0) but its W/L/D
    #     and win_rate are IDENTICAL to the clean fixture's Italian.
    italian_dirty = by_opening["Italian Game"]
    italian_clean = result_clean["by_opening"]["Italian Game"]
    assert abs(italian_dirty["weighted_count"] - 4.0) < 1e-3, (
        f"opening-results aborted: Italian weighted_count should be 4.0 "
        f"(3 clean + 1 aborted), got {italian_dirty['weighted_count']}"
    )
    assert abs(italian_dirty["weighted_count"]
               - italian_clean["weighted_count"] - 1.0) < 1e-3, (
        f"opening-results aborted: Italian weighted_count should be "
        f"clean+1 (the aborted game counts toward the bucket's mass), "
        f"got {italian_dirty['weighted_count']} vs clean "
        f"{italian_clean['weighted_count']}"
    )
    # W/L/D and win_rate IDENTICAL clean vs dirty.
    assert italian_dirty["weighted_wins"] == italian_clean["weighted_wins"], (
        f"opening-results aborted: Italian weighted_wins should be "
        f"identical clean vs dirty (the '*' game contributes to NONE of "
        f"the W/L/D numerators), got {italian_dirty['weighted_wins']} vs "
        f"{italian_clean['weighted_wins']}"
    )
    assert italian_dirty["weighted_losses"] == italian_clean["weighted_losses"], (
        f"opening-results aborted: Italian weighted_losses should be "
        f"identical clean vs dirty, got {italian_dirty['weighted_losses']} "
        f"vs {italian_clean['weighted_losses']}"
    )
    assert italian_dirty["weighted_draws"] == italian_clean["weighted_draws"], (
        f"opening-results aborted: Italian weighted_draws should be "
        f"identical clean vs dirty, got {italian_dirty['weighted_draws']} "
        f"vs {italian_clean['weighted_draws']}"
    )
    assert italian_dirty["win_rate"] == italian_clean["win_rate"], (
        f"opening-results aborted: Italian win_rate should be identical "
        f"clean vs dirty (the '*' game is excluded from win_rate's "
        f"denominator AND numerator, so the rate is unchanged), got "
        f"{italian_dirty['win_rate']} vs {italian_clean['win_rate']}"
    )
    print(f"  [PASS] Italian bucket: aborted game added to weighted_count "
          f"({italian_clean['weighted_count']} -> {italian_dirty['weighted_count']}) "
          f"but W/L/D and win_rate IDENTICAL (decisive test of the "
          f"'*' denominator-exclusion contract)")

    # The other two buckets are untouched by the aborted additions.
    for fam in ("Sicilian Defense", "Scotch Game"):
        assert by_opening[fam] == result_clean["by_opening"][fam], (
            f"opening-results aborted: {fam} should be identical clean vs "
            f"dirty (no aborted game added to it), got "
            f"{by_opening[fam]} vs {result_clean['by_opening'][fam]}"
        )
    print(f"  [PASS] Sicilian and Scotch buckets identical clean vs dirty")


def main():
    print("opponent_style live test harness")
    print(f"NOW (unix) = {NOW}")
    four_year_weight = 2.71828 ** (-1.0 * 4)
    print(f"4-year-decay weight = {round(four_year_weight, 4)}  (expect ~0.018)")

    thin, pool_t = _run_fixture("thin", _fixture_thin())
    _print("A. THIN (2 games)", thin, pool_t)
    _assert_thin(thin)

    est, pool_e = _run_fixture("established", _fixture_established())
    _print("B. ESTABLISHED (12 games, balanced)", est, pool_e)
    _assert_established(est)

    shift, pool_s = _run_fixture("recent-shift", _fixture_recent_shift())
    _print("C. RECENT-SHIFT (6 old quiet + 6 recent sac)", shift, pool_s)
    _assert_recent_shift(shift)

    # Sanity fondle: also verify the undecayed (raw) rate by re-feeding
    # the same data with all end_times = 0 (neutral weight 1.0) and confirm
    # the weighted rate collapses toward the unweighted rate.
    raw_games = _fixture_recent_shift()
    for g in raw_games:
        g["end_time"] = 0
    raw_only, _ = _run_fixture("recent-shift (all neutral)", raw_games)
    _print("C-prime. SAME FIXTURE all-neutral (recency disarmed)", raw_only, _FakePool([]))
    print(f"  weighted sac freq all-neutral = {raw_only['sacrifice_frequency']}")
    print(f"  raw sac rate from previous run = {round(shift['sacrifice_events']/shift['opponent_moves'],4)}")
    assert abs(raw_only["sacrifice_frequency"] - shift["sacrifice_events"]/shift["opponent_moves"]) < 1e-3, (
        "neutral-weight fixture should match the raw unweighted rate"
    )
    print("  [PASS] recency-disarmed control matches raw unweighted rate.")

    # D. SIGNALS — engineeres 6 games for the three new v1 signals.
    print("\n=== D. SIGNALS (6 games; 3 K / 1 Q / 2 never; 2 queen trades; 4 q-stay-on) ===")
    sig, pool_d = _run_fixture("signals", _fixture_signals())
    _print("D. SIGNALS (6 engineered games, neutral weighting)", sig, pool_d)
    _assert_signals(sig)
    # D-prime: UNIFORM-AGE SANITY CHECK on the same fixture — when all
    # games are the same age (end_time=0, weight 1.0), the weighted
    # aggregates collapse to the unweighted aggregates. Confirms the
    # decay math doesn't distort results when there's nothing to distort.
    print("\n=== D-prime. UNIFORM-AGE SANITY CHECK (same fixture, all neutral) ===")
    _assert_uniform_age_sanity(sig)

    # E. UNPARSEABLE EXCLUSION — same 6 games + 2 deliberately malformed
    # PGN rows. Verifies the denominator-consistency fix: all five signals
    # must be identical between the clean 6-game fixture (D) and the dirty
    # 8-row fixture (E), because the 2 bad rows are excluded from every
    # signal's denominator.
    print("\n=== E. UNPARSEABLE EXCLUSION (6 good + 2 bad PGNs) ===")
    sig_dirty, pool_e = _run_fixture("signals-with-bad-pgns", _fixture_signals_with_bad_pgns())
    _print("E. SIGNALS + 2 BAD PGNs (8 rows, 6 parseable, neutral weighting)",
           sig_dirty, pool_e)
    _assert_unparseable_excluded(sig, sig_dirty)

    # F. TIME-CONTROL DISTRIBUTION — synthetic games across 3 time-control
    # buckets under neutral weighting, verifying both the percentage
    # breakdown and the most-common pick (the spec for the new
    # preferred/most-common time control signal).
    print("\n=== F. TIME-CONTROL DISTRIBUTION (6 games across 3 buckets, neutral) ===")
    tc_games = _fixture_time_controls()
    tc_result = style_mod.compute_time_control_distribution(tc_games)
    print(json.dumps(tc_result, indent=2, default=str))
    _assert_time_controls(tc_result)

    # G. TIME-CONTROL RECENCY TILT — same buckets, but the bucket with FEWER
    # raw games is RECENT and the bucket with MORE raw games is OLD. With
    # the recency decay applied (reuse of STYLE_RECENCY_DECAY_LAMBDA_PER
    # _YEAR), the recent bucket must win "most_common" despite losing on
    # raw count — proving this new function inherits the same decay the
    # other signals use, instead of being a raw-tally shortcut.
    print("\n=== G. TIME-CONTROL RECENCY TILT (recency overrides raw count) ===")
    tc_tilt_games = _fixture_time_controls_recency_tilt()
    tc_tilt_result = style_mod.compute_time_control_distribution(tc_tilt_games)
    print(json.dumps(tc_tilt_result, indent=2, default=str))
    _assert_time_control_recency_tilt(tc_tilt_games, tc_tilt_result)

    # H. TIME-CONTROL FLOOR — sub-MIN_STYLE_GAMES game set must report
    # sufficient=False and None for both the distribution and the most-
    # common pick, so the sparring page never prefills off a 2-game
    # opponent (the same fall-through contract compute_opponent_style uses).
    print("\n=== H. TIME-CONTROL BELOW FLOOR (2 games) ===")
    tc_thin_games = _fixture_time_controls()[:2]
    tc_thin_result = style_mod.compute_time_control_distribution(tc_thin_games)
    print(json.dumps(tc_thin_result, indent=2, default=str))
    _assert_time_control_floor(tc_thin_result)

    # I. OPENING RESULTS — 7 games across 3 openings, opponent playing both
    # colors, mix of wins/losses/draws. Verifies the win-rate math against
    # a closed-form expected value AND the NO-FLOOR contract (a 1-game
    # Scotch bucket with win_rate=0.0 must appear, not be filtered).
    print("\n=== I. OPENING RESULTS (7 games, 3 openings, both colors, W/L/D) ===")
    or_games = _fixture_opening_results()
    or_result = style_mod.compute_opening_results(or_games)
    print(json.dumps(or_result, indent=2, default=str))
    _assert_opening_results(or_result)

    # J. OPENING RESULTS WITH ABORTED GAMES — the 7-game fixture PLUS 2
    # deliberately-aborted games (Result="*"): one in a NEW single-game
    # bucket, one added to an EXISTING bucket. Verifies the "*" denominator-
    # exclusion contract: "*" contributes to weighted_count only, to NONE
    # of the W/L/D numerators, and is excluded from win_rate's denominator
    # (an all-"*" bucket has win_rate=None, not 0.0-as-signal).
    print("\n=== J. OPENING RESULTS WITH ABORTED GAMES (7 clean + 2 aborted) ===")
    or_dirty_games = _fixture_opening_results_with_aborted()
    or_dirty_result = style_mod.compute_opening_results(or_dirty_games)
    print(json.dumps(or_dirty_result, indent=2, default=str))
    _assert_opening_results_with_aborted(or_dirty_result, or_result)

    # K. STALE-ONLY SAC — the MIRROR of fixture C: the sacrifice pattern
    # appears ONLY in the old games. The recent games are all quiet (zero
    # sacs). Under recency weighting, the old pattern should still produce
    # a SOFT signal (weighted sac freq > 0) but be much weaker than the
    # unweighted rate (decay suppresses the old-only pattern).
    print("\n=== K. STALE-ONLY SAC (6 recent quiet + 6 old high-sac) ===")
    stale, pool_k = _run_fixture("stale-only-sac", _fixture_stale_only_sac())
    _print("K. STALE-ONLY SAC (mirror of C: pattern only in old games)", stale, pool_k)
    _assert_stale_only_soft_signal(stale)

    # L. TIME-CONTROL SAME-TC DOMINANCE — 4 rapid (sac) + 4 blitz (quiet) +
    # 4 bullet (quiet), all recent, under a RAPID sparring session. The
    # rapid games must dominate the aggregates: eff_sample = 7.2 (closed
    # form), the rapid bucket is the dominant per-bucket mass, and the sac
    # frequency is HIGHER than under no-TC weighting (the quiet blitz/bullet
    # games no longer dilute the sac rate).
    print("\n=== L. TC SAME-TC DOMINANCE (4 rapid sac + 4 blitz quiet + 4 bullet quiet; rapid sparring) ===")
    tc_dom_games = _fixture_tc_same_dominance()
    tc_dom, _ = _run_fixture("tc-same-dominance", tc_dom_games, sparring_tc="rapid")
    # No-TC control run of the SAME fixture (recency-only) for the sac-freq
    # comparison.
    tc_dom_no, _ = _run_fixture("tc-same-dominance (no-TC)", tc_dom_games)
    _print("L. TC SAME-TC DOMINANCE (rapid sparring)", tc_dom, _FakePool([]))
    _assert_tc_same_dominance(tc_dom, tc_dom_no)

    # M. TC CROSS-TC DOWN-WEIGHT — 10 blitz games under a rapid sparring
    # session. Each game's weight is 1.0 (recency) x 0.6 (blitz<->rapid) =
    # 0.6, so eff_sample = 6.0 (vs 10.0 recency-only). 10 games clears the
    # floor even under the 0.6 down-weighting (6.0 >= 5.0) so the sufficient
    # path + per-bucket mass are visible. Cross-TC games are down-weighted,
    # NOT purged.
    print("\n=== M. TC CROSS-TC DOWN-WEIGHT (10 blitz games, rapid sparring) ===")
    tc_cross_games = [
        _pgn(_QUIET_ITALIAN, opponent_plays_white=True, eco="C50",
             opening="Italian Game", end_time=0, time_class="blitz")
        for _ in range(10)
    ]
    tc_cross, _ = _run_fixture("tc-cross", tc_cross_games, sparring_tc="rapid")
    _print("M. TC CROSS-TC DOWN-WEIGHT (blitz under rapid)", tc_cross, _FakePool([]))
    _assert_tc_cross_down_weight(tc_cross)

    # N. TC UNKNOWN SPARRING FALLBACK — same 4 blitz games, NO sparring TC.
    # The TC factor collapses to 1.0 (recency-only), no crash, and
    # sparring_time_control_bucket is None.
    print("\n=== N. TC UNKNOWN SPARRING FALLBACK (no sparring TC supplied) ===")
    tc_unknown, _ = _run_fixture("tc-unknown", tc_cross_games)
    _print("N. TC UNKNOWN SPARRING (no TC)", tc_unknown, _FakePool([]))
    _assert_tc_unknown_fallback(tc_unknown)

    # O. COMBINED RECENCY x TC — 4 blitz games (2 recent + 2 one-year-old)
    # under rapid sparring. The TC run's eff_sample should be exactly 0.6 x
    # the recency-only run's eff_sample (the 0.6 TC factor scales uniformly),
    # proving the two factors compose multiplicatively.
    print("\n=== O. TC COMBINED RECENCY x TC (4 blitz: 2 recent + 2 old; rapid sparring) ===")
    one_year_ago = int((time.time()) - 365.25 * 86400)
    tc_combined_games = []
    for _ in range(2):
        tc_combined_games.append(_pgn(_QUIET_ITALIAN, opponent_plays_white=True,
                                     eco="C50", opening="Italian Game",
                                     end_time=0, time_class="blitz"))
    for _ in range(2):
        tc_combined_games.append(_pgn(_QUIET_ITALIAN, opponent_plays_white=True,
                                     eco="C50", opening="Italian Game",
                                     end_time=one_year_ago, time_class="blitz"))
    tc_combined, _ = _run_fixture("tc-combined", tc_combined_games, sparring_tc="rapid")
    tc_combined_no, _ = _run_fixture("tc-combined (no-TC)", tc_combined_games)
    _print("O. TC COMBINED RECENCY x TC (blitz, mixed age, rapid sparring)",
           tc_combined, _FakePool([]))
    _assert_tc_combined_product(tc_combined, tc_combined_no)

    # P. UNIFORM-TC SANITY — 6 rapid games, all recent, under rapid
    # sparring. Since every game matches the sparring bucket, the TC factor
    # is 1.0 for all, so eff_sample must EQUAL the recency-only eff_sample
    # (TC introduces no distortion when there's nothing to down-weight).
    print("\n=== P. UNIFORM-TC SANITY (6 rapid games, rapid sparring) ===")
    tc_uni_games = [
        _pgn(_QUIET_ITALIAN, opponent_plays_white=True, eco="C50",
             opening="Italian Game", end_time=0, time_class="rapid")
        for _ in range(6)
    ]
    tc_uni, _ = _run_fixture("tc-uniform", tc_uni_games, sparring_tc="rapid")
    tc_uni_no, _ = _run_fixture("tc-uniform (no-TC)", tc_uni_games)
    _print("P. UNIFORM-TC SANITY (all rapid, rapid sparring)", tc_uni, _FakePool([]))
    _assert_tc_uniform_sanity(tc_uni, tc_uni_no)

    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()