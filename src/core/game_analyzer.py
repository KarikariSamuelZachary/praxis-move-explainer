"""
Core game analyzer that orchestrates chess engine and LLM.
"""
import chess
import chess.pgn
from io import StringIO
from typing import List

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
    
    def analyze_pgn(self, pgn_string: str, target_color: str = 'both') -> List[AnalyzedMistake]:
        """
        Analyze a chess game from PGN and find mistakes.
        
        Args:
            pgn_string: PGN text of the game
            target_color: Which player to analyze ('white', 'black', or 'both')
            
        Returns:
            List of AnalyzedMistake objects
        """
        # Parse PGN
        pgn = StringIO(pgn_string)
        game = chess.pgn.read_game(pgn)
        
        if not game:
            raise ValueError("Invalid PGN: could not parse game")
        
        mistakes = []
        board = game.board()
        
        # Iterate through all moves
        for node in game.mainline():
            move = node.move
            
            # Determine whose move this is (BEFORE playing it)
            move_color = 'white' if board.turn == chess.WHITE else 'black'
            
            # Skip if not analyzing this color
            if target_color != 'both' and target_color != move_color:
                board.push(move)
                continue
            
            # Evaluate position BEFORE the move
            eval_before = self.engine.evaluate(board)
            
            # Capture position state before move
            fen_before = board.fen()
            move_number = board.fullmove_number
            
            # Convert move to SAN BEFORE playing it
            move_san = board.san(move)
            
            # Play the move
            board.push(move)
            
            # Evaluate position AFTER the move
            eval_after = self.engine.evaluate(board)
            
            # Flip eval_after to match perspective (it's now opponent's turn)
            eval_after_flipped = Evaluation(
                score_cp=-eval_after.score_cp,
                best_move_uci=eval_after.best_move_uci,
                best_move_san=eval_after.best_move_san
            )
            
            # Check if this is a blunder
            if self.engine.is_blunder(eval_before, eval_after_flipped, self.blunder_threshold):
                # Capture position after move
                fen_after = board.fen()
                
                # Build Position objects
                position_before = Position(
                    fen=fen_before,
                    move_number=move_number,
                    player_color=move_color
                )
                
                position_after = Position(
                    fen=fen_after,
                    move_number=move_number,
                    player_color=move_color
                )
                
                # Build Mistake object
                mistake = Mistake(
                    position_before_move=position_before,
                    position_after_move=position_after,
                    move_played=move_san,
                    evaluation_before=eval_before,
                    evaluation_after=eval_after_flipped,
                    eval_drop_cp=eval_before.score_cp - eval_after_flipped.score_cp
                )
                
                # Get explanation from LLM
                explanation = self.explainer.explain_mistake(mistake)
                
                # Create AnalyzedMistake
                analyzed_mistake = AnalyzedMistake(
                    the_mistake=mistake,
                    the_explanation=explanation
                )
                
                mistakes.append(analyzed_mistake)
        
        return mistakes