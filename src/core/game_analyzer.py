"""
Core game analyzer that orchestrates chess engine and LLM.
"""
from dataclasses import asdict
import math
import chess
import chess.pgn
from io import StringIO
from typing import Any, Callable, Dict, List, Optional, Tuple

from schemas.models import Position, Evaluation, Mistake, AnalyzedMistake
from engines.stockfish_engine import StockfishEngine
from llms.base import LLMExplainer


# --- Expected Points (Chess.com Classification V2) --------------------------
#
# Chess.com classifies moves by the change in EXPECTED POINTS (EP), not raw
# centipawns: EP is a rating-aware winning probability in [0, 1] derived from
# the engine evaluation. The official EP bands (support.chess.com "How are
# moves classified?" + chess.com/terms/game-review) are:
#
#   Best 0.00 | Excellent (0.00, 0.02] | Good (0.02, 0.05]
#   Inaccuracy (0.05, 0.10] | Mistake (0.10, 0.20] | Blunder (0.20, 1.00]
#
# Chess.com's 2023 Game Review update adds that a Blunder must ALSO lose
# material or allow checkmate; `classify_move` enforces that via the
# `blunder_consequence` flag computed by the caller.
#
# The exact cp -> EP mapping is proprietary. This is a documented
# approximation: a logistic anchored to Lichess's published win-probability
# slope at the 1500-rating reference, scaled flatter for lower ratings and
# steeper for higher ratings (Chess.com: the same eval swing is worth less
# to a beginner and more to a master). Every constant below is a calibration
# target, not a verified Chess.com value.
EP_LOGISTIC_K_REFERENCE = 0.00368208  # Lichess's published slope (1500 anchor)
EP_RATING_REFERENCE = 1500
EP_RATING_SENSITIVITY = 0.00035  # k scales by 1 + sensitivity*(rating - ref)
EP_K_FACTOR_MIN = 0.55
EP_K_FACTOR_MAX = 1.8
EP_EXCELLENT_MAX = 0.02
EP_GOOD_MAX = 0.05
EP_INACCURACY_MAX = 0.10
EP_MISTAKE_MAX = 0.20
DEFAULT_PLAYER_RATING = 1500
MIN_PLAYER_RATING = 100
MAX_PLAYER_RATING = 3500

# Static piece values for the one-ply "did the opponent's best reply win
# material" blunder check (see `_loses_material_after_best_reply`).
_PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}


def _clamp_rating(player_rating: Optional[int]) -> int:
    """Coerce a PGN Elo / profile rating into the supported range."""
    try:
        rating = int(player_rating)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_PLAYER_RATING
    return max(MIN_PLAYER_RATING, min(MAX_PLAYER_RATING, rating))


def expected_points(
    cp: float, player_rating: Optional[int] = None
) -> float:
    """Rating-aware expected points in [0, 1] for a centipawn evaluation.

    Mate scores arrive normalized to +/-10000, so the logistic saturates to
    0.0/1.0 without special-casing.
    """
    factor = (
        1.0
        + (float(_clamp_rating(player_rating)) - EP_RATING_REFERENCE)
        * EP_RATING_SENSITIVITY
    )
    factor = max(EP_K_FACTOR_MIN, min(EP_K_FACTOR_MAX, factor))
    k = EP_LOGISTIC_K_REFERENCE * factor
    return 1.0 / (1.0 + math.exp(-k * float(cp)))


def _material_for(board: chess.Board, color: bool) -> int:
    return sum(
        len(board.pieces(piece_type, color)) * value
        for piece_type, value in _PIECE_VALUES.items()
    )


