"""
Stockfish engine wrapper for chess position analysis.
Detects blunders and evaluates positions.
"""
import chess
import chess.engine
import glob
import logging
import os
import shutil
from threading import Lock
from typing import Optional
from schemas.models import Evaluation

log = logging.getLogger(__name__)

STOCKFISH_CANDIDATE_PATHS = (
    "/workspace/.apt/usr/games/stockfish",
    "/usr/games/stockfish",
    "/usr/bin/stockfish",
)


def resolve_stockfish_path(stockfish_path: Optional[str] = None) -> str:
    configured_path = stockfish_path or os.getenv("STOCKFISH_PATH")
    if configured_path and os.path.exists(configured_path):
        return configured_path

    if configured_path:
        log.warning("Configured Stockfish path does not exist: %s", configured_path)

    for candidate in STOCKFISH_CANDIDATE_PATHS:
        if os.path.exists(candidate):
            return candidate

    discovered_path = shutil.which("stockfish")
    if discovered_path:
        return discovered_path

    workspace_matches = glob.glob("/workspace/**/stockfish", recursive=True)
    if workspace_matches:
        return workspace_matches[0]

    return "stockfish"


def _validate_strength_value(value, name: str, opt) -> int:
    """Validate a strength-limiting int against an advertised UCI spin option.

    Mirrors best_move_candidates()'s strict-int rule for multipv: bool (an
    int subclass) is rejected, and float/str are rejected even though they
    would coerce through int(). `opt` is the Option object read from
    `engine.options`; its .min/.max supply the valid range, so a different
    Stockfish build cannot silently drift the range out from under us.
    """
    lo, hi = opt.min, opt.max
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{name} must be an integer in [{lo}, {hi}]; got {value!r} "
            f"(type {type(value).__name__})"
        )
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ValueError(f"{name} must be in [{lo}, {hi}]; got {value}")
    return value


def configure_strength(
    engine: chess.engine.SimpleEngine,
    elo: Optional[int] = None,
    skill_level: Optional[int] = None,
) -> dict:
    """Configure Stockfish's UCI strength-limiting options on `engine`.

    Supports two limiting mechanisms, whichever the bundled binary advertises
    (read from `engine.options` at call time, never hardcoded):

      * UCI_LimitStrength (check) + UCI_Elo (spin) -- fine-grained Elo control.
      * Skill Level (spin) -- coarse 0-20 control.

    The Stockfish 16 binary in this repo advertises BOTH: `UCI_Elo` spin
    1320..3190 (default 1320), `Skill Level` spin 0..20 (default 20), and
    `UCI_LimitStrength` check default False -- verified live against the
    binary (see engines/stockfish_engine_test.py). The valid Elo/Skill ranges
    are read from `engine.options` here, so a future Stockfish build that
    changes them is handled automatically rather than trusted from memory.

    Precedence:
      1. `elo` given + UCI_Elo advertised            -> Elo path.
      2. `elo` given + UCI_Elo NOT advertised        -> fall back to
         `skill_level` (required; ValueError if also absent).
      3. only `skill_level` given                    -> Skill Level path.
      4. neither given                               -> full-strength reset.

    Both provided values are validated strictly up front (even when a
    higher-precedence value wins) so a wrong-type or out-of-range argument is
    always loud, never silently ignored.

    Stale-state note: every path sets BOTH limiting gates explicitly, so a
    setting from a prior call cannot leak into a later call that didn't ask
    for it (the same class of stale-Elo bug that bit the Maia side). The Elo
    path also resets Skill Level to its max because the two mechanisms are
    independent -- a leftover low Skill Level would otherwise keep weakening
    the engine on top of the Elo limit. The Skill Level path and the reset
    both disable UCI_LimitStrength, because a stale `LimitStrength=true` +
    `UCI_Elo` would otherwise still apply even with Skill Level at full.

    Persistence (verified live): UCI setoptions persist across subsequent
    analyse()/play() calls on the SAME subprocess until changed again. They
    do NOT need to be re-applied per move -- call this once per strength
    change and every later analyse() runs at that strength. Re-applying per
    move is harmless (one cheap setoption round-trip) but unnecessary.

    Returns a small dict describing what was applied:

        {"limit_strength": bool, "elo": Optional[int],
         "skill_level": Optional[int]}
    """
    options = engine.options
    limit_opt = options.get("UCI_LimitStrength")
    elo_opt = options.get("UCI_Elo")
    skill_opt = options.get("Skill Level")

    # Validate whatever the caller provided, strictly, regardless of which
    # mechanism ends up winning.
    if elo is not None and elo_opt is not None:
        _validate_strength_value(elo, "elo", elo_opt)
    if skill_level is not None:
        if skill_opt is None:
            raise ValueError(
                "skill_level was requested but this engine does not advertise "
                "the Skill Level option."
            )
        _validate_strength_value(skill_level, "skill_level", skill_opt)

    if elo is not None:
        if elo_opt is not None:
            mechanism = "elo"
        elif skill_level is not None:
            mechanism = "skill"
        else:
            raise ValueError(
                "elo was requested but this engine does not advertise the "
                "UCI_Elo option; pass skill_level as a fallback strength limit."
            )
    elif skill_level is not None:
        mechanism = "skill"
    else:
        mechanism = "full"

    if mechanism == "elo":
        config: dict = {"UCI_LimitStrength": True, "UCI_Elo": elo}
        if skill_opt is not None:
            config["Skill Level"] = skill_opt.max
    elif mechanism == "skill":
        config = {"Skill Level": skill_level}
        if limit_opt is not None:
            config["UCI_LimitStrength"] = False
    else:
        config = {}
        if limit_opt is not None:
            config["UCI_LimitStrength"] = False
        if skill_opt is not None:
            config["Skill Level"] = skill_opt.max

    engine.configure(config)

    return {
        "limit_strength": mechanism == "elo",
        "elo": elo if mechanism == "elo" else None,
        "skill_level": skill_level if mechanism == "skill" else None,
    }


