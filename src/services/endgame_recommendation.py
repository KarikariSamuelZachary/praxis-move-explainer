"""
Endgame weakness recommendation: pick the material category the user is
weakest at, from the failure/review data the app already persists.

WHY THIS EXISTS
===============
The Train page's "Recommended For You" card needs a defensible answer to
"which endgame should I drill next?". There is no per-theme skill model in
the app: the rated trainer is stateless and only FAILED drills are
persisted (as endgame Woodpecker cards), while practice writes nothing at
all. This module therefore ranks the drillable material categories by a
weakness score built from exactly what exists:

  * endgame_woodpecker_entries -- one active (unmastered) card per failed
    position, carrying the category (its `theme` column), lapses and age;
  * endgame_woodpecker_attempts -- one row per review attempt, carrying
    solved_correctly, hints_used and attempted_at.

THE SCORE (and why not raw failure counts)
==========================================
Raw failures are exposure-biased: the category played most accumulates the
most failures. A raw fail RATE is also wrong at low volume (1/1 = 100%).
So the score combines volume, repetition, rate and recency:

    weakness = 0.40 * min(active_failures / 5, 1)   # failed & unmastered
             + 0.25 * min(lapses / 5, 1)            # repeat failures
             + 0.20 * review_fail_rate              # last WINDOW_DAYS
             + 0.10 * hint_rate                     # last WINDOW_DAYS
             + 0.05 * min(recent_failures / 3, 1)   # last RECENT_DAYS

Every term is bounded to [0, 1], so the score is too. The weights are
deliberately round and documented rather than learned: at this data volume
a fitted model would overfit one user's history. The terms are recomputed
per request, so tuning a weight is a code change, not a migration.

The two rate terms are additionally gated on MIN_EVIDENCE_ATTEMPTS review
attempts: a single failed review (1/1 = 100%) is noise and must not
outrank a category with a much larger failure backlog. Verified against
the dev data, where it did exactly that before the gate.

GATES
=====
  * content: a category needs MIN_CONTENT_POSITIONS sourced rows -- a
    recommendation the drill pool cannot honor is a broken recommendation;
  * evidence: >= MIN_EVIDENCE_FAILURES active failures OR >=
    MIN_EVIDENCE_ATTEMPTS review attempts. Below that, any rate is noise;
  * mastery: only unmastered entries feed the score, so a category the
    user has cleaned up cannot win on old history.

FALLBACK
========
When nothing clears the evidence gate (a new user, or a user with no
failures yet) the recommendation is a starter category: the category whose
average topic difficulty is closest to the user's endgame rating, or
pure_pawn for an unrated user (king-and-pawn fundamentals). The response
marks this with is_fallback=True so the card can phrase it as a starting
point rather than a weakness.

WHAT THIS DELIBERATELY DOES NOT SEE
===================================
Solved rated drills and practice drills are not persisted anywhere, so
they cannot inform the score (a category the user is silently good at is
invisible, not "weak"). If recommendations should reflect those too, the
prerequisite is an attempt log written on every resolution -- see the
module docstring's counterpart in routers/endgames.py. Until then this is
honestly a "most failed-and-lapsing category" recommender.
"""
import logging
from typing import List, Optional, Tuple

from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

log = logging.getLogger(__name__)

# --- Tunables (documented, not learned -- see the module docstring) ------
WINDOW_DAYS = 60
RECENT_DAYS = 30
MIN_CONTENT_POSITIONS = 50
MIN_EVIDENCE_FAILURES = 2
MIN_EVIDENCE_ATTEMPTS = 5

W_FAILURES = 0.40
W_LAPSES = 0.25
W_REVIEW_RATE = 0.20
W_HINT_RATE = 0.10
W_RECENT = 0.05

# Values at which each bounded term reaches 1.0.
N_FAILURES_FOR_FULL = 5.0
N_LAPSES_FOR_FULL = 5.0
N_RECENT_FOR_FULL = 3.0

