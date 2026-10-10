"""Stage 1 screen: the Review per-ply path over one game, read-only.

Runs ``GameAnalyzer`` (deterministic, MultiPV=2) over every ply exactly
like ``iter_full_game`` (previous-eval reuse, contiguous book state), then
labels each *user* ply with an exclusion reason or None (included).

Exclusion precedence (first match wins, counted per ply):
  variant -> time_class -> book/book_fallback -> clock.
Non-user plies are counted separately, never recorded.

Book fallback: when no opening-book lookup is available (``book_lookup``
None), user plies with ``move_number <= min_move`` are excluded as
``book_fallback`` instead.

Clock convention: a PGN ``[%clk]`` comment is attached to the move node and
records time remaining when that move was made, so the ``[%clk]`` value on
the user's own move node is the user's remaining time AFTER the move. It is
the best available proxy for "user was in time trouble on this ply". The
exclusion threshold is relative to the time control parsed from the
``[TimeControl]`` header: ``remaining < max(floor_s, frac * base_s)``
(defaults ``frac=0.10``, ``floor_s=10``). When the base time is unknown the
legacy fixed ``fallback_s=30`` applies (documented in the report).
"""

from __future__ import annotations

import re
from io import StringIO
from typing import Any, Callable, Dict, List, Optional

import chess
import chess.pgn

# PGN clock comment e.g. {[%clk 0:03:00]} / {[%clk 0:00:29.5]}.
# Lichess and Chess.com both emit H:MM:SS (tenths optional); be liberal and
# also accept MM:SS.
_CLK_RE = re.compile(r"\[%clk\s+([0-9:.]+)\]")


def parse_clk_seconds(comment: str) -> Optional[float]:
    """Parse ``[%clk]`` seconds from a PGN node comment, None when absent."""
    m = _CLK_RE.search(comment or "")
    if not m:
        return None
    parts = m.group(1).split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        h, mi, s = nums
        return h * 3600.0 + mi * 60.0 + s
    if len(nums) == 2:
        mi, s = nums
        return mi * 60.0 + s
    if len(nums) == 1:
        return nums[0]
    return None


def position_key_4(fen: str) -> str:
    """First four FEN fields (board, turn, castling, en passant)."""
    return " ".join((fen or "").split()[:4])


# TimeControl header e.g. "300+3" (Lichess/Chess.com) -> base 300s.
# Anything else ("?", "-", FIDE "40/7200:3600", missing) -> unknown (None).
_TC_RE = re.compile(r"^\s*(\d+)(?:\+\d+)?\s*$")


