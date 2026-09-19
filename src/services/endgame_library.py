"""
Endgame library selection: pick the next drill position from the seeded
content library (endgame_topics / endgame_positions).

Three functions share this module:

  * select_drill_position(conn, category=..., topic_id=..., sourced_only=...)
    returns ONE randomly drawn position from the matching pool, for the
    browse/drill entry point: either a whole category ("any rook ending")
    or one specific topic ("just Lucena"). Exactly one of the two selectors
    must be given. (This is the library/category-picker path; the MVP
    trainer mode deliberately has no category picker.) sourced_only=True
    restricts the pool to imported rows (source_puzzle_id IS NOT NULL) --
    the practice-mode picker uses it so the hand-authored Lucena topic,
    which is conceptually the nav-library's teaching set, cannot leak into
    a material-category draw. The payload now carries the source puzzle id
    and stored solution line for sourced rows, exactly like the
    rating-matched selector.

  * select_rating_matched_position(conn, min_rating=..., max_rating=...)
    is the MVP trainer's "next position" query: ONE random position whose
    TOPIC difficulty falls inside the caller's rating window. The caller
    (routers/endgames.py) resolves that window from
    users.endgame_trainer_rating the same way routers/puzzles.py resolves
    its window from users.tactical_rating.

  * list_categories(conn, sourced_only=...) returns the categories that
    actually have positions, with per-category position counts -- the
    practice picker's category list. Only present categories are returned
    (the CHECK set's three unseeded values are simply absent), and
    sourced_only=True counts only imported rows.

SELECTION PRECEDENT (checked against routers/puzzles.py before building)
========================================================================
The brief was to mirror Puzzles' theme-filter selection as "pooled-random
from the filtered pool". The actual Puzzles code is a random-pivot keyset
scan, not a uniform pooled draw: it picks a random 5-char puzzle id, then
`WHERE id >= pivot AND themes @> ... ORDER BY id LIMIT N`, wrapping around
with `id < pivot ORDER BY id DESC` when the pivot lands near the end. That
is a randomized START into a contiguous id-ordered scan, built because the
puzzles table is ~5.9M rows where `ORDER BY random()` does not scale. It
is not a uniform random sample of the filtered pool, and it never returns
a single row.

The endgame library is tiny by comparison (22,388 rows today: 10 curated
Lucena variants + 22,378 sourced rows across 112 material topics), so this
module draws the true pooled-random the brief asked for: `ORDER BY
random() LIMIT 1` over the filtered pool. Same intent (random draw, never
sequential/ordered), simpler and exactly uniform at this scale; the keyset
trick's complexity buys nothing below ~100k rows. If the library ever
grows enormous, the Puzzles pattern is the reference for that scale.

WHY THE PUZZLES SELECTOR IS NOT A DROP-IN REUSE (checked before building)
=========================================================================
There is no shared selection helper to call: the logic lives inline in
routers/puzzles.py's GET /puzzles handler and is coupled to the puzzles
table's shape at four points, none of which transfer to endgame_positions:

  1. ID TYPE / KEYSET PIVOT. The pivot is a random fixed-width 5-char
     alphanumeric puzzle id (`WHERE id >= pivot ORDER BY id`), built for
     the 5.88M-row puzzles table where `ORDER BY random()` does not scale.
     endgame_positions.id is a UUID and the table is 22k rows; an id-ordered
     keyset scan would still be correct but pointlessly non-uniform (UUID
     ordering is arbitrary) and needlessly complex.
  2. RATING COLUMN LOCATION. puzzles.rating is a per-row column with its
     own btree index. endgame_positions has NO rating column: difficulty
     lives on endgame_topics.difficulty_rating, so the filter must join
     topics. The Puzzles SQL shape cannot be pointed at the endgame table.
  3. THEME FILTER / GIN INDEX. Puzzles filters `themes @> ARRAY[%s]` with
     a GIN index. endgame_positions has no themes column (the coarse
     category lives on the joined topic, and there is no GIN index to use).
     The MVP mode also has no category picker, so no theme predicate exists
     at all.
  4. ROW PAYLOAD. Puzzles returns `moves`/`game_url` from its own row.
     Endgame solution lines live in the source puzzle row (linked by
     endgame_positions.source_puzzle_id), and the first stored move is the
     opponent's setup move that the stored drill FEN already contains.

What IS reused: core.rating's +/-100 window and clamp math (now shared with
routers/puzzles.py, see core/rating.py), core.rating.calculate_rating_change
for the solve/fail delta (used by services/endgame_session.py), and the
"one random row from the filtered pool" intent. The right-sized equivalent
at 22k rows is `ORDER BY random() LIMIT 1` over the rating-window pool,
which is what select_rating_matched_position does.

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
from typing import List, Literal, Optional
from uuid import UUID

import logging

from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, Field

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
    """One drillable position plus the topic metadata a drill screen needs.

    Both selectors join the source puzzle, so source_puzzle_id and
    solution_moves are populated for sourced rows and stay at their
    defaults only for curated rows (which have no puzzle provenance).
    """

    position_id: UUID
    fen: str
    is_winning: bool
    topic_id: UUID
    topic_name: str
    topic_category: str
    topic_difficulty_rating: int
    topic_sort_order: Optional[int] = None
    # Provenance link back to the Lichess puzzle this sourced row came from
    # (NULL for curated rows).
    source_puzzle_id: Optional[str] = None
    # The known solution line FROM this drill FEN, as UCI strings: the
    # source puzzle's moves with the opponent's setup move removed (the
    # stored FEN is already the position after that setup move). Empty for
    # curated rows. Informational (hint / show-solution); grading never
    # assumes the user follows it, and for sourced rows it may end at a
    # decisive-material point rather than at checkmate.
    solution_moves: List[str] = Field(default_factory=list)


class EndgameSelection(BaseModel):
    """Result of a selection request. status="empty" always carries a
    human-actionable reason; position is None in that case."""

    status: Literal["ok", "empty"]
    position: Optional[DrillPosition] = None
    reason: Optional[str] = None


class CategorySummary(BaseModel):
    """One picker row: a category that has positions, and how many."""

    category: str
    position_count: int


def select_drill_position(
    conn,
    *,
    category: Optional[str] = None,
    topic_id: Optional[str] = None,
    sourced_only: bool = False,
) -> EndgameSelection:
    """Draw one random position from the given category or topic pool.

    Raises ValueError (before touching the DB) when both or neither
    selector is given, when the category is not one of CATEGORIES, or when
    topic_id is not a UUID. Returns status="empty" when the scope is valid
    but has no matching positions.

    sourced_only=True restricts the pool to imported rows
    (source_puzzle_id IS NOT NULL): the practice-mode picker's guarantee
    that its counts and its draws agree, and that the curated Lucena topic
    (category "rook", source_puzzle_id NULL) stays with the nav-library
    path. Default False preserves the browse/library behavior, curated rows
    included.

    The SELECT joins the source puzzle so sourced rows carry
    source_puzzle_id and the stored solution line (first/setup ply
    stripped); curated rows keep the empty defaults.
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

    # Static SQL fragment (no user input): the sourced-only pool filter.
    sourced_clause = "AND p.source_puzzle_id IS NOT NULL" if sourced_only else ""

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Same NULL-guard idiom as routers/puzzles.py's theme filter: exactly
        # one of the two guards is active per call, the other passes through.
        # LEFT JOIN the source puzzle (NULL for curated rows) so the response
        # can include the known solution line -- matching
        # select_rating_matched_position's payload exactly.
        cur.execute(
            f"""
            SELECT p.id AS position_id,
                   p.fen,
                   p.is_winning,
                   t.id AS topic_id,
                   t.name AS topic_name,
                   t.category AS topic_category,
                   t.difficulty_rating AS topic_difficulty_rating,
                   t.sort_order AS topic_sort_order,
                   p.source_puzzle_id,
                   z.moves AS source_moves
            FROM endgame_positions p
            JOIN endgame_topics t ON t.id = p.topic_id
            LEFT JOIN puzzles z ON z.id = p.source_puzzle_id
            WHERE (%s::text IS NULL OR t.category = %s::text)
              AND (%s::uuid IS NULL OR t.id = %s::uuid)
              {sourced_clause}
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
        if sourced_only:
            scope += " (sourced pool only)"
        return EndgameSelection(
            status="empty",
            reason=f"no drill positions available for {scope} yet",
        )

    source_moves = (row.pop("source_moves", None) or "").split()
    # source_moves[0] is the opponent's setup move already baked into the
    # stored FEN; only the remainder is playable from the drill position.
    row["solution_moves"] = source_moves[1:]
    selection = EndgameSelection(
        status="ok",
        position=DrillPosition(**row),
    )
    _enqueue_prefetch(selection)
    return selection


def list_categories(
    conn,
    *,
    sourced_only: bool = False,
) -> List[CategorySummary]:
    """The categories that actually have positions, with counts.

    Dynamic by design: the DB is the source of truth, so categories with no
    seeded rows (three of the CHECK set's ten today) are simply absent and
    new sourced content appears without a code change. Ordered by category
    name for a deterministic picker list. sourced_only=True counts only
    imported rows, matching select_drill_position(sourced_only=True).
    """
    sourced_clause = "AND p.source_puzzle_id IS NOT NULL" if sourced_only else ""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT t.category,
                   COUNT(*) AS position_count
            FROM endgame_topics t
            JOIN endgame_positions p ON p.topic_id = t.id
            WHERE TRUE
              {sourced_clause}
            GROUP BY t.category
            ORDER BY t.category ASC
            """
        )
        rows = cur.fetchall()
    return [CategorySummary(**row) for row in rows]


def select_rating_matched_position(
    conn,
    *,
    min_rating: int,
    max_rating: int,
) -> EndgameSelection:
    """Draw one random position whose TOPIC difficulty is in [min, max].

    This is the MVP trainer's selection: rating-matched, no category or
    topic scope. The caller supplies the window (routers/endgames.py
    resolves it from users.endgame_trainer_rating with the same +/-100
    window and skill-band fallback routers/puzzles.py uses). Raises
    ValueError when max_rating < min_rating, before touching the DB.
    Returns status="empty" (never an error) when the window has no seeded
    positions -- the caller decides whether that is a 404 or a fallback.

    Unlike the Puzzles keyset scan this filter joins endgame_topics (the
    rating lives on the topic, not the position row) and uses a true
    pooled-random draw; see the module docstring's "WHY THE PUZZLES
    SELECTOR IS NOT A DROP-IN REUSE" section.
    """
    if max_rating < min_rating:
        raise ValueError(
            f"min_rating {min_rating} cannot be greater than max_rating "
            f"{max_rating}"
        )

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # LEFT JOIN the source puzzle (NULL for curated rows) to return the
        # known solution line with the response. The stored FEN is already
        # the position after the puzzle's first (setup) move, so the drill
        # line is the puzzle's moves with that first entry removed.
        cur.execute(
            """
            SELECT p.id AS position_id,
                   p.fen,
                   p.is_winning,
                   t.id AS topic_id,
                   t.name AS topic_name,
                   t.category AS topic_category,
                   t.difficulty_rating AS topic_difficulty_rating,
                   t.sort_order AS topic_sort_order,
                   p.source_puzzle_id,
                   z.moves AS source_moves
            FROM endgame_positions p
            JOIN endgame_topics t ON t.id = p.topic_id
            LEFT JOIN puzzles z ON z.id = p.source_puzzle_id
            WHERE t.difficulty_rating BETWEEN %s AND %s
            ORDER BY random()
            LIMIT 1
            """,
            (min_rating, max_rating),
        )
        row = cur.fetchone()

    if row is None:
        return EndgameSelection(
            status="empty",
            reason=(
                f"no endgame drill positions in rating range "
                f"{min_rating}-{max_rating}"
            ),
        )

    source_moves = (row.pop("source_moves", None) or "").split()
    # source_moves[0] is the opponent's setup move already baked into the
    # stored FEN; only the remainder is playable from the drill position.
    row["solution_moves"] = source_moves[1:]
    selection = EndgameSelection(
        status="ok",
        position=DrillPosition(**row),
    )
    _enqueue_prefetch(selection)
    return selection


def _enqueue_prefetch(selection: EndgameSelection) -> None:
    """Best-effort cache warm-up of the drill's known solution line (see
    services/endgame_prefetch.py). Enqueue-only, so it never blocks the
    selection request; a no-op unless the app registered the persistent
    tablebase cache."""
    try:
        from services import endgame_prefetch

        endgame_prefetch.enqueue_drill_line(str(selection.position.position_id))
    except Exception as exc:  # noqa: BLE001 - an optimization must never break selection
        log.warning("drill prefetch enqueue failed: %s", exc)
