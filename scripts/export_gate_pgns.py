#!/usr/bin/env python
"""Export frozen gate PGN sets A and B from opponent_games.

Set A is for choosing the nodes budget (N); set B is the untouched final
gate. Games are split deterministically and balanced by opponent (alternate
within each opponent, ordered by opponent/end_time/id) so one scouted
opponent cannot dominate either set. Every game must have at least
--min-plies mainline plies.

Writes data/gate_set_A.pgn, data/gate_set_B.pgn and a manifest with SHA-256
hashes plus the exact row ids, so the gate can be reproduced even after the
import pipeline trims old games.

Usage:
  python scripts/export_gate_pgns.py --per-set 120 --min-plies 20
"""
import argparse
import hashlib
import io
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import chess.pgn
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-set", type=int, default=120)
    parser.add_argument("--min-plies", type=int, default=20)
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "data"))
    return parser.parse_args()


def fetch_games(min_plies):
    import psycopg2

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set (root .env)")
    conn = psycopg2.connect(url, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, pgn, opponent_username, end_time
                FROM opponent_games
                WHERE pgn IS NOT NULL AND length(pgn) > 0
                ORDER BY opponent_username, end_time, id
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    usable = []
    for game_id, pgn, opponent, end_time in rows:
        game = chess.pgn.read_game(io.StringIO(pgn))
        if game is None:
            continue
        plies = sum(1 for _ in game.mainline_moves())
        if plies < min_plies:
            continue
        usable.append(
            {
                "id": game_id,
                "pgn": str(game),
                "opponent": opponent,
                "end_time": (
                    end_time.isoformat()
                    if hasattr(end_time, "isoformat")
                    else (str(end_time) if end_time is not None else None)
                ),
                "plies": plies,
            }
        )
    return usable


def split_balanced(games, per_set):
    """Round-robin across opponents so no scouted opponent dominates a set."""
    by_opponent = defaultdict(list)
    for game in games:
        by_opponent[game["opponent"] or "unknown"].append(game)

    opponents = sorted(by_opponent)
    indices = {opponent: 0 for opponent in opponents}
    set_a, set_b = [], []

    while len(set_a) < per_set or len(set_b) < per_set:
        progressed = False
        for opponent in opponents:
            index = indices[opponent]
            pool = by_opponent[opponent]
            if index >= len(pool):
                continue
            game = pool[index]
            indices[opponent] = index + 1
            target = set_a if index % 2 == 0 else set_b
            if len(target) < per_set:
                target.append(game)
            progressed = True
            if len(set_a) >= per_set and len(set_b) >= per_set:
                break
        if not progressed:
            break

    return set_a, set_b


def write_set(path, games):
    with open(path, "w", encoding="utf-8") as fh:
        for game in games:
            fh.write(game["pgn"].strip())
            fh.write("\n\n")
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return digest


def distribution(games):
    return dict(sorted(Counter(g["opponent"] or "unknown" for g in games).items()))


def main():
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    games = fetch_games(args.min_plies)
    set_a, set_b = split_balanced(games, args.per_set)
    if not set_a or not set_b:
        raise SystemExit(
            f"not enough usable games: {len(games)} total, "
            f"A={len(set_a)} B={len(set_b)}"
        )

    path_a = out_dir / "gate_set_A.pgn"
    path_b = out_dir / "gate_set_B.pgn"
    hash_a = write_set(path_a, set_a)
    hash_b = write_set(path_b, set_b)

    manifest = {
        "source": "opponent_games",
        "min_plies": args.min_plies,
        "per_set": args.per_set,
        "usable_games": len(games),
        "set_a": {
            "file": str(path_a),
            "sha256": hash_a,
            "games": len(set_a),
            "opponents": distribution(set_a),
            "ids": [g["id"] for g in set_a],
        },
        "set_b": {
            "file": str(path_b),
            "sha256": hash_b,
            "games": len(set_b),
            "opponents": distribution(set_b),
            "ids": [g["id"] for g in set_b],
        },
    }
    manifest_path = out_dir / "gate_sets.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"usable games: {len(games)}")
    print(f"set A: {len(set_a)} games  sha256={hash_a}")
    print(f"set B: {len(set_b)} games  sha256={hash_b}")
    print(f"opponents A: {distribution(set_a)}")
    print(f"opponents B: {distribution(set_b)}")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
