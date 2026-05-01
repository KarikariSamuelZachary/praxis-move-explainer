import csv
import io
import logging
import zstandard as zstd
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "dbname": os.getenv("DB_NAME", "praxis"),
    "user": os.getenv("DB_USER", "praxis_user"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
}

CSV_PATH = "../data/raw/lichess_db_puzzle.csv.zst"
BATCH_SIZE = 10_000
LOG_EVERY = 100_000

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


def seed():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    log.info("Starting seed from %s", CSV_PATH)

    batch = []
    total = 0

    with open(CSV_PATH, "rb") as fh:
        dctx = zstd.ZstdDecompressor()
        stream = dctx.stream_reader(fh)
        text_stream = io.TextIOWrapper(stream, encoding="utf-8")
        reader = csv.DictReader(text_stream)

        for row in reader:
            themes = row["Themes"].split() if row["Themes"] else []
            opening_tags = row["OpeningTags"].strip() or None

            batch.append((
                row["PuzzleId"],
                row["FEN"],
                row["Moves"],
                int(row["Rating"]),
                themes,
                row["GameUrl"] or None,
                opening_tags,
            ))

            if len(batch) >= BATCH_SIZE:
                insert_batch(cur, batch)
                conn.commit()
                total += len(batch)
                batch = []
                if total % LOG_EVERY == 0:
                    log.info("Inserted %s rows...", f"{total:,}")

        # Insert remaining rows
        if batch:
            insert_batch(cur, batch)
            conn.commit()
            total += len(batch)

    cur.close()
    conn.close()
    log.info("Done. Total rows inserted: %s", f"{total:,}")


def insert_batch(cur, batch):
    cur.executemany(
        """
        INSERT INTO puzzles (id, fen, moves, rating, themes, game_url, opening_tags)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        batch
    )


if __name__ == "__main__":
    seed()