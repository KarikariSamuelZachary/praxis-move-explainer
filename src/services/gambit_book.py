"""
Probabilistic gambit-book bypass for Engine Sparring personas.

A clean, standalone "check the gambit book, maybe return a move" utility:

    maybe_play_gambit(board, probability=SACRIFICER_OFFER_PROBABILITY) -> Optional[dict]

It consults the FEN-keyed classical-gambit lookup table
(services/data/gambit_openings.json -- see scripts/build_gambit_openings.py
for its provenance and validation) and, with probability `probability`,
returns one gambit offer for the CURRENT position. It returns None when
there is nothing to offer or the roll fails. It is deliberately
PERSONA-AGNOSTIC and never touches the reranker.

STEERING (why the endpoint calls steer_to_gambit, not this function)
===================================================================
maybe_play_gambit() is a pure RESPONDER: it can only return a gambit when
the live position is ALREADY an offer square of the book. From the game's
start that makes opening variety structurally impossible: the position
after 1.e4 has exactly ONE black-offered entry in the whole asset (Duras
Gambit, 1...f5), so every Sacrificer/Gambiter game vs 1.e4 opened with the
same f5, and 302 of the 345 offer positions have a pool of exactly one
anyway. The fix is steer_to_gambit(): a SECOND index built by replaying
every offer entry's full PGN ply by ply, so the persona can pick a random
gambit line compatible with the game so far and WALK toward its offer:

  * At each persona turn, the candidate pool is every offer-kind entry
    whose line passes through the CURRENT normalized position with the
    entry's offer still AHEAD of (or exactly at) this ply, AND whose
    offerer color is the side to move (the persona never steers the
    opponent into a gambit). One member is picked UNIFORMLY AT RANDOM --
    an offer playable right now is simply one candidate among the lines
    (it must NOT exclusively win: after 1.e4 the lone move-1 Duras offer
    would otherwise crowd out the other 100 lines and reproduce the same
    f5 every game -- the exact monotony steering exists to fix). A
    consequence, accepted by design: at an offer square the persona may
    steer PAST the available offer toward a different, later gambit.
  * The picked member's move is played. When the opponent leaves the
    line, the lookup simply finds whatever other lines pass through the
    new position (or nothing, falling through to the reranker).
  * After the offer lands, the entry's later plies are NOT indexed: the
    persona is done steering and returns to the persona reranker.

The probability semantics are identical to maybe_play_gambit's (validate
before lookup; one roll, only when a pool exists). Sacrificer and Gambiter
both call steer_to_gambit at their (equal) rate constants.

LOOKUP NORMALIZATION (the load-bearing detail)
==============================================
The stored FENs include the halfmove clock and fullmove number of the
CANONICAL line, which a live game's FEN will almost never share even when
the board position is identical. Every lookup therefore normalizes to the
first 4 FEN fields (board layout, active color, castling rights, en-passant
target) via _normalized_position_key(). That is the same 4-field position
convention already used across this codebase for FEN-keyed lookups
(opponent_traps.py's position_key, opponent_repertoire.py, repertoire_service.py,
opponent_game_analysis.py). Castling rights and the en-passant target are
part of the key on purpose: they change move legality, so two positions that
differ there must NOT share a book entry. Move counters are excluded on
purpose: they never affect legality.

MULTI-MATCH POSITIONS (decision + justification)
================================================
195 of the asset's 913 distinct normalized positions match MORE than one
named entry (transpositions and same-position-at-different-depths entries,
up to 17 matches). Selection is a two-stage rule:

  1. The candidate pool is ONLY entries with offer=True. Entries flagged
     offer=False are "Gambit Accepted/Declined..." continuation lines whose
     stored move is the defender's reply (or a deep line move), NOT a gambit
     offer the side to move could make -- returning one as "the gambit offer"
     would be wrong data, so they are excluded before the roll.
  2. Among the offer-kind pool, one entry is picked UNIFORMLY AT RANDOM.

Why uniform random rather than a deterministic preference (lowest ECO,
shortest name, ...): at a transposition-rich position the persona should be
able to offer ANY of the gambits book says are available -- variety is the
point of a sparring persona, a fixed preference would make Sacrificer
predictable and repeat the same gambit at every transposition, and uniform
random keeps the "maybe_play_gambit" contract parameter-free (no hidden
tuning knobs) while remaining fully deterministic under a seeded RNG in
tests.

PROBABILITY SEMANTICS
=====================
The single roll happens ONLY when the offer pool is non-empty: a position
with no gambit offer available never "burns" the probability check (same
principle as the no-match case). probability=p means: of the positions
where a gambit offer exists in book, 100% are offered (the live
SACRIFICER_OFFER_PROBABILITY). probability values below 1.0 make only that
share of in-book-offer positions get the offer; probability=1.0 turns the
function into the deterministic book lookup Gambiter needs; probability
outside [0, 1] is a programming error and raises ValueError loudly.
"""
import json
import random
import re
from pathlib import Path
from typing import Optional, Union