# --- Special labels (Brilliant / Great / Miss) ------------------------------
#
# Chess.com defines these beyond the EP bands (help center + 2023 update):
#   * Brilliant: best or nearly best + a good piece sacrifice; must not end
#     in a bad position and must not already be completely winning. Pawn
#     sacrifices count more generously for newer players.
#   * Great: the only good move in the position (the second-best line is
#     clearly worse).
#   * Miss: failing to capitalize on the opponent's mistake (or a forced
#     mate) and letting a winning position slip to equal or worse.
# Every value is a calibration target — Chess.com's exact rules are
# proprietary. EP is already rating-normalized, so the thresholds are EP
# constants; the sacrifice definition is the one rating-aware knob.
BRILLIANT_MAX_EP_BEFORE = 0.85  # "not already completely winning"
BRILLIANT_MIN_EP_AFTER = 0.35  # "not in a bad position after"
BRILLIANT_MIN_SACRIFICE_CP = 180  # exchange sacrifice or bigger
BRILLIANT_PAWN_SAC_CP = 100  # pure pawn sacrifices...
BRILLIANT_PAWN_SAC_MAX_RATING = 1600  # ...count only for newer players
GREAT_ONLY_GOOD_GAP_EP = 0.15  # second-best line at least this much worse
WINNING_EP_THRESHOLD = 0.75  # "winning position" in the EP model
MISS_OPPONENT_EP_LOSS = 0.20  # opponent's previous move was a blunder


def _brilliant_sacrifice_threshold(player_rating: Optional[int]) -> int:
    """Minimum sacrificed material for Brilliant, rating-aware."""
    if _clamp_rating(player_rating) < BRILLIANT_PAWN_SAC_MAX_RATING:
        return BRILLIANT_PAWN_SAC_CP
    return BRILLIANT_MIN_SACRIFICE_CP


def _least_valuable_attacker(
    board: chess.Board, square: int, color: bool
) -> Optional[Tuple[int, int]]:
    """(piece_type, from_square) of `color`'s least valuable attacker of
    `square`, or None. Seed for the swap-algorithm SEE below."""
    for piece_type in (
        chess.PAWN,
        chess.KNIGHT,
        chess.BISHOP,
        chess.ROOK,
        chess.QUEEN,
        chess.KING,
    ):
        candidates = board.attackers(color, square) & board.pieces(piece_type, color)
        if candidates:
            return piece_type, min(candidates)
    return None


def _see_capture_gain(board: chess.Board, square: int, color: bool) -> int:
    """Simplified static exchange evaluation (swap algorithm, no x-rays).

    Returns the material `color` can win by starting the capture sequence on
    `square`, assuming both sides capture with their least valuable attacker
    and either side may decline a losing exchange. Mutates and restores
    `board` (push/pop). Good enough to recognise the classic sacrifice
    shapes (Bxf7+, exchange sacs, hanging pieces); it is a v1 approximation,
    not a full SEE.
    """
    attacker = _least_valuable_attacker(board, square, color)
    if attacker is None:
        return 0
    piece_type, from_square = attacker
    target = board.piece_at(square)
    captured = _PIECE_VALUES.get(target.piece_type, 0) if target is not None else 0

    move = chess.Move(from_square, square)
    if piece_type == chess.PAWN and chess.square_rank(square) in (0, 7):
        move = chess.Move(from_square, square, promotion=chess.QUEEN)
    if move not in board.legal_moves:
        return 0

    board.push(move)
    try:
        gain = captured - _see_capture_gain(board, square, not color)
    finally:
        board.pop()
    return max(0, gain)


def _sacrifice_material(
    board_before: chess.Board, move: chess.Move, board_after: chess.Board
) -> int:
    """Material the played move gives up, in cp (0 = not a sacrifice).

    Net = value captured by the move - what the opponent can win by starting
    a capture sequence on the destination square. A fair trade nets 0, a
    bishop sac on f7 nets ~230, an exchange sacrifice ~180, a hanging queen
    its full value. Limitation: only sacrifices on the destination square
    are seen — moving a defender away from a piece hanging elsewhere is not
    detected (documented v1 gap).
    """
    target = board_before.piece_at(move.to_square)
    captured = _PIECE_VALUES.get(target.piece_type, 0) if target is not None else 0
    risk = _see_capture_gain(board_after.copy(), move.to_square, board_after.turn)
    return max(0, risk - captured)


