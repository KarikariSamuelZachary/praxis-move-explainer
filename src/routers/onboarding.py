from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

from core.database import get_db
from routers.puzzles import SKILL_RATING_BANDS

router = APIRouter()

VALID_SKILL_LEVELS = set(SKILL_RATING_BANDS.keys())


class SkillLevelBody(BaseModel):
    skill_level: str


@router.get("/skill-level")
def get_skill_level(request: Request, conn=Depends(get_db)):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT skill_level FROM users WHERE clerk_id = %s", (clerk_id,))
        row = cur.fetchone()

    if row is None:
        return {"skill_level": None}

    return {"skill_level": row["skill_level"]}


@router.post("/skill-level")
def set_skill_level(request: Request, body: SkillLevelBody, conn=Depends(get_db)):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")

    if body.skill_level not in VALID_SKILL_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid skill_level. Must be one of: {', '.join(sorted(VALID_SKILL_LEVELS))}",
        )

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT 1 FROM users WHERE clerk_id = %s", (clerk_id,))
        user_exists = cur.fetchone() is not None

        band = SKILL_RATING_BANDS[body.skill_level]
        starting_rating = (band[0] + band[1]) // 2

        if user_exists:
            cur.execute(
                """
                UPDATE users
                SET skill_level = %s,
                    tactical_rating = COALESCE(tactical_rating, %s)
                WHERE clerk_id = %s
                """,
                (body.skill_level, starting_rating, clerk_id),
            )
        else:
            email = request.headers.get("X-Clerk-User-Email")
            if not email:
                raise HTTPException(status_code=404, detail="User not found")

            cur.execute(
                """
                INSERT INTO users (clerk_id, email, skill_level, tactical_rating)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE
                SET clerk_id = EXCLUDED.clerk_id,
                    skill_level = EXCLUDED.skill_level,
                    tactical_rating = COALESCE(users.tactical_rating, EXCLUDED.tactical_rating)
                """,
                (clerk_id, email, body.skill_level, starting_rating),
            )
    conn.commit()

    return {"success": True}
