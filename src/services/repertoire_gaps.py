"""Gap-finding for the Repertoire trainer.

For a given repertoire, find positions where the opponent has common
replies the user hasn't PICKED A RESPONSE to.

The "gap" semantics are deliberately narrow and worth flagging
upfront so downstream code doesn't quietly misread them:

  * "Gap" means the user has no `repertoire_positions` row at the FEN
    reached after the opponent's reply — i.e. they have not chosen a
    move to play from that position. It is NOT a claim about whether
    the user has ever PLAYED a game reaching that position. The
    coverage check (`resulting_fen in covered_fens`) is built only
    from rows the user has deliberately authored, so a gap
    specifically marks "no prepared response," not "unseen position."

  * A user-turn row at `resulting_fen` counts as coverage regardless of
    the move quality stored there — the gap-finder does not assess
    whether the user's prepared move is good, only whether one exists.
    A separate "your prepared response is statistically weak" check
    would be a different feature with a different output schema.

Algorithm (see `find_repertoire_gaps`):

  1. Load every (id, fen, move) row in `repertoire_positions` for the
     given repertoire_id.
  2. For each row, push its stored `move` (UCI, via python-chess) onto
     a board constructed from its 4-field normalized `fen`. The
     resulting position has the opponent to move.
  3. Query the Lichess Opening Explorer API
     (https://explorer.lichess.ovh/lichess?fen=<URL-encoded FEN>) for
     that position and read the opponent's most common replies.
  4. For every reply whose frequency ≥ MIN_FREQUENCY_PERCENT, push it
     on the board, take the resulting FEN, normalize it to 4 fields
     (via the SAME `_normalize_fen` used at write time — imported from
     services/repertoire_service.py, not reimplemented), and look it up
     in the set of fens already covered by this repertoire.
  5. If the normalized resulting FEN is NOT in the coverage set, emit a
     `RepertoireGap` row with the parent position's id/fen, the
     opponent move's UCI+SAN, frequency %, and the resulting fen.

Frequency math denominator: every per-move percentage divides the
move's game count by the position's AUTHORITATIVE total — Explorer's
top-level `white+draws+black` field — NOT the sum of `moves` array
entries. Explorer caps the `moves` list on busy opening positions, so
summing only the returned moves would undercount the position's real
total and silently inflate each returned move's frequency percentage
relative to its true rate. A 3%-of-all-games reply could read as 6%
if half the position's games went into moves Explorer didn't bother
returning. `_resolve_total_games` prefers the top-level totals and
falls back to the moves-list sum only when the top-level totals are
absent or malformed (shape-drift robustness preserved).

Failure handling (the spec's 'do not let these fail silently' rule):

  * Lichess Explorer unreachable/non-2xx/non-JSON for ONE position -> log
    at WARNING, append an `UncheckedPosition` row with the upstream
    reason, continue with the rest. The report's `unchecked_positions`
    field surfaces these to the client.

  * A stored (fen, move) that python-chess rejects on the push (the
    move is illegal on its own fen — should never happen since
    `upsert_repertoire_positions` validates moves when writing, but
    defense-in-depth) -> log, mark unchecked with the python-chess
    error, continue with the rest.

  * Empty repertoire (no rows) -> return an empty
    `RepertoireGapReport(gaps=[], unchecked_positions=[])` (no error).

  * Explorer returned no moves above the threshold or zero games for
    this position -> not a failure (the API succeeded cleanly); that
    position just contributes zero gaps.

The HTTP client matches the project's existing pattern in
`integrations/lichess.py` and `integrations/chess_com.py`:
  * stdlib `urllib.request` + `User-Agent` header (same UA string).
  * No `requests` / `httpx` dependency is added; `requirements.txt` is
    untouched.
  * A typed exception (`LichessExplorerError`) is raised for any
    upstream failure, mirroring `LichessError` / `ChessComError`. The
    gap-finder catches it per-position so one dead Explorer call
    doesn't blank the whole report.

The full FEN with all six fields (board, side, castling, ep, halfmove
clock, fullmove number) is what we send to Explorer — python-chess's
`board.fen()` produces it. The halfmove clock matters for the upstream
lookup. The 4-field normalization is used only for the COVERAGE check
against stored rows, since stored fens are normalized.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Set

import chess
from psycopg2.extras import RealDictCursor

from schemas.repertoire_schemas import (
    RepertoireGap,
    RepertoireGapReport,
    UncheckedPosition,
)
from services.repertoire_service import _normalize_fen

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lichess Opening Explorer HTTP client.
#
# Mirrors `integrations/lichess.py`'s pattern verbatim (same module-level
# URL/UA/timeout constants, same typed Exception subclass, same try/except
# shape for HTTPError and URLError). No new dependency is added — this
# uses only Python's stdlib `urllib.request`.
# ---------------------------------------------------------------------------

EXPLORER_URL = "https://explorer.lichess.ovh/lichess"

# Same User-Agent string as `integrations/lichess.py` /
# `integrations/chess_com.py` — keeps the project's API-citizenship
# convention uniform across Lichess touches.
USER_AGENT = "PraxisMove/1.0 (contact: praxis.app.dev@gmail.com)"

# Same 30s ceiling as `integrations/lichess.py` for any Lichess-family
# call. Explorer can be slow on hot positions; 30s is the established
# precedent.
REQUEST_TIMEOUT_SECONDS = 30


class LichessExplorerError(Exception):
    """Raised when the Lichess Opening Explorer API fails for any reason
    (non-2xx HTTP, network error, non-JSON body). Caught per-position by
    `find_repertoire_gaps` so one dead call can't blank the report.
    """


def _http_get_json(url: str) -> Dict[str, Any]:
    """Perform a GET request and decode the JSON body.

    Mirrors the `_http_get_json` helper in `integrations/chess_com.py`
    but raises a single typed `LichessExplorerError` covering both
    HTTPError and URLError — we have no use case for distinguishing
    404 vs 429 vs 500 on the explorer endpoint (every failure maps to
    "skip this position's gap analysis"). The chess_com helper DOES
    split out the 404 case because it has a distinct user-facing
    semantic ("username not found"); the explorer FEN lookup never has
    an analogous "fen not found" outcome worth surfacing separately.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise LichessExplorerError(
            f"Lichess Explorer request failed (HTTP {exc.code} {exc.reason})"
        ) from exc
    except urllib.error.URLError as exc:
        raise LichessExplorerError(
            f"Unable to reach Lichess Explorer: {exc.reason}"
        ) from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LichessExplorerError(
            f"Lichess Explorer returned a non-JSON body: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise LichessExplorerError(
            f"Lichess Explorer response was not a JSON object (got {type(data).__name__})"
        )
    return data


