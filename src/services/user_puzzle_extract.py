"""Personal puzzle extraction from the user's own games (background job).

Pipeline (valided by scripts/puzzle_extract_pilot/ against SF19):
  fetch (read-only integrations; Lichess with clocks=true) ->
  persist user_games -> Review per-ply screen on a PRIVATE deterministic
  engine (MultiPV=2 @150k nodes) -> exclusions -> candidates
  (ep>=0.15, cp>=100, no label gate) -> dedupe + top-3/game cap ->
  Stage-2 verification on a nodes-only fresh-token engine
  (MultiPV=4 @500k, Threads=1 Hash=16) + EP-margin uniqueness (>=0.10) ->
  user_puzzles + user_puzzle_entries.

Runs in a FastAPI BackgroundTasks thread like the opponent import worker:
own pool connection, per-game commits, heartbeat counters on the job row,
private engines (never the Review singleton). Single-move puzzles only.
"""

import logging
import os
import re
from io import StringIO
from typing import Any, Dict, List, Optional

import chess
import chess.engine
import chess.pgn
from psycopg2.extras import Json, RealDictCursor

from core import database
from core.game_analyzer import GameAnalyzer, expected_points
from engines.stockfish_engine import StockfishEngine
from llms.mock_explainer import MockExplainer

log = logging.getLogger(__name__)

# Pilot placeholders (no labeling data yet; see the skipped-labeling note).
# Screen budget follows the pilot's Stage-1 precedent (MultiPV=2 @50k):
# screening only generates candidates, Stage-2 verifies. Margin follows the
# pilot's margin-yield grids (0.05 keeps ~2-3x the candidates of 0.10 on
# real-game mistakes); the bad-puzzle button is the quality backstop.
SCREEN_NODES = 50_000
STAGE2_NODES = 500_000
CAND_EP = 0.15
CAND_CP = 100
MARGIN_M = 0.05
PER_GAME_CAP = 3
MIN_MOVE = 8
CLOCK_FRAC = 0.10
CLOCK_FLOOR_S = 10.0
CLOCK_FALLBACK_S = 30.0
# A job whose heartbeat is older than this is presumed to have lost its
# worker (dev-server restart kills BackgroundTasks silently) and is
# reclaimable: marked failed so the queue never shows a stuck "running".
HEARTBEAT_STALE_SECONDS = 600

BULLET_TIME_CLASSES = frozenset(
    {"bullet", "ultrabullet", "ultra", "hyper", "hyperbullet"}
)
_CLK_RE = re.compile(r"\[%clk\s+([0-9:.]+)\]")
_TC_RE = re.compile(r"^\s*(\d+)(?:\+\d+)?\s*$")
_PIECE_VALUES = {
    chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
    chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0,
}


def parse_clk_seconds(comment: str) -> Optional[float]:
    m = _CLK_RE.search(comment or "")
    if not m:
        return None
    try:
        nums = [float(p) for p in m.group(1).split(":")]
    except ValueError:
        return None
    if len(nums) == 3:
        h, mi, s = nums
        return h * 3600.0 + mi * 60.0 + s
    if len(nums) == 2:
        mi, s = nums
        return mi * 60.0 + s
    return nums[0] if len(nums) == 1 else None


