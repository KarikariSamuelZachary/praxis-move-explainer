"""
Single-callable reranker for the Engine Sparring persona pipeline.

This module TIES TOGETHER the layers that were each built and proven in
isolation, into one function a future API endpoint can call:

    suggest()  (StockfishEngine, raw MultiPV list)
      -> canonicalize_by_score()   (persona_weights: cp-descending contract)
      -> compute_style_scores()    (persona_features: per-candidate StyleScores)
      -> game_phase()              (persona_bounds: computed ONCE per position)
      -> <selected persona>_score() (persona_weights: raw score in [-1, 1])
      -> persona_adjusted_score()  (persona_weights: norm + 100*bias, floored)
      -> sorted descending by final

Each upstream contract is consumed exactly as its owner documents it; nothing
is reimplemented here. See each helper's docstring for the arguments for that
contract (trust curve, phase gating, demotion floor, MultiPV canonicalization).

SCOPE -- MOVE SELECTION ONLY
============================
This module deliberately contains NO session/game-state logic, NO request/
response schemas, and NO router/endpoint code. It knows nothing about users,
games, or history: give it a board and a persona, it returns ranked moves.
Session tracking is a separate, later task. A caller that wants to reuse one
engine across many requests must manage that itself (see ENGINE LIFECYCLE).

PERSONA SELECTION MECHANISM (decision + justification)
======================================================
The endpoint will receive the persona choice from the frontend as a STRING,
not as a Python function reference. The mechanism is a `str`-based Enum
(PersonaType) plus an explicit dict of {PersonaType: scorer}, resolved through
resolve_persona():

  * `PersonaType(str, Enum)` makes the enum values directly comparable to the
    raw strings the frontend sends ("attacker" == PersonaType.ATTACKER), so a
    FastAPI/Pydantic endpoint can use PersonaType as a parameter type and get
    validation plus OpenAPI enum documentation for free.
  * Untrusted input is validated LOUDLY and CHEAPLY: resolve_persona() raises
    ValueError naming the bad value and the valid choices, BEFORE any engine
    is started -- a bad request fails fast and fails clearly, never silently
    defaulting to some persona.
  * Input is normalized (stripped, lowercased) so "ATTACKER" and " attacker "
    are accepted; anything else is rejected. Normalization is deliberate and
    documented -- it is NOT silent defaulting.
  * Extension: adding a persona means adding one Enum member, one dict
    entry, and nothing else. The dict (not a match statement) is the single
    dispatch point.

ENGINE LIFECYCLE (decision + justification)
===========================================
rerank_moves() spawns a FRESH StockfishEngine per call via the class's own
context manager (start/close handled by __enter__/__exit__, the same pattern
the existing test harnesses use), NOT the module-level stockfish singleton.

Why not the singleton: configure_strength()'s UCI setoptions PERSIST on the
subprocess (documented in stockfish_engine.py), and that singleton is shared
with the sparring safety check. Configuring Elo/skill on the shared engine
would leak that strength setting into the safety check's later evaluations --
exactly the stale-setting bug class configure_strength() documents itself.
A fresh engine per call is stateless and safe; the cost (~0.3-0.7s process
spawn + UCI handshake) is a known, deliberate tradeoff until a later task
decides on pooling with per-request strength isolation.

OUTPUT CONTRACT
===============
Each returned entry is a plain dict with keys consistent with the established
harness/schema naming ("uci", "san", "score_cp" as suggest() defines them):

    {"uci": ..., "san": ..., "score_cp": ...,       # engine's raw numbers
     "engine_norm_cp": ...,                          # score_cp - best score_cp
     "persona_score": ...,                           # selected persona's raw [-1, 1]
     "persona_final_cp": ...}                        # persona_adjusted_score() output

The list is sorted by persona_final_cp descending (STABLE: persona ties keep
canonical engine order -- a persona tie is genuinely unrankable by the bias,
so the engine's own preference stays in charge, same rule canonicalize_by_score
applies to cp ties).

KNOWN LIMITATION (inherited, not introduced): mate-score norms distort
trust(math) whenever a candidate's score is a coerced +/-10000 mate score --
documented as the MATE-SCORE NORM DISTORTION entry on engine_trust(). This
module adds no mate-distance handling of its own.
"""
import chess
from enum import Enum
from typing import Callable, Optional, Union

from engines.stockfish_engine import StockfishEngine, configure_strength
from services.persona_bounds import game_phase
from services.persona_features import compute_style_scores
from services.persona_weights import (
    canonicalize_by_score,
    defender_score,
    attacker_score,
    gambiter_score,
    positional_score,
    persona_adjusted_score,
    sacrificer_score,
)


class PersonaType(str, Enum):
    """The selectable sparring personas (the string IS the API value)."""

    ATTACKER = "attacker"
    SACRIFICER = "sacrificer"
    DEFENDER = "defender"
    POSITIONAL = "positional"
    GAMBITER = "gambiter"