# The unrated cold-start category: king-and-pawn fundamentals, and one of
# the two largest sourced pools (7,954 rows), so the drill pool always has
# content for it.
STARTER_CATEGORY = "pure_pawn"


class CategoryWeakness(BaseModel):
    """One category's weakness inputs, all from persisted data. The score
    itself is computed by weakness_score() so the math stays unit-testable
    without a database."""

    category: str
    positions: int
    avg_difficulty: Optional[float] = None
    # Active (unmastered) failed positions in this category.
    active_failures: int = 0
    # Sum of FSRS lapses over those active entries (repeat failures).
    lapses: int = 0
    # Active failures added in the last RECENT_DAYS.
    recent_failures: int = 0
    # Review attempts in the last WINDOW_DAYS.
    attempts: int = 0
    failed_attempts: int = 0
    hinted_attempts: int = 0
    last_failed_at: Optional[object] = None
    last_attempt_at: Optional[object] = None


class EndgameRecommendation(BaseModel):
    """The Recommended For You card payload.

    `category` is a material category (the same key the practice endpoint
    accepts), `reason` is the human-readable justification shown on the
    card, and `sample_fen` is a representative position for the thumbnail
    (the user's most recent failed position in that category when there is
    one, else a deterministic sourced position from the category).
    `is_fallback` marks a cold-start/difficulty-matched pick, so the card
    can phrase it as a starting point rather than a diagnosed weakness.
    """

    category: str
    reason: str
    is_fallback: bool = False
    sample_fen: Optional[str] = None


def review_fail_rate(row: CategoryWeakness) -> float:
    """Failed review attempts / all review attempts in the window (0 when
    there were no attempts -- the evidence gate keeps that case from
    winning)."""
    return row.failed_attempts / row.attempts if row.attempts else 0.0


def hint_rate(row: CategoryWeakness) -> float:
    """Hint-assisted review attempts / all review attempts in the window."""
    return row.hinted_attempts / row.attempts if row.attempts else 0.0


def rate_terms_apply(row: CategoryWeakness) -> bool:
    """Whether the review-rate terms are meaningful for this category.

    Below MIN_EVIDENCE_ATTEMPTS attempts a rate is noise: one failed review
    (1/1 = 100%) must not outrank a category with a much larger failure
    backlog. The volume terms (failures, lapses, recency) are unaffected."""
    return row.attempts >= MIN_EVIDENCE_ATTEMPTS


def _bounded(value: float, full: float) -> float:
    return min(max(value, 0.0), full) / full


def weakness_score(row: CategoryWeakness) -> float:
    """The weighted weakness score in [0, 1]. See the module docstring."""
    score = (
        W_FAILURES * _bounded(row.active_failures, N_FAILURES_FOR_FULL)
        + W_LAPSES * _bounded(row.lapses, N_LAPSES_FOR_FULL)
        + W_RECENT * _bounded(row.recent_failures, N_RECENT_FOR_FULL)
    )
    if rate_terms_apply(row):
        score += W_REVIEW_RATE * review_fail_rate(row)
        score += W_HINT_RATE * hint_rate(row)
    return score


def qualifies(row: CategoryWeakness) -> bool:
    """The minimum-evidence gate: enough failures OR enough review attempts
    for the score to mean something."""
    return (
        row.active_failures >= MIN_EVIDENCE_FAILURES
        or row.attempts >= MIN_EVIDENCE_ATTEMPTS
    )


def _pick_fallback(
    rows: List[CategoryWeakness], rating: Optional[int]
) -> CategoryWeakness:
    """Cold start: difficulty-matched starter category.

    Unrated -> STARTER_CATEGORY (or the first category by name when the
    pool does not contain it). Rated -> the category whose average topic
    difficulty is closest to the rating. Deterministic in both cases.
    """
    if rating is None:
        preferred = [row for row in rows if row.category == STARTER_CATEGORY]
        pool = preferred or rows
        return min(pool, key=lambda row: row.category)
    return min(
        rows,
        key=lambda row: (abs((row.avg_difficulty or 0.0) - rating), row.category),
    )


