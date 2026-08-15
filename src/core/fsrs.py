from datetime import datetime, timezone

from fsrs import Card, Rating, Scheduler, State

# Single shared scheduler instance. Default parameters/retention/steps are
# used for now; parameter tuning is a separate concern.
scheduler = Scheduler()

# A puzzle is considered mastered when the FSRS-scheduled interval between the
# last review and the next due date exceeds this many days.
MASTERED_INTERVAL_DAYS = 60


def card_from_row(row) -> Card:
    """Reconstruct a py-fsrs Card from a woodpecker_entries row (dict-like).

    `state` is an INTEGER in woodpecker_entries (1=Learning, 2=Review,
    3=Relearning) — see migration in src/core/migrations.py — so we use
    `State(row["state"])` (value lookup on the IntEnum).
    """
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


def card_from_repertoire_position_row(row) -> Card:
    """Reconstruct a py-fsrs Card from a repertoire_positions row
    (dict-like).

    Same reconstruction contract as `card_from_row`, but
    `repertoire_positions.state` is a TEXT column storing the FSRS State
    enum NAME ('Learning' / 'Review' / 'Relearning') rather than the
    integer value (see migration in src/core/migrations.py: the column is
    declared `TEXT NOT NULL DEFAULT 'Learning'`). We therefore use
    `State[row["state"]]` — bracket lookup on the IntEnum by name —
    instead of the value lookup that `card_from_row` does.

    Mirrors `card_from_row`'s NULL-step handling: a Learning card with a
    NULL step is treated as step 0 (matches py-fsrs's Card.__init__).
    """
    state = State[row["state"]]

    step = row["step"]
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