"""
Core game analyzer that orchestrates chess engine and LLM.
"""
from dataclasses import asdict
import chess
import chess.pgn
from io import StringIO
from typing import Any, Dict, List, Optional

from schemas.models import Position, Evaluation, Mistake, AnalyzedMistake
from engines.stockfish_engine import StockfishEngine
from llms.base import LLMExplainer


class GameAnalyzer:
    """Analyzes chess games to find and explain mistakes."""
    
    def __init__(self, engine: StockfishEngine, explainer: LLMExplainer, blunder_threshold: float = 100):
        """
        Initialize game analyzer.
        
        Args:
            engine: StockfishEngine instance (already initialized)
            explainer: LLMExplainer implementation (e.g., OpenAIExplainer)
            blunder_threshold: Centipawn drop to classify as blunder (default: 100)
        """
        self.engine = engine
        self.explainer = explainer
        self.blunder_threshold = blunder_threshold

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
    ) -> str:
        """
        Classify a move by centipawn loss.

        Mate scores are already normalized to +/-10000 by the engine wrapper,
        so the centipawn math stays numeric and safe.
        """
        if is_book_move:
            return "book"

        cp_loss = self._compute_cp_loss(eval_before, eval_after, turn_color)
        if cp_loss <= 10:
            return "best"
        if cp_loss <= 30:
            return "excellent"
        if cp_loss <= 50:
            return "good"
        if cp_loss <= 100:
            return "inaccuracy"
        if cp_loss <= 300:
            return "mistake"
        return "blunder"

    def _build_mistake(
        self,
        fen_before: str,
        fen_after: str,
        move_number: int,
        move_color: str,
        move_san: str,
        eval_before: Evaluation,
        eval_after: Evaluation,
    ) -> Mistake:
        """Build the existing Mistake object for explanation generation."""
        eval_after_for_mover = Evaluation(
            score_cp=-eval_after.score_cp,
            best_move_uci=eval_after.best_move_uci,
            best_move_san=eval_after.best_move_san,
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
        )

    def analyze_full_game(
        self,
        pgn_string: str,
        target_color: str = "both",
    ) -> List[Dict[str, Any]]:
        """
        Analyze every move in a PGN and return a JSON-ready review list.

        Each entry includes the position before the move, SAN, mover color,
        classification, and centipawn loss. Mistakes and blunders also include
        an explanation payload from the configured LLM.
        """
        game = self._parse_game(pgn_string)
        board = game.board()
        results: List[Dict[str, Any]] = []

        for ply_index, node in enumerate(game.mainline()):
            move = node.move
            move_color = "white" if board.turn == chess.WHITE else "black"

            fen_before = board.fen()
            move_number = board.fullmove_number
            move_san = board.san(move)
            eval_before = self.engine.evaluate(board)

            board.push(move)
            fen_after = board.fen()
            eval_after = self.engine.evaluate(board)

            if target_color != "both" and target_color != move_color:
                continue

            is_book_move = ply_index < 10
            cp_loss = 0 if is_book_move else self._compute_cp_loss(eval_before, eval_after, move_color)
            classification = self.classify_move(
                eval_before,
                eval_after,
                move_color,
                is_book_move=is_book_move,
            )

            turn_entry: Dict[str, Any] = {
                "fen": fen_before,
                "san": move_san,
                "color": move_color,
                "classification": classification,
                "cp_loss": cp_loss,
            }

            if classification in {"mistake", "blunder"}:
                mistake = self._build_mistake(
                    fen_before=fen_before,
                    fen_after=fen_after,
                    move_number=move_number,
                    move_color=move_color,
                    move_san=move_san,
                    eval_before=eval_before,
                    eval_after=eval_after,
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
        mistakes: List[AnalyzedMistake] = []

        for ply_index, node in enumerate(game.mainline()):
            move = node.move
            move_color = "white" if board.turn == chess.WHITE else "black"

            fen_before = board.fen()
            move_number = board.fullmove_number
            move_san = board.san(move)
            eval_before = self.engine.evaluate(board)

            board.push(move)
            fen_after = board.fen()
            eval_after = self.engine.evaluate(board)

            if target_color != "both" and target_color != move_color:
                continue

            classification = self.classify_move(
                eval_before,
                eval_after,
                move_color,
                is_book_move=ply_index < 10,
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
            )
            explanation = self.explainer.explain_mistake(mistake)
            mistakes.append(
                AnalyzedMistake(
                    the_mistake=mistake,
                    the_explanation=explanation,
                )
            )

        return mistakes
