"""
Import an opponent's Chess.com games via the existing import infrastructure
(TEST-ONLY). Reuses fetch_recent_chesscom_games + the store/index path from
services.opponent_import exactly, but skips the Stockfish analysis chain
(blunder classification is not needed for self-consistency / held-out
accuracy measurements, and traps are out of scope for this baseline).

Usage: venv/bin/python scripts/import_opponent.py <chesscom_username> [limit]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # noqa: BLE001
    pass

from core import database  # noqa: E402
from services.opponent_import import (  # noqa: E402
    _fetch_and_store_provider_games,
    _mark_job_completed,
    _mark_job_failed,
    _mark_job_running,
    create_opponent_import_job,
)

UID = "user_3I3cNvpU6XgdUBTqEaYASS251nJ"
PROVIDER = "chesscom"


def main() -> int:
    username = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 500

    database.init_db()

    job = create_opponent_import_job(
        requested_by_user_id=UID,
        lichess_username=None,
        chesscom_username=username,
        limit=limit,
    )
    conn = database.connection_pool.getconn()
    try:
        _mark_job_running(conn, job["job_id"])
        conn.commit()
        errors: list[str] = []
        n = _fetch_and_store_provider_games(
            conn,
            requested_by_user_id=UID,
            job_id=job["job_id"],
            provider=PROVIDER,
            username=username,
            limit=limit,
            errors=errors,
        )
        if errors:
            _mark_job_failed(conn, job["job_id"], n, "; ".join(errors))
        else:
            _mark_job_completed(conn, job["job_id"], n)
        conn.commit()
        print(f"imported {n} games for {username} (job {job['job_id']})")
        if errors:
            print("errors:", "; ".join(errors))
            return 1
        return 0
    finally:
        database.connection_pool.putconn(conn)


if __name__ == "__main__":
    raise SystemExit(main())
