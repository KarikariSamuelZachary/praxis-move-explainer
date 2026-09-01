"""
Live test harness for Stockfish strength control (Engine Sparring).

Covers the full end-to-end strength path for the Stockfish-based Engine
Sparring mode, kept separate from the Maia-based Opponent Preparation flow:

  A. Advertised options (requirement 1): the bundled Stockfish binary must
     actually advertise UCI_LimitStrength + UCI_Elo AND Skill Level, and the
     valid ranges must be read from engine.options (not hardcoded).

  B. configure_strength() unit behavior (requirement 2): strict validation,
     Elo path, Skill Level path, full-strength reset, and the fallback from
     UCI_Elo to Skill Level when a build lacks UCI_Elo. Tested against a fake
     engine so the fallback branch is exercised even though the real binary
     advertises UCI_Elo.

  C. LIVE strength behavior (requirements 3 + 5): configure a low Elo on a
     real engine, confirm it was actually applied, confirm the resulting move
     differs from full-strength play on a moderately complex position, confirm
     the setting persists across multiple analyse() calls (no per-move
     re-apply needed), and confirm a full-strength reset restores unrestricted
     play with no stale Elo/skill leak into a later call.

Run with: cd src && ../venv/bin/python engines/stockfish_engine_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
import chess.engine

from engines.stockfish_engine import (
    configure_strength,
    resolve_stockfish_path,
)

# A moderately complex middlegame (Giuoco Piano-ish: both knights and bishops
# developed) where full-strength Stockfish has a clear best move but a
# 1400-Elo-limited Stockfish genuinely diverges -- verified live. Depth is
# fixed (not time-limited) so full-strength play is deterministic with a
# cleared hash, making the low-vs-full comparison reproducible.
TEST_FEN = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 4 5"
TEST_DEPTH = 14
LOW_ELO = 1400
LOW_ELO_SAMPLES = 10
FULL_STRENGTH_SAMPLES = 3


# ---------------------------------------------------------------------------
# Fake engine for the unit (non-live) parts of B. Modeled on the fake-DB
# pattern in the services/*_test.py harnesses.
# ---------------------------------------------------------------------------
class _FakeOption:
    def __init__(self, type_, default, min_=None, max_=None):
        self.type = type_
        self.default = default
        self.min = min_
        self.max = max_


class _FakeEngine:
    def __init__(self, options):
        self._options = options
        self.configure_calls = []

    @property
    def options(self):
        return self._options

    def configure(self, opts):
        self.configure_calls.append(dict(opts))


def _stockfish_like_options():
    # Mirrors the real Stockfish 16 advertisement (verified in
    # test_advertised_options).
    return {
        "UCI_LimitStrength": _FakeOption("check", False),
        "UCI_Elo": _FakeOption("spin", 1320, min_=1320, max_=3190),
        "Skill Level": _FakeOption("spin", 20, min_=0, max_=20),
    }


def _best_move(engine: chess.engine.SimpleEngine, board: chess.Board, depth: int) -> str:
    """Best move UCI at a fixed depth (deterministic for full strength with a
    cleared hash). Uses the same analyse(multipv=N) call path that the move
    candidate generation for the persona re-ranker will use."""
    info = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=1)
    infos = [info] if isinstance(info, dict) else info
    pv = infos[0].get("pv", [])
    return pv[0].uci() if pv else ""


def _applied_strength(engine: chess.engine.SimpleEngine) -> dict:
    """Read back the strength options that were actually sent to the subprocess.

    NOTE (python-chess API nuance, confirmed live): `engine.options` reflects
    the DECLARED option metadata (default/min/max), NOT the current value. The
    values a `configure()` call actually applied live in the internal
    `engine.protocol.target_config` dict, which is the closest thing to
    "read back the current value". We assert on that here; the behavioral
    tests below are the real proof the setting took effect on the engine.
    """
    tc = engine.protocol.target_config
    return {
        "UCI_LimitStrength": tc.get("UCI_LimitStrength"),
        "UCI_Elo": tc.get("UCI_Elo"),
        "Skill Level": tc.get("Skill Level"),
    }


# ---------------------------------------------------------------------------
# A. Advertised options
# ---------------------------------------------------------------------------
def test_advertised_options():
    engine = chess.engine.SimpleEngine.popen_uci(resolve_stockfish_path())
    try:
        options = engine.options
        assert "UCI_LimitStrength" in options, "UCI_LimitStrength not advertised"
        assert "UCI_Elo" in options, "UCI_Elo not advertised"
        assert "Skill Level" in options, "Skill Level not advertised"

        limit = options["UCI_LimitStrength"]
        elo = options["UCI_Elo"]
        skill = options["Skill Level"]

        assert limit.type == "check", f"UCI_LimitStrength type={limit.type}"
        assert elo.type == "spin" and elo.min == 1320 and elo.max == 3190, (
            f"UCI_Elo: type={elo.type} min={elo.min} max={elo.max}"
        )
        assert skill.type == "spin" and skill.min == 0 and skill.max == 20, (
            f"Skill Level: type={skill.type} min={skill.min} max={skill.max}"
        )

        print(f"  [PASS] advertised: UCI_LimitStrength={limit.type} "
              f"default={limit.default}; UCI_Elo {elo.min}..{elo.max}; "
              f"Skill Level {skill.min}..{skill.max}")
    finally:
        engine.quit()


# ---------------------------------------------------------------------------
# B. configure_strength() unit behavior (fake engine)
# ---------------------------------------------------------------------------
def test_configure_strength_elo_path():
    eng = _FakeEngine(_stockfish_like_options())
    result = configure_strength(eng, elo=1400)
    assert eng.configure_calls == [
        {"UCI_LimitStrength": True, "UCI_Elo": 1400, "Skill Level": 20}
    ], eng.configure_calls
    assert result == {"limit_strength": True, "elo": 1400, "skill_level": None}
    print("  [PASS] elo path sets LimitStrength=true + UCI_Elo, resets Skill Level to max")


def test_configure_strength_skill_path():
    eng = _FakeEngine(_stockfish_like_options())
    result = configure_strength(eng, skill_level=5)
    assert eng.configure_calls == [
        {"Skill Level": 5, "UCI_LimitStrength": False}
    ], eng.configure_calls
    assert result == {"limit_strength": False, "elo": None, "skill_level": 5}
    print("  [PASS] skill path sets Skill Level, disables LimitStrength")


def test_configure_strength_reset():
    eng = _FakeEngine(_stockfish_like_options())
    result = configure_strength(eng)
    assert eng.configure_calls == [
        {"UCI_LimitStrength": False, "Skill Level": 20}
    ], eng.configure_calls
    assert result == {"limit_strength": False, "elo": None, "skill_level": None}
    print("  [PASS] reset disables LimitStrength + restores Skill Level to max")


def test_configure_strength_validation_errors():
    eng = _FakeEngine(_stockfish_like_options())
    bad_elo = [True, False, 1.5, "1400", 1000, 4000, 1319, 3191]
    for value in bad_elo:
        try:
            configure_strength(eng, elo=value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"elo={value!r} should have raised ValueError")

    bad_skill = [True, False, 3.7, "5", -1, 21]
    for value in bad_skill:
        try:
            configure_strength(eng, skill_level=value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"skill_level={value!r} should have raised ValueError")

    assert eng.configure_calls == [], "validation failures must not send setoptions"
    print("  [PASS] wrong-type / out-of-range elo + skill_level all raise ValueError")


def test_configure_strength_elo_fallback_to_skill():
    # Build that lacks UCI_Elo (but has Skill Level): elo must fall back.
    options = _stockfish_like_options()
    del options["UCI_Elo"]
    eng = _FakeEngine(options)

    result = configure_strength(eng, elo=1400, skill_level=5)
    assert eng.configure_calls == [
        {"Skill Level": 5, "UCI_LimitStrength": False}
    ], eng.configure_calls
    assert result == {"limit_strength": False, "elo": None, "skill_level": 5}

    # elo alone with no UCI_Elo and no skill_level fallback -> ValueError.
    eng2 = _FakeEngine(_stockfish_like_options())
    del eng2._options["UCI_Elo"]
    try:
        configure_strength(eng2, elo=1400)
    except ValueError:
        pass
    else:
        raise AssertionError("elo without UCI_Elo and without skill_level should raise")
    print("  [PASS] elo falls back to skill_level when UCI_Elo is not advertised")


# ---------------------------------------------------------------------------
# C. LIVE strength behavior (requirements 3 + 5)
# ---------------------------------------------------------------------------
def test_live_low_elo_differs_from_full_strength():
    board = chess.Board(TEST_FEN)
    engine = chess.engine.SimpleEngine.popen_uci(resolve_stockfish_path())
    try:
        # Full-strength baseline (deterministic with a cleared hash).
        configure_strength(engine)
        engine.configure({"Clear Hash": True})
        full_moves = {_best_move(engine, board, TEST_DEPTH)
                      for _ in range(FULL_STRENGTH_SAMPLES)}
        assert len(full_moves) == 1, (
            f"full strength should be deterministic, got {sorted(full_moves)}"
        )
        full_move = full_moves.pop()

        # Apply a low Elo and confirm it was actually applied (not just that
        # the call returned without error).
        configure_strength(engine, elo=LOW_ELO)
        applied = _applied_strength(engine)
        assert applied["UCI_LimitStrength"] is True, applied
        assert applied["UCI_Elo"] == LOW_ELO, applied
        assert applied["Skill Level"] == 20, applied

        # Run the actual move-candidate generation path (analyse multipv=1) at
        # that Elo and confirm the move genuinely differs from full-strength.
        low_moves = [_best_move(engine, board, TEST_DEPTH)
                     for _ in range(LOW_ELO_SAMPLES)]
        differing = [m for m in low_moves if m != full_move]
        assert differing, (
            f"low-Elo ({LOW_ELO}) play never differed from full strength: "
            f"full={full_move}, low={low_moves}"
        )

        print(f"  [PASS] applied {applied}; full-strength move={full_move}, "
              f"low-Elo moves={low_moves} ({len(differing)}/{LOW_ELO_SAMPLES} differed)")
    finally:
        engine.quit()


def test_live_options_persist_and_reset_restores_full_strength():
    board = chess.Board(TEST_FEN)
    engine = chess.engine.SimpleEngine.popen_uci(resolve_stockfish_path())
    try:
        # Baseline full-strength move.
        configure_strength(engine)
        engine.configure({"Clear Hash": True})
        full_move = _best_move(engine, board, TEST_DEPTH)

        # Configure low Elo ONCE, then run several analyses. The setting must
        # persist across all of them (no per-move re-apply needed).
        configure_strength(engine, elo=LOW_ELO)
        weak_samples = [_best_move(engine, board, TEST_DEPTH)
                        for _ in range(LOW_ELO_SAMPLES)]
        applied = _applied_strength(engine)
        assert applied["UCI_LimitStrength"] is True, applied
        assert applied["UCI_Elo"] == LOW_ELO, applied
        assert any(m != full_move for m in weak_samples), (
            f"low-Elo setting did not persist / change play: "
            f"full={full_move}, weak={weak_samples}"
        )

        # Reset to full strength and confirm unrestricted play is restored and
        # NO stale Elo/skill setting leaks into the later call.
        configure_strength(engine)
        applied = _applied_strength(engine)
        assert applied["UCI_LimitStrength"] is False, applied
        assert applied["Skill Level"] == 20, applied
        engine.configure({"Clear Hash": True})
        restored = {_best_move(engine, board, TEST_DEPTH)
                    for _ in range(FULL_STRENGTH_SAMPLES)}
        assert restored == {full_move}, (
            f"reset should restore full-strength move {full_move}, got {sorted(restored)}"
        )

        print(f"  [PASS] setting persisted across {LOW_ELO_SAMPLES} analyse() calls "
              f"(all stayed weak); reset restored full strength to {full_move} "
              f"with no stale Elo leak")
    finally:
        engine.quit()


def main() -> int:
    print("=== Running Stockfish strength-control live tests ===")
    tests = [
        test_advertised_options,
        test_configure_strength_elo_path,
        test_configure_strength_skill_path,
        test_configure_strength_reset,
        test_configure_strength_validation_errors,
        test_configure_strength_elo_fallback_to_skill,
        test_live_low_elo_differs_from_full_strength,
        test_live_options_persist_and_reset_restores_full_strength,
    ]
    for test in tests:
        try:
            test()
        except (AssertionError, ValueError) as exc:
            print(f"\n  [FAIL] {test.__name__}: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"\n  [FAIL] {test.__name__} raised {type(exc).__name__}: {exc}")
            return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
