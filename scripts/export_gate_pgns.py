#!/usr/bin/env python
"""Export frozen gate PGN sets A and B from opponent_games.

Set A is for choosing the nodes budget (N); set B is the untouched final
gate. Opponent pools are disjoint: every game of an opponent lands in exactly
one set, so a scouted opponent cannot leak across both. Neutral games (Lichess
exports/dumps) go entirely into set B as one separately hashed file per
source, each capped so no single file dominates set B. Every game must have
at least --min-plies mainline plies.

Writes data/gate_set_A.pgn, data/gate_set_B.pgn (plus one
data/gate_set_B.neutral.<source>.pgn per neutral source) and a manifest with
per-file SHA-256 hashes plus the exact row ids, so the gate can be reproduced
even after the import pipeline trims old games.

Neutral games come from a local dump (--neutral-pgn) or authenticated
Lichess exports (--neutral-user, one or more usernames, LICHESS_TOKEN).
Bot-involved games (either side titled BOT) are dropped unless
--neutral-include-bot-games is passed. A Lichess 429 waits a full minute and
is retried once, then fails.

Usage:
  python scripts/export_gate_pgns.py --per-set 120 --min-plies 20
  python scripts/export_gate_pgns.py --neutral-pgn dump.pgn
  LICHESS_TOKEN=... python scripts/export_gate_pgns.py --neutral-user someone
  LICHESS_TOKEN=... python scripts/export_gate_pgns.py --neutral-user alice bob
"""
import argparse
import hashlib
import io
import json
import os
import re
import time
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
    """Ids that must never enter set B (already used for tuning).

    Fails loudly instead of silently excluding nothing: a missing file, bad
    JSON, an unrecognized shape, non-id entries, or a set that resolves to
    empty all raise SystemExit. Omit the flag entirely to opt out of
    exclusions on purpose.
    """
    if not path:
        return frozenset()
    source = Path(path)
    if not source.exists():
        raise SystemExit(f"exclude-ids file not found: {path}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"exclude-ids file is not valid JSON: {path}: {exc}") from exc
    ids: list = []
    if isinstance(data, list):
        ids = list(data)
    elif isinstance(data, dict):
        if "excluded_from_b_union" in data:
            union = data["excluded_from_b_union"]
            if not isinstance(union, list):
                raise SystemExit(
                    f"exclude-ids file {path}: 'excluded_from_b_union' "
                    f"must be a list, got {type(union).__name__}"
                )
            ids = list(union)
        elif "excluded_from_b" in data:
            block = data["excluded_from_b"]
            if not isinstance(block, dict) or not isinstance(block.get("ids"), list):
                raise SystemExit(
                    f"exclude-ids file {path}: 'excluded_from_b' must be "
                    "an object with an 'ids' list"
                )
            ids = list(block["ids"])
        elif "set_a" in data or "set_b" in data:
            for key in ("set_a", "set_b"):
                block = data.get(key) or {}
                if not isinstance(block, dict) or not isinstance(
                    block.get("ids"), list
                ):
                    raise SystemExit(
                        f"exclude-ids file {path}: '{key}' must be an "
                        "object with an 'ids' list"
                    )
                ids.extend(block["ids"])
        else:
            raise SystemExit(
                f"exclude-ids file {path} has an unrecognized shape: "
                f"expected a JSON list of ids or an object with "
                f"'excluded_from_b_union', 'excluded_from_b.ids', or "
                f"'set_a'/'set_b' blocks with 'ids' lists; "
                f"got keys {sorted(data)}"
            )
    else:
        raise SystemExit(
            f"exclude-ids file {path} has an unrecognized shape: expected a "
            f"JSON list or object, got {type(data).__name__}"
        )
    offenders = [
        value for value in ids if not isinstance(value, (str, int)) or str(value) == ""
    ]
    if offenders:
        preview = ", ".join(repr(value) for value in offenders[:5])
        raise SystemExit(
            f"exclude-ids file {path} contains {len(offenders)} non-id "
            f"entries (empty or non-string, e.g. {preview}); refusing to run"
        )
    resolved = frozenset(str(value) for value in ids)
    if not resolved:
        raise SystemExit(
            f"exclude-ids file {path} resolved to an empty id set; refusing "
            "to run with no exclusions (omit the flag to opt out explicitly)"
        )
    return resolved


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
        nargs="*",
        default=None,
        help="Lichess usernames; one authenticated export per name via "
        "LICHESS_TOKEN, each written to its own neutral file.",
    )
    parser.add_argument(
        "--neutral-max", type=int, default=300, help="Games fetched per user."
    )
    parser.add_argument(
        "--neutral-file-cap",
        type=int,
        default=None,
        help="Max games kept per neutral file so none dominates set B "
        "(default: --per-set).",
    )
    parser.add_argument(
        "--neutral-min-games",
        type=int,
        default=50,
        help="Minimum games per neutral file after filtering; a source "
        "below this fails loudly instead of freezing a thin file.",
    )
    parser.add_argument(
        "--neutral-include-bot-games",
        action="store_true",
        help="Keep games where either side is titled BOT (default: dropped).",
    )
    parser.add_argument(
        "--exclude-ids-json",
        default=None,
        help="Manifest or id list whose games must never enter set B "
        "(e.g. a previous gate manifest already used for tuning).",
    )
    parser.add_argument(
        "--exclude-opponents",
        default="",
        help="Comma-separated opponent names dropped from both sets "
        "(bot accounts; matched case-insensitively after alias resolution).",
    )
    return parser.parse_args()


