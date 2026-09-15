"""
Live test harness for the probabilistic gambit-book bypass
(services/gambit_book.py). NOT a persona_reranker test-suite member: this
module is wired into the Engine Sparring endpoint (Sacrificer-only), not into
the reranker pipeline, so it ships its own standalone harness in the
codebase's script-test style (cd src && ../venv/bin/python services/gambit_book_test.py).

Parts:
  1. index integrity: built from the committed asset, keys are 4-field
     normalized FENs, every asset entry indexed, multi-match positions exist.
  2. no-match -> None with ZERO rolls (call-counted _roll spy -- proving no
     roll happened, not just that the result was None).
  3. forced-success roll at the real King's Gambit position -> the real
     King's Gambit entry (uci/san/name/eco contract, move legal in board),
     and exactly ONE roll.
  4. forced-failure roll -> None.
  5. probability validation: out-of-range raises ValueError BEFORE any roll;
     probability=0.0 never offers even on a 0.0 roll; probability=1.0 always
     offers even on a 0.99999 roll (the future Gambiter's always-in-book
     reuse of this same function).
  6. FEN normalization: board-identical FENs differing ONLY in move counters
     both hit the same index entry (the counters are stripped from the key).
  7. multi-match selection: at the asset's real two-offer transposition
      (Van Geet Damhaug/Warsteiner), forced rolls across 200 seeded trials
      only ever return one of the two real offers, and both occur; a
      constructed position with one offer + one declined continuation
      returns only the offer; a position whose matches are ALL continuations
      returns None with ZERO rolls (no probability check burned).
  8. steering index integrity: built only from offer-kind entries, every
      offer entry appears exactly once as an is_offer member at its own
      stored position, keys are 4-field normalized FENs.
  9. steering variety (the monotony bug steer_to_gambit exists to fix):
      after 1.e4 the responder pool has exactly ONE black offer (Duras
      Gambit -- the old always-1...f5 behavior), while steer_to_gambit
      across 600 seeded trials returns >= 10 DISTINCT black first moves,
      all legal, every picked line black-offered (the persona never steers
      the opponent into their own gambit), with Duras still reachable.
  10. steering follows the line: after 1.e4 Nf6 2.e5 the steered move is
      a black-offered book continuation (e.g. the Alekhine gambit Nd5);
      after 1.e4 f5 2.exf5 (offer landed) steering is out of book -> None
      with ZERO rolls (persona hands back to the reranker).
  11. steering at an offer square: uniform over ALL compatible lines --
      the King's Gambit offer (2.f4) occurs but does not exclusively win
      (>= 5 distinct moves across 600 trials); failed roll -> None (one
      roll burned); out-of-range probability raises BEFORE any roll even
      out of book; probability=0.0 never offers; no-candidate positions
      never roll.
  12. white-persona steering variety: at the game's start the pool is ALL
      white-offered lines (218), giving >= 10 distinct first moves across
      600 seeded trials, every picked line white-offered.

Run with: cd src && ../venv/bin/python services/gambit_book_test.py
"""
import json
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess

import services.gambit_book as gambit_book
from services.gambit_book import maybe_play_gambit, steer_to_gambit


def _load_entries() -> list[dict]:
    path = gambit_book.DATA_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["entries"]


def test_index_integrity():
    entries = _load_entries()
    index = gambit_book.load_gambit_index()
    # Every asset entry is reachable through the index (nothing dropped).
    total = sum(len(v) for v in index.values())
    assert total == len(entries), (total, len(entries))
    # Keys are exactly 4-field normalized FENs (the convention: board, turn,
    # castling, en-passant -- no halfmove clock, no fullmove number).
    for key in index:
        assert len(key.split()) == 4, key
    multi = {k: v for k, v in index.items() if len(v) > 1}
    assert multi, "expected transposition multi-match positions in the asset"
    print(f"    indexed {len(entries)} entries under {len(index)} normalized keys;"
          f" {len(multi)} multi-match positions (max pool {max(len(v) for v in multi.values())})")
    print("  [PASS] index built once from the committed asset; keys 4-field;"
          " multi-match positions present")