def _fetch_opponent_moves(post_user_move_fen: str) -> List[Dict[str, Any]]:
    """Hit the Lichess Opening Explorer for the position reached AFTER
    the user's stored move (so it's the opponent's turn), and return the
    raw `moves` array from the response.

    The explorer returns JSON of shape:
      {
        "white": <int>, "draws": <int>, "black": <int>,
        "moves": [
          {"uci": "e7e5", "san": "e5", "white": .., "draws": .., "black": .., ...},
          ...
        ],
        "opening": {...}, "topGames": [...]
      }

    We only consume `moves`; callers handle the per-move {white, draws,
    black} tuple as game counts. The list is normally sorted by
    Explorer descending-by-frequency, but the gap-finder does not
    depend on that order — it re-ranks via the per-move game count
    inside the threshold comparison.

    NOTE: this returns ONLY the moves array. The position's authoritative
    game total (data.white + data.draws + data.black) is fetched
    separately by `_fetch_explorer_position(...)`, which is the version
    the gap-finder actually uses; `total_games` MUST come from those
    top-level fields and NOT from summing the moves this function
    returns, because Explorer's moves list can be TRUNCATED on busy
    opening positions (Explorer ships a top-N slice, not every legal
    reply). Summing only the returned moves would undercount the
    position's real total and inflate each returned move's frequency
    percentage relative to its true rate. See
    `_fetch_explorer_position` for the truncation-safe denominator.
    """
    url = EXPLORER_URL + "?fen=" + urllib.parse.quote(post_user_move_fen)
    data = _http_get_json(url)
    moves = data.get("moves", [])
    if not isinstance(moves, list):
        return []
    return moves


