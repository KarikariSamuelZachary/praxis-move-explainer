# Seed data for the Endgame Trainer content library.
#
# Follows the out-of-band seeding pattern used by seed_puzzles.py: a
# standalone script run manually (or at deploy time) against the database
# configured via DATABASE_URL / DB_* env vars. Schema itself lives in
# core.migrations; this script only INSERTs rows.
#
# Idempotent: re-running never duplicates rows.
#   - topics:    ON CONFLICT (name) DO NOTHING
#   - positions: ON CONFLICT (topic_id, fen) DO NOTHING
#
# Provenance of is_winning: every row was probed against the Lichess
# 7-man Syzygy tablebase service (tablebase.lichess.ovh) at seed-authoring
# time (2026-09-16); category ("win"/"draw") and DTM are recorded per row
# below. No tablebase client code lives in this repo yet — the values are
# frozen at seed time on purpose, so seed data stays deterministic and
# reviewable in diffs. Re-verification belongs to the tablebase
# integration step.

import logging
import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
}
# Fall back to DATABASE_URL (the app's own connection string) when the
# discrete DB_* vars are not set, mirroring core.database.init_db().
if not all([DB_CONFIG["dbname"], DB_CONFIG["user"], DB_CONFIG["password"]]):
    DB_CONFIG = {"dsn": os.getenv("DATABASE_URL")}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# difficulty_rating uses the puzzles.rating scale (Lichess-style ~400-3000).
# Lucena = 1450: it is the gateway rook-endgame technique (taught before
# Philidor and every other named rook ending), but executing the bridge
# requires precise rook maneuvering and a check-absorbing intermezzo, which
# puts it above the beginner band (SKILL_RATING_BANDS "new"/"beginner" =
# 800-1300) and in the upper-middle of the "intermediate" band (1300-1600).
#
# sort_order = 10: slots 1-9 are reserved for pure_pawn fundamentals
# (opposition, key squares, king+pawn vs king, the square rule). Lucena
# opens the rook-endings block; Philidor and later rook topics follow.
TOPICS = [
    {
        "name": "Lucena Position",
        "category": "rook",
        "description": (
            "The Lucena position is the key winning position in rook-and-pawn "
            "versus rook endgames: the attacking side's king stands directly in "
            "front of its own pawn on the seventh rank, with the rook nearby and "
            "the defending king cut off. It is named after Luis Ramirez de "
            "Lucena, a 15th-century Spanish chess writer. The position matters "
            "because rook endgames are the most commonly reached endgame in "
            "practical play, and this setup is the target the attacker steers "
            "toward whenever a single pawn survives. The winning method is the "
            "famous 'building a bridge' technique: the defending rook gives "
            "checks from behind, and the attacker interposes the own rook "
            "between the checker and the king, forming a shelter ('bridge') "
            "behind which the king walks toward the promotion square while the "
            "pawn keeps advancing. Success hinges on two details. First, the "
            "pawn cannot be a rook's pawn: with an a- or h-pawn there is no "
            "room to build the bridge, the defending king reaches the corner, "
            "and the position is only a draw. Second, the attacking king must "
            "be in front of the pawn — behind it, the position drifts back "
            "toward a draw. For the defender, the saving plan is to give "
            "checks from the long side and keep the own king near the "
            "promotion square; falling to the short side loses the game."
        ),
        "difficulty_rating": 1450,
        "sort_order": 10,
    },
]


