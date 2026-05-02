from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extras import RealDictCursor
from typing import List, Optional

from core.database import get_db
from schemas.puzzle_schemas import PuzzleResponse

router = APIRouter()


@router.get("/puzzles", response_model=List[PuzzleResponse])
def get_puzzles(
    theme: Optional[str] = Query(None, description="Tactical theme e.g. mateIn1, fork, pin"),
    min_rating: int = Query(800, ge=400, le=3000),
    max_rating: int = Query(2000, ge=400, le=3000),
    limit: int = Query(10, ge=1, le=50),
    conn=Depends(get_db),
):
    if theme is not None:
        theme = theme.strip()
        if not theme:
            raise HTTPException(status_code=400, detail="Theme cannot be empty")
    if min_rating > max_rating:
        raise HTTPException(
            status_code=400,
            detail="min_rating cannot be greater than max_rating"
        )

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, fen, moves, rating, themes, game_url
            FROM puzzles
            WHERE (%s::text IS NULL OR %s = ANY(themes))
            AND rating BETWEEN %s AND %s
            ORDER BY RANDOM()
            LIMIT %s
            """,
            (theme or None, theme, min_rating, max_rating, limit),
        )
        rows = cur.fetchall()

    if not rows:
        theme_detail = f" for theme '{theme}'" if theme else ""
        raise HTTPException(
            status_code=404,
            detail=f"No puzzles found{theme_detail} in rating range {min_rating}-{max_rating}"
        )

    return [
        PuzzleResponse(
            id=row["id"],
            fen=row["fen"],
            moves=row["moves"].split(),
            rating=row["rating"],
            themes=row["themes"],
            gameUrl=row["game_url"],
        )
        for row in rows
    ]