import chess

# The validated classical-gambit asset (1296 entries; 396 offer-kind).
DATA_PATH = Path(__file__).resolve().parent / "data" / "gambit_openings.json"

# Sacrificer's gambit-offer rate: of the positions where the book HAS a
# gambit offer for the side to move, offer one this often. Kept here
# (not in the persona layer) so both the endpoint wiring and future callers
# reference one number; callers pass it explicitly to stay self-documenting
# (the future Gambiter passes 1.0 as well).
#
# LIVE VALUE 1.0 = the Gambiter-equivalent mode: whenever the game sits
# exactly on a pre-offer square, Sacrificer plays the gambit. Raised from
# the original 0.17 after live observation: the bottleneck for gambit
# visibility is REACHING pre-offer squares (opponent cooperation + the
# reranker's own non-book moves), so a low roll rate made the persona's
# gambit character nearly invisible. To re-enable probabilistic sparring,
# lower this ONE constant (e.g. 0.5); nothing else changes.
SACRIFICER_OFFER_PROBABILITY = 1.0

# Gambiter's gambit-offer rate: ALWAYS offer at book squares -- the
# deterministic mode this function was designed for (see the Reusability
# contract in maybe_play_gambit's docstring). At probability=1.0 the single
# roll still HAPPENS but can never fail: _roll() returns uniform [0.0, 1.0)
# (random.random()'s documented interval), and the acceptance check is
# _roll() >= probability, so 1.0 is strictly greater than every possible
# roll -- the outcome is deterministic "offer" without special-casing the
# function.
GAMBITER_OFFER_PROBABILITY = 1.0

# Built once, on first use. Idempotent: the asset is a committed, validated
# static file, so rebuilding on a later call would only waste work.
_GAMBIT_INDEX: Optional[dict[str, list[dict]]] = None


def _normalized_position_key(board_or_fen: Union[chess.Board, str]) -> str:
    """First 4 FEN fields -- the codebase-wide position key.

    Same normalization convention as opponent_traps.position_key,
    opponent_repertoire._position_key, repertoire_service.normalize_fen and
    opponent_game_analysis._position_key (" ".join(fen.split()[:4])).
    Strips the halfmove clock + fullmove number so a live-game FEN matches
    the asset's canonical-line FEN whenever the PLAYABLE position matches
    (board, side to move, castling rights, en-passant target).
    """
    fen = board_or_fen.fen() if isinstance(board_or_fen, chess.Board) else board_or_fen
    return " ".join(fen.split()[:4])


def load_gambit_index() -> dict[str, list[dict]]:
    """The gambit lookup index: normalized 4-field FEN -> matching entries.

    Built once from gambit_openings.json on first call and cached at module
    level. Multiple entries CAN share a normalized position (transpositions,
    same position reached at different named depths) -- that is why the value
    is a LIST, and why maybe_play_gambit() has an explicit selection rule
    (see the module docstring). Raises loudly on a missing/unparseable asset:
    the file is a committed build product, so failing fast beats returning a
    silently-empty book.
    """
    global _GAMBIT_INDEX
    if _GAMBIT_INDEX is None:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        index: dict[str, list[dict]] = {}
        for entry in data["entries"]:
            index.setdefault(_normalized_position_key(entry["fen"]), []).append(entry)
        _GAMBIT_INDEX = index
    return _GAMBIT_INDEX


def _roll() -> float:
    """The probability-check die. Module-level seam so tests can force both
    outcomes AND count that a roll did or did not happen."""
    return random.random()


