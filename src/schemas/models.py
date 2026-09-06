from dataclasses import dataclass
from typing import Optional

@dataclass
class Position:
    fen:str
    move_number:int
    player_color:str

@dataclass
class Evaluation:
    score_cp:float
    best_move_uci:str
    best_move_san:str
    mate: Optional[int] = None

@dataclass
class Mistake:
    position_before_move:Position
    position_after_move:Position
    move_played:str
    evaluation_before:Evaluation
    evaluation_after:Evaluation
    eval_drop_cp:float

@dataclass
class Explanation:
    why_good:str
    why_failed:str
    concept_involved:str
    typical_pattern:str

@dataclass
class AnalyzedMistake:
    the_mistake:Mistake
    the_explanation:Explanation
