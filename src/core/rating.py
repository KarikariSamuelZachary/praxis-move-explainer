"""
Shared rating math for the Puzzle and Endgame Trainer modes.

calculate_rating_change is the original Puzzles Elo banding (+/-3/5/8) and
is reused verbatim by the Endgame Trainer (services/endgame_session.py).
rating_window / clamp_rating were extracted from routers/puzzles.py when the
Endgame Trainer added a second rating-matched selector: the +/-100 window
around the user's current rating, clamped to the shared [400, 3000] scale,
is now defined once so the two modes cannot drift apart. The routers still
own the skill-level band fallback (routers.puzzles.SKILL_RATING_BANDS),
exactly as before.

The CALC_* constants at the bottom are the ONE place for Blind Calculation
tuning (seed, clamp, K): routers/calc.py imports them from here.
"""

RATING_FLOOR = 400
RATING_CEILING = 3000
RATING_WINDOW = 100

# users.endgame_trainer_rating / users.tactical_rating can be NULL on rows
# that predate the column. routers/puzzles.py falls back to 1100 on the
# update path; the Endgame Trainer mirrors that exact fallback so the two
# modes grade identically.
DEFAULT_TRAINER_RATING = 1100


def calculate_rating_change(
    user_rating: int,
    puzzle_rating: int,
    solved: bool,
) -> int:
    difficulty = puzzle_rating - user_rating

    if solved:
        if difficulty >= 100:
            return 8
        if difficulty <= -100:
            return 3
        return 5

    if difficulty >= 100:
        return -3
    if difficulty <= -100:
        return -8
    return -5


def rating_window(
    user_rating: int,
    floor: int = RATING_FLOOR,
    ceiling: int = RATING_CEILING,
    spread: int = RATING_WINDOW,
) -> tuple[int, int]:
    """The inclusive [min, max] rating window around the user's rating."""
    return (
        max(floor, user_rating - spread),
        min(ceiling, user_rating + spread),
    )


def clamp_rating(rating: int) -> int:
    """Clamp a post-update rating to the shared [400, 3000] scale."""
    return max(RATING_FLOOR, min(RATING_CEILING, rating))


# --- Blind Calculation trainer ---------------------------------------------
# Measured min/max puzzle rating in calc_segments (run corpus-sf19-1):
# SELECT MIN(rating), MAX(rating) -> 1200, 3246. The seed clamp uses these
# corpus bounds (a fresh user's seed must be servable); post-update ratings
# still use the shared clamp_rating above.
CALC_RATING_MIN = 1200
CALC_RATING_MAX = 3246
CALC_SEED_TACTICAL_OFFSET = 200
CALC_SEED_DEFAULT = DEFAULT_TRAINER_RATING
# Standard Elo K for fractional-score updates. calculate_rating_change has
# no K to reuse (banded +/-3/5/8 table), so the factor lives here, next to
# the sibling that uses it.
CALC_K_FACTOR = 32
# Selection window spreads, tried in order until a window is non-empty.
CALC_WINDOW_SPREADS = (100, 200, 400)


def calc_seed_rating(tactical_rating: int | None) -> int:
    """Seed for a new (user, n) row: tactical - 200 clamped to the corpus
    range, else the fixed default."""
    if tactical_rating is None:
        return CALC_SEED_DEFAULT
    return max(CALC_RATING_MIN,
               min(CALC_RATING_MAX, tactical_rating - CALC_SEED_TACTICAL_OFFSET))


def calc_clamp_rating(rating: int) -> int:
    """Post-update clamp for calc ratings: the corpus range, NOT the shared
    [400, 3000] scale. clamp_rating would pin every rating above 3000 down
    to exactly 3000 on each update (destroying information) and, via the
    shared rating_window ceiling, strand the 3000-3246 corpus top where no
    window can reach it. Calc ratings are per-depth and only ever compared
    against calc_segments ratings (measured range 1200-3246)."""
    return max(CALC_RATING_MIN, min(CALC_RATING_MAX, rating))


def calc_expected_score(user_rating: int, segment_rating: int) -> float:
    """Elo expectancy against the segment's puzzle rating."""
    return 1.0 / (1.0 + 10.0 ** ((segment_rating - user_rating) / 400.0))


def calculate_fractional_rating_change(
    user_rating: int,
    puzzle_rating: int,
    score: float,
    k: int = CALC_K_FACTOR,
) -> int:
    """Sibling of calculate_rating_change for fractional scores in [0, 1].

    Standard Elo update rounded to an integer. calculate_rating_change is
    untouched (binary banded outcomes only).
    """
    return round(k * (score - calc_expected_score(user_rating, puzzle_rating)))