# The single dispatch point. One entry per PersonaType; adding a persona means
# adding the weight function's entry here (and the enum member above).
_PERSONA_SCORERS: dict[PersonaType, Callable] = {
    PersonaType.ATTACKER: attacker_score,
    PersonaType.SACRIFICER: sacrificer_score,
    PersonaType.DEFENDER: defender_score,
    PersonaType.POSITIONAL: positional_score,
    PersonaType.GAMBITER: gambiter_score,
}


def resolve_persona(persona: Union[PersonaType, str]) -> PersonaType:
    """Validate/normalize an untrusted persona selector into a PersonaType.

    Accepts a PersonaType (passthrough) or a string, which is stripped and
    lowercased before lookup (so "ATTACKER" and " attacker " both resolve).
    Anything else raises ValueError naming the received value and the valid
    choices -- loud, specific, and cheap: callers (including rerank_moves)
    invoke this BEFORE any engine work so a bad request never pays for an
    engine start.
    """
    if isinstance(persona, PersonaType):
        return persona
    if isinstance(persona, str):
        try:
            return PersonaType(persona.strip().lower())
        except ValueError:
            valid = ", ".join(p.value for p in PersonaType)
            raise ValueError(
                f"unknown persona {persona!r}; valid personas: {valid}"
            ) from None
    raise ValueError(
        f"persona must be a PersonaType or string; got {persona!r} "
        f"(type {type(persona).__name__})"
    )


def rerank_moves(
    board: chess.Board,
    persona: Union[PersonaType, str],
    num_moves: int = 5,
    time_limit: Optional[float] = None,
    elo: Optional[int] = None,
    skill_level: Optional[int] = None,
) -> list[dict]:
    """Return the engine's top candidate moves RERANKED by one persona.

    The full pipeline (see the module docstring) in one call:

      1. persona validated via resolve_persona() -- BEFORE the engine starts,
         so an invalid persona fails fast and cheap;
      2. fresh StockfishEngine started (context manager guarantees close);
         configure_strength() applied only when elo/skill_level is given --
         its own validation (e.g. Elo outside the advertised UCI range) is
         intentionally allowed to propagate uncaught;
      3. suggest() -> canonicalize_by_score() -- the mandatory input-adapter
         contract: MultiPV list order is not trusted as a ranking;
      4. game_phase() computed ONCE for the position (it depends only on the
         board before the move, which is shared by every candidate);
      5. per candidate, in canonical order: compute_style_scores(),
         selected persona's scorer, persona_adjusted_score(norm, score, phase);
      6. returned sorted by persona_final_cp descending, stable on persona
         ties (canonical engine order wins -- see the OUTPUT CONTRACT).

    Terminal positions (checkmate/stalemate) return [] (suggest() already
    does; this function passes that through rather than erroring).
    """
    selected = resolve_persona(persona)
    scorer = _PERSONA_SCORERS[selected]

    with StockfishEngine() as engine:
        if elo is not None or skill_level is not None:
            configure_strength(engine.engine, elo=elo, skill_level=skill_level)
        suggestions = canonicalize_by_score(
            engine.suggest(board, num_moves=num_moves, time_limit=time_limit)
        )

    if not suggestions:
        return []

    phase = game_phase(board)
    best_cp = suggestions[0]["score_cp"]

    rows = []
    for index, suggestion in enumerate(suggestions):
        move = chess.Move.from_uci(suggestion["uci"])
        scores, _ = compute_style_scores(board, move)
        engine_norm_cp = suggestion["score_cp"] - best_cp
        persona_score = scorer(scores, board)
        rows.append({
            "uci": suggestion["uci"],
            "san": suggestion["san"],
            "score_cp": suggestion["score_cp"],
            "engine_norm_cp": engine_norm_cp,
            "persona_score": persona_score,
            "persona_final_cp": persona_adjusted_score(
                engine_norm_cp, persona_score, phase
            ),
            # Canonical rank is kept ONLY as the stable-sort tie-breaker below;
            # it is not part of the returned dict (the output contract is the
            # persona ranking, and the ordering already encodes it).
            "_rank": index,
        })

    rows.sort(key=lambda row: (-row["persona_final_cp"], row["_rank"]))
    for row in rows:
        del row["_rank"]
    return rows


def best_persona_move(
    board: chess.Board,
    persona: Union[PersonaType, str],
    num_moves: int = 5,
    time_limit: Optional[float] = None,
    elo: Optional[int] = None,
    skill_level: Optional[int] = None,
) -> Optional[dict]:
    """The single move the selected persona would play: rerank_moves()[0].

    Returns None for a terminal position (checkmate/stalemate), mirroring
    rerank_moves()'s empty list, so a single-move endpoint can map that to
    its own "no move available" response instead of catching an IndexError.
    All other arguments pass through to rerank_moves() unchanged.
    """
    ranked = rerank_moves(
        board,
        persona,
        num_moves=num_moves,
        time_limit=time_limit,
        elo=elo,
        skill_level=skill_level,
    )
    return ranked[0] if ranked else None