class GameAnalyzer:
    """Analyzes chess games to find and explain mistakes."""
    
    def __init__(
        self,
        engine: StockfishEngine,
        explainer: LLMExplainer,
        blunder_threshold: float = 100,
        book_lookup: Optional[Callable[[chess.Board, chess.Move], bool]] = None,
        multipv: int = 1,
    ):
        """
        Initialize game analyzer.

        Args:
            engine: StockfishEngine instance (already initialized)
            explainer: LLMExplainer implementation (e.g., OpenAIExplainer)
            blunder_threshold: Centipawn drop to classify as blunder (default: 100)
            book_lookup: Optional callable (board, move) -> bool telling whether
                the move is opening theory for the position. Injected by the
                callers (services.opening_book.is_book_move) so this module
                stays free of DB dependencies and tests can use a fake. When
                None, no move is ever classified "book".
            multipv: Engine MultiPV width per position. 1 (default) keeps the
                historical single-PV cost; 2 lets the Great-move check see
                the second-best line ("only good move"). Callers that only
                consume mistake/blunder (opponent analysis) keep 1 to avoid
                doubling their Stockfish bill; the review path uses 2.
        """
        self.engine = engine
        self.explainer = explainer
        self.blunder_threshold = blunder_threshold
        self.book_lookup = book_lookup
        self.multipv = max(1, int(multipv))

    def _parse_game(self, pgn_string: str) -> chess.pgn.Game:
        """Parse and validate a PGN string."""
        pgn = StringIO(pgn_string)
        game = chess.pgn.read_game(pgn)
        if not game:
            raise ValueError("Invalid PGN: could not parse game")
        return game

    def _score_for_mover(
        self,
        evaluation: Evaluation,
        turn_color: str,
        is_after_move: bool = False,
    ) -> int:
        """
        Normalize an engine score to the side who made the move.

        The engine wrapper returns scores from the current side-to-move's
        perspective. After a move is played, the turn flips, so we negate the
        score to keep both before/after values in the mover's frame.
        """
        del turn_color  # Included for API clarity and future white-centric scoring changes.
        score_cp = evaluation.score_cp
        if is_after_move:
            score_cp = -score_cp
        return int(round(score_cp))

    def _compute_cp_loss(
        self,
        eval_before: Evaluation,
        eval_after: Evaluation,
        turn_color: str,
    ) -> int:
        """Compute non-negative centipawn loss for the side that just moved."""
        before_cp = self._score_for_mover(eval_before, turn_color)
        after_cp = self._score_for_mover(eval_after, turn_color, is_after_move=True)
        return max(0, before_cp - after_cp)

    def classify_move(
        self,
        eval_before: Evaluation,
        eval_after: Evaluation,
        turn_color: str,
        is_book_move: bool = False,
        player_rating: Optional[int] = None,
        move_uci: str = "",
        blunder_consequence: bool = False,
        delivers_mate: bool = False,
        sacrifice_cp: int = 0,
        opponent_prev_ep_loss: Optional[float] = None,
    ) -> str:
        """Classify a move with Chess.com's Expected Points model.

        Precedence mirrors Chess.com: Book is an identity fact, then the
        special labels (Brilliant → Great → Miss) sit outside the EP bands,
        then Best (the engine's top choice) and the EP-loss bands. See the
        module constants for the special-label rules and their caveats.

        `blunder_consequence` carries the 2023 rule that a Blunder must also
        lose material or allow checkmate; `delivers_mate` short-circuits
        mating moves, whose terminal position has no engine score;
        `sacrifice_cp` is the material the move gives up (SEE-based);
        `opponent_prev_ep_loss` is the previous ply's EP loss, used for Miss.
        """
        if is_book_move:
            return "book"
        if delivers_mate:
            return "best"

        best_cp = self._score_for_mover(eval_before, turn_color)
        played_cp = self._score_for_mover(eval_after, turn_color, is_after_move=True)
        ep_best = expected_points(best_cp, player_rating)
        ep_played = expected_points(played_cp, player_rating)
        ep_loss = max(0.0, ep_best - ep_played)

        best_uci = eval_before.best_move_uci or ""
        is_top = bool(move_uci and best_uci and move_uci == best_uci)
        near_best = is_top or ep_loss <= EP_EXCELLENT_MAX

        # Brilliant: best/nearly-best good piece sacrifice that leaves a
        # playable position and was not already completely winning.
        if (
            near_best
            and sacrifice_cp >= _brilliant_sacrifice_threshold(player_rating)
            and ep_best <= BRILLIANT_MAX_EP_BEFORE
            and ep_played >= BRILLIANT_MIN_EP_AFTER
        ):
            return "brilliant"

        # Great: the only good move — the second-best line is clearly worse.
        if near_best and eval_before.second_best_cp is not None:
            second_ep = expected_points(eval_before.second_best_cp, player_rating)
            if ep_best - second_ep >= GREAT_ONLY_GOOD_GAP_EP:
                return "great"

        # Miss: failed to convert a winning chance the opponent handed over
        # (or a forced mate slipped) and the position is no longer winning.
        missed_mate = (
            eval_before.mate is not None
            and eval_before.mate > 0
            and not (eval_after.mate is not None and eval_after.mate < 0)
        )
        opponent_gift = (
            opponent_prev_ep_loss is not None
            and opponent_prev_ep_loss >= MISS_OPPONENT_EP_LOSS
        )
        if (
            (opponent_gift or missed_mate)
            and ep_best >= WINNING_EP_THRESHOLD
            and ep_played < WINNING_EP_THRESHOLD
        ):
            return "miss"

        if is_top:
            return "best"

        if ep_loss <= EP_EXCELLENT_MAX:
            return "excellent"
        if ep_loss <= EP_GOOD_MAX:
            return "good"
        if ep_loss <= EP_INACCURACY_MAX:
            return "inaccuracy"
        if ep_loss <= EP_MISTAKE_MAX:
            return "mistake"
        return "blunder" if blunder_consequence else "mistake"

    def expected_points_loss(
        self,
        eval_before: Evaluation,
        eval_after: Evaluation,
        turn_color: str,
        player_rating: Optional[int] = None,
    ) -> float:
        """EP of the best line minus EP of the played line, mover's POV."""
        best_cp = self._score_for_mover(eval_before, turn_color)
        played_cp = self._score_for_mover(eval_after, turn_color, is_after_move=True)
        return max(
            0.0,
            expected_points(best_cp, player_rating)
            - expected_points(played_cp, player_rating),
        )

    def _loses_material_after_best_reply(
        self,
        board_after: chess.Board,
        eval_after: Evaluation,
        mover_color: str,
    ) -> bool:
        """Approximate "loses material" for the 2023 blunder rule.

        Applies the opponent's engine-best reply to the position after the
        played move and compares static material from the mover's POV. This
        catches hanging pieces and bad captures; it is a one-ply
        approximation until the stage-3 sacrifice/SEE machinery lands.
        """
        reply_uci = eval_after.best_move_uci or ""
        if not reply_uci:
            return False
        try:
            reply = chess.Move.from_uci(reply_uci)
        except ValueError:
            return False
        if reply not in board_after.legal_moves:
            return False

        mover_is_white = mover_color == "white"
        before = _material_for(board_after, mover_is_white)
        probe = board_after.copy()
        probe.push(reply)
        after = _material_for(probe, mover_is_white)
        return after < before

    @staticmethod
    def _ratings_from_headers(game: chess.pgn.Game) -> Dict[str, int]:
        """Per-color ratings from PGN Elo headers, defaulted when absent."""
        ratings: Dict[str, int] = {}
        for color, header in (("white", "WhiteElo"), ("black", "BlackElo")):
            raw = (game.headers.get(header) or "").strip()
            try:
                ratings[color] = _clamp_rating(int(raw))
            except ValueError:
                ratings[color] = DEFAULT_PLAYER_RATING
        return ratings

    def _build_mistake(
        self,
        fen_before: str,
        fen_after: str,
        move_number: int,
        move_color: str,
        move_san: str,
        eval_before: Evaluation,
        eval_after: Evaluation,
        classification: Optional[str] = None,
    ) -> Mistake:
        """Build the existing Mistake object for explanation generation."""
        eval_after_for_mover = Evaluation(
            score_cp=-eval_after.score_cp,
            best_move_uci=eval_after.best_move_uci,
            best_move_san=eval_after.best_move_san,
            mate=-eval_after.mate if eval_after.mate is not None else None,
        )
        eval_drop_cp = eval_before.score_cp - eval_after_for_mover.score_cp

        return Mistake(
            position_before_move=Position(
                fen=fen_before,
                move_number=move_number,
                player_color=move_color,
            ),
            position_after_move=Position(
                fen=fen_after,
                move_number=move_number,
                player_color=move_color,
            ),
            move_played=move_san,
            evaluation_before=eval_before,
            evaluation_after=eval_after_for_mover,
            eval_drop_cp=eval_drop_cp,
            classification=classification,
        )

    def analyze_full_game(
        self,
        pgn_string: str,
        target_color: str = "both",
        include_explanations: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Analyze every move in a PGN and return a JSON-ready review list.

        Each entry includes the position, SAN, mover color, classification,
        cp loss and EP (expected points) loss. Mistakes and blunders include
        an explanation payload only when `include_explanations` is True.
        Classification follows Chess.com's Expected Points model (see the
        module constants) with the mover's PGN Elo header as the rating.
        """
        game = self._parse_game(pgn_string)
        board = game.board()
        ratings = self._ratings_from_headers(game)
        results: List[Dict[str, Any]] = [
            {
                "fen": chess.STARTING_FEN,
                "san": "Start",
                "color": "white",
                "classification": "book",
                "cp_loss": 0,
                "ep_loss": 0.0,
                "eval_cp": 0.0,
                "eval_mate": None,
                "best_move_san": None,
                "best_move_uci": None,
            }
        ]

        # The position after move N is exactly the position before move N+1.
        # Reusing the previous ply's "after" evaluation as this ply's "before"
        # evaluation halves the engine searches (N+1 instead of 2N) with
        # identical inputs -- previously every position was searched twice.
        previous_eval: Optional[Evaluation] = None
        # EP loss of the previous ply (always the opponent's move), for Miss.
        previous_ep_loss: Optional[float] = None

        # Contiguous opening book: once the game leaves the book it never
        # re-enters, and lookups stop for the rest of the game.
        in_book = True

        for node in game.mainline():
            move = node.move
            move_color = "white" if board.turn == chess.WHITE else "black"
            player_rating = ratings[move_color]

            fen_before = board.fen()
            move_number = board.fullmove_number
            move_san = board.san(move)

            if in_book and self.book_lookup is not None:
                is_book_move = bool(self.book_lookup(board, move))
            else:
                is_book_move = False
            if not is_book_move:
                in_book = False

            eval_before = (
                previous_eval
                if previous_eval is not None
                else self.engine.evaluate(board, multipv=self.multipv)
            )

            board.push(move)
            fen_after = board.fen()
            delivers_mate = board.is_checkmate()
            eval_after = self.engine.evaluate(board, multipv=self.multipv)
            previous_eval = eval_after

            if is_book_move:
                cp_loss = 0
                ep_loss = 0.0
                blunder_consequence = False
                sacrifice_cp = 0
            else:
                cp_loss = self._compute_cp_loss(eval_before, eval_after, move_color)
                ep_loss = self.expected_points_loss(
                    eval_before, eval_after, move_color, player_rating
                )
                allows_mate = eval_after.mate is not None and eval_after.mate > 0
                blunder_consequence = (
                    allows_mate
                    or self._loses_material_after_best_reply(
                        board, eval_after, move_color
                    )
                )
                sacrifice_cp = (
                    0
                    if delivers_mate
                    else _sacrifice_material(chess.Board(fen_before), move, board)
                )

            # The next ply's Miss check needs THIS ply's EP loss, including
            # for plies filtered out of the response by target_color.
            opponent_prev_ep_loss = previous_ep_loss
            previous_ep_loss = ep_loss

            if target_color != "both" and target_color != move_color:
                continue

            classification = self.classify_move(
                eval_before,
                eval_after,
                move_color,
                is_book_move=is_book_move,
                player_rating=player_rating,
                move_uci=move.uci(),
                blunder_consequence=blunder_consequence,
                delivers_mate=delivers_mate,
                sacrifice_cp=sacrifice_cp,
                opponent_prev_ep_loss=opponent_prev_ep_loss,
            )

            turn_entry: Dict[str, Any] = {
                "fen": fen_after,
                "fen_before": fen_before,
                "move_number": move_number,
                "san": move_san,
                "color": move_color,
                "classification": classification,
                "cp_loss": cp_loss,
                "ep_loss": round(ep_loss, 4),
                # Position evaluation from White's perspective (positive =
                # White better). `eval_after` is scored from the side to move
                # after the move, which is the opponent of `move_color`.
                "eval_cp": round(
                    eval_after.score_cp if move_color == "black" else -eval_after.score_cp,
                    1,
                ),
                "eval_mate": (
                    eval_after.mate if move_color == "black" else -eval_after.mate
                ) if eval_after.mate is not None else None,
                "best_move_san": (
                    eval_before.best_move_san
                    if eval_before.best_move_san and eval_before.best_move_san != "(none)"
                    else None
                ),
                "best_move_uci": (
                    eval_before.best_move_uci
                    if eval_before.best_move_uci and eval_before.best_move_uci != "(none)"
                    else None
                ),
            }

            if include_explanations and classification in {"mistake", "blunder"}:
                mistake = self._build_mistake(
                    fen_before=fen_before,
                    fen_after=fen_after,
                    move_number=move_number,
                    move_color=move_color,
                    move_san=move_san,
                    eval_before=eval_before,
                    eval_after=eval_after,
                    classification=classification,
                )
                explanation = self.explainer.explain_mistake(mistake)
                turn_entry["explanation"] = asdict(explanation)

            results.append(turn_entry)

        return results

    def analyze_pgn(self, pgn_string: str, target_color: str = "both") -> List[AnalyzedMistake]:
        """
        Backward-compatible mistake-only analysis based on the full game review.

        Returns only moves classified as mistake/blunder with the existing
        AnalyzedMistake schema so older callers continue to work.
        """
        game = self._parse_game(pgn_string)
        board = game.board()
        ratings = self._ratings_from_headers(game)
        mistakes: List[AnalyzedMistake] = []

        # See analyze_full_game: carry the previous ply's "after" evaluation
        # forward instead of searching every position twice, and keep the
        # contiguous opening-book state for the EP classifier.
        previous_eval: Optional[Evaluation] = None
        previous_ep_loss: Optional[float] = None
        in_book = True

        for node in game.mainline():
            move = node.move
            move_color = "white" if board.turn == chess.WHITE else "black"
            player_rating = ratings[move_color]

            fen_before = board.fen()
            move_number = board.fullmove_number
            move_san = board.san(move)

            if in_book and self.book_lookup is not None:
                is_book_move = bool(self.book_lookup(board, move))
            else:
                is_book_move = False
            if not is_book_move:
                in_book = False

            eval_before = (
                previous_eval
                if previous_eval is not None
                else self.engine.evaluate(board, multipv=self.multipv)
            )

            board.push(move)
            fen_after = board.fen()
            delivers_mate = board.is_checkmate()
            eval_after = self.engine.evaluate(board, multipv=self.multipv)
            previous_eval = eval_after

            if is_book_move:
                ep_loss = 0.0
                blunder_consequence = False
                sacrifice_cp = 0
            else:
                ep_loss = self.expected_points_loss(
                    eval_before, eval_after, move_color, player_rating
                )
                allows_mate = eval_after.mate is not None and eval_after.mate > 0
                blunder_consequence = (
                    allows_mate
                    or self._loses_material_after_best_reply(
                        board, eval_after, move_color
                    )
                )
                sacrifice_cp = (
                    0
                    if delivers_mate
                    else _sacrifice_material(chess.Board(fen_before), move, board)
                )

            opponent_prev_ep_loss = previous_ep_loss
            previous_ep_loss = ep_loss

            if target_color != "both" and target_color != move_color:
                continue

            classification = self.classify_move(
                eval_before,
                eval_after,
                move_color,
                is_book_move=is_book_move,
                player_rating=player_rating,
                move_uci=move.uci(),
                blunder_consequence=blunder_consequence,
                delivers_mate=delivers_mate,
                sacrifice_cp=sacrifice_cp,
                opponent_prev_ep_loss=opponent_prev_ep_loss,
            )

            if classification not in {"mistake", "blunder"}:
                continue

            mistake = self._build_mistake(
                fen_before=fen_before,
                fen_after=fen_after,
                move_number=move_number,
                move_color=move_color,
                move_san=move_san,
                eval_before=eval_before,
                eval_after=eval_after,
                classification=classification,
            )
            explanation = self.explainer.explain_mistake(mistake)
            mistakes.append(
                AnalyzedMistake(
                    the_mistake=mistake,
                    the_explanation=explanation,
                )
            )

        return mistakes