class StockfishEngine:
    FAST_ANALYSIS_TIME = 0.1

    def __init__(self, stockfish_path: Optional[str] = None, depth: int = 12):
        self.stockfish_path = resolve_stockfish_path(stockfish_path)
        self.depth = depth
        self.engine: Optional[chess.engine.SimpleEngine] = None
        # Serializes analyse() calls on this instance. python-chess's
        # SimpleEngine submits UCI commands from whatever thread calls it
        # without a command-level lock, so two concurrent callers would
        # interleave UCI writes on one protocol and corrupt responses.
        self._call_lock = Lock()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def start(self):
        self.engine = chess.engine.SimpleEngine.popen_uci(self.stockfish_path)

    def close(self):
        if self.engine:
            self.engine.quit()
            self.engine = None

    def evaluate(
        self,
        board: chess.Board,
        depth_limit: Optional[int] = None,
        pov: Optional[chess.Color] = None,
        time_limit: Optional[float] = None,
    ) -> Evaluation:
        if not self.engine:
            raise RuntimeError("Engine not started. Use context manager or call start()")

        effective_depth = depth_limit if depth_limit is not None else self.depth
        effective_time = (
            time_limit if time_limit is not None else self.FAST_ANALYSIS_TIME
        )
        with self._call_lock:
            info = self.engine.analyse(
                board,
                chess.engine.Limit(time=effective_time, depth=effective_depth),
            )

        score = info.get("score")
        pv = info.get("pv", [])

        # Convert score to centipawns from the requested side's perspective.
        if score:
            score_pov = pov if pov is not None else board.turn
            normalized_score = score.pov(score_pov)
            mate = normalized_score.mate() if normalized_score.is_mate() else None
            cp_score = self._score_to_centipawns(score, score_pov)
        else:
            cp_score = 0
            mate = None

        # Extract best move and convert to UCI/SAN
        if pv:
            best_move = pv[0]
            best_move_uci = best_move.uci()
            best_move_san = board.san(best_move)
        else:
            best_move_uci = ""
            best_move_san = "(none)"

        return Evaluation(
            score_cp=cp_score,
            best_move_uci=best_move_uci,
            best_move_san=best_move_san,
            mate=mate,
        )

    def suggest(
        self,
        board: chess.Board,
        num_moves: int = 5,
        time_limit: Optional[float] = None,
    ) -> list:
        """Return Stockfish's top `num_moves` legal moves for `board`.

        Uses a multi-PV analysis (`multipv=N`) so the caller gets a ranked
        list of candidate moves rather than just the single best move that
        `evaluate()` surfaces. Each entry is a plain dict:

            {"uci": "e2e4", "san": "e4", "score_cp": 36}

        `score_cp` is centipawns from the SIDE-TO-MOVE's perspective
        (positive = good for the mover). Mate scores are coerced to
        ±10000 by `_score_to_centipawns` (same convention `evaluate`
        uses), so a "mate in 3" reads as a large positive score.

        `num_moves` is clamped to the number of legal moves so a terminal
        or near-terminal position can't make Stockfish error on an
        out-of-range multipv.
        """
        if not self.engine:
            raise RuntimeError("Engine not started. Use context manager or call start()")

        legal_count = board.legal_moves.count()
        if legal_count == 0:
            return []

        requested = max(1, min(num_moves, legal_count))
        analysis_time = time_limit if time_limit is not None else self.FAST_ANALYSIS_TIME

        with self._call_lock:
            infos = self.engine.analyse(
                board,
                chess.engine.Limit(time=analysis_time),
                multipv=requested,
            )

        suggestions = []
        for info in infos:
            pv = info.get("pv", [])
            if not pv:
                continue
            move = pv[0]
            score = info.get("score")
            cp = self._score_to_centipawns(score, board.turn) if score else 0
            suggestions.append({
                "uci": move.uci(),
                "san": board.san(move),
                "score_cp": int(cp),
            })
        return suggestions

    def _score_to_centipawns(self, score: chess.engine.Score, turn: chess.Color) -> float:
        normalized_score = score.pov(turn)
        if normalized_score.is_mate():
            mate_in = normalized_score.mate()
            cp = 10000 if mate_in and mate_in > 0 else -10000
        else:
            cp = normalized_score.score()
        return cp or 0

    def is_blunder(self, eval_before: Evaluation, eval_after: Evaluation, threshold: float = 100) -> bool:
        eval_drop = eval_before.score_cp - eval_after.score_cp
        return eval_drop >= threshold