def _pick(pool: list[dict]) -> dict:
    """Uniform random selection from the offer pool (see module docstring)."""
    return random.choice(pool)


def maybe_play_gambit(
    board: chess.Board,
    probability: float = SACRIFICER_OFFER_PROBABILITY,
) -> Optional[dict]:
    """Maybe return a gambit-book move for `board`, else None.

    Returns None (without rolling -- see below) when the position has no
    gambit offer in the book. Otherwise rolls ONCE against `probability`:
    on success returns a dict describing the chosen offer

        {"uci": ..., "san": ..., "gambit_name": ..., "eco": ..., "pgn": ...}

    (naming consistent with persona_reranker's move-dict contract; pgn is
    the book line for context). On a failed roll, or when the position's
    matches contain no offer-kind entry, returns None.

    Match semantics: the lookup key is _normalized_position_key(board) --
    the first 4 FEN fields -- so two boards identical except for move
    counters resolve to the same entries (the counters never affect which
    moves are legal; castling rights and the en-passant target are kept in
    the key because they DO).

    Order of operations (both deliberate):
      * the probability validation happens BEFORE any lookup -- an invalid
        `probability` is a caller bug and must fail loudly even when the
        position is out of book;
      * the roll happens only when an offer EXISTS, so out-of-book
        positions never consume randomness and `probability` means
        exactly "share of in-book-offer positions that get offered".

    Reusability contract: this function never calls rerank_moves() or any
    persona scoring, and reads nothing but `board` -- with probability=1.0
    it becomes the pure "always play the book's offer" primitive. NOTE the
    Engine Sparring endpoint now uses steer_to_gambit() (a superset of this
    lookup that also steers pre-offer lines -- see the module docstring);
    this function remains the pure responder primitive.
    """
    if not 0.0 <= probability <= 1.0:
        raise ValueError(
            f"probability must be within [0, 1]; got {probability!r}"
        )

    matches = load_gambit_index().get(_normalized_position_key(board))
    if not matches:
        return None

    offers = [entry for entry in matches if entry["offer"]]
    if not offers:
        # Matches exist but they are all Accepted/Declined continuations:
        # nothing this function can offer, so no roll is burned (same
        # principle as the no-match case).
        return None

    if _roll() >= probability:
        return None

    entry = _pick(offers)
    move = chess.Move.from_uci(entry["uci"])
    if move not in board.legal_moves:
        # Unreachable for the validated asset (the 4-field key includes
        # castling rights + en-passant target, which pin move legality);
        # guarded anyway so a corrupted book can never emit an illegal
        # move -- it degrades to "no book move".
        return None

    return {
        "uci": entry["uci"],
        "san": entry["san"],
        "gambit_name": entry["name"],
        "eco": entry["eco"],
        "pgn": entry["pgn"],
    }


# ---------------------------------------------------------------------------
# STEERING: the ply-index that gives the personas opening variety.
#
# maybe_play_gambit() above only ever answers at positions that are ALREADY
# offer squares. From the game's start that collapses to a single gambit:
# the position after 1.e4 has exactly ONE black-offered entry in the whole
# asset (Duras Gambit, 1...f5) -- so Sacrificer/Gambiter always opened 1...f5
# vs 1.e4, and 302 of the 345 offer positions are singleton pools anyway.
# steer_to_gambit() fixes this by steering: at each persona turn it picks a
# random gambit line that is still REACHABLE from the live position and plays
# that line's next move, walking the game toward its randomly chosen offer.
# ---------------------------------------------------------------------------

# Built once, on first use (same idempotence contract as _GAMBIT_INDEX).
_STEERING_INDEX: Optional[dict[str, list[dict]]] = None


def _pgn_plies(pgn: str) -> list[str]:
    """SAN tokens of a book line, move numbers stripped.

    Same tokenization as scripts/validate_gambit_openings.parse_pgn_moves
    ("1." / "1..." are move numbers, everything else is a SAN token).
    """
    return [tok for tok in pgn.split() if not re.fullmatch(r"\d+\.*", tok)]


def load_gambit_steering_index() -> dict[str, list[dict]]:
    """Normalized position key -> steering candidates along gambit lines.

    Built once from the same validated asset by REPLAYING every offer-kind
    entry's full PGN (not just its endpoint, which is what the responder
    index keys): for each ply position the entry's line passes through
    BEFORE its offer -- with the offerer's color to move there -- one
    candidate {"entry", "uci", "is_offer"} is indexed, plus one
    is_offer=True member at the entry's own offer square. Post-offer plies
    are never indexed (steering ends once the offer has landed). The
    candidate's "uci" is the move to play AT THAT POSITION (the line's next
    move; for is_offer members the entry's own offer move), so a lookup
    returns a move that is legal by construction for the color to move.

    Loud failure on drift: an offer entry whose stored FEN does not lie on
    its own PGN raises ValueError during the build (the asset's committed
    validator guarantees this never fires for the shipped file).
    """
    global _STEERING_INDEX
    if _STEERING_INDEX is None:
        index: dict[str, list[dict]] = {}
        entries = [e for bucket in load_gambit_index().values() for e in bucket]
        for entry in entries:
            if not entry["offer"]:
                continue
            offerer = entry["fen"].split()[1]
            offer_key = _normalized_position_key(entry["fen"])
            board = chess.Board()
            found_offer = False
            for san in _pgn_plies(entry["pgn"]):
                key = _normalized_position_key(board.fen())
                if key == offer_key:
                    # The offer itself, playable at exactly this position.
                    index.setdefault(key, []).append(
                        {"entry": entry, "uci": entry["uci"], "is_offer": True}
                    )
                    found_offer = True
                    break
                if key.split()[1] == offerer:
                    # Pre-offer ply where the persona is to move: a legal
                    # steering move toward this entry's future offer.
                    move = board.parse_san(san)
                    index.setdefault(key, []).append(
                        {"entry": entry, "uci": move.uci(), "is_offer": False}
                    )
                board.push_san(san)
            if not found_offer:
                raise ValueError(
                    f"gambit book entry {entry['name']!r}: stored offer "
                    f"position {offer_key!r} is not on its own pgn line"
                )
        _STEERING_INDEX = index
    return _STEERING_INDEX


def steer_to_gambit(
    board: chess.Board,
    probability: float = SACRIFICER_OFFER_PROBABILITY,
) -> Optional[dict]:
    """Maybe return a gambit-book move for `board`, steering toward a random
    gambit line; else None.

    Same dict contract as maybe_play_gambit
    ({"uci", "san", "gambit_name", "eco", "pgn"}), same probability
    semantics (validate before lookup; ONE roll, only when a pool exists),
    same defensive legality guard -- but with the steering pool rule (see
    the module docstring):

      * candidates = offer-kind entries whose line passes through the
        current normalized position with the offer still ahead of (or at)
        this ply, whose offerer is the side to move;
      * ONE candidate is picked uniformly at random and its move played --
        offer-now candidates do NOT outrank pre-offer steering lines (a
        lone move-1 offer would otherwise repeat the same opening every
        game, the monotony steering exists to fix);
      * out-of-book positions return None WITHOUT burning the roll.

    On a steer, "gambit_name"/"eco"/"pgn" name the line being steered
    toward (the offer may still be several plies away); "san" is computed
    live from `board`. Coherence note: the endpoint is FEN-only (no move
    history, no session seed), so no cross-turn commitment is possible --
    when several lines share the current position the persona may switch
    between them, but only ever among lines passing through the exact
    position, so every book move remains positionally coherent.
    """
    if not 0.0 <= probability <= 1.0:
        raise ValueError(
            f"probability must be within [0, 1]; got {probability!r}"
        )

    bucket = load_gambit_steering_index().get(_normalized_position_key(board))
    if not bucket:
        # Nothing steerable here (out of book, or every passing line has
        # already made its offer) -- no probability check burned.
        return None

    if _roll() >= probability:
        return None

    member = _pick(bucket)
    move = chess.Move.from_uci(member["uci"])
    if move not in board.legal_moves:
        # Same defensive guard as maybe_play_gambit: a corrupted book must
        # degrade to "no book move", never to an illegal move.
        return None

    entry = member["entry"]
    return {
        "uci": member["uci"],
        "san": board.san(move),
        "gambit_name": entry["name"],
        "eco": entry["eco"],
        "pgn": entry["pgn"],
    }
