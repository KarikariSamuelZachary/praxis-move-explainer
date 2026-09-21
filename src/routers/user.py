from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2.extras import RealDictCursor

from core.database import get_db
from routers.puzzles import SKILL_RATING_BANDS

router = APIRouter()


@router.get("/rating")
def get_user_rating(request: Request, conn=Depends(get_db)):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT tactical_rating, endgame_trainer_rating, skill_level
            FROM users WHERE clerk_id = %s
            """,
            (clerk_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    tactical_rating = row["tactical_rating"]

    if tactical_rating is None and row["skill_level"] in SKILL_RATING_BANDS:
        band = SKILL_RATING_BANDS[row["skill_level"]]
        tactical_rating = (band[0] + band[1]) // 2

    # endgame_trainer_rating is deliberately returned RAW (NULL until the
    # first solved/failed endgame drill): the Endgame Trainer derives its
    # own window from users.endgame_trainer_rating with a skill-band
    # fallback, but there is no single "effective" number to show the way
    # there is for tactical_rating -- the frontend renders NULL as
    # "Unrated" and fills the real value from the first rated response.
    return {
        "tactical_rating": tactical_rating,
        "endgame_trainer_rating": row["endgame_trainer_rating"],
        "skill_level": row["skill_level"],
    }