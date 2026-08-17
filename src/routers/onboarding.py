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

    # Email is best-effort: Clerk's currentUser() can briefly return
    # null right after an SSO sign-up, so the onboarding POST may land
    # before the email is available. clerk_id is the real key; email is
    # only used to reconcile a stale row from a deleted-then-recreated
    # account that happened to share the same address.
    email = request.headers.get("X-Clerk-User-Email") or None

    band = SKILL_RATING_BANDS[body.skill_level]
    starting_rating = (band[0] + band[1]) // 2

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # If a stale row exists for the same email but an old clerk_id
        # (account deleted + recreated in Clerk), reclaim it under the
        # new clerk_id before the upsert. This avoids a UNIQUE(email)
        # collision on insert and preserves any non-skill columns.
        if email:
            cur.execute(
                """
                UPDATE users
                SET clerk_id = %s
                WHERE email = %s AND clerk_id <> %s
                """,
                (clerk_id, email, clerk_id),
            )

        # Upsert keyed on clerk_id. Email is filled in if we have it
        # (COALESCE keeps any existing value when the header is absent).
        cur.execute(
            """
            INSERT INTO users (clerk_id, email, skill_level, tactical_rating)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (clerk_id) DO UPDATE
            SET skill_level = EXCLUDED.skill_level,
                tactical_rating = COALESCE(users.tactical_rating, EXCLUDED.tactical_rating),
                email = COALESCE(EXCLUDED.email, users.email)
            """,
            (clerk_id, email, body.skill_level, starting_rating),
        )
    conn.commit()

    return {"success": True}