def _resolve_total_games(
    explorer_data: Dict[str, Any],
    moves_list_sum: int,
) -> int:
    """Pick the most accurate denominator for a position's frequency
    math.

    Prefers the response's top-level `white + draws + black` totals over
    the moves-list sum. The top-level totals are Explorer's
    authoritative count of games played at the queried position; the
    moves-list sum, by contrast, can be SHORT because Explorer caps the
    `moves` array on busy opening positions (it returns a top-N slice,
    not every legal reply). Using the truncated sum as the denominator
    would silently inflate every returned move's percentage relative to
    its true rate — a 3%-of-all-games reply could read as 6% if half
    the position's games went into moves Explorer didn't bother
    returning. The top-level totals are strictly safer in the
    truncation case and equal to the moves sum in the no-truncation
    case, so they dominate as the denominator choice regardless of
    whether truncation actually occurs on a given position.

    Falls back to `moves_list_sum` only when the top-level totals are
    absent or non-integer-typed (preserves the original shape-drift
    robustness concern — a mis-formed response from Explorer still
    yields a usable denominator so long as the per-move counts make
    sense). If neither source yields a positive total, returns 0; the
    gap-finder's threshold check (`percent < MIN_FREQUENCY_PERCENT`)
    naturally filters everything out at 0, which is the desired
    behavior for a position that Explorer has no data on.
    """
    white = explorer_data.get("white")
    draws = explorer_data.get("draws")
    black = explorer_data.get("black")
    if (
        isinstance(white, int) and isinstance(draws, int) and isinstance(black, int)
        and white + draws + black > 0
    ):
        return white + draws + black
    # Shape drift on the top-level totals (missing keys, stringy
    # numbers, all-zero). Fall back to the moves-list sum if it's
    # positive — a degraded denominator is better than none.
    return moves_list_sum if moves_list_sum > 0 else 0


def _fetch_explorer_position(post_user_move_fen: str) -> "tuple[List[Dict[str, Any]], int]":
    """Hit Lichess Explorer for the position reached AFTER the user's
    stored move, and return `(moves, total_games_at_position)`.

    `moves` is the raw `moves` array per `_fetch_opponent_moves`.

    `total_games_at_position` is the truncation-safe author position
    total returned by `_resolve_total_games` — top-level
    `white+draws+black` if present and valid, else the moves-list sum
    as a shape-drift fallback. The gap-finder uses this as the
    denominator for every per-move frequency computation.

    Raises `LichessExplorerError` on any HTTP / network / JSON failure;
    the gap-finder catches it per-position so one dead call can't
    blank the whole report.
    """
    url = EXPLORER_URL + "?fen=" + urllib.parse.quote(post_user_move_fen)
    data = _http_get_json(url)
    moves = data.get("moves", [])
    if not isinstance(moves, list):
        moves = []
    moves_list_sum = 0
    for m in moves:
        if isinstance(m, dict):
            moves_list_sum += _game_count(m)
    total_games = _resolve_total_games(data, moves_list_sum)
    return moves, total_games


# ---------------------------------------------------------------------------
# Gap finding.
# ---------------------------------------------------------------------------

# Frequency threshold for an opponent reply to count as a "common" gap.
# The spec asked for 5 % of games at the post-user-move position; named
# (not magic) so the threshold is discoverable and tunable in one place.
# Computed as:
#     percent = (move.white + move.draws + move.black)
#               / total_games_at_position * 100
# where `total_games_at_position` is Explorer's TOP-LEVEL
# (white+draws+black), NOT the sum of the returned `moves` array.
# Explorer caps the moves list on busy opening positions (returns a
# top-N slice), so the moves-list sum would undercount the position's
# real total and inflate each returned move's percentage relative to
# its true rate. See `_resolve_total_games` for the fallback to the
# moves-list sum that handles Explorer responses missing the
# top-level totals.
#
# Boundary semantics: replies at exactly 5.0% are INCLUDED (the
# comparison is `percent < MIN_FREQUENCY_PERCENT` for exclusion, so
# the threshold is inclusive at the floor).
MIN_FREQUENCY_PERCENT = 5.0


def _game_count(entry: Dict[str, Any]) -> int:
    """Sum an Explorer move (or position-level) entry's white/draws/black
    counts into a single integer. Returns 0 on any shape drift — a
    defensive default chosen because a 0-count reply is naturally
    filtered out by the threshold check (>= 5% of a 0-total position
    is never satisfied).
    """
    white = entry.get("white", 0) or 0
    draws = entry.get("draws", 0) or 0
    black = entry.get("black", 0) or 0
    try:
        return int(white) + int(draws) + int(black)
    except (TypeError, ValueError):
        return 0


