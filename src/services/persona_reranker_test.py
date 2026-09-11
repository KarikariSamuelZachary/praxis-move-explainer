"""
Live test harness for the production reranker entrypoint
(src/services/persona_reranker.py).

PART 1 (no engine) -- persona-selector validation:
  * all four persona names resolve to the right PersonaType members
  * normalization: case/whitespace variants accepted ("ATTACKER", " Defender ")
  * PersonaType passthrough
  * invalid names raise ValueError NAMING the bad value and the valid choices
  * wrong types raise ValueError too

PART 2 (spy, no live engine) -- fail-fast BEFORE any engine work:
  * a spy class patched over persona_reranker.StockfishEngine proves an
    INVALID persona never constructs an engine (the spy raises the moment it
    is constructed, so a single construction fails the test), while a VALID
    persona DOES reach the engine path (spy constructed, then the fake
    engine's error surfaces as the failure) -- bidirectional proof.

PART 3 (live engine) -- deterministic equivalence regression:
  For each of 3 fixtures the test captures ONE real raw suggest() list, then
  (with suggest() monkeypatched to replay that exact capture) asserts that
  rerank_moves() output is EXACTLY equal to the manual pipeline computed
  inline with the same steps persona_weights_test.py Part 4 uses
  (canonicalize_by_score -> compute_style_scores -> <persona>_score ->
  persona_adjusted_score(norm, score, phase)). Identical inputs must give
  bit-identical rankings: the production path must reproduce the proven
  harness computation, not merely resemble it.

PART 4 (live engine) -- fixture orders for all four personas on the same 3
  fixtures, compared against the SAVED Part 4 harness run (persona_weights_
  test.py output captured 2026-09-11, same binary/settings): when the live
  candidate draw matches the saved run's candidate list, the persona ORDER
  must match too (asserted); when the draw differs, the mismatch is printed
  and flagged as MultiPV jitter (documented; the capture changes between
  runs even one search apart) rather than asserted -- Part 3 is the exact
  regression, Part 4 is the live eyeball + jitter demonstration.

PART 5 (live engine) -- best_persona_move() == rerank_moves()[0] exactly
  (same replayed capture), plus a live smoke check on a real engine.

PART 6 (live engine) -- terminal positions: checkmate and stalemate both
  return [] from rerank_moves() and None from best_persona_move().

PART 7 (live engine) -- configure_strength()'s ValueError on out-of-range
  Elo propagates out of rerank_moves() UNCAUGHT.

Run with: cd src && ../venv/bin/python services/persona_reranker_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess

from engines.stockfish_engine import StockfishEngine
from services.persona_bounds import game_phase
from services.persona_features import compute_style_scores
from services.persona_fixtures import FIXTURES
from services.persona_reranker import PersonaType, best_persona_move, rerank_moves, resolve_persona
from services.persona_weights import (
    attacker_score,
    canonicalize_by_score,
    defender_score,
    persona_adjusted_score,
    positional_score,
    sacrificer_score,
)

FIXTURES_BY_NAME = {f["name"]: f for f in FIXTURES}
LIVE_FIXTURES = [
    "obvious sacrifice",
    "near-equal candidates mixed style",
    "quiet positional middlegame",
]
ALL_PERSONAS = list(PersonaType)

# REGRESSION BASELINE: candidate draw + persona orders recorded from the
# persona_weights_test.py Part 4 harness run (same Stockfish 16 binary,
# num_moves=5, time_limit=0.3) that this task's delivery must agree with.
# SAN lists; converted to moves through the fixture FEN at runtime.
SAVED_HARNESS_RUN = {
    "obvious sacrifice": {
        "draw": [("Bxh7+", 805), ("Nxh7", 752), ("Nf3", 710), ("Ne4", 694), ("h4", 684)],
        "orders": {
            PersonaType.ATTACKER: ["Bxh7+", "Nxh7", "Nf3", "Ne4", "h4"],
            PersonaType.SACRIFICER: ["Bxh7+", "Nxh7", "Nf3", "Ne4", "h4"],
            PersonaType.DEFENDER: ["Bxh7+", "Nxh7", "Nf3", "Ne4", "h4"],
            PersonaType.POSITIONAL: ["Bxh7+", "Nxh7", "Nf3", "Ne4", "h4"],
        },
    },
    "near-equal candidates mixed style": {
        "draw": [("Qd2", 32), ("Re1", 31), ("a4", 22), ("b3", 21), ("Ne5", 19)],
        "orders": {
            PersonaType.ATTACKER: ["Qd2", "Re1", "Ne5", "a4", "b3"],
            PersonaType.SACRIFICER: ["Qd2", "Re1", "Ne5", "a4", "b3"],
            PersonaType.DEFENDER: ["Re1", "Qd2", "a4", "b3", "Ne5"],
            PersonaType.POSITIONAL: ["Qd2", "Re1", "a4", "b3", "Ne5"],
        },
    },
    "quiet positional middlegame": {
        "draw": [("h6", 0), ("a5", -12), ("a6", -16), ("Bb6", -18), ("Kh8", -18)],
        "orders": {
            PersonaType.ATTACKER: ["h6", "a5", "a6", "Bb6", "Kh8"],
            PersonaType.SACRIFICER: ["h6", "a5", "a6", "Bb6", "Kh8"],
            PersonaType.DEFENDER: ["h6", "a5", "Kh8", "a6", "Bb6"],
            PersonaType.POSITIONAL: ["h6", "a5", "a6", "Bb6", "Kh8"],
        },
    },
}

CHECKMATE_FEN = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
STALEMATE_FEN = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"


def test_selector_validation():
    for member in ALL_PERSONAS:
        assert resolve_persona(member.value) is member, (member, member.value)
        assert resolve_persona(member) is member
    # Normalization is deliberate: case/whitespace variants of valid names work.
    assert resolve_persona("ATTACKER") is PersonaType.ATTACKER
    assert resolve_persona("  Defender ") is PersonaType.DEFENDER
    assert resolve_persona("Positional") is PersonaType.POSITIONAL
    # Invalid names raise LOUDLY, naming the value and the valid choices.
    for bad in ("wizard", "attackerx", "Gambit", ""):
        try:
            resolve_persona(bad)
        except ValueError as exc:
            message = str(exc)
            assert "unknown persona" in message and repr(bad) in message, message
            for member in ALL_PERSONAS:
                assert member.value in message, message
        else:
            raise AssertionError(f"invalid persona {bad!r} did not raise")
    # Wrong types fail too (never silently treated as a name).
    try:
        resolve_persona(7)
    except ValueError as exc:
        assert "must be a PersonaType or string" in str(exc), exc
    else:
        raise AssertionError("non-string persona did not raise")
    print("    all 4 names resolve; case/whitespace normalized;")
    print("    'wizard'/''/7 rejected with ValueError naming valid choices")
    print("  [PASS] selector validation: loud, specific, no silent defaults")


class _SpyEngine:
    """Patched over persona_reranker.StockfishEngine: construction = failure.

    Rerank code must NEVER construct an engine for an invalid persona; the
    spy makes any construction an immediate, unmistakable test failure.
    """

    constructed = False

    def __init__(self, *args, **kwargs):
        _SpyEngine.constructed = True
        raise AssertionError(
            "engine constructed during persona validation -- "
            "rerank_moves must reject a bad persona BEFORE engine work"
        )

    @classmethod
    def reset(cls):
        cls.constructed = False


class _FakeEngine:
    """Replays ONE captured raw suggest() list (deterministic, no engine)."""

    def __init__(self, captured):
        self._captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def suggest(self, board, num_moves=5, time_limit=None):
        return [dict(entry) for entry in self._captured]


def test_fail_fast_before_engine():
    original = rerank_moves.__globals__["StockfishEngine"]

    # Invalid persona: the spy must never even be CONSTRUCTED.
    rerank_moves.__globals__["StockfishEngine"] = _SpyEngine
    _SpyEngine.reset()
    board = chess.Board(FIXTURES_BY_NAME["obvious sacrifice"]["fen"])
    try:
        rerank_moves(board, "wizard")
    except ValueError as exc:
        assert "unknown persona" in str(exc), exc
        assert not _SpyEngine.constructed, (
            "engine was constructed while validating an invalid persona"
        )
    else:
        raise AssertionError("rerank_moves accepted persona 'wizard'")
    try:
        best_persona_move(board, "wizard")
    except ValueError:
        assert not _SpyEngine.constructed
    else:
        raise AssertionError("best_persona_move accepted persona 'wizard'")

    # Positive control (same spy, VALID persona): the engine path IS reached
    # (spy constructs, then the fake's own error surfaces) -- proving the spy
    # is actually wired into the code path the first half claims to avoid.
    class _ConstructingSpy(_SpyEngine):
        def __init__(self, *args, **kwargs):
            _SpyEngine.constructed = True
            raise RuntimeError("spy engine reached (positive control)")

    rerank_moves.__globals__["StockfishEngine"] = _ConstructingSpy
    try:
        rerank_moves(board, "attacker")
    except RuntimeError as exc:
        assert _SpyEngine.constructed, "spy not consulted for a valid persona"
        assert "spy engine reached" in str(exc), exc
    else:
        raise AssertionError("valid persona did not reach the engine path")
    finally:
        rerank_moves.__globals__["StockfishEngine"] = original
        _SpyEngine.reset()
    print("    'wizard': ValueError, engine NEVER constructed (spy proves it)")
    print("    'attacker': engine path reached (same spy, positive control)")
    print("  [PASS] persona validated before any engine work, both entrypoints")


def _capture_suggest(fixture_name, num_moves=5, time_limit=0.3):
    """One real raw suggest() capture (the input for the replayed runs)."""
    fixture = FIXTURES_BY_NAME[fixture_name]
    board = chess.Board(fixture["fen"])
    with StockfishEngine() as engine:
        raw = engine.suggest(board, num_moves=num_moves, time_limit=time_limit)
    assert raw, f"no candidates for {fixture_name}"
    return raw


def _manual_pipeline(board, raw_suggestions, scorer):
    """The Part 4 harness computation, inline (the equivalence reference)."""
    suggestions = canonicalize_by_score(raw_suggestions)
    phase = game_phase(board)
    best_cp = suggestions[0]["score_cp"]
    expected = []
    for s in suggestions:
        move = chess.Move.from_uci(s["uci"])
        scores, _ = compute_style_scores(board, move)
        norm_cp = s["score_cp"] - best_cp
        persona_score = scorer(scores, board)
        expected.append({
            "uci": s["uci"],
            "san": s["san"],
            "score_cp": s["score_cp"],
            "engine_norm_cp": norm_cp,
            "persona_score": persona_score,
            "persona_final_cp": persona_adjusted_score(norm_cp, persona_score, phase),
        })
    expected.sort(key=lambda row: -row["persona_final_cp"])
    return expected


def _patch_suggest(captured):
    rerank_moves.__globals__["StockfishEngine"] = lambda *a, **k: _FakeEngine(captured)


def _unpatch_suggest():
    rerank_moves.__globals__["StockfishEngine"] = StockfishEngine


def test_deterministic_equivalence(captures):
    print("  (capturing one real raw suggest() list per fixture...)")
    for fixture_name in LIVE_FIXTURES:
        captures[fixture_name] = _capture_suggest(fixture_name)
        raw = captures[fixture_name]
        print(f"    {fixture_name}: captured {len(raw)} candidates"
              f" ({raw[0]['san']} {raw[0]['score_cp']} ... "
              f"{raw[-1]['san']} {raw[-1]['score_cp']})")
    board_by_fixture = {
        name: chess.Board(FIXTURES_BY_NAME[name]["fen"]) for name in LIVE_FIXTURES
    }
    scorers = {
        PersonaType.ATTACKER: attacker_score,
        PersonaType.SACRIFICER: sacrificer_score,
        PersonaType.DEFENDER: defender_score,
        PersonaType.POSITIONAL: positional_score,
    }
    try:
        for fixture_name in LIVE_FIXTURES:
            _patch_suggest(captures[fixture_name])
            for persona, scorer in scorers.items():
                got = rerank_moves(board_by_fixture[fixture_name], persona)
                want = _manual_pipeline(board_by_fixture[fixture_name],
                                        captures[fixture_name], scorer)
                assert got == want, (
                    f"{fixture_name}/{persona.value}: rerank_moves != manual pipeline\n"
                    f"  got : {got}\n  want: {want}"
                )
                order = " > ".join(row["san"] for row in got)
                print(f"    {fixture_name:<34} {persona.value:<11} {order}"
                      "   == manual pipeline (exact)")
    finally:
        _unpatch_suggest()
    print("  [PASS] rerank_moves output EXACTLY equals the Part 4 harness"
          " pipeline on identical inputs (12 fixture/persona pairs)")


def test_live_orders_and_regression(captures):
    try:
        for fixture_name in LIVE_FIXTURES:
            fixture = FIXTURES_BY_NAME[fixture_name]
            board = chess.Board(fixture["fen"])
            baseline = SAVED_HARNESS_RUN[fixture_name]
            print(f"--- {fixture_name} ---")
            print(f"    saved harness draw: "
                  + " ".join(f"{san}({cp})" for san, cp in baseline["draw"]))
            for persona in ALL_PERSONAS:
                ranked = rerank_moves(board, persona, time_limit=0.3)
                assert ranked, f"empty rerank for {fixture_name}"
                finals = [row["persona_final_cp"] for row in ranked]
                assert finals == sorted(finals, reverse=True), finals
                for row in ranked:
                    for key in ("uci", "san", "score_cp", "engine_norm_cp",
                                "persona_score", "persona_final_cp"):
                        assert key in row, (key, row)
                order = [row["san"] for row in ranked]
                print(f"    {persona.value:<11} " + " > ".join(
                    f"{row['san']}({row['persona_final_cp']:+.2f})" for row in ranked))
                live_draw = [(row["san"], row["score_cp"]) for row in ranked]
                if live_draw == [(san, cp) for san, cp in baseline["draw"]]:
                    assert order == baseline["orders"][persona], (
                        f"{fixture_name}/{persona.value}: live order {order} != "
                        f"saved harness order {baseline['orders'][persona]}"
                    )
                    print(f"               (draw matches saved harness run;"
                          f" order match ASSERTED)")
                else:
                    print("               (NOTE: live draw differs from the saved"
                          " harness run -- normal MultiPV jitter between engine"
                          " processes; exact-equivalence is asserted in Part 3)")
    finally:
        _unpatch_suggest()
    print("  [PASS] live orders structurally valid; saved-run agreement asserted"
          " whenever the draw matched")


def _same_scores(raw, ranked):
    """True iff the ranked rows' engine numbers equal the captured draw."""
    best_cp = max(s["score_cp"] for s in raw)
    canon = canonicalize_by_score(raw)
    return all(
        row["score_cp"] == s["score_cp"]
        and row["engine_norm_cp"] == s["score_cp"] - best_cp
        for row, s in zip(ranked, canon)
    )