def test_no_match_returns_none_without_roll():
    # A real closed-Ruy position, verified OUT of book first, so the test
    # proves the no-match branch rather than assuming it. (The shallower
    # 1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 4.Ba4 Nf6 5.O-O position IS in book -- it
    # is the pre-move state of Ruy Lopez: Central Countergambit -- which is
    # itself a useful demonstration of how often book positions recur.)
    board = chess.Board()
    for san in (
        "e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O",
        "Be7", "Re1", "b5", "Bb3", "d6", "c3", "O-O",
    ):
        board.push_san(san)
    key = gambit_book._normalized_position_key(board)
    assert key not in gambit_book.load_gambit_index(), (
        "chosen no-match position is actually in the book -- pick another"
    )
    rolls = []
    original = gambit_book._roll
    gambit_book._roll = lambda: rolls.append(1) or 0.0
    try:
        result = maybe_play_gambit(board, probability=0.17)
    finally:
        gambit_book._roll = original
    assert result is None, result
    assert rolls == [], rolls
    print(f"    out-of-book position {key!r}: result None, rolls={len(rolls)}"
          " (probability check NOT burned)")
    print("  [PASS] no match -> None immediately, zero rolls")


def _kings_gambit_position() -> chess.Board:
    """The position immediately before 2.f4 (after 1.e4 e5), taken from the
    committed asset itself so the test tracks the data, not a hardcode."""
    entry = next(
        e for e in _load_entries()
        if e["name"] == "King's Gambit" and e["offer"]
    )
    return chess.Board(entry["fen"]), entry


def test_forced_success_returns_real_entry_with_one_roll():
    board, entry = _kings_gambit_position()
    rolls = []
    original = gambit_book._roll
    gambit_book._roll = lambda: rolls.append(1) or 0.0
    try:
        result = maybe_play_gambit(board, probability=0.17)
    finally:
        gambit_book._roll = original
    assert result is not None, "forced-success roll must return the offer"
    assert rolls == [1], rolls  # exactly ONE roll, not two, not zero
    assert result["uci"] == entry["uci"] == "f2f4"
    assert result["san"] == entry["san"] == "f4"
    assert result["gambit_name"] == "King's Gambit"
    assert result["eco"] == entry["eco"] == "C30"
    assert result["pgn"] == entry["pgn"]
    assert chess.Move.from_uci(result["uci"]) in board.legal_moves
    print(f"    forced success -> {result}")
    print("    contract fields uci/san/gambit_name/eco/pgn all present and valid")
    print("  [PASS] match + forced-success roll -> real King's Gambit 2.f4 (1 roll)")


def test_forced_failure_returns_none():
    board, _ = _kings_gambit_position()
    original = gambit_book._roll
    gambit_book._roll = lambda: 0.99
    try:
        result = maybe_play_gambit(board, probability=0.17)
    finally:
        gambit_book._roll = original
    assert result is None, result
    print("    roll=0.99 >= 0.17 -> None at an in-book position")
    print("  [PASS] match + forced-failure roll -> None")


def test_probability_bounds_and_extremes():
    board, _ = _kings_gambit_position()
    rolls = []
    original = gambit_book._roll
    gambit_book._roll = lambda: rolls.append(1) or 0.0
    try:
        for bad in (-0.1, 1.5):
            try:
                maybe_play_gambit(board, probability=bad)
                raise AssertionError(f"probability={bad} did not raise")
            except ValueError as exc:
                assert "probability" in str(exc)
        assert rolls == [], rolls  # rejected before any roll
        print("    probability=-0.1 / 1.5 -> ValueError before the lookup roll")

        # probability=0.0: even a 0.0 roll cannot pass.
        result = maybe_play_gambit(board, probability=0.0)
        assert result is None and len(rolls) == 1, (result, rolls)
        print("    probability=0.0 -> never offers (roll happened, check failed)")

        # probability=1.0: always offers, even on an almost-1.0 roll -- the
        # future Gambiter's deterministic always-in-book reuse.
        gambit_book._roll = lambda: rolls.append(1) or 0.99999
        result = maybe_play_gambit(board, probability=1.0)
        assert result is not None and result["uci"] == "f2f4", result
        print("    probability=1.0 -> always offers (roll 0.99999 passed):"
              f" {result['gambit_name']} {result['san']}")
    finally:
        gambit_book._roll = original
    print("  [PASS] probability bounds loud; 0.0 never offers; 1.0 always offers")