def find_repertoire_gaps(
    conn,
    *,
    repertoire_id,
    owner_color: str,
) -> RepertoireGapReport:
    """Compute the gap report for a single repertoire.

    Read-only relative to the DB: LOADS the repertoire's position rows,
    queries Lichess Explorer, and produces `RepertoireGap` /
    `UncheckedPosition` rows. Writes nothing.

    The caller (the router) is responsible for ownership verification
    (404/403 via `_load_owned_repertoire`) BEFORE invoking this, so the
    `repertoire_id` is already known to belong to the authenticated
    user.

    `owner_color` is the repertoire's color ('white' or 'black'). The
    outer loop iterates ONLY owner-side rows: a gap is "the opponent
    has a common reply I haven't prepared a response to," which is a
    per-owner-row computation. If opponent-side rows (the new
    persistence model saves both sides) were iterated, the gap-finder
    would push an opponent-row's move onto an opponent FEN, get an
    owner-turn FEN, query Explorer for OWNER-side moves there, and
    emit spurious "the owner's moves are unprepared opponent replies"
    gaps. Filtering to owner rows by `split_part(fen, ' ', 2)` keeps
    the gap semantics the spec describes.

    Args:
        conn: open psycopg2 conn. No commit is issued by this function.
        repertoire_id: id of a `repertoires` row (already verified by
            the caller for ownership).
        owner_color: 'white' or 'black' — the repertoire's color, used
            to filter the outer loop to owner-side rows only.

    Returns:
        A `RepertoireGapReport` with two lists:

          * `gaps`: opponent replies the user has no prepared
            response to, in alphabetical-stable storage order (parent
            position first, then explorer rank within each position).
          * `unchecked_positions`: positions the gap-finder skipped
            (Explorer failure or stale/corrupt stored move), each
            carrying a human-readable reason.

        Empty repertoire -> empty lists (no error).

    Raises:
        Nothing intentionally. Any unexpected exception escapes to the
        router's `get_db()` rollback, which is fine — the report is
        read-only so a rollback drops any unread state harmlessly.
    """
    rid = str(repertoire_id)
    # The kolor letter as it appears in the FEN's side-to-move field.
    owner_letter = "w" if owner_color == "white" else "b"

    # Load only OWNER-side rows of this repertoire. Storage order (PK
    # ascending) is the deterministic output order — no FSRS / due-time
    # ordering is meaningful for a gap report which is about coverage,
    # not scheduling. (We `ORDER BY id ASC` because `created_at` ties
    # between rows inserted in the same upsert call.) The
    # `split_part(fen, ' ', 2) = %s` clause keeps opponent-side rows
    # out — these exist now that the writer saves both sides, but
    # iterating them in the outer loop would emit spurious gaps (see
    # the docstring's owner_color rationale).
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, fen, move
            FROM repertoire_positions
            WHERE repertoire_id = %s
              AND split_part(fen, ' ', 2) = %s
            ORDER BY id ASC
            """,
            (rid, owner_letter),
        )
        rows = cur.fetchall()

    if not rows:
        # Empty repertoire: spec says return an empty gap list, NOT an
        # error. We still return the report envelope (with both lists
        # empty) so the client receives a stable shape regardless of
        # the storage size.
        return RepertoireGapReport(gaps=[], unchecked_positions=[])

    # Pre-built set of normalized fens the repertoire already covers.
    # Membership test is O(1) per candidate reply; the set is built once
    # from the snapshot we already loaded. The stored fens are already
    # 4-field normalized by `upsert_repertoire_positions`, so no
    # re-normalization is needed at load time.
    #
    # NOTE: covered_fens here is BUILT FROM OWNER ROWS ONLY (filtered
    # above). An opponent reply's "coverage" means "the user has a
    # stored owner-row at the resulting position after this reply,"
    # which is exactly the owner-rows-only check we want. Opponent
    # rows themselves live at opp-to-move FENs, which never equal an
    # owner-to-move resulting FEN, so they wouldn't show up as covered
    # even if we did load them here — but loading only owner rows
    # ALSO keeps the set small and the semantics crisp.
    covered_fens: Set[str] = {r["fen"] for r in rows}

    gaps: List[RepertoireGap] = []
    unchecked: List[UncheckedPosition] = []

    for row in rows:
        pos_id = row["id"]
        pos_fen = row["fen"]
        pos_move_uci = row["move"]

        # Push the user's stored move to reach the opponent-to-move
        # position. `chess.Board` accepts the 4-field normalized fen we
        # stored — python-chess auto-fills the missing halfmove/fullmove
        # fields with defaults (0 and 1 respectively). `parse_uci`
        # re-validates legality and raises ValueError on a stale row,
        # which we treat as 'unchecked' rather than crashing the
        # whole report (defense-in-depth against corrupt storage).
        try:
            board = chess.Board(pos_fen)
            user_move = board.parse_uci(pos_move_uci)
            board.push(user_move)
        except ValueError as exc:
            log.warning(
                "gap-finder: skipping illegitimate position %s "
                "in repertoire %s (move=%r fen=%r): %s",
                pos_id, rid, pos_move_uci, pos_fen, exc,
            )
            unchecked.append(UncheckedPosition(
                position_id=str(pos_id),
                fen=pos_fen,
                reason=f"illegal stored move: {exc}",
            ))
            continue

        post_user_move_fen = board.fen()  # full 6-field FEN, opp to move

        # Query Lichess Explorer for this position's opponent replies
        # AND its authoritative position total (top-level white+draws+
        # black, NOT the moves-list sum — Explorer caps the moves list
        # on busy opening positions, so the sum undercounts).
        # Any failure (HTTP, network, JSON) is converted to an
        # UncheckedPosition entry and the gap-finder moves on to the
        # next stored row — one dead call never blanks the report.
        try:
            explorer_moves, total_games = _fetch_explorer_position(post_user_move_fen)
        except LichessExplorerError as exc:
            log.warning(
                "gap-finder: Lichess Explorer failed for position %s "
                "(post-move fen=%r): %s",
                pos_id, post_user_move_fen, exc,
            )
            unchecked.append(UncheckedPosition(
                position_id=str(pos_id),
                fen=pos_fen,
                # Report the PRE-move fen (the stored one) so the
                # client can locate which row in the repertoire was
                # skipped — the post-move intermediate fen is internal
                # to gap analysis and isn't a row the user can act on.
                reason=f"Lichess Explorer request failed: {exc}",
            ))
            continue

        for m in explorer_moves:
            if not isinstance(m, dict):
                # Explorer shape drift — skip silently; this is an
                # upstream quirk, not a user-affecting gap.
                continue

            uci = m.get("uci")
            san = m.get("san")
            if not isinstance(uci, str) or not isinstance(san, str):
                # A reply missing its uci or san can't be replayed or
                # reported; skip silently.
                continue

            if total_games <= 0:
                # No Lichess coverage for this position (returned 0
                # moves, or all moves had 0 games). Not a failure mode —
                # the position just contributes no gaps.
                continue

            move_games = _game_count(m)
            percent = (move_games / total_games) * 100.0
            if percent < MIN_FREQUENCY_PERCENT:
                continue

            # Apply the opponent's reply to compute the FEN the user
            # would need a response for. python-chess's push() expects
            # pseudo-legal; we use parse_uci (validates legality) so a
            # mis-shaped move from Explorer can't raise a bare
            # AssertionError on push — same defensive pattern used by
            # `services/repertoire_service._replay_and_plan`.
            try:
                reply_board = chess.Board(post_user_move_fen)
                reply_move = reply_board.parse_uci(uci)
                reply_board.push(reply_move)
            except ValueError as exc:
                # Explorer returned a move that's illegal on this
                # position — should not happen, but the gap-finder
                # must not choke on upstream quirk; skip silently
                # with a warning (not unchecked — this is per-reply,
                # not per-position).
                log.warning(
                    "gap-finder: Explorer returned illegal reply %s "
                    "on post-move fen %r: %s",
                    uci, post_user_move_fen, exc,
                )
                continue

            resulting_fen = _normalize_fen(reply_board.fen())
            # Coverage check: if this normalized fen is already a row
            # the user has prepared, the gap-finder does NOT emit a
            # gap. `covered_fens` was built from the same snapshot
            # loaded earlier — fast and duplicate-free.
            if resulting_fen in covered_fens:
                continue

            gaps.append(RepertoireGap(
                parent_position_id=str(pos_id),
                parent_fen=pos_fen,
                opponent_move_uci=uci,
                opponent_move_san=san,
                frequency_percent=percent,
                resulting_fen=resulting_fen,
            ))

    return RepertoireGapReport(gaps=gaps, unchecked_positions=unchecked)