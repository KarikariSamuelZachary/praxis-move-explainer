"""
Stockfish engine wrapper for chess position analysis.
Detects blunders and evaluates positions.
"""
import chess
import chess.engine
from typing import Optional
from pathlib import Path
from schemas.models import Evaluation

class StockfishEngine:
    
    def __init__(self, stockfish_path: Optional[str] = None, depth: int = 20):
        self.stockfish_path = stockfish_path or "stockfish"
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
    
    def evaluate(self, board: chess.Board) -> Evaluation:
        if not self.engine:
            raise RuntimeError("Engine not started. Use context manager or call start()")
        
        info = self.engine.analyse(board, chess.engine.Limit(depth=self.depth))
        
        score = info.get("score")
        pv = info.get("pv", [])
        
        # Convert score to centipawns from current player's perspective
        if score:
            cp_score = self._score_to_centipawns(score, board.turn)
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
    
    def _score_to_centipawns(self, score: chess.engine.Score, turn: chess.Color) -> float:
        # Get score relative to white
        if score.is_mate():
            # Mate score: use large number
            mate_in = score.relative.mate()
            cp = 10000 if mate_in > 0 else -10000
        else:
            cp = score.relative.score()
        
        # Flip if black to move
        if turn == chess.BLACK:
            cp = -cp if cp is not None else 0
        
        return cp or 0
    
    def is_blunder(self, eval_before: Evaluation, eval_after: Evaluation, threshold: float = 100) -> bool:
        eval_drop = eval_before.score_cp - eval_after.score_cp
        return eval_drop >= threshold