def test_fen_counter_normalization():
    _, entry = _kings_gambit_position()
    stored_fen = entry["fen"]  # e.g. "... w KQkq - 0 2" (canonical counters)
    rewritten_fen = stored_fen.replace(
        stored_fen.split()[-1], "99"
    ).replace(
        stored_fen.split()[-2], "37"
    )
    assert rewritten_fen != stored_fen  # counters genuinely differ
    b_canonical = chess.Board(stored_fen)
    b_shifted = chess.Board(rewritten_fen)
    assert b_canonical.fen() != b_shifted.fen()
    assert (
        gambit_book._normalized_position_key(b_canonical)
        == gambit_book._normalized_position_key(b_shifted)
    ), "board-identical positions must share the normalized key"
    original = gambit_book._roll
    gambit_book._roll = lambda: 0.0
    try:
        from_canonical = maybe_play_gambit(b_canonical, probability=0.17)
        from_shifted = maybe_play_gambit(b_shifted, probability=0.17)
    finally:
        gambit_book._roll = original
    assert from_canonical is not None and from_shifted is not None
    assert from_canonical["uci"] == from_shifted["uci"] == "f2f4"
    assert from_canonical["gambit_name"] == from_shifted["gambit_name"]
    print(f"    stored:   {stored_fen}")
    print(f"    shifted:  {rewritten_fen}")
    print("    both resolve to the same King's Gambit offer (counters stripped)")
    print("  [PASS] FEN normalization: counter-only differences match")


def _fake_index(position_fen: str, entries: list[dict]) -> dict[str, list[dict]]:
    key = " ".join(position_fen.split()[:4])
    return {key: entries}


def test_multi_match_selection_on_real_transposition():
    # The asset's real two-offer transposition: same normalized position,
    # two DIFFERENT named gambit offers (Damhaug 1...e5, Warsteiner 1...g5).
    index = gambit_book.load_gambit_index()
    position_key, pool = next(
        (k, v) for k, v in index.items()
        if len(v) >= 2 and all(e["offer"] for e in v)
    )
    assert len(pool) == 2, pool
    allowed = {e["uci"] for e in pool}
    board = chess.Board(" ".join([position_key, "0", "1"]))
    assert all(chess.Move.from_uci(u) in board.legal_moves for u in allowed)

    original_roll, original_pick = gambit_book._roll, gambit_book._pick
    gambit_book._roll = lambda: 0.0  # forced success
    random.seed(20260915)
    try:
        seen = set()
        for _ in range(200):
            result = maybe_play_gambit(board, probability=0.17)
            assert result is not None
            assert result["uci"] in allowed, result
            seen.add(result["uci"])
        assert seen == allowed, seen  # both offers actually reachable
        names = {e["name"] for e in pool}
        print(f"    position {position_key}")
        print(f"    offers: {sorted(names)}")
        print(f"    200 forced trials -> returned moves {sorted(seen)}"
              " (uniform pick reachable for both)")
    finally:
        gambit_book._roll, gambit_book._pick = original_roll, original_pick
    print("  [PASS] multi-match: pool contains ONLY real offers, pick is uniform")


def test_multi_match_excludes_continuations():
    # Constructed position: one real offer + one Accepted-continuation row
    # sharing the same normalized position. The continuation's move (the
    # defender's reply, not an offer) must never be returned.
    position_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    entries = [
        {
            "name": "Fake Opening: Real Offer Gambit", "eco": "A00",
            "fen": position_fen, "uci": "e2e4", "san": "e4",
            "pgn": "1. e4", "kind": "offer", "offer": True,
        },
        {
            "name": "Fake Opening: Real Offer Gambit Accepted, Defense",
            "eco": "A00",
            "fen": position_fen, "uci": "d2d4", "san": "d4",
            "pgn": "1. e4 e5 2. d4 d5", "kind": "accepted", "offer": False,
        },
    ]
    original_load = gambit_book.load_gambit_index
    gambit_book.load_gambit_index = lambda: _fake_index(position_fen, entries)
    original_roll = gambit_book._roll
    gambit_book._roll = lambda: 0.0  # forced success
    try:
        board = chess.Board(position_fen)
        result = maybe_play_gambit(board, probability=0.17)
        assert result is not None
        assert result["uci"] == "e2e4", result
        assert result["gambit_name"] == "Fake Opening: Real Offer Gambit"
        print(f"    offer+continuation both match -> returned the OFFER only:"
              f" {result['uci']} / {result['gambit_name']}")
        assert chess.Move.from_uci(result["uci"]) in board.legal_moves

        # Same position, but EVERY match is a continuation: nothing can be
        # offered -> None, and the probability check is NOT burned.
        entries_all_declined = [
            {**e, "kind": "accepted", "offer": False} for e in entries
        ]
        rolls = []
        gambit_book._roll = lambda: rolls.append(1) or 0.0
        gambit_book.load_gambit_index = (
            lambda: _fake_index(position_fen, entries_all_declined)
        )
        result = maybe_play_gambit(board, probability=0.17)
        assert result is None, result
        assert rolls == [], rolls
        print("    all-continuation position -> None with zero rolls")
    finally:
        gambit_book.load_gambit_index = original_load
        gambit_book._roll = original_roll
    print("  [PASS] selection rule: offers only; continuations never returned")


def test_modern_gambits_findable_alien_and_martian():
    """The two modern (2024, Witty_Alien/CM Volen Dyulgerov) gambits are in
    the SAME table: the lichess ECO dataset itself incorporated them
    (B15/B18), so the classical build already carries both as offer-kind
    entries. This test pins that maybe_play_gambit() finds and returns each
    one at the EXACT position its source documents -- replaying the
    Chess.com-cited move orders, which transpose at move 3 versus the stored
    lichess line (Chess.com's Alien page defines the line via 3.Nd2; the
    stored line records 3.Nc3; both converge to the identical position after
    3...dxe4 4.Nxe4 -- verified FEN-identical, not merely key-identical).

    Sources (Chess.com's own opening pages for both):
      * Alien Gambit --
        https://www.chess.com/openings/Caro-Kann-Defense-Alien-Gambit
        "The Alien Gambit starts after the moves 1.e4 c6 2.d4 d5 3.Nd2
        dxe4 4.Nxe4 Nf6 5.Ng5 h6 6.Nxf7" (offer = 6.Nxf7, the knight sac;
        also confirmed by the creator interview at chess.com/article/view/
        creator-of-the-month-witty-alien: "I played Ng5, and after h6,
        Nxf7").
      * Martian Gambit --
        https://www.chess.com/openings/Caro-Kann-Defense-Classical-Martian-Gambit
        "The Martian Gambit starts with the moves 1.e4 c6 2.d4 d5 3.Nc3
        dxe4 4.Nxe4 Bf5 5.Ng5 Bg6 6.N1f3 h6 7.Ne6" (offer = 7.Ne6; the
        page's main continuation "7.Ne6 fxe6 8.Ne5" confirms the knight is
        the offered material).
    """
    cases = [
        # (name suffix, Chess.com-sourced pre-offer SANs, expected san/uci/eco)
        ("Alien Gambit",
         ["e4", "c6", "d4", "d5", "Nd2", "dxe4", "Nxe4", "Nf6", "Ng5", "h6"],
         "Nxf7", "g5f7", "B15"),
        ("Martian Gambit",
         ["e4", "c6", "d4", "d5", "Nc3", "dxe4", "Nxe4", "Bf5", "Ng5", "Bg6",
          "N1f3", "h6"],
         "Ne6", "g5e6", "B18"),
    ]
    index = gambit_book.load_gambit_index()
    for name, sans, san_last, uci_last, eco in cases:
        board = chess.Board()
        for san in sans:
            board.push(board.parse_san(san))
        key = gambit_book._normalized_position_key(board)
        matches = index.get(key, [])
        assert matches, f"{name}: Chess.com-sourced position not in book"
        entry = next(e for e in matches if e["offer"])
        assert entry["name"].endswith(name), entry["name"]
        assert entry["eco"] == eco, (entry["eco"], eco)
        assert entry["uci"] == uci_last and entry["san"] == san_last
        assert entry["kind"] == "offer" and entry["offer"] is True
        rolls = []
        original = gambit_book._roll
        gambit_book._roll = lambda: rolls.append(1) or 0.0  # forced success
        try:
            result = maybe_play_gambit(board, probability=0.17)
        finally:
            gambit_book._roll = original
        assert rolls == [1], rolls
        assert result is not None
        assert result["uci"] == uci_last, result
        assert result["san"] == san_last, result
        assert result["gambit_name"] == entry["name"]
        assert result["eco"] == eco
        assert chess.Move.from_uci(result["uci"]) in board.legal_moves
        print(f"    {name:<15} ({eco}): sourced position -> {result['gambit_name']}"
              f" {result['san']} ({result['uci']}) [1 roll]")
    print("  [PASS] Alien + Martian Gambits findable at their Chess.com-sourced"
          " positions")