def parse_base_time(time_control: str) -> Optional[float]:
    """Base time in seconds from a ``[TimeControl]`` header, None if unknown."""
    m = _TC_RE.match(time_control or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


# Clock-exclusion placeholders: remaining < max(floor, frac * base).
CLOCK_FRAC = 0.10
CLOCK_FLOOR_S = 10.0
CLOCK_FALLBACK_S = 30.0


def clock_threshold_s(base_time_s: Optional[float]) -> float:
    """Exclusion threshold for remaining clock seconds."""
    if base_time_s is None:
        return CLOCK_FALLBACK_S
    return max(CLOCK_FLOOR_S, CLOCK_FRAC * base_time_s)


# Normalized bullet/hyper speeds. Lichess `speed` values are camelCase
# (ultraBullet/bullet/blitz/rapid/classical/correspondence); Chess.com
# `time_class` values are lowercase (bullet/blitz/rapid/daily).
BULLET_TIME_CLASSES = frozenset(
    {"bullet", "ultrabullet", "ultra", "hyper", "hyperbullet"}
)


def is_bullet_time_class(time_class: str) -> bool:
    return (time_class or "").strip().lower() in BULLET_TIME_CLASSES


def screen_game(
    pgn: str,
    *,
    user_color: str,
    user_rating: Optional[int],
    time_class: str,
    analyzer,
    book_lookup: Optional[Callable[[Any, Any], bool]],
    include_extras: bool = True,
    min_move: int = 8,
) -> Dict[str, Any]:
    """Screen one game. Returns a dict with headers, rows, and counters.

    rows: one dict per USER ply (both included and excluded). Each row has
    the Review fields (fen_before/after, played/best/second + PVs, eval,
    ep_loss, cp_loss, classification, mode later attached by caller) plus
    ``exclusion`` (None when included), ``clk_seconds`` (remaining),
    ``base_time_s`` (parsed TimeControl base, None if unknown),
    ``clk_present`` and ``position_key``.

    When ``book_lookup`` is None the opening book is unavailable and user
    plies with ``move_number <= min_move`` are excluded as ``book_fallback``.
    """
    game = chess.pgn.read_game(StringIO(pgn))
    if game is None:
        raise ValueError("Invalid PGN: could not parse game")
    headers = dict(game.headers)
    variant = (headers.get("Variant") or "Standard").strip() or "Standard"
    base_time_s = parse_base_time(headers.get("TimeControl") or "")
    ratings = analyzer._ratings_from_headers(game)
    # Prefer the PGN Elo header for the user's rating; the caller-supplied
    # summary rating is only a fallback when the header is absent.
    _elo_header = "WhiteElo" if user_color == "white" else "BlackElo"
    try:
        _header_rating = int((headers.get(_elo_header) or "").strip())
    except (TypeError, ValueError):
        _header_rating = None

    board = game.board()
    rows: List[Dict[str, Any]] = []
    n_plies = 0
    n_user_plies = 0
    n_non_user = 0
    clk_plies = 0
    exclusion_counts: Dict[str, int] = {}

    previous_eval = None
    previous_ep_loss = None
    in_book = True

    for node in game.mainline():
        move = node.move
        n_plies += 1
        move_color = "white" if board.turn == chess.WHITE else "black"
        player_rating = ratings.get(move_color)
        # Prefer the caller-supplied user rating for user plies (PGN header
        # for the user's color, else the summary fallback); otherwise the
        # analyzer's per-color header rating.
        if move_color == user_color:
            rating_for_ply = _header_rating or user_rating or player_rating
        else:
            rating_for_ply = player_rating

        if in_book and book_lookup is not None:
            try:
                is_book_move = bool(book_lookup(board, move))
            except Exception:  # noqa: BLE001 -- book is best-effort, read-only
                is_book_move = False
        else:
            is_book_move = False
        if not is_book_move:
            in_book = False

        eval_before = (
            previous_eval if previous_eval is not None else analyzer._evaluate(board)
        )
        opponent_prev_ep_loss = previous_ep_loss
        turn_entry, eval_after, raw_ep_loss = analyzer.analyze_ply(
            board,
            move,
            eval_before,
            is_book_move=is_book_move,
            player_rating=rating_for_ply,
            opponent_prev_ep_loss=opponent_prev_ep_loss,
            include_extras=include_extras,
        )
        # NOTE: analyze_ply pushes `move` onto `board` itself; do NOT push.
        previous_eval = eval_after
        previous_ep_loss = raw_ep_loss

        if move_color != user_color:
            n_non_user += 1
            continue
        n_user_plies += 1

        comment = node.comment or ""
        clk_seconds = parse_clk_seconds(comment)
        if clk_seconds is not None:
            clk_plies += 1

        # Exclusion precedence: variant -> time_class -> book/book_fallback
        # -> clock (relative threshold, 30s fallback when base unknown).
        exclusion: Optional[str] = None
        if variant != "Standard":
            exclusion = "variant"
        elif is_bullet_time_class(time_class):
            exclusion = "time_class"
        elif book_lookup is None:
            if (turn_entry.get("move_number") or 0) <= min_move:
                exclusion = "book_fallback"
        elif is_book_move:
            exclusion = "book"
        if exclusion is None and clk_seconds is not None and (
            clk_seconds < clock_threshold_s(base_time_s)
        ):
            exclusion = "clock"
        if exclusion is not None:
            exclusion_counts[exclusion] = exclusion_counts.get(exclusion, 0) + 1

        rows.append(
            {
                "ply_index": n_plies - 1,
                "move_number": turn_entry.get("move_number"),
                "color": move_color,
                "fen_before": turn_entry.get("fen_before"),
                "fen_after": turn_entry.get("fen"),
                "played_san": turn_entry.get("san"),
                "played_uci": move.uci(),
                "best_san": turn_entry.get("best_move_san"),
                "best_uci": turn_entry.get("best_move_uci"),
                # Screen-PV for context only (v1 grades single moves, never
                # the PV). Comes from the pre-move evaluation, not the row.
                "best_pv_uci": list(eval_before.principal_variation_uci or []),
                "second_san": turn_entry.get("second_best_move_san"),
                "second_uci": turn_entry.get("second_best_move_uci"),
                "second_pv_uci": turn_entry.get("second_best_pv_uci") or [],
                "eval_cp_mover": _mover_cp(eval_before, eval_after, move_color),
                "eval_mate": turn_entry.get("eval_mate"),
                "ep_loss": turn_entry.get("ep_loss", 0.0),
                "cp_loss": turn_entry.get("cp_loss", 0),
                "ep_best": turn_entry.get("ep_best", 0.0),
                "classification": turn_entry.get("classification"),
                "player_rating": rating_for_ply,
                "is_book": is_book_move,
                "exclusion": exclusion,
                "clk_seconds": clk_seconds,
                "base_time_s": base_time_s,
                "clk_present": clk_seconds is not None,
                "position_key": position_key_4(turn_entry.get("fen_before") or ""),
            }
        )

    return {
        "headers": headers,
        "variant": variant,
        "rows": rows,
        "n_plies": n_plies,
        "n_user_plies": n_user_plies,
        "n_non_user": n_non_user,
        "clk_plies": clk_plies,
        "exclusion_counts": exclusion_counts,
    }


def _mover_cp(eval_before, eval_after, move_color: str) -> float:
    """Post-move eval from the mover's POV (informational)."""
    try:
        after = float(eval_after.score_cp)
    except (TypeError, ValueError):
        return 0.0
    return -after
