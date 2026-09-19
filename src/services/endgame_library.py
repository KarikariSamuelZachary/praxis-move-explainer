"""
Endgame library selection: pick the next drill position from the seeded
content library (endgame_topics / endgame_positions).

select_drill_position(conn, category=..., topic_id=...) returns ONE
randomly drawn position from the matching pool, for the browse/drill entry
point: either a whole category ("any rook ending") or one specific topic
("just Lucena"). Exactly one of the two selectors must be given.

SELECTION PRECEDENT (checked against routers/puzzles.py before building)
========================================================================
The brief was to mirror Puzzles' theme-filter selection as "pooled-random
from the filtered pool". The actual Puzzles code is a random-pivot keyset
scan, not a uniform pooled draw: it picks a random 5-char puzzle id, then
`WHERE id >= pivot AND themes @> ... ORDER BY id LIMIT N`, wrapping around
with `id < pivot ORDER BY id DESC` when the pivot lands near the end. That
is a randomized START into a contiguous id-ordered scan, built because the
puzzles table is ~500k rows where `ORDER BY random()` does not scale. It
is not a uniform random sample of the filtered pool, and it never returns
a single row.

The endgame library is tiny by comparison (10 seeded positions; a mature
topic holds ~100), so this module draws the true pooled-random the brief
asked for: `ORDER BY random() LIMIT 1` over the filtered pool. Same intent
(random draw, never sequential/ordered), simpler and exactly uniform at
this scale; the keyset trick's complexity buys nothing below ~100k rows.
If the library ever grows enormous, the Puzzles pattern is the reference
for that scale.

EMPTY POOLS ARE A RESULT, NOT AN ERROR
======================================
A category with no seeded content yet (9 of the 10 categories today)
returns EndgameSelection(status="empty", reason=...) -- never a 404/500
and never a bare None the frontend has to guess about. The caller decides
presentation.

NON-REPEAT (reasoned about, deliberately not built here)
========================================================
Avoiding "the same position as the user just had" requires knowing what
they were just served. Endgame drills are stateless by design (the
frontend owns the session; there is still no endgame session/history table
-- session tables are a later step). So THIS function cannot know the
user's recent positions, and server-side repeat avoidance cannot exist
without new state machinery. The stateless-compatible design for the
route/frontend step is an optional client-supplied exclusion list (the
frontend already remembers what it drew this sitting): a single extra
`AND p.id <> ALL(%s::uuid[])` clause, no new tables, no server session
state. That is deliberately NOT built now -- with no caller to supply it,
it would be an unused parameter.

PATTERN NOTES
=============
  * Raw psycopg2, `conn` as a parameter; the CALLER owns the transaction
    (same contract as services/repertoire_service.py).
  * Pydantic result models live here, like TablebaseResult in
    services/tablebase.py, so the future browse route can return them
    as-is without a translation layer.
  * CATEGORIES mirrors the endgame_topics.category CHECK constraint in
    src/core/migrations.py. Validating here turns a bad value into a
    clean ValueError the route maps to 400 before any query runs.
"""
from typing import Literal, Optional
from uuid import UUID

import logging

from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

log = logging.getLogger(__name__)

# The ten CHECK-constrained values of endgame_topics.category.
CATEGORIES = (
    "pure_pawn",
    "knight",
    "bishop",
    "bishop_bishop",
    "bishop_knight",
    "rook",
    "bishop_rook",
    "knight_rook",
    "queen",
    "multi_piece",
)


class DrillPosition(BaseModel):
    """One drillable position plus the topic metadata a drill screen needs."""

    position_id: UUID
    fen: str
    is_winning: bool
    topic_id: UUID
    topic_name: str
    topic_category: str
    topic_difficulty_rating: int
    topic_sort_order: Optional[int] = None


class EndgameSelection(BaseModel):
    """Result of a selection request. status="empty" always carries a
    human-actionable reason; position is None in that case."""

    status: Literal["ok", "empty"]
    position: Optional[DrillPosition] = None
    reason: Optional[str] = None


def select_drill_position(
    conn,
    *,
    category: Optional[str] = None,
    topic_id: Optional[str] = None,
) -> EndgameSelection:
    """Draw one random position from the given category or topic pool.

    Raises ValueError (before touching the DB) when both or neither
    selector is given, when the category is not one of CATEGORIES, or when
    topic_id is not a UUID. Returns status="empty" when the scope is valid
    but has no seeded positions yet.
    """
    if (category is None) == (topic_id is None):
        raise ValueError("provide exactly one of category or topic_id")
    if category is not None and category not in CATEGORIES:
        raise ValueError(
            f"unknown category {category!r}; valid categories: "
            + ", ".join(CATEGORIES)
        )

    parsed_topic_id: Optional[UUID] = None
    if topic_id is not None:
        try:
            parsed_topic_id = UUID(str(topic_id))
        except ValueError as exc:
            raise ValueError(f"topic_id {topic_id!r} is not a valid UUID") from exc

    # Pass the canonical STRING form: psycopg2 does not adapt uuid.UUID
    # objects out of the box, and the query's `%s::uuid` casts the text.
    topic_param = str(parsed_topic_id) if parsed_topic_id is not None else None

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Same NULL-guard idiom as routers/puzzles.py's theme filter: exactly
        # one of the two guards is active per call, the other passes through.
        cur.execute(
            """
            SELECT p.id AS position_id,
                   p.fen,
                   p.is_winning,
                   t.id AS topic_id,
                   t.name AS topic_name,
                   t.category AS topic_category,
                   t.difficulty_rating AS topic_difficulty_rating,
                   t.sort_order AS topic_sort_order
            FROM endgame_positions p
            JOIN endgame_topics t ON t.id = p.topic_id
            WHERE (%s::text IS NULL OR t.category = %s::text)
              AND (%s::uuid IS NULL OR t.id = %s::uuid)
            ORDER BY random()
            LIMIT 1
            """,
            (category, category, topic_param, topic_param),
        )
        row = cur.fetchone()

    if row is None:
        if topic_id is not None:
            scope = f"topic {topic_id}"
        else:
            scope = f"category {category!r}"
        return EndgameSelection(
            status="empty",
            reason=f"no drill positions available for {scope} yet",
        )

    selection = EndgameSelection(
        status="ok",
        position=DrillPosition(**row),
    )
    # Best-effort cache warm-up of the drill's known solution line (see
    # services/endgame_prefetch.py). Enqueue-only, so it never blocks the
    # selection request; a no-op unless the app registered the persistent
    # tablebase cache.
    try:
        from services import endgame_prefetch

        endgame_prefetch.enqueue_drill_line(str(selection.position.position_id))
    except Exception as exc:  # noqa: BLE001 - an optimization must never break selection
        log.warning("drill prefetch enqueue failed: %s", exc)
    return selection