def test_steering_index_integrity():
    entries = _load_entries()
    offer_entries = [e for e in entries if e["offer"]]
    index = gambit_book.load_gambit_steering_index()
    # Keys are exactly 4-field normalized FENs (the shared convention).
    for key in index:
        assert len(key.split()) == 4, key
    # Every member comes from an OFFER-kind entry (continuations are never
    # steerable) and every offer entry appears EXACTLY ONCE as an is_offer
    # member, at its own stored position, with its own uci.
    offer_members = [m for bucket in index.values() for m in bucket if m["is_offer"]]
    assert len(offer_members) == len(offer_entries), (
        len(offer_members), len(offer_entries)
    )
    for member in (m for bucket in index.values() for m in bucket):
        assert member["entry"]["offer"] is True, member["entry"]["name"]
        if member["is_offer"]:
            assert member["uci"] == member["entry"]["uci"], member
            assert (
                gambit_book._normalized_position_key(member["entry"]["fen"])
                == next(k for k, b in index.items() if member in b)
            ), member["entry"]["name"]
    print(f"    steering index: {len(index)} keys, "
          f"{sum(len(v) for v in index.values())} members, "
          f"{len(offer_members)} is_offer members == {len(offer_entries)} offers")
    print("  [PASS] steering index: offer entries only, one is_offer member each")


def test_steering_variety_vs_e4():
    # THE bug steer_to_gambit exists to fix: after 1.e4 the responder-only
    # pool is exactly ONE black offer (Duras Gambit, 1...f5), so the old
    # maybe_play_gambit behavior opened 1...f5 in EVERY game. Steering must
    # spread over the black-offered 1.e4 lines instead: >= 10 distinct
    # first moves across 600 forced trials, all legal, all picked lines
    # black-offered, with Duras (f7f5) still reachable.
    board = chess.Board()
    board.push_san("e4")
    key = gambit_book._normalized_position_key(board)

    # Premise: the old responder sees exactly one offer here.
    offers = [e for e in gambit_book.load_gambit_index().get(key, [])
              if e["offer"]]
    assert len(offers) == 1 and offers[0]["uci"] == "f7f5", offers
    assert offers[0]["name"] == "Duras Gambit", offers[0]["name"]

    # Premise: the steering bucket is much bigger and 1 offer + 100 steers.
    bucket = gambit_book.load_gambit_steering_index()[key]
    assert len(bucket) > 10, len(bucket)
    assert sum(m["is_offer"] for m in bucket) == 1

    original_roll, original_pick = gambit_book._roll, gambit_book._pick
    picked = []
    try:
        gambit_book._roll = lambda: 0.0  # forced success
        def recording_pick(pool):
            member = original_pick(pool)
            picked.append(member)
            return member
        gambit_book._pick = recording_pick
        random.seed(20260915)
        seen_moves, seen_names = set(), set()
        for _ in range(600):
            result = steer_to_gambit(board, probability=1.0)
            assert result is not None
            assert chess.Move.from_uci(result["uci"]) in board.legal_moves, result
            member = picked[-1]
            # The persona (black here) never steers a white-offered gambit.
            assert member["entry"]["fen"].split()[1] == "b", member
            assert result["gambit_name"] == member["entry"]["name"]
            assert result["san"], result  # live-computed san present
            seen_moves.add(result["uci"])
            seen_names.add(result["gambit_name"])
        assert len(seen_moves) >= 10, sorted(seen_moves)
        assert "f7f5" in seen_moves, sorted(seen_moves)  # Duras reachable
        print(f"    bucket {len(bucket)} members -> 600 trials: "
              f"{len(seen_moves)} distinct first moves, "
              f"{len(seen_names)} distinct gambit names, f5 present")
        print("    sample moves:", sorted(seen_moves)[:8], "...")
    finally:
        gambit_book._roll, gambit_book._pick = original_roll, original_pick
    print("  [PASS] steering variety vs 1.e4 (>= 10 first moves, Duras"
          " reachable, black-offered only)")


