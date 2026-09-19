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