# --- Long-lived singleton (used by the sparring safety check) ---------------
#
# The sparring move endpoint previously spawned a fresh Stockfish subprocess
# per request (`with StockfishEngine():`), paying process spawn + UCI
# handshake on EVERY move (~0.3-0.7s) before the two 0.1s evaluations even
# started. A process-lifetime singleton removes that per-move cost. The
# instance lock above keeps concurrent request threads from interleaving
# UCI commands; they simply queue behind each other's analyse() calls.

_stockfish: Optional[StockfishEngine] = None
_stockfish_lifecycle_lock = Lock()


def start_stockfish_singleton() -> StockfishEngine:
    """Start the long-lived Stockfish used by the sparring safety check.

    Idempotent: returns the existing singleton when it already has a live
    subprocess. Raises if the engine cannot be spawned so callers (startup
    logging, request paths) can degrade loudly instead of silently.
    """
    global _stockfish
    with _stockfish_lifecycle_lock:
        if _stockfish is not None and _stockfish.engine is not None:
            return _stockfish
        instance = StockfishEngine()
        instance.start()  # raises on failure BEFORE we publish the global
        _stockfish = instance
        return _stockfish


def get_stockfish_singleton() -> StockfishEngine:
    """Return the long-lived Stockfish, starting it on first use."""
    global _stockfish
    if _stockfish is None:
        return start_stockfish_singleton()
    return _stockfish


def reset_stockfish_singleton() -> None:
    """Drop the long-lived Stockfish after a failure.

    Best-effort `quit()` so a still-alive subprocess doesn't leak; any
    exception is swallowed because the caller is already handling a failed
    evaluate() — the next sparring request simply starts a fresh subprocess.
    """
    global _stockfish
    with _stockfish_lifecycle_lock:
        instance = _stockfish
        _stockfish = None
    if instance is not None:
        try:
            instance.close()
        except Exception:  # noqa: BLE001 -- reset path must never raise
            pass


def close_stockfish_singleton() -> None:
    """Quit the long-lived Stockfish (app shutdown). Best-effort, no raise."""
    global _stockfish
    with _stockfish_lifecycle_lock:
        instance = _stockfish
        _stockfish = None
    if instance is not None:
        try:
            instance.close()
        except Exception:  # noqa: BLE001 -- shutdown must never raise
            pass


# --- Long-lived singleton (used by game review) -----------------------------
#
# Game review previously spawned a fresh Stockfish subprocess per request
# (`StockfishEngine(depth=REVIEW_DEPTH)` + start/close in the handler), paying
# process spawn + UCI handshake (~0.3-0.7s) before the first of ~2N
# evaluations even started. Review gets its OWN singleton rather than
# borrowing the sparring one because UCI `setoption` strength limits persist
# on a subprocess until changed (see configure_strength's persistence note and
# persona_reranker's "why not the singleton" rationale) -- a dedicated process
# guarantees review always runs full strength regardless of what the sparring
# path configured, and gives the two paths independent failure domains: a
# crashed review engine is reset without disturbing an in-flight sparring
# session and vice versa.

_review_stockfish: Optional[StockfishEngine] = None
_review_stockfish_lifecycle_lock = Lock()


def start_review_stockfish(depth: int = 12) -> StockfishEngine:
    """Start the long-lived full-strength Stockfish used by game review.

    Idempotent: returns the existing singleton when it already has a live
    subprocess. `depth` only applies when the subprocess is first created
    (REVIEW_DEPTH is static per deployment). Raises if the engine cannot be
    spawned so callers can degrade loudly instead of silently.
    """
    global _review_stockfish
    with _review_stockfish_lifecycle_lock:
        if _review_stockfish is not None and _review_stockfish.engine is not None:
            return _review_stockfish
        instance = StockfishEngine(depth=depth)
        instance.start()  # raises on failure BEFORE we publish the global
        _review_stockfish = instance
        return _review_stockfish


