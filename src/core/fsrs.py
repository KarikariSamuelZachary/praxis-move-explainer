from datetime import datetime, timezone

from fsrs import Card, Rating, Scheduler, State

# Single shared scheduler instance. Default parameters/retention/steps are
# used for now; parameter tuning is a separate concern.
scheduler = Scheduler()

# A puzzle is considered mastered when the FSRS-scheduled interval between the
# last review and the next due date exceeds this many days.
MASTERED_INTERVAL_DAYS = 60


def card_from_row(row) -> Card:
    """Reconstruct a py-fsrs Card from a woodpecker_entries row (dict-like)."""
    state = State(row["state"])

    step = row["step"]
    # Mirrors Card.__init__: a Learning card with a NULL step is treated as
    # step 0 (the default learning step).
    if state == State.Learning and step is None:
        step = 0

    return Card(
        card_id=0,
        state=state,
        step=step,
        stability=row["stability"],
        difficulty=row["difficulty"],
        due=row["due"],
        last_review=row["last_review"],
    )


def rating_for(solved: bool) -> Rating:
    """Binary mapping: correct -> Good, incorrect -> Again."""
    return Rating.Good if solved else Rating.Again


def is_lapse(prior_state: State, rating: Rating) -> bool:
    """A lapse is a Review-state card rated Again (Review -> Relearning)."""
    return prior_state == State.Review and rating == Rating.Again


def is_mastered(card: Card) -> bool:
    """True when the freshly-scheduled interval (due - last_review) > 60 days."""
    if card.last_review is None or card.due is None:
        return False
    return (card.due - card.last_review).days > MASTERED_INTERVAL_DAYS


def now_utc() -> datetime:
    return datetime.now(timezone.utc)