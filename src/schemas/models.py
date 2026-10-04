from dataclasses import dataclass, field
from typing import List, Optional

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
    # Score of the second-best MultiPV line (side-to-move POV, cp), when the
    # caller asked the engine for more than one PV. Feeds the "only good
    # move" Great-move check; None when unavailable (single-PV search).
    second_best_cp: Optional[float] = None
    # Engine continuation already returned with the score; used by the
    # blunder consequence check without issuing another search.
    principal_variation_uci: List[str] = field(default_factory=list)
    # Second MultiPV line (the "suggestion" when the played move is not best).
    second_best_move_uci: Optional[str] = None
    second_best_move_san: Optional[str] = None
    second_best_pv_uci: List[str] = field(default_factory=list)
    # Search telemetry. None when the engine did not report the field (e.g.
    # terminal positions return no nodes); deterministic-mode gating reads
    # these, and backstop detection must guard nodes is None before comparing.
    nodes: Optional[int] = None
    depth: Optional[int] = None
    nps: Optional[int] = None
    # True when a nodes limit was requested but the wall-clock backstop
    # stopped the search early: the result is not reproducible and must not
    # be cached or labelled.
    backstop_fired: bool = False

@dataclass
class Mistake:
    position_before_move:Position
    position_after_move:Position
    move_played:str
    evaluation_before:Evaluation
    evaluation_after:Evaluation
    eval_drop_cp:float
    # The classifier's label for this move ("mistake"/"blunder" today).
    # Optional so hand-built Mistake objects keep working; explainers
    # prefer it over re-deriving a label from centipawns.
    classification: Optional[str] = None

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
