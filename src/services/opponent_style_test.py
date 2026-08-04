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
         site="https://example.test"):
    game = chess.pgn.Game()
    game.headers["White"] = OPP_NAME if opponent_plays_white else OTHER_NAME
    game.headers["Black"] = OTHER_NAME if opponent_plays_white else OPP_NAME
    game.headers["Event"] = "TestGame"
    game.headers["Site"] = site
    game.headers["Date"] = "2024.01.01"
    game.headers["Round"] = "-"
    game.headers["Result"] = "*"
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
    return {"pgn": pgn_text, "end_time": end_time}


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
    """2 games — below MIN_STYLE_GAMES=3."""
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
    # 6 old quiet games — decayed to ~0.135 weight each.
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


# ---------------------------------------------------------------------------
# Test runner.
# ---------------------------------------------------------------------------
def _run_fixture(label, games):
    fake_pool = _FakePool(games)
    database.connection_pool = fake_pool
    try:
        result = style_mod.compute_opponent_style(
            requested_by_user_id="user_test_001",
            provider="lichess",
            opponent_username=OPP_NAME,
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
    lean = result["opening_family_lean"]
    print(f"  family_lean = {lean}")
    sicilian_share = lean.get("Sicilian Defense", 0.0)
    assert sicilian_share > 0.30, (
        f"shift: expected Sicilian Defense dominant; got {sicilian_share}"
    )
    print(f"  [PASS] Sicilian Defense leans dominant: share={sicilian_share}")


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
        f"signals: 6 games is above MIN_STYLE_GAMES=3; "
        f"got sufficient={result['sufficient']}"
    )
    assert result["game_count"] == 6, (
        f"signals: expected 6 games, got {result['game_count']}"
    )

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


def main():
    print("opponent_style live test harness")
    print(f"NOW (unix) = {NOW}")
    four_year_weight = 2.71828 ** (-0.5 * 4)
    print(f"4-year-decay weight = {round(four_year_weight, 4)}  (expect ~0.135)")

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

    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()