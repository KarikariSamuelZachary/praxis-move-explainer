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
    ) -> Evaluation:
        if not self.engine:
            raise RuntimeError("Engine not started. Use context manager or call start()")

        effective_depth = depth_limit if depth_limit is not None else self.depth
        info = self.engine.analyse(
            board,
            chess.engine.Limit(time=self.FAST_ANALYSIS_TIME, depth=effective_depth),
        )

        score = info.get("score")
        pv = info.get("pv", [])

        # Convert score to centipawns from the requested side's perspective.
        if score:
            cp_score = self._score_to_centipawns(score, pov if pov is not None else board.turn)
        else:
            cp_score = 0

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
            best_move_san=best_move_san
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
