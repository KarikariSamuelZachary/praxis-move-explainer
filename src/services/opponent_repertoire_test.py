"""Standalone smoke test for the new OpponentProfileResponse derivations.

Run with: cd src && ../venv/bin/python services/opponent_repertoire_test.py

This file is the test harness for the helpers added to opponent_repertoire.py
(_playing_style_from_sac_freq, _ratings_by_time_class, _openings_lost_against)
and the new weighted_sacrifice_frequency field on compute_opening_results.
Asserts shape + closed-form expected values across a small set of fixtures.
"""
import sys

from services.opponent_repertoire import (
    _openings_lost_against,
    _playing_style_from_sac_freq,
    _ratings_by_time_class,
)
from services.opponent_style import compute_opening_results


def _print_pass(label: str) -> None:
    print(f"  [PASS] {label}")


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def test_playing_style_bands() -> None:
    _print_section("TEST 1: playing-style pill derivation")

    cases = [
        (None, None, "None corpus -> None pill"),
        (0.0, "Passive", "0.0 < 0.05 -> Passive"),
        (0.04, "Passive", "0.04 < 0.05 -> Passive"),
        (0.05, "Balanced", "0.05 hits the lower Balanced band"),
        (0.10, "Balanced", "0.10 in Balanced band"),
        (0.1499, "Balanced", "0.1499 still Balanced (just under Aggressive threshold)"),
        (0.15, "Aggressive", "0.15 hits the Aggressive floor"),
        (0.30, "Aggressive", "0.30 Aggressive"),
        (1.0, "Aggressive", "1.0 (every move a sac) Aggressive"),
    ]
    for sac, expected, label in cases:
        actual = _playing_style_from_sac_freq(sac)
        assert actual == expected, (
            f"playing-style: {label} expected {expected!r}, got {actual!r}"
        )
        _print_pass(label)


def _row(
    username: str, rating: int, time_class: str
) -> tuple:
    """Build a (white_player, black_player, time_class) triple for the per-class
    helper. Opponent always plays white in these fixtures for simplicity."""
    return ({"username": username, "rating": rating}, {"username": "someone", "rating": 1500}, time_class)


def test_ratings_by_time_class_basic() -> None:
    _print_section("TEST 2: per-time-class ratings aggregation")

    # 3 bullet games where opponent plays white (mean 3050).
    # 2 rapid games where opponent plays white (mean 3250).
    # 1 blitz game where opponent plays white (rating 3150).
    # 1 game where opponent plays BLACK with rating 2900 -- this
    # gets included on the BLACK side and contributes to bullet's mean
    # (so 4 bullet games contribute: 3000+3050+3100+2900=12050/4=3012).
    # 1 unknown time_class -- ignored entirely.
    rows = [
        _row("hikaru", 3000, "bullet"),
        _row("hikaru", 3050, "bullet"),
        _row("hikaru", 3100, "bullet"),
        _row("hikaru", 3200, "rapid"),
        _row("hikaru", 3300, "rapid"),
        _row("hikaru", 3150, "blitz"),
        ({"username": "opp", "rating": 9999}, {"username": "hikaru", "rating": 2900}, "bullet"),
        _row("hikaru", 2900, "weekly_puzzle"),
    ]
    white_players = [r[0] for r in rows]
    black_players = [r[1] for r in rows]
    time_classes = [r[2] for r in rows]

    result = _ratings_by_time_class(
        opponent_username="hikaru",
        white_players=white_players,
        black_players=black_players,
        time_classes=time_classes,
    )
    print(f"  ratings_by_time_class = {result}")
    # 4 bullet games (3 white-side + 1 black-side): mean = 12050/4 = 3012.5 -> 3012
    # 2 rapid games (both white-side): mean = 6500/2 = 3250
    # 1 blitz game (white-side): 3150
    # Unknown time_class ("weekly_puzzle") excluded entirely.
    assert result == {"bullet": 3012, "rapid": 3250, "blitz": 3150}, (
        f"expected {{bullet:3012, rapid:3250, blitz:3150}}, got {result}"
    )
    _print_pass("4 bullet games (incl. 1 black-side) -> 3012; 2 rapid -> 3250; 1 blitz -> 3150; unknown time_class ignored")


def test_ratings_by_time_class_empty() -> None:
    _print_section("TEST 3: empty / unknown opponent -> None")

    result = _ratings_by_time_class(
        opponent_username="hikaru",
        white_players=[{"username": "someone", "rating": 1500}],
        black_players=[{"username": "someone", "rating": 1500}],
        time_classes=["bullet"],
    )
    assert result is None, f"expected None for opponent with no games, got {result}"
    _print_pass("no matching games -> None (not empty dict)")