def pick_category(
    rows: List[CategoryWeakness], rating: Optional[int]
) -> Tuple[CategoryWeakness, bool]:
    """Pick (category, is_fallback) from the per-category inputs.

    Highest weakness_score among the categories that clear the evidence
    gate; ties break by more active failures, then more lapses, then the
    category name (deterministic). With no qualifying category, falls back
    to the difficulty-matched starter. Raises ValueError for an empty pool
    (the caller has no content to recommend -- a deployment problem, not a
    user state).
    """
    if not rows:
        raise ValueError("no endgame categories with enough content to recommend")

    qualified = [row for row in rows if qualifies(row)]
    if qualified:
        winner = max(
            qualified,
            key=lambda row: (
                weakness_score(row),
                row.active_failures,
                row.lapses,
                row.category,
            ),
        )
        return winner, False
    return _pick_fallback(rows, rating), True


def reason_for(
    row: CategoryWeakness, *, is_fallback: bool, rating: Optional[int]
) -> str:
    """The card's one-line justification. Names the strongest signals that
    actually drove the pick, so the recommendation is auditable when it
    feels wrong."""
    if is_fallback:
        if rating is None:
            return "The best place to start your endgame training"
        return "A good next step for your endgame rating"

    parts: List[str] = []
    if row.active_failures:
        noun = "position" if row.active_failures == 1 else "positions"
        parts.append(f"{row.active_failures} failed {noun} not yet mastered")
    if row.lapses:
        noun = "repeat failure" if row.lapses == 1 else "repeat failures"
        parts.append(f"{row.lapses} {noun}")
    if not parts and row.attempts and rate_terms_apply(row):
        parts.append(f"{round(review_fail_rate(row) * 100)}% of recent reviews failed")
    if not parts and row.hinted_attempts:
        parts.append("Recent reviews needed hints")
    return " · ".join(parts) if parts else "Your weakest endgame category"


def _load_rows(cur, clerk_id: str) -> List[CategoryWeakness]:
    """The per-category inputs for one user. Content is the base set (only
    categories with MIN_CONTENT_POSITIONS sourced rows survive); the two
    user-data CTEs LEFT JOIN onto it so a category with no history is still
    a candidate for the cold-start fallback."""
    cur.execute(
        """
        WITH active AS (
            SELECT e.theme AS category,
                   COUNT(*) AS active_failures,
                   COALESCE(SUM(e.lapses), 0) AS lapses,
                   COUNT(*) FILTER (
                       WHERE e.added_at >= NOW() - make_interval(days => %s)
                   ) AS recent_failures,
                   MAX(e.added_at) AS last_failed_at
            FROM endgame_woodpecker_entries e
            WHERE e.user_id = %s
              AND e.is_mastered = FALSE
            GROUP BY e.theme
        ),
        review AS (
            SELECT e.theme AS category,
                   COUNT(*) AS attempts,
                   COUNT(*) FILTER (
                       WHERE a.solved_correctly = FALSE
                   ) AS failed_attempts,
                   COUNT(*) FILTER (
                       WHERE a.hints_used > 0
                   ) AS hinted_attempts,
                   MAX(a.attempted_at) AS last_attempt_at
            FROM endgame_woodpecker_attempts a
            JOIN endgame_woodpecker_entries e ON e.id = a.entry_id
            WHERE a.user_id = %s
              AND a.attempted_at >= NOW() - make_interval(days => %s)
            GROUP BY e.theme
        ),
        content AS (
            SELECT t.category,
                   COUNT(*) AS positions,
                   AVG(t.difficulty_rating) AS avg_difficulty
            FROM endgame_positions p
            JOIN endgame_topics t ON t.id = p.topic_id
            WHERE p.source_puzzle_id IS NOT NULL
            GROUP BY t.category
        )
        SELECT c.category,
               c.positions,
               c.avg_difficulty,
               COALESCE(act.active_failures, 0) AS active_failures,
               COALESCE(act.lapses, 0) AS lapses,
               COALESCE(act.recent_failures, 0) AS recent_failures,
               act.last_failed_at,
               COALESCE(r.attempts, 0) AS attempts,
               COALESCE(r.failed_attempts, 0) AS failed_attempts,
               COALESCE(r.hinted_attempts, 0) AS hinted_attempts,
               r.last_attempt_at
        FROM content c
        LEFT JOIN active act ON act.category = c.category
        LEFT JOIN review r ON r.category = c.category
        WHERE c.positions >= %s
        ORDER BY c.category ASC
        """,
        (RECENT_DAYS, clerk_id, clerk_id, WINDOW_DAYS, MIN_CONTENT_POSITIONS),
    )
    return [CategoryWeakness(**row) for row in cur.fetchall()]


