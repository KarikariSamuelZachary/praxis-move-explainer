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