def test_steering_follows_line_and_hands_back():
    # Following a steered line: 1.e4 Nf6 2.e5 -> the persona plays a
    # black-offered book continuation (an Alekhine-family gambit move).
    board = chess.Board()
    for san in ("e4", "Nf6", "e5"):
        board.push_san(san)
    original_pick = gambit_book._pick
    picked = []

    def recording_pick(pool):
        member = original_pick(pool)
        picked.append(member)
        return member

    original_roll = gambit_book._roll
    gambit_book._roll = lambda: 0.0
    gambit_book._pick = recording_pick
    try:
        result = steer_to_gambit(board, probability=1.0)
        assert result is not None, "steered line must continue after 2.e5"
        assert chess.Move.from_uci(result["uci"]) in board.legal_moves
        assert picked[-1]["entry"]["fen"].split()[1] == "b", picked[-1]
        print(f"    after 1.e4 Nf6 2.e5 -> {result['gambit_name']}: {result['san']}")
    finally:
        gambit_book._roll, gambit_book._pick = original_roll, original_pick

    # Offer landed -> steering is over: after 1.e4 f5 2.exf5 there is
    # nothing steerable -> None with ZERO rolls (reranker takes over).
    board = chess.Board()
    for san in ("e4", "f5", "exf5"):
        board.push_san(san)
    key = gambit_book._normalized_position_key(board)
    assert key not in gambit_book.load_gambit_steering_index(), (
        "post-offer position unexpectedly steerable -- premise changed"
    )
    rolls = []
    gambit_book._roll = lambda: rolls.append(1) or 0.0
    try:
        assert steer_to_gambit(board, probability=1.0) is None
        assert rolls == [], rolls
    finally:
        gambit_book._roll = original_roll
    print("    after 1.e4 f5 2.exf5 -> None, zero rolls (reranker takes over)")
    print("  [PASS] steering follows lines; hands back to the reranker"
          " after the offer")