def test_best_persona_move(captures):
    scorers = {
        PersonaType.ATTACKER: attacker_score,
        PersonaType.POSITIONAL: positional_score,
    }
    try:
        for fixture_name in ("obvious sacrifice", "near-equal candidates mixed style"):
            _patch_suggest(captures[fixture_name])
            board = chess.Board(FIXTURES_BY_NAME[fixture_name]["fen"])
            for persona in scorers:
                ranked = rerank_moves(board, persona)
                best = best_persona_move(board, persona)
                assert best == ranked[0], (best, ranked[0])
                print(f"    {fixture_name:<34} {persona.value:<11} "
                      f"best={best['san']} == rerank_moves()[0] (exact dict match)")
    finally:
        _unpatch_suggest()
    print("  [PASS] best_persona_move returns exactly rerank_moves()[0]")
    board = chess.Board(FIXTURES_BY_NAME["obvious sacrifice"]["fen"])
    best = best_persona_move(board, "sacrificer", time_limit=0.3)
    assert isinstance(best, dict) and best["uci"] and best["san"]
    assert set(best) == {"uci", "san", "score_cp", "engine_norm_cp",
                         "persona_score", "persona_final_cp"}, set(best)
    print(f"    live smoke: best={best['san']} (keys exactly the output contract)")
    print("  [PASS] best_persona_move live smoke on a real engine")