def _involves_bot(game) -> bool:
    """True when either side carries the Lichess BOT title."""
    for side in ("White", "Black"):
        if (game.headers.get(f"{side}Title") or "").strip().lower() == "bot":
            return True
    return False


def parse_pgn_text(text, min_plies, exclude_bots=False):
    """Parse PGN text into (kept, stats): min-plies and bot filtering."""
    kept = []
    fetched = short = bots = 0
    stream = io.StringIO(text)
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        fetched += 1
        if exclude_bots and _involves_bot(game):
            bots += 1
            continue
        if sum(1 for _ in game.mainline_moves()) < min_plies:
            short += 1
            continue
        kept.append(str(game))
    return kept, {"fetched": fetched, "bot_dropped": bots, "short_dropped": short}


def read_pgn_games(path, min_plies, exclude_bots=False):
    with open(path, encoding="utf-8", errors="replace") as fh:
        kept, _ = parse_pgn_text(fh.read(), min_plies, exclude_bots)
    return kept


def sanitize_username(user: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", user.lower()).strip("-") or "neutral"


def fetch_lichess_text(user, max_games):
    """One authenticated Lichess export; a 429 waits 60s and retries once."""
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

    def _get():
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/x-chess-pgn",
            },
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read().decode("utf-8", errors="replace")

    try:
        return _get()
    except urllib.error.HTTPError as exc:
        if exc.code != 429:
            raise
        print(f"Lichess rate limit (429) for {user}; waiting 60s, one retry...")
        time.sleep(60)
        try:
            return _get()
        except urllib.error.HTTPError as retry_exc:
            raise SystemExit(
                f"Lichess still rate-limited (429) for {user} after the "
                "60s wait; aborting instead of hammering the API."
            ) from retry_exc


def build_neutral_set(key, text, args, file_cap, source):
    """Filter, cap, and validate one neutral source. Fails loudly on thin."""
    exclude_bots = not args.neutral_include_bot_games
    kept, stats = parse_pgn_text(text, args.min_plies, exclude_bots)
    if len(kept) < args.neutral_min_games:
        raise SystemExit(
            f"neutral source '{key}' kept only {len(kept)} games "
            f"(fetched={stats['fetched']} bot_dropped={stats['bot_dropped']} "
            f"short_dropped={stats['short_dropped']}, "
            f"min={args.neutral_min_games}); refusing to freeze a thin file"
        )
    capped_from = len(kept)
    if len(kept) > file_cap:
        kept = kept[:file_cap]
    return {
        "key": key,
        "source": source,
        "pgns": kept,
        "fetched": stats["fetched"],
        "bot_dropped": stats["bot_dropped"],
        "short_dropped": stats["short_dropped"],
        "capped_from": capped_from,
        "cap": file_cap,
        "bot_games_excluded": exclude_bots,
    }