# (fen, is_winning, note)
# All rows: 4- or 5-man K+R+P vs K+R probe results from the Lichess Syzygy
# service. "loss for the side to move" variants (same geometry, defender to
# move) were probed too and are deliberately NOT included: endgame_positions
# only carries tablebase wins and draws as drillable content, per the schema
# design. Mirrors include the counterpart's DTM in the note — the DTM match
# is the sanity check that the mirror geometry is exact.
POSITIONS = [
    # --- wins, white attacker, white to move (textbook geometry per file) ---
    (
        "1K1k4/1P6/8/8/8/8/r7/5R2 w - - 0 1",
        True,
        "b-file textbook: Kb8/Pb7/kd8, ra2/Rf1, WTM | tablebase: win, DTM 41, DTZ 15",
    ),
    (
        "2K5/2P1k3/8/8/8/8/r7/6R1 w - - 0 1",
        True,
        "c-file textbook: Kc8/Pc7/ke7, ra2/Rg1, WTM | tablebase: win, DTM 35, DTZ 5",
    ),
    (
        "3K4/3P1k2/8/8/8/8/r7/6R1 w - - 0 1",
        True,
        "d-file textbook: Kd8/Pd7/kf7, ra2/Rg1, WTM | tablebase: win, DTM 37, DTZ 9",
    ),
    (
        "4K3/4P1k1/8/8/8/8/r7/5R2 w - - 0 1",
        True,
        "e-file: Ke8/Pe7/kg7, ra2/Rf1, WTM | tablebase: win, DTM 35, DTZ 11",
    ),
    (
        "3K4/3P4/8/4k3/8/8/1r6/7R w - - 0 1",
        True,
        "d-file, defender king far (e5), rb2/Rh1, WTM | tablebase: win, DTM 33, DTZ 3",
    ),
    # --- wins, black attacker, black to move (exact color-mirrors of the above) ---
    (
        "5r2/R7/8/8/8/8/1p6/1k1K4 b - - 0 1",
        True,
        "mirror of b-file textbook: kb1/pb2/Kd1, Rb8/rf8, BTM | tablebase: win, DTM 41 (= b-file WTM)",
    ),
    (
        "6r1/R7/8/8/8/8/2p1K3/2k5 b - - 0 1",
        True,
        "mirror of c-file: kc1/pc2/Ke1, Rc8/rg8, BTM | tablebase: win, DTM 35 (= c-file WTM)",
    ),
    (
        "6r1/R7/8/8/8/8/3p1K2/3k4 b - - 0 1",
        True,
        "mirror of d-file: kd1/pd2/Kf1, Rd8/rg8, BTM | tablebase: win, DTM 37 (= d-file WTM)",
    ),
    # --- draws (is_winning FALSE = tablebase draw for the side to move) ---
    # UX note carried into the session-UI design: the first draw row and the
    # e-file WTM win row differ ONLY in the side-to-move character, yet have
    # opposite outcomes (defender-to-move holds, attacker-to-move wins).
    # That is the intended pedagogical pair — the whole Lucena lesson is that
    # the tempo decides — but the trainer UI must label drill role and side
    # to move prominently so a user never reads these as a duplicated
    # position with contradictory verdicts.
    (
        "4K3/4P1k1/8/8/8/8/r7/5R2 b - - 0 1",
        False,
        "same placement as e-file WTM but defender to move: kg7 holds | tablebase: draw (attacker WTM wins DTM 35 — STM decides)",
    ),
    (
        "K1k5/P7/8/8/8/8/1r6/7R w - - 0 1",
        False,
        "rook's pawn anti-Lucena: Ka8/Pa7/kc8 — no bridge room on the rim | tablebase: draw, DTZ 0",
    ),
]


def seed():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    for t in TOPICS:
        cur.execute(
            """
            INSERT INTO endgame_topics (name, category, description, difficulty_rating, sort_order)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (name) DO NOTHING
            """,
            (t["name"], t["category"], t["description"], t["difficulty_rating"], t["sort_order"]),
        )

    for t in TOPICS:
        cur.execute("SELECT id FROM endgame_topics WHERE name = %s", (t["name"],))
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"Topic {t['name']!r} not found after upsert")
        topic_id = row[0]

        for fen, is_winning, note in POSITIONS:
            # note is kept in the script as seed provenance/documentation;
            # the schema has no note column, so it is not inserted.
            cur.execute(
                """
                INSERT INTO endgame_positions (topic_id, fen, is_winning)
                VALUES (%s, %s, %s)
                ON CONFLICT (topic_id, fen) DO NOTHING
                """,
                (topic_id, fen, is_winning),
            )

    conn.commit()

    cur.execute(
        """
        SELECT p.fen, p.is_winning
        FROM endgame_positions p
        JOIN endgame_topics t ON t.id = p.topic_id
        WHERE t.name = %s
        ORDER BY p.created_at, p.fen
        """,
        (TOPICS[0]["name"],),
    )
    rows = cur.fetchall()
    log.info("Seeded %d positions under %r", len(rows), TOPICS[0]["name"])
    for fen, is_winning in rows:
        log.info("  %-42s is_winning=%s", fen, is_winning)

    cur.close()
    conn.close()


if __name__ == "__main__":
    seed()