def get_review_stockfish(depth: int = 12) -> StockfishEngine:
    """Return the long-lived review Stockfish, starting it on first use."""
    global _review_stockfish
    if _review_stockfish is None:
        return start_review_stockfish(depth)
    return _review_stockfish


def reset_review_stockfish() -> None:
    """Drop the long-lived review Stockfish after a failure.

    Best-effort `quit()` so a still-alive subprocess doesn't leak; any
    exception is swallowed because the caller is already handling a failed
    evaluate() -- the next review request starts a fresh subprocess.
    """
    global _review_stockfish
    with _review_stockfish_lifecycle_lock:
        instance = _review_stockfish
        _review_stockfish = None
    if instance is not None:
        try:
            instance.close()
        except Exception:  # noqa: BLE001 -- reset path must never raise
            pass


def close_review_stockfish() -> None:
    """Quit the long-lived review Stockfish (app shutdown). Best-effort."""
    global _review_stockfish
    with _review_stockfish_lifecycle_lock:
        instance = _review_stockfish
        _review_stockfish = None
    if instance is not None:
        try:
            instance.close()
        except Exception:  # noqa: BLE001 -- shutdown must never raise
            pass


# --- Long-lived singleton (used by endgame opponent replies) ----------------
#
# Endgame Trainer reply generation reuses the SAME process-lifetime discipline
# as review/sparring (never a fresh subprocess per call) but gets its OWN
# singleton, for two reasons:
#
#   1. FAILURE DOMAINS. The review route resets its engine on any analyse
#      failure (reset_review_stockfish). Sharing would let a review failure
#      kill the engine between an endgame drill's reply and the user's next
#      move, and vice versa.
#   2. LATENCY ISOLATION. StockfishEngine._call_lock serializes analyse()
#      calls per subprocess. A game review holds its engine for ~2N
#      consecutive evaluations; an endgame reply queued behind that would
#      wait seconds. A dedicated process keeps reply latency independent of
#      review load.
#
# This path never calls configure_strength(), so the engine stays
# full-strength for the whole deployment (the strength-limits-persist
# hazard documented in configure_strength cannot bite here). The sparring
# singleton was rejected as a reuse target for the same strength reason:
# it is the depth-12 safety-check engine, not a full-strength defender.

_endgame_stockfish: Optional[StockfishEngine] = None
_endgame_stockfish_lifecycle_lock = Lock()


def start_endgame_stockfish(depth: int = 18) -> StockfishEngine:
    """Start the long-lived full-strength Stockfish used for endgame replies.

    Idempotent: returns the existing singleton when it already has a live
    subprocess. `depth` only applies when the subprocess is first created
    (ENDGAME_REPLY_DEPTH is static per deployment). Raises if the engine
    cannot be spawned so callers can degrade loudly instead of silently.
    """
    global _endgame_stockfish
    with _endgame_stockfish_lifecycle_lock:
        if _endgame_stockfish is not None and _endgame_stockfish.engine is not None:
            return _endgame_stockfish
        instance = StockfishEngine(depth=depth)
        instance.start()  # raises on failure BEFORE we publish the global
        _endgame_stockfish = instance
        return _endgame_stockfish


def get_endgame_stockfish(depth: int = 18) -> StockfishEngine:
    """Return the long-lived endgame Stockfish, starting it on first use."""
    global _endgame_stockfish
    if _endgame_stockfish is None:
        return start_endgame_stockfish(depth)
    return _endgame_stockfish


def reset_endgame_stockfish() -> None:
    """Drop the endgame Stockfish after a failure. Best-effort `quit()`; the
    next reply request starts a fresh subprocess. Mirrors the review reset."""
    global _endgame_stockfish
    with _endgame_stockfish_lifecycle_lock:
        instance = _endgame_stockfish
        _endgame_stockfish = None
    if instance is not None:
        try:
            instance.close()
        except Exception:  # noqa: BLE001 -- reset path must never raise
            pass


def close_endgame_stockfish() -> None:
    """Quit the long-lived endgame Stockfish (app shutdown). Best-effort."""
    global _endgame_stockfish
    with _endgame_stockfish_lifecycle_lock:
        instance = _endgame_stockfish
        _endgame_stockfish = None
    if instance is not None:
        try:
            instance.close()
        except Exception:  # noqa: BLE001 -- shutdown must never raise
            pass