def read_neutral_sets(args):
    """One filtered, capped, validated game list per neutral source."""
    file_cap = args.neutral_file_cap or args.per_set
    sources = []
    for user in args.neutral_user or []:
        text = fetch_lichess_text(user, args.neutral_max)
        sources.append(
            build_neutral_set(
                sanitize_username(user), text, args, file_cap,
                source=f"lichess-user:{user}",
            )
        )
    if args.neutral_pgn:
        with open(args.neutral_pgn, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        sources.append(
            build_neutral_set("dump", text, args, file_cap,
                              source=f"local-dump:{args.neutral_pgn}")
        )
    return sources


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


def excluded_opponent_groups(raw):
    """Lowercased alias groups to drop from both sets (bots, test accounts)."""
    return frozenset(
        name.strip().lower() for name in (raw or "").split(",") if name.strip()
    )


def split_by_opponent(
    games, per_set, exclude_b_ids=frozenset(), exclude_opponents=frozenset()
):
    """Disjoint opponent pools: every game of an opponent goes to one set.

    Opponents are dealt largest-first, alternating between the sets; aliased
    accounts stay in one pool. Games whose alias group is in
    exclude_opponents never enter either set (bot accounts on either side).
    Set B additionally drops every id in exclude_b_ids, so a game already
    used for tuning can never reappear in the final gate.
    """
    by_opponent = defaultdict(list)
    for game in games:
        name = game["opponent"] or "unknown"
        group = OPPONENT_ALIASES.get(name, name)
        if group.lower() in exclude_opponents:
            continue
        by_opponent[group].append(game)

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
    exclude_opponents = excluded_opponent_groups(args.exclude_opponents)
    set_a, set_b = split_by_opponent(
        games,
        args.per_set,
        exclude_b_ids,
        exclude_opponents,
    )
    if exclude_opponents:
        dropped = sum(
            1
            for game in games
            if (
                OPPONENT_ALIASES.get(game["opponent"] or "unknown", game["opponent"] or "unknown")
                .lower()
                in exclude_opponents
            )
        )
        print(f"excluded opponents {sorted(exclude_opponents)}: {dropped} games dropped")
    if not set_a or not set_b:
        raise SystemExit(
            f"not enough usable games: {len(games)} total, "
            f"A={len(set_a)} B={len(set_b)}"
        )

    path_a = out_dir / "gate_set_A.pgn"
    path_b = out_dir / "gate_set_B.pgn"
    hash_a = write_set(path_a, set_a)
    hash_b = write_set(path_b, set_b)

    neutral_sets = read_neutral_sets(args)
    neutral_files = []
    for entry in neutral_sets:
        path_nb = out_dir / f"gate_set_B.neutral.{entry['key']}.pgn"
        hash_nb = write_pgn_file(path_nb, entry["pgns"])
        neutral_files.append(
            {
                "key": entry["key"],
                "source": entry["source"],
                "file": str(path_nb),
                "sha256": hash_nb,
                "games": len(entry["pgns"]),
                "fetched": entry["fetched"],
                "bot_dropped": entry["bot_dropped"],
                "short_dropped": entry["short_dropped"],
                "capped_from": entry["capped_from"],
                "cap": entry["cap"],
                "bot_games_excluded": entry["bot_games_excluded"],
            }
        )
    neutral_manifest = {
        "files": neutral_files,
        "total_games": sum(item["games"] for item in neutral_files),
        "file_cap": args.neutral_file_cap or args.per_set,
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
        "excluded_opponents": sorted(exclude_opponents),
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
    for item in neutral_files:
        print(
            f"neutral {item['key']}: {item['games']} games "
            f"(fetched={item['fetched']} bot_dropped={item['bot_dropped']} "
            f"short_dropped={item['short_dropped']} "
            f"capped_from={item['capped_from']}) sha256={item['sha256']}"
        )
    print(f"opponents A: {distribution(set_a)}")
    print(f"opponents B: {distribution(set_b)}")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