def parse_base_time(time_control: str) -> Optional[float]:
    m = _TC_RE.match(time_control or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def clock_threshold_s(base: Optional[float]) -> float:
    if base is None:
        return CLOCK_FALLBACK_S
    return max(CLOCK_FLOOR_S, CLOCK_FRAC * base)


def position_key_4(fen: str) -> str:
    return " ".join((fen or "").split()[:4])


# ---------------------------------------------------------------------------
# Job rows
# ---------------------------------------------------------------------------

def create_user_puzzle_job(
    *,
    requested_by_user_id: str,
    lichess_username: Optional[str],
    chesscom_username: Optional[str],
    limit: int,
) -> Dict[str, Any]:
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Reclaim jobs whose worker died silently (dev restarts kill
            # BackgroundTasks without marking the row): only same-user rows
            # with a stale heartbeat, never another user's jobs.
            cur.execute(
                """
                UPDATE user_puzzle_jobs
                SET status='failed',
                    error_message='worker lost: reclaimed stale running job',
                    completed_at=NOW()
                WHERE requested_by_user_id = %s
                  AND status = 'running'
                  AND (heartbeat_at IS NULL OR heartbeat_at < NOW() - (%s || ' seconds')::interval)
                """,
                (requested_by_user_id, str(HEARTBEAT_STALE_SECONDS)),
            )
            cur.execute(
                """
                INSERT INTO user_puzzle_jobs (
                    requested_by_user_id, lichess_username, chesscom_username,
                    requested_limit, status
                )
                VALUES (%s, %s, %s, %s, 'queued')
                RETURNING id AS job_id, status
                """,
                (requested_by_user_id, lichess_username, chesscom_username, limit),
            )
            row = cur.fetchone()
        conn.commit()
        return {"job_id": str(row["job_id"]), "status": row["status"]}
    finally:
        database.connection_pool.putconn(conn)


def get_user_puzzle_job(*, job_id: str, requested_by_user_id: str) -> Optional[Dict[str, Any]]:
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id AS job_id, status, lichess_username, chesscom_username,
                       requested_limit, fetched_games, screened_games,
                       candidates, puzzles_kept, error_message, summary
                FROM user_puzzle_jobs
                WHERE id = %s::uuid AND requested_by_user_id = %s
                """,
                (job_id, requested_by_user_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            row["job_id"] = str(row["job_id"])
            return dict(row)
    finally:
        database.connection_pool.putconn(conn)


# ---------------------------------------------------------------------------
# Fetch (read-only; Lichess with clocks=true, src/integrations untouched)
# ---------------------------------------------------------------------------

def fetch_lichess_with_clocks(username: str, limit: int) -> List[Dict[str, Any]]:
    from integrations import lichess as lich_mod

    username = (username or "").strip()
    if not username:
        raise ValueError("username must not be empty")
    url = "{base}?max={limit}&pgnInJson=true&opening=true&clocks=true".format(
        base=lich_mod.GAMES_URL.format(username=username), limit=limit
    )
    records = lich_mod._http_get_ndjson(url, username=username)
    games = [lich_mod._summarize_game(rec) for rec in records]
    games.sort(key=lambda g: g.get("end_time") or 0, reverse=True)
    return games[:limit]


def derive_color(summary: Dict[str, Any], username: str) -> Optional[str]:
    want = (username or "").strip().lower()
    if not want:
        return None
    w = str(((summary.get("white") or {}).get("username")) or "").strip().lower()
    b = str(((summary.get("black") or {}).get("username")) or "").strip().lower()
    if w == want:
        return "white"
    if b == want:
        return "black"
    return None


# ---------------------------------------------------------------------------
# Stage-2 engine (nodes-only, fresh token; calc_probe parity)
# ---------------------------------------------------------------------------

class _Stage2Engine:
    def __init__(self, path: str):
        self._engine = chess.engine.SimpleEngine.popen_uci(path, timeout=120)
        self._engine.configure({"Threads": 1, "Hash": 16, "UCI_ShowWDL": True})

    def close(self) -> None:
        try:
            self._engine.quit()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _entry(info, turn) -> Dict[str, Any]:
        pv = info.get("pv") or []
        entry: Dict[str, Any] = {
            "move": pv[0].uci() if pv else None,
            "cp": None, "mate": None,
            "pv_uci": [m.uci() for m in pv],
        }
        score = info.get("score")
        if score is not None:
            pov = score.pov(turn)
            if pov.is_mate():
                entry["mate"] = pov.mate()
            else:
                entry["cp"] = float(pov.score())
        return entry

    def analyse_before(self, fen: str, nodes: int):
        board = chess.Board(fen)
        infos = self._engine.analyse(
            board, chess.engine.Limit(nodes=nodes), multipv=4, game=object()
        )
        return [self._entry(i, board.turn) for i in infos]

    def analyse_after(self, fen: str, nodes: int):
        board = chess.Board(fen)
        info = self._engine.analyse(
            board, chess.engine.Limit(nodes=nodes), multipv=1, game=object()
        )
        if isinstance(info, list):
            info = info[0] if info else {}
        return self._entry(info, board.turn)


def _mate_equiv(mate: Optional[int]) -> Optional[float]:
    if mate is None:
        return None
    return 10000.0 if mate > 0 else -10000.0


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def run_user_puzzle_job(job_id: str) -> None:
    """Background worker: fetch -> screen -> verify -> store. Own connection."""
    saved_env = {k: os.environ.get(k) for k in ("REVIEW_DETERMINISTIC", "REVIEW_NODES")}
    # The deterministic screen budget is deployment config read per-call by
    # core/analysis_mode; the worker pins the pilot-validated values for its
    # own run and restores them after so concurrent review requests keep
    # their configured budget.
    os.environ["REVIEW_DETERMINISTIC"] = "1"
    os.environ["REVIEW_NODES"] = str(SCREEN_NODES)
    conn = database.connection_pool.getconn()
    screen_engine = None
    stage2 = None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM user_puzzle_jobs WHERE id = %s::uuid FOR UPDATE",
                (job_id,),
            )
            job = cur.fetchone()
            if job is None:
                conn.rollback()
                log.error("user puzzle job %s not found", job_id)
                return
            cur.execute(
                "UPDATE user_puzzle_jobs SET status='running', started_at=NOW() "
                "WHERE id = %s::uuid",
                (job_id,),
            )
        conn.commit()
        clerk_id = job["requested_by_user_id"]
        limit = job["requested_limit"]

        fetched: List[Dict[str, Any]] = []
        if job["lichess_username"]:
            try:
                for g in fetch_lichess_with_clocks(job["lichess_username"], limit):
                    g["provider"] = "lichess"
                    fetched.append(g)
            except Exception as exc:  # noqa: BLE001 -- report partial corpus
                log.warning("lichess fetch failed for job %s: %s", job_id, exc)
        if job["chesscom_username"]:
            try:
                from integrations.chess_com import fetch_recent_chesscom_games

                for g in fetch_recent_chesscom_games(job["chesscom_username"], limit=limit):
                    g["provider"] = "chesscom"
                    fetched.append(g)
            except Exception as exc:  # noqa: BLE001
                log.warning("chesscom fetch failed for job %s: %s", job_id, exc)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE user_puzzle_jobs SET fetched_games=%s WHERE id=%s::uuid",
                (len(fetched), job_id),
            )
        conn.commit()
        if not fetched:
            raise RuntimeError("no games fetched from either provider")

        try:
            from services.opening_book import ensure_book_revision, is_book_move

            book_lookup = is_book_move
            book_ok = ensure_book_revision() is not None
            if not book_ok:
                book_lookup = None  # empty book: fall back to move-number rule
        except Exception:  # noqa: BLE001
            book_lookup = None

        screen_engine = StockfishEngine()
        screen_engine.start()
        try:
            from core.analysis_mode import current_mode_string

            mode_str = current_mode_string(screen_engine.name, 2, SCREEN_NODES)
        except Exception:  # noqa: BLE001
            mode_str = "rev-det-v1|engine=%s|nodes=%d|multipv=2" % (
                screen_engine.name, SCREEN_NODES)
        analyzer = GameAnalyzer(
            engine=screen_engine, explainer=MockExplainer(), multipv=2, deterministic=True
        )
        stage2 = _Stage2Engine(screen_engine.stockfish_path)

        candidates: List[Dict[str, Any]] = []
        screened = 0
        exclusion_counts: Dict[str, int] = {}
        for g in fetched:
            provider = g.get("provider", "")
            my_name = job["lichess_username"] if provider == "lichess" else job["chesscom_username"]
            color = derive_color(g, my_name or "")
            if color is None:
                continue
            try:
                game_id = _store_user_game(conn, clerk_id, provider, my_name or "", g)
                rows = _screen_game(conn, analyzer, book_lookup, g, color)
                screened += 1
                for r in rows:
                    if r["exclusion"] is not None:
                        exclusion_counts[r["exclusion"]] = exclusion_counts.get(r["exclusion"], 0) + 1
                    elif r["ep_loss"] >= CAND_EP and r["cp_loss"] >= CAND_CP:
                        r["db_game_id"] = game_id
                        r["game_url"] = g.get("url", "")
                        candidates.append(r)
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE user_puzzle_jobs SET screened_games=%s, candidates=%s, "
                        "heartbeat_at=NOW() WHERE id=%s::uuid",
                        (screened, len(candidates), job_id),
                    )
                conn.commit()
            except Exception:  # noqa: BLE001 -- per-game failure is not fatal
                conn.rollback()
                log.exception("screen failed for a game in job %s", job_id)

        # Volume control: dedupe by position_key, top-3 per game by ep_loss.
        seen: Dict[str, Dict[str, Any]] = {}
        for r in sorted(candidates, key=lambda r: r["ep_loss"], reverse=True):
            if r["position_key"] not in seen:
                seen[r["position_key"]] = r
        by_game: Dict[str, List[Dict[str, Any]]] = {}
        for r in seen.values():
            by_game.setdefault(r["db_game_id"], []).append(r)
        capped: List[Dict[str, Any]] = []
        for rs in by_game.values():
            capped.extend(sorted(rs, key=lambda r: r["ep_loss"], reverse=True)[:PER_GAME_CAP])

        kept = 0
        reject_counts: Dict[str, int] = {}
        for r in capped:
            try:
                reason = _verify_and_store(conn, clerk_id, job_id, stage2, r, mode_str)
                if reason == "":
                    kept += 1
                else:
                    reject_counts[reason] = reject_counts.get(reason, 0) + 1
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE user_puzzle_jobs SET puzzles_kept=%s, heartbeat_at=NOW() "
                        "WHERE id=%s::uuid",
                        (kept, job_id),
                    )
                conn.commit()
            except Exception:  # noqa: BLE001
                conn.rollback()
                reject_counts["engine_error"] = reject_counts.get("engine_error", 0) + 1
                log.exception("verify failed for a candidate in job %s", job_id)

        import json as _json

        summary = {"exclusions": exclusion_counts, "rejects": reject_counts}
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE user_puzzle_jobs SET status='completed', completed_at=NOW(), "
                "puzzles_kept=%s, heartbeat_at=NOW(), summary=%s WHERE id=%s::uuid",
                (kept, _json.dumps(summary), job_id),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        log.exception("user puzzle job %s failed", job_id)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE user_puzzle_jobs SET status='failed', error_message=%s, "
                    "completed_at=NOW() WHERE id=%s::uuid",
                    (str(exc)[:500], job_id),
                )
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
    finally:
        try:
            if stage2 is not None:
                stage2.close()
            if screen_engine is not None:
                screen_engine.close()
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            database.connection_pool.putconn(conn)


def _store_user_game(conn, clerk_id: str, provider: str, source_username: str, g: Dict[str, Any]):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO user_games (
                user_id, provider, source_username, game_url, pgn,
                white_player, black_player, result, end_time, time_class
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, provider, game_url)
            DO UPDATE SET pgn = EXCLUDED.pgn, result = EXCLUDED.result,
                          end_time = EXCLUDED.end_time
            RETURNING id
            """,
            (clerk_id, provider, source_username, g.get("url", ""), g.get("pgn", ""),
             Json(g.get("white") or {}), Json(g.get("black") or {}),
             g.get("result", ""), g.get("end_time", 0), g.get("time_class", "")),
        )
        return cur.fetchone()["id"]


def _screen_game(conn, analyzer: GameAnalyzer, book_lookup, g: Dict[str, Any], color: str):
    """Review eligible plies; avoid engine searches before a usable user move."""
    game = chess.pgn.read_game(StringIO(g.get("pgn", "")))
    if game is None:
        raise ValueError("unparseable PGN")
    headers = dict(game.headers)
    variant = (headers.get("Variant") or "Standard").strip() or "Standard"
    base_time = parse_base_time(headers.get("TimeControl") or "")
    ratings = analyzer._ratings_from_headers(game)
    elo_header = "WhiteElo" if color == "white" else "BlackElo"
    try:
        header_rating = int((headers.get(elo_header) or "").strip())
    except (TypeError, ValueError):
        header_rating = None
    time_class = g.get("time_class", "")

    board = game.board()
    rows: List[Dict[str, Any]] = []
    previous_eval = None
    previous_ep_loss = None
    in_book = True
    ply_index = 0
    # The shortcut below depends on fixed-node fresh-token searches. Keep the
    # historical full scan if this helper is ever used with another mode.
    screening_started = not analyzer.deterministic
    for node in game.mainline():
        move = node.move
        move_color = "white" if board.turn == chess.WHITE else "black"
        move_number = board.fullmove_number
        rating = (header_rating or ratings.get(move_color)) if move_color == color else ratings.get(move_color)
        if in_book and book_lookup is not None:
            try:
                is_book = bool(book_lookup(board, move))
            except Exception:  # noqa: BLE001
                is_book = False
        else:
            is_book = False
        if not is_book:
            in_book = False

        # Determine exclusions before searching. Until the first move we
        # could keep, the fixed-node fresh-token evaluation at that position
        # is identical to the value we'd have carried forward through every
        # skipped opening/excluded ply. Fast-forward those moves without
        # starting Stockfish; candidate evaluations remain unchanged.
        exclusion = None
        clk = parse_clk_seconds(node.comment or "") if move_color == color else None
        if move_color == color:
            if variant != "Standard":
                exclusion = "variant"
            elif (time_class or "").strip().lower() in BULLET_TIME_CLASSES:
                exclusion = "time_class"
            elif book_lookup is None:
                if move_number <= MIN_MOVE:
                    exclusion = "book_fallback"
            elif is_book:
                exclusion = "book"
            if (
                exclusion is None
                and clk is not None
                and clk < clock_threshold_s(base_time)
            ):
                exclusion = "clock"

        if not screening_started:
            if move_color == color and exclusion is not None:
                fen_before = board.fen()
                played_san = board.san(move)
                board.push(move)
                rows.append({
                    "ply_index": ply_index + 1,
                    "move_number": move_number,
                    "color": move_color,
                    "fen_before": fen_before,
                    "fen_after": board.fen(),
                    "position_key": position_key_4(fen_before),
                    "played_san": played_san,
                    "played_uci": move.uci(),
                    "best_san": "",
                    "best_uci": "",
                    "best_pv_uci": [],
                    "ep_loss": 0.0,
                    "cp_loss": 0,
                    "player_rating": int(rating or 1500),
                    "exclusion": exclusion,
                })
                ply_index += 1
                continue
            if move_color != color:
                board.push(move)
                ply_index += 1
                continue

            # Fixed-node evaluations use a fresh engine token, so this is
            # the same evaluation as evaluating the same position while
            # processing the preceding excluded move.
            screening_started = True
            previous_eval = analyzer._evaluate(board)

        eval_before = previous_eval if previous_eval is not None else analyzer._evaluate(board)
        turn_entry, eval_after, raw_ep = analyzer.analyze_ply(
            board, move, eval_before, is_book_move=is_book,
            player_rating=rating, opponent_prev_ep_loss=previous_ep_loss,
            include_extras=True,
        )
        previous_eval = eval_after
        previous_ep_loss = raw_ep
        ply_index += 1
        if move_color != color:
            continue
        rows.append({
            "ply_index": ply_index,
            "move_number": turn_entry.get("move_number") or 0,
            "color": move_color,
            "fen_before": turn_entry.get("fen_before") or "",
            "fen_after": turn_entry.get("fen") or "",
            "position_key": position_key_4(turn_entry.get("fen_before") or ""),
            "played_san": turn_entry.get("san") or "",
            "played_uci": move.uci(),
            "best_san": turn_entry.get("best_move_san") or "",
            "best_uci": turn_entry.get("best_move_uci") or "",
            "best_pv_uci": list(eval_before.principal_variation_uci or []),
            "ep_loss": float(turn_entry.get("ep_loss") or 0.0),
            "cp_loss": int(turn_entry.get("cp_loss") or 0),
            "player_rating": int(rating or 1500),
            "exclusion": exclusion,
        })
    return rows


def _verify_and_store(conn, clerk_id: str, job_id: str, stage2: _Stage2Engine,
                      r: Dict[str, Any], mode_str: str) -> str:
    """Stage-2 uniqueness check; stores puzzle + queue entry.

    Returns "" when kept, otherwise a short reject reason for the job
    summary (so a 0-kept run explains itself).
    """
    board = chess.Board(r["fen_before"])
    if sum(1 for _ in board.legal_moves) <= 1:
        return "single_legal"
    pvs = stage2.analyse_before(r["fen_before"], STAGE2_NODES)
    after = stage2.analyse_after(r["fen_after"], STAGE2_NODES)
    if not pvs or not pvs[0].get("move"):
        return "no_engine_line"
    rating = int(r["player_rating"] or 1500)
    best, second = pvs[0], (pvs[1] if len(pvs) > 1 else None)
    best_cp = best["cp"] if best["cp"] is not None else _mate_equiv(best["mate"])
    second_cp = None
    if second is not None:
        second_cp = second["cp"] if second["cp"] is not None else _mate_equiv(second["mate"])
    opp_cp = after["cp"] if after["cp"] is not None else _mate_equiv(after["mate"])
    played_cp = -opp_cp if opp_cp is not None else None
    if best_cp is None or played_cp is None:
        return "no_engine_line"
    # Stage 1 is a low-budget candidate screen. Recheck at Stage 2 that the
    # played move is still a meaningful mistake; a unique best move alone
    # does not make this position a puzzle. This also rejects candidates
    # where the deeper search now prefers the move the user actually played.
    if best.get("move") == r["played_uci"]:
        return "played_is_best"
    verified_ep_loss = max(
        0.0,
        expected_points(best_cp, rating) - expected_points(played_cp, rating),
    )
    verified_cp_loss = max(0, int(round(best_cp - played_cp)))
    if verified_ep_loss < CAND_EP or verified_cp_loss < CAND_CP:
        return "loss_below_threshold"
    if best.get("mate") and best["mate"] > 0 and second is not None and (second.get("mate") or 0) > 0:
        return "mate_mate"
    if best.get("mate") and best["mate"] > 0:
        margin = None
    elif second_cp is None:
        return "no_second_line"
    else:
        margin = max(0.0, expected_points(best_cp, rating) - expected_points(second_cp, rating))
        if margin < MARGIN_M:
            return "margin_lt_%.2f" % MARGIN_M
    try:
        best_san = board.san(chess.Move.from_uci(best["move"]))
    except ValueError:
        return "no_engine_line"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO user_puzzles (
                requested_by_user_id, job_id, source_game_id, game_url,
                ply_index, move_number, color, fen_before, position_key,
                played_move_san, played_move_uci, best_move_san, best_move_uci,
                best_pv_uci, ep_loss, cp_loss, margin_ep, player_rating, mode
            )
            VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s)
            ON CONFLICT (requested_by_user_id, game_url, ply_index) DO NOTHING
            RETURNING id
            """,
            (clerk_id, job_id, str(r["db_game_id"]), r["game_url"],
             r.get("ply_index", r["move_number"]), r["move_number"], r["color"],
             r["fen_before"], r["position_key"], r["played_san"], r["played_uci"],
             best_san, best["move"],
             " ".join(best.get("pv_uci") or []),
             max(0.0, expected_points(best_cp, rating) - expected_points(played_cp, rating)),
             max(0, int(round(best_cp - played_cp))),
             margin, rating, mode_str),
        )
        row = cur.fetchone()
        if row is None:
            # Re-extraction of an already-stored position: reuse, don't duplicate.
            cur.execute(
                "SELECT id FROM user_puzzles WHERE requested_by_user_id=%s "
                "AND game_url=%s AND ply_index=%s",
                (clerk_id, r["game_url"], r.get("ply_index", r["move_number"])),
            )
            puzzle_id = cur.fetchone()["id"]
        else:
            puzzle_id = row["id"]
        cur.execute(
            """
            INSERT INTO user_puzzle_entries (user_id, puzzle_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (clerk_id, str(puzzle_id)),
        )
    return ""