def test_ratings_by_time_class_black_side_excluded_when_wrong_user() -> None:
    _print_section("TEST 4: black-side opponent rating counted only for the OPPONENT")

    # Opponent plays black in row 0; the helper should pick up the
    # opponent's rating from the BLACK player dict and NOT from the
    # WHITE player dict (whose username is "other").
    rows = [
        ({"username": "other", "rating": 9999}, {"username": "hikaru", "rating": 2800}, "rapid"),
    ]
    result = _ratings_by_time_class(
        opponent_username="hikaru",
        white_players=[rows[0][0]],
        black_players=[rows[0][1]],
        time_classes=[rows[0][2]],
    )
    print(f"  ratings_by_time_class = {result}")
    assert result == {"rapid": 2800}, f"expected rapid:2800 from black-side, got {result}"
    _print_pass("opponent's black-side rating (2800) counted; white-side (9999, wrong user) excluded")


def test_openings_lost_against_projection() -> None:
    _print_section("TEST 4: openings_lost_against projection")

    by_opening = {
        "Sicilian Defense": {
            "weighted_count": 7,
            "weighted_wins": 2,
            "weighted_losses": 5,
            "weighted_draws": 0,
            "win_rate": 2 / 7,
        },
        "Italian Game": {
            "weighted_count": 3,
            "weighted_wins": 2,
            "weighted_losses": 1,
            "weighted_draws": 0,
            "win_rate": 2 / 3,
        },
        "Caro-Kann Defense": {
            "weighted_count": 2,
            "weighted_wins": 0,
            "weighted_losses": 0,
            "weighted_draws": 0,
            "win_rate": None,
        },
    }
    rows = _openings_lost_against(by_opening)
    print(f"  projected rows = {rows}")
    assert len(rows) == 2, (
        f"expected 2 rows (Caro-Kann excluded for 0% decisive), got {len(rows)}"
    )
    assert rows[0]["name"] == "Sicilian Defense" and rows[0]["loss_percentage"] == round(5 / 7, 4), (
        f"row[0] should be Sicilian Defense with loss%={round(5/7,4)}, got {rows[0]}"
    )
    assert rows[1]["name"] == "Italian Game" and rows[1]["loss_percentage"] == round(1 / 3, 4), (
        f"row[1] should be Italian Game with loss%={round(1/3,4)}, got {rows[1]}"
    )
    _print_pass("Caro-Kann (all-'*') excluded; Sicilian ahead of Italian by loss%")


def test_openings_lost_against_none_and_empty() -> None:
    _print_section("TEST 5: empty / None input")

    assert _openings_lost_against(None) == [], "None -> []"
    assert _openings_lost_against({}) == [], "{} -> []"
    _print_pass("None and {} -> []")


def test_sac_frequency_on_existing_fixture() -> None:
    _print_section("TEST 6: compute_opening_results exposes weighted_sacrifice_frequency")

    games = [
        {
            "pgn": "[White \"opp\"]\n1. e4 e5 2. Nf3 Nc6 1-0\n\n",
            "end_time": 0,
            "opponent_username": "opp",
        },
        {
            "pgn": "[White \"opp\"]\n1. e4 e5 2. Nf3 Nc6 1/2-1/2\n\n",
            "end_time": 0,
            "opponent_username": "opp",
        },
    ]
    result = compute_opening_results(games)
    print(f"  weighted_sacrifice_frequency = {result.get('weighted_sacrifice_frequency')}")
    assert "weighted_sacrifice_frequency" in result, (
        "weighted_sacrifice_frequency must be a key on the response"
    )
    assert result["weighted_sacrifice_frequency"] == 0.0, (
        f"no-sac corpus should give 0.0, got {result['weighted_sacrifice_frequency']}"
    )
    _print_pass("no-sac corpus -> weighted_sacrifice_frequency=0.0; key is present")


def main() -> int:
    print("=== Running opponent_repertoire derivation smoke tests ===")
    try:
        test_playing_style_bands()
        test_ratings_by_time_class_basic()
        test_ratings_by_time_class_empty()
        test_ratings_by_time_class_black_side_excluded_when_wrong_user()
        test_openings_lost_against_projection()
        test_openings_lost_against_none_and_empty()
        test_sac_frequency_on_existing_fixture()
    except AssertionError as exc:
        print(f"\n  [FAIL] {exc}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())