def load_category_weakness(conn, clerk_id: str) -> List[CategoryWeakness]:
    """Public read of the per-category inputs (tests/ops); the endpoint
    uses recommend_category(), which shares one cursor for everything."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        return _load_rows(cur, clerk_id)


def _sample_fen(cur, clerk_id: str, category: str) -> Optional[str]:
    """A representative position for the card's thumbnail: the user's most
    recent active failed position in this category when there is one, else
    a deterministic sourced position from the category. None only when the
    category has no sourced rows at all (cannot happen for a recommended
    category -- it cleared the content gate)."""
    cur.execute(
        """
        SELECT p.fen
        FROM endgame_woodpecker_entries e
        JOIN endgame_positions p ON p.id = e.position_id
        WHERE e.user_id = %s
          AND e.is_mastered = FALSE
          AND e.theme = %s
        ORDER BY e.added_at DESC, e.id ASC
        LIMIT 1
        """,
        (clerk_id, category),
    )
    row = cur.fetchone()
    if row is not None:
        return row["fen"]

    cur.execute(
        """
        SELECT p.fen
        FROM endgame_positions p
        JOIN endgame_topics t ON t.id = p.topic_id
        WHERE t.category = %s
          AND p.source_puzzle_id IS NOT NULL
        ORDER BY p.created_at ASC, p.id ASC
        LIMIT 1
        """,
        (category,),
    )
    row = cur.fetchone()
    return row["fen"] if row is not None else None


def recommend_category(conn, clerk_id: str) -> Optional[EndgameRecommendation]:
    """The endpoint's entry point: one recommendation for this user, or None
    when the deployment has no drillable content at all (the route maps that
    to a 404, the same "empty library" signal GET /next uses).

    Read-only: one cursor, no writes, no commit (the caller owns the
    transaction, matching endgame_library's selectors)."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT endgame_trainer_rating FROM users WHERE clerk_id = %s",
            (clerk_id,),
        )
        user = cur.fetchone()
        # A missing user row is not fatal here: the recommendation degrades
        # to the unrated starter rather than 404ing the card (unlike
        # GET /next, nothing user-scoped is being served).
        rating = user["endgame_trainer_rating"] if user is not None else None

        rows = _load_rows(cur, clerk_id)
        if not rows:
            return None

        winner, is_fallback = pick_category(rows, rating)
        sample_fen = _sample_fen(cur, clerk_id, winner.category)

    log.info(
        "endgame recommendation for %s: %s (fallback=%s, score=%.3f, "
        "failures=%d, lapses=%d, attempts=%d)",
        clerk_id,
        winner.category,
        is_fallback,
        weakness_score(winner),
        winner.active_failures,
        winner.lapses,
        winner.attempts,
    )
    return EndgameRecommendation(
        category=winner.category,
        reason=reason_for(winner, is_fallback=is_fallback, rating=rating),
        is_fallback=is_fallback,
        sample_fen=sample_fen,
    )