def test_steering_offer_square_and_probability():
    # At an offer square the pool is ALL compatible lines (uniform): the
    # King's Gambit offer 2.f4 occurs across forced trials but does not
    # exclusively win. Plus the probability contract: failed roll -> None
    # (one roll burned), out-of-range raises BEFORE any roll, 0.0 never
    # offers, 1.0 always offers.
    board = chess.Board(
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
    )
    original_roll = gambit_book._roll
    try:
        gambit_book._roll = lambda: 0.0  # forced success
        random.seed(20260915)
        seen = set()
        for _ in range(600):
            result = steer_to_gambit(board, probability=1.0)
            assert result is not None
            seen.add(result["uci"])
        assert "f2f4" in seen, sorted(seen)  # the KG offer is reachable
        assert len(seen) >= 5, sorted(seen)  # ...but does not win by default
        print(f"    KG offer square: {len(seen)} distinct moves across 600"
              f" trials, f4 included: {sorted(seen)}")

        # Failed roll at an in-book position -> None, exactly one roll.
        rolls = []
        gambit_book._roll = lambda: rolls.append(1) or 0.99
        assert steer_to_gambit(board, probability=0.17) is None
        assert rolls == [1], rolls
        print("    roll=0.99 >= 0.17 -> None (one roll burned at an in-book"
              " steerable position)")

        # probability validation BEFORE the lookup: out-of-book position,
        # invalid probability -> ValueError with zero rolls.
        rolls = []
        gambit_book._roll = lambda: rolls.append(1) or 0.0
        closed = chess.Board()
        for san in ("e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6",
                    "O-O", "Be7", "Re1", "b5", "Bb3", "d6", "c3", "O-O"):
            closed.push_san(san)
        assert (gambit_book._normalized_position_key(closed)
                not in gambit_book.load_gambit_steering_index())
        for bad in (-0.1, 1.5):
            try:
                steer_to_gambit(closed, probability=bad)
                raise AssertionError(f"probability={bad} did not raise")
            except ValueError as exc:
                assert "probability" in str(exc)
        assert rolls == [], rolls
        print("    probability=-0.1 / 1.5 -> ValueError before any roll")

        # 0.0: even a 0.0 roll cannot pass; 1.0: always steers/offers.
        gambit_book._roll = lambda: rolls.append(1) or 0.0
        assert steer_to_gambit(board, probability=0.0) is None
        assert len(rolls) == 1, rolls
        gambit_book._roll = lambda: rolls.append(1) or 0.99999
        result = steer_to_gambit(board, probability=1.0)
        assert result is not None, result
        print(f"    probability=0.0 -> never offers; probability=1.0 ->"
              f" always ({result['gambit_name']} {result['san']})")
    finally:
        gambit_book._roll = original_roll
    print("  [PASS] offer-square uniformity + probability contract")


def test_steering_white_first_move_variety():
    # Persona = White at the game's start: the pool is ALL white-offered
    # lines (no color leaks: every picked entry is white-offered), and
    # steering yields >= 10 distinct first moves across 600 forced trials.
    board = chess.Board()
    key = gambit_book._normalized_position_key(board)
    bucket = gambit_book.load_gambit_steering_index()[key]
    assert len(bucket) > 10, len(bucket)
    assert all(m["entry"]["fen"].split()[1] == "w" for m in bucket), (
        "initial-position pool must be white-offered lines only"
    )
    assert not any(m["is_offer"] for m in bucket), (
        "no gambit offers on move 1 for White in the asset"
    )

    original_roll, original_pick = gambit_book._roll, gambit_book._pick
    picked = []
    try:
        gambit_book._roll = lambda: 0.0
        def recording_pick(pool):
            member = original_pick(pool)
            picked.append(member)
            return member
        gambit_book._pick = recording_pick
        random.seed(20260915)
        seen_moves = set()
        for _ in range(600):
            result = steer_to_gambit(board, probability=1.0)
            assert result is not None
            assert chess.Move.from_uci(result["uci"]) in board.legal_moves
            assert picked[-1]["entry"]["fen"].split()[1] == "w", picked[-1]
            seen_moves.add(result["uci"])
        assert len(seen_moves) >= 10, sorted(seen_moves)
        print(f"    initial pool {len(bucket)} members -> 600 trials: "
              f"{len(seen_moves)} distinct first moves")
        print("    sample:", sorted(seen_moves)[:8], "...")
    finally:
        gambit_book._roll, gambit_book._pick = original_roll, original_pick
    print("  [PASS] white-persona steering variety at the game's start")


def main() -> int:
    print("=== gambit book bypass tests ===")
    for test in (
        test_index_integrity,
        test_no_match_returns_none_without_roll,
        test_forced_success_returns_real_entry_with_one_roll,
        test_forced_failure_returns_none,
        test_probability_bounds_and_extremes,
        test_fen_counter_normalization,
        test_multi_match_selection_on_real_transposition,
        test_multi_match_excludes_continuations,
        test_modern_gambits_findable_alien_and_martian,
        test_steering_index_integrity,
        test_steering_variety_vs_e4,
        test_steering_follows_line_and_hands_back,
        test_steering_offer_square_and_probability,
        test_steering_white_first_move_variety,
    ):
        try:
            test()
        except AssertionError as exc:
            print(f"\n  [FAIL] {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"\n  [FAIL] raised {type(exc).__name__}: {exc}")
            return 1
    print("\nAll gambit-book bypass tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
