#!/usr/bin/env python
"""Export frozen gate PGN sets A and B from opponent_games.

Set A is for choosing the nodes budget (N); set B is the untouched final
gate. Opponent pools are disjoint: every game of an opponent lands in exactly
one set, so a scouted opponent cannot leak across both. Neutral games (Lichess
export/dump) go entirely into set B as a separate file. Every game must have
at least --min-plies mainline plies.

Writes data/gate_set_A.pgn, data/gate_set_B.pgn (plus
data/gate_set_B.neutral.pgn when neutral games exist) and a manifest with
per-file SHA-256 hashes plus the exact row ids, so the gate can be reproduced
even after the import pipeline trims old games.

Neutral games come from a local dump (--neutral-pgn) or one authenticated
Lichess export (--neutral-user, LICHESS_TOKEN). Rate-limit responses fail
immediately instead of being retried.

Usage:
  python scripts/export_gate_pgns.py --per-set 120 --min-plies 20
  python scripts/export_gate_pgns.py --neutral-pgn dump.pgn
  LICHESS_TOKEN=... python scripts/export_gate_pgns.py --neutral-user someone
"""
import argparse
import hashlib
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import chess.pgn
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

# Accounts known to belong to the same person must never be split across sets.
OPPONENT_ALIASES = {"iaminspiredbroo": "iaminspiredbro"}


def load_exclude_ids(path):
    """Ids that must never enter set B (already used for tuning)."""
    if not path:
        return frozenset()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return frozenset(str(value) for value in data)
    ids = set()
    for key in ("set_a", "set_b"):
        for value in (data.get(key) or {}).get("ids", []) or []:
            ids.add(str(value))
    return frozenset(ids)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-set", type=int, default=120)
    parser.add_argument("--min-plies", type=int, default=20)
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "data"))
    parser.add_argument(
        "--neutral-pgn",
        default=None,
        help="Local neutral PGN dump; its games go into set B.",
    )
    parser.add_argument(
        "--neutral-user",
        default=None,
        help="Lichess username; one authenticated export via LICHESS_TOKEN.",
    )
    parser.add_argument("--neutral-max", type=int, default=300)
    parser.add_argument(
        "--exclude-ids-json",
        default=None,
        help="Manifest or id list whose games must never enter set B "
        "(e.g. a previous gate manifest already used for tuning).",
    )
    return parser.parse_args()


def read_pgn_games(path, min_plies):
    games = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        while True:
            game = chess.pgn.read_game(fh)
            if game is None:
                break
            if sum(1 for _ in game.mainline_moves()) >= min_plies:
                games.append(str(game))
    return games


def fetch_lichess_dump(user, max_games, out_dir):
    """One authenticated Lichess export; never retries on 429."""
    token = os.environ.get("LICHESS_TOKEN")
    if not token:
        raise SystemExit(
            "LICHESS_TOKEN is not set; use --neutral-pgn with a downloaded "
            "dump instead of retrying the anonymous API"
        )
    query = urllib.parse.urlencode(
        {"max": max_games, "clocks": "false", "evals": "false", "opening": "true"}
    )
    url = f"https://lichess.org/api/games/user/{user}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/x-chess-pgn",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise SystemExit(
                "Lichess rate limit (429); wait or use a dump. Not retrying."
            ) from exc
        raise
    dump_path = out_dir / "lichess_neutral_dump.pgn"
    dump_path.write_text(data, encoding="utf-8")
    return dump_path


def read_neutral_games(args, out_dir):
    path = args.neutral_pgn
    if not path and args.neutral_user:
        path = str(fetch_lichess_dump(args.neutral_user, args.neutral_max, out_dir))
    if not path:
        return []
    return read_pgn_games(path, args.min_plies)


def write_pgn_file(path, pgns):
    with open(path, "w", encoding="utf-8") as fh:
        for pgn in pgns:
            fh.write(pgn.strip())
            fh.write("\n\n")
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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


def _take_from_pools(pools, per_set):
    """Share per_set evenly across whole-opponent pools, then fill the rest."""
    if not pools:
        return []
    share = max(1, per_set // len(pools))
    chosen = []
    for pool in pools:
        chosen.extend(pool[:share])
    if len(chosen) < per_set:
        for pool in pools:
            for game in pool[share:]:
                if len(chosen) >= per_set:
                    break
                chosen.append(game)
    return chosen[:per_set]


def split_by_opponent(games, per_set, exclude_b_ids=frozenset()):
    """Disjoint opponent pools: every game of an opponent goes to one set.

    Opponents are dealt largest-first, alternating between the sets; aliased
    accounts stay in one pool. Set B additionally drops every id in
    exclude_b_ids, so a game already used for tuning can never reappear in
    the final gate.
    """
    by_opponent = defaultdict(list)
    for game in games:
        name = game["opponent"] or "unknown"
        by_opponent[OPPONENT_ALIASES.get(name, name)].append(game)

    pools = sorted(by_opponent.items(), key=lambda item: (-len(item[1]), item[0]))
    groups = ([], [])
    turn = 0
    for _, pool in pools:
        groups[turn].append(pool)
        turn = 1 - turn

    b_pools = [
        [game for game in pool if str(game["id"]) not in exclude_b_ids]
        for pool in groups[1]
    ]
    return _take_from_pools(groups[0], per_set), _take_from_pools(b_pools, per_set)


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
    exclude_b_ids = load_exclude_ids(args.exclude_ids_json)
    set_a, set_b = split_by_opponent(games, args.per_set, exclude_b_ids)
    if not set_a or not set_b:
        raise SystemExit(
            f"not enough usable games: {len(games)} total, "
            f"A={len(set_a)} B={len(set_b)}"
        )

    path_a = out_dir / "gate_set_A.pgn"
    path_b = out_dir / "gate_set_B.pgn"
    hash_a = write_set(path_a, set_a)
    hash_b = write_set(path_b, set_b)

    neutral = read_neutral_games(args, out_dir)
    neutral_manifest = {}
    if neutral:
        path_nb = out_dir / "gate_set_B.neutral.pgn"
        hash_nb = write_pgn_file(path_nb, neutral)
        neutral_manifest = {
            "file": str(path_nb),
            "sha256": hash_nb,
            "games": len(neutral),
        }

    manifest = {
        "source": "opponent_games",
        "min_plies": args.min_plies,
        "per_set": args.per_set,
        "usable_games": len(games),
        "split": "disjoint_opponent_pools",
        "opponent_aliases": OPPONENT_ALIASES,
        "excluded_from_b": {
            "count": len(exclude_b_ids),
            "ids": sorted(exclude_b_ids),
        },
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
            "neutral": neutral_manifest,
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
