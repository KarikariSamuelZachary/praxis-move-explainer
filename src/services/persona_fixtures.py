"""
Fixture position set for the Engine Sparring persona-reranker feature dump.

Pure data: a hand-picked list of positions, each targeting a SPECIFIC known
failure mode of the style feature extractor (persona_features.py). The
companion tool persona_dump.py iterates these positions at multiple engine
strength levels so a human can eyeball whether compute_style_scores() returns
sane numbers on real Stockfish candidates BEFORE any persona weight vectors
are built on top of them.

Each entry is {"name", "fen", "description"}. The description records WHY the
position is included (the failure mode it probes). FENs are constructed and
verified legal; the descriptions name the key move(s) to watch for.
"""

FIXTURES = [
    {
        "name": "obvious sacrifice",
        "fen": "r1bq1rk1/pppnbppp/8/4P1N1/3P4/3B4/PPP2PPP/RNBQ1RK1 w - - 0 1",
        "description": (
            "Greek gift: Bxh7+ (and Nxh7) sacrifice a piece on h7 and are the "
            "engine's top moves. Probes that sacrifice_signal fires on a real, "
            "engine-approved material sacrifice (not just a hanging piece)."
        ),
    },
    {
        "name": "normal even trade",
        "fen": "6k1/5ppp/4p3/5n2/3N4/8/8/6K1 w - - 0 1",
        "description": (
            "Nxf5 is a plain knight-for-knight trade (then ...exf5). REGRESSION: "
            "the capturing knight is left en prise but nets equal material, so "
            "sacrifice_signal must stay 0.0 -- the exact bug class that misfired "
            "in the related opponent-style reranker."
        ),
    },
    {
        "name": "obvious attack",
        "fen": "5rk1/5ppp/7Q/5N2/8/8/5PPP/6K1 w - - 0 1",
        "description": (
            "Qxg7# is mate in one. The mating move is the strongest possible "
            "king-pressure increase (checks=1.0, king-zone pressure spikes); the "
            "quiet alternatives (Qg5/Qf4/etc.) must score much lower attack_gain."
        ),
    },
    {
        "name": "fake attack",
        "fen": "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1",
        "description": (
            "Opening position. Developing moves (Nf3, Bc4) aim toward the enemy "
            "kingside but create no real pressure. Probes that geometric nearness "
            "to the enemy king does NOT inflate attack_gain into 'attacker' territory."
        ),
    },
    {
        "name": "defensive consolidating",
        "fen": "4r1k1/5ppp/8/8/8/8/5PPP/4KB2 w - - 0 1",
        "description": (
            "White is in check from a rook on the open e-file; Bf1-e2 interposes "
            "and blocks the line. defense_gain must be clearly positive "
            "(line_blocking + enemy-pressure reduction), and this defensive block "
            "must NOT read as a sacrifice."
        ),
    },
    {
        "name": "quiet developing not defensive",
        "fen": "6k1/pppppppp/8/8/8/8/P1PPPPPP/2B2RK1 w - - 0 1",
        "description": (
            "White is castled kingside; Bc1-b2 develops a piece but defends no "
            "king-zone square and blocks nothing. defense_gain must be ~0 -- "
            "development on the same flank as the king must NOT read as defense."
        ),
    },
    {
        "name": "king move not improving safety",
        "fen": "4rrk1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1",
        "description": (
            "Kg1-f1 steps off its pawn shelter toward the open e-file (two enemy "
            "rooks). defense_gain must be negative -- a king move that walks into "
            "the open must not be scored as safer (pawn_shield and enemy-pressure "
            "reduction both worsen)."
        ),
    },
    {
        "name": "castling",
        "fen": "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4",
        "description": (
            "Italian opening; O-O is a top candidate. Castling tucks the king "
            "behind the f/g/h pawns and connects the rooks -- defense_gain should "
            "be positive (pawn shelter + reduced exposure), not neutral/negative."
        ),
    },
    {
        "name": "active endgame king",
        "fen": "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1",
        "description": (
            "King-and-pawn endgame (game_phase should read near 1.0). Ke1-d2 "
            "centralizes the king; king_mobility should be positive (the king "
            "activates). Note pawn_shield goes negative here, which is CORRECT for "
            "an endgame and is exactly why phase-gating exists downstream."
        ),
    },
    {
        "name": "quiet positional middlegame",
        "fen": "r1bq1rk1/ppp2ppp/2np1n2/2b1p3/2B1P3/2PP1N2/PP3PPP/RNBQ1RK1 b - - 0 1",
        "description": (
            "Balanced Italian (Black to move); no tactics available. All top moves "
            "are quiet positional (a6/h6/Ne7/a5/Bb6). Probes that a 'Positional' "
            "persona has sound quiet moves to prefer when nothing sharp exists."
        ),
    },
    {
        "name": "sharp tactical no quiet alternative",
        "fen": "r1bqkb1r/ppp2ppp/2n5/3np1N1/2B5/8/PPPP1PPP/RNBQK2R w KQkq - 0 6",
        "description": (
            "Fried Liver: Nxf7 is the sharp best move by a wide margin (~115+ cp); "
            "the quiet moves (d3, O-O) are far worse. Probes that a persona cannot "
            "manufacture a quiet near-best alternative when none really exists."
        ),
    },
    {
        "name": "near-equal candidates mixed style",
        "fen": "rnbq1rk1/ppp1ppbp/5np1/3p4/3P1B2/2N2NP1/PPP1PPBP/R2Q1RK1 w - - 0 1",
        "description": (
            "QID-style middlegame where several top moves sit within ~20 cp of each "
            "other. Probes that the persona bias can meaningfully REORDER near-equal "
            "candidates by style (here mostly quiet/positional, so the bias should "
            "be subtle rather than forcing a sharp move that isn't there)."
        ),
    },
]


def iter_fixtures():
    """Yield each fixture dict in order."""
    return iter(FIXTURES)