def test_terminal_positions():
    board = chess.Board(CHECKMATE_FEN)
    assert board.is_checkmate(), board.fen()
    assert rerank_moves(board, "attacker") == []
    assert best_persona_move(board, "defender") is None
    board2 = chess.Board(STALEMATE_FEN)
    assert board2.is_stalemate(), board2.fen()
    assert rerank_moves(board2, "positional") == []
    assert best_persona_move(board2, "attacker") is None
    print(f"    checkmate ({board.fen()}): rerank=[] best=None")
    print(f"    stalemate ({board2.fen()}): rerank=[] best=None")
    print("  [PASS] terminal positions: empty list / None, no error")


def test_bad_elo_propagates():
    board = chess.Board(FIXTURES_BY_NAME["obvious sacrifice"]["fen"])
    for bad in (100, 5000):
        try:
            rerank_moves(board, "attacker", elo=bad)
        except ValueError as exc:
            message = str(exc).lower()
            assert "elo" in message, exc
        else:
            raise AssertionError(f"bad elo {bad} did not raise ValueError")
    print("    elo=100 and elo=5000 -> ValueError from configure_strength"
          " (UCI_Elo 1320..3190), uncaught")
    print("  [PASS] out-of-range Elo propagates loudly out of rerank_moves")


def main() -> int:
    print("=== persona_reranker tests (parts 1-2: no live engine) ===")
    captures: dict = {}
    tests_no_engine = [
        test_selector_validation,
        lambda: test_fail_fast_before_engine(),
    ]
    for test in tests_no_engine:
        try:
            test()
        except AssertionError as exc:
            print(f"\n  [FAIL] {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"\n  [FAIL] raised {type(exc).__name__}: {exc}")
            return 1

    print("\n=== parts 3-7 (live engine) ===")
    for test in (
        lambda: test_deterministic_equivalence(captures),
        lambda: test_live_orders_and_regression(captures),
        lambda: test_best_persona_move(captures),
        test_terminal_positions,
        test_bad_elo_propagates,
    ):
        try:
            test()
        except AssertionError as exc:
            print(f"\n  [FAIL] {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"\n  [FAIL] raised {type(exc).__name__}: {exc}")
            return 1
    print("\nAll persona_reranker tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
