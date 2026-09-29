"""Standalone smoke test for the new OpponentProfileResponse derivations.

Run with: cd src && ../venv/bin/python services/opponent_repertoire_test.py

This file is the test harness for the helpers added to opponent_repertoire.py
(_playing_style_from_sac_freq, _ratings_by_time_class, _openings_lost_against)
and the new weighted_sacrifice_frequency field on compute_opening_results.
Asserts shape + closed-form expected values across a small set of fixtures.
"""
import sys

from services.opponent_repertoire import (
    _blunder_ply,
    _blunder_precedence,
    _initial_opening_game_id,
    _openings_lost_against,
    _order_opening_games,
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
    assert rows[0]["legacy"] is True and rows[0]["color"] is None, (
        f"weighted-only buckets must project through the legacy path, got {rows[0]}"
    )
    _print_pass("Caro-Kann (all-'*') excluded; Sicilian ahead of Italian by loss%; legacy tagged")


def test_openings_lost_against_raw_projection() -> None:
    _print_section(
        "TEST 4b: raw projection (color split, floor, shrinkage, low-sample backfill)"
    )

    def _raw(count, wins, losses, draws, color):
        return {
            "raw_count": count,
            "raw_wins": wins,
            "raw_losses": losses,
            "raw_draws": draws,
            "raw_by_color": {
                color: {"count": count, "wins": wins, "losses": losses, "draws": draws}
            },
            "raw_by_time_class": {
                "blitz": {"count": count, "wins": wins, "losses": losses, "draws": draws}
            },
        }

    by_opening = {
        "Sicilian Defense": _raw(8, 1, 6, 1, "black"),
        "Italian Game": _raw(6, 3, 2, 1, "white"),
        "Caro-Kann Defense": _raw(4, 0, 3, 0, "black"),  # low sample (3..4)
        "French Defense": _raw(2, 0, 2, 0, "black"),  # below backfill floor
        "_unknown": _raw(10, 5, 5, 0, "white"),  # prior-only, never a row
    }
    rows = _openings_lost_against(by_opening)
    print(f"  projected rows = {rows}")

    assert len(rows) == 3, (
        f"expected 2 qualified + 1 low-sample backfill, got {len(rows)}: {rows}"
    )
    assert rows[0]["family"] == "Sicilian Defense" and rows[0]["color"] == "black", (
        f"row[0] should be Sicilian as black, got {rows[0]}"
    )
    assert rows[0]["low_sample"] is False, "row[0] cleared the raw floor"
    assert rows[1]["family"] == "Italian Game" and rows[1]["color"] == "white", (
        f"row[1] should be Italian as white, got {rows[1]}"
    )
    assert rows[2]["family"] == "Caro-Kann Defense" and rows[2]["low_sample"] is True, (
        f"row[2] should be the low-sample Caro-Kann backfill, got {rows[2]}"
    )
    assert all(row["family"] != "French Defense" for row in rows), (
        "a 2-game bucket must not surface"
    )
    assert all(row["family"] != "_unknown" for row in rows), (
        "_unknown must never be a displayed row"
    )
    assert rows[0]["name"] == "Sicilian Defense · as Black", (
        f"row name should carry the side, got {rows[0]['name']}"
    )

    # Shrinkage: prior = overall 18/29 (including _unknown); Sicilian 6/8 raw
    # must land strictly between the prior and the raw rate.
    prior = 18 / 29
    expected = (6 + 5 * prior) / (8 + 5)
    assert rows[0]["loss_rate"] == round(expected, 4), (
        f"shrunk rate should be {round(expected, 4)}, got {rows[0]['loss_rate']}"
    )
    assert prior < rows[0]["loss_rate"] < 6 / 8, (
        "shrinkage must pull the raw rate toward the overall rate"
    )
    assert rows[0]["loss_percentage"] == rows[0]["loss_rate"], (
        "transitional loss_percentage must mirror loss_rate"
    )
    assert rows[0]["games"] == 8 and rows[0]["raw_games"] == 8, (
        "transitional games must mirror raw_games"
    )
    _print_pass(
        "color split, raw floor, overall-rate shrinkage, _unknown drop, "
        "low-sample backfill all correct"
    )


def test_openings_lost_against_raw_no_backfill_when_enough_qualify() -> None:
    _print_section("TEST 4c: no low-sample backfill when >= 3 rows qualify")

    def _raw(count, wins, losses, draws):
        return {
            "raw_count": count,
            "raw_wins": wins,
            "raw_losses": losses,
            "raw_draws": draws,
            "raw_by_color": {
                "white": {"count": count, "wins": wins, "losses": losses, "draws": draws}
            },
            "raw_by_time_class": {
                "rapid": {"count": count, "wins": wins, "losses": losses, "draws": draws}
            },
        }

    by_opening = {
        "Opening A": _raw(6, 0, 6, 0),
        "Opening B": _raw(5, 0, 5, 0),
        "Opening C": _raw(7, 1, 6, 0),
        "Opening D": _raw(3, 0, 3, 0),  # low sample; must NOT appear
    }
    rows = _openings_lost_against(by_opening)
    assert len(rows) == 3, f"expected exactly the 3 qualified rows, got {rows}"
    assert all(row["low_sample"] is False for row in rows), (
        f"no backfill should happen when 3 rows qualify, got {rows}"
    )
    assert all(row["family"] != "Opening D" for row in rows), (
        "the 3-game bucket must be excluded when enough rows qualify"
    )
    assert [row["loss_rate"] for row in rows] == sorted(
        [row["loss_rate"] for row in rows], reverse=True
    ), f"rows must be sorted by descending shrunk loss rate, got {rows}"
    _print_pass("3 qualified rows returned; low-sample bucket excluded; sorted desc")


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


def test_blunder_ply_conversion() -> None:
    _print_section("TEST 7: blunder move_number + side -> ply conversion")

    cases = [
        (1, "white", 1),
        (1, "black", 2),
        (7, "white", 13),
        (7, "black", 14),
        (15, "white", 29),
        (15, "black", 30),
    ]
    for move_number, side, expected in cases:
        actual = _blunder_ply(move_number, side)
        assert actual == expected, (
            f"{side} move {move_number} should be ply {expected}, got {actual}"
        )
    _print_pass("white Nth = 2N-1, black Nth = 2N (no off-by-one for Black)")


def test_opening_game_ordering_and_initial() -> None:
    _print_section("TEST 8: opening game list ordering + initial game")

    games = [
        {"game_id": "w-new", "result": "win", "end_time": 300, "first_blunder": None},
        {"game_id": "l-old", "result": "loss", "end_time": 100, "first_blunder": None},
        {"game_id": "l-new-nb", "result": "loss", "end_time": 200, "first_blunder": None},
        {"game_id": "l-old-bl", "result": "loss", "end_time": 50, "first_blunder": {"ply": 7}},
        {"game_id": "d-mid", "result": "draw", "end_time": 150, "first_blunder": None},
        {"game_id": "l-new-bl", "result": "loss", "end_time": 250, "first_blunder": {"ply": 9}},
        {"game_id": "star", "result": "*", "end_time": 999, "first_blunder": None},
    ]

    ordered = _order_opening_games(games)
    assert [g["game_id"] for g in ordered] == [
        "l-new-bl",
        "l-new-nb",
        "l-old",
        "l-old-bl",
        "d-mid",
        "w-new",
        "star",
    ], "expected losses newest-first, then draws, wins, then '*'"

    assert _initial_opening_game_id(ordered) == "l-new-bl"

    without_blunders = [g for g in ordered if g["first_blunder"] is None]
    assert _initial_opening_game_id(without_blunders) == "l-new-nb"

    no_losses = [g for g in ordered if g["result"] != "loss"]
    assert _initial_opening_game_id(no_losses) == "d-mid"

    _print_pass("losses->draws->wins, newest first; initial = newest blunder-loss")


def test_blunder_precedence() -> None:
    _print_section("TEST 9: jump target = earliest blunder, else earliest mistake")

    mistake_early = {"classification": "mistake", "ply": 10}
    mistake_late = {"classification": "mistake", "ply": 40}
    blunder_early = {"classification": "blunder", "ply": 20}
    blunder_late = {"classification": "blunder", "ply": 60}

    assert _blunder_precedence(blunder_late) < _blunder_precedence(mistake_early), (
        "a later blunder must still outrank an earlier mistake"
    )
    assert _blunder_precedence(blunder_early) < _blunder_precedence(blunder_late), (
        "among blunders the earliest ply wins"
    )
    assert _blunder_precedence(mistake_early) < _blunder_precedence(mistake_late), (
        "among mistakes the earliest ply wins"
    )
    assert _blunder_precedence(blunder_early) < _blunder_precedence(mistake_late)

    winner = min(
        [mistake_early, blunder_late, blunder_early, mistake_late],
        key=_blunder_precedence,
    )
    assert winner is blunder_early, "four-error game must jump to the first blunder"

    winner_no_blunders = min(
        [mistake_late, mistake_early], key=_blunder_precedence
    )
    assert winner_no_blunders is mistake_early, (
        "blunder-free game must jump to the first mistake"
    )

    _print_pass("blunder outranks mistake; earliest ply inside each class")


def main() -> int:
    print("=== Running opponent_repertoire derivation smoke tests ===")
    try:
        test_playing_style_bands()
        test_ratings_by_time_class_basic()
        test_ratings_by_time_class_empty()
        test_ratings_by_time_class_black_side_excluded_when_wrong_user()
        test_openings_lost_against_projection()
        test_openings_lost_against_raw_projection()
        test_openings_lost_against_raw_no_backfill_when_enough_qualify()
        test_openings_lost_against_none_and_empty()
        test_sac_frequency_on_existing_fixture()
        test_blunder_ply_conversion()
        test_opening_game_ordering_and_initial()
        test_blunder_precedence()
    except AssertionError as exc:
        print(f"\n  [FAIL] {exc}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())