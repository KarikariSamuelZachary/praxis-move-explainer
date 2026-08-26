"""
Player self-consistency measurement (TEST-ONLY).

Empirical ceiling for move-prediction accuracy: if the opponent doesn't
reliably play the same move from the same position across their own games,
no predictor can beat that inherent inconsistency.

Reads the EXISTING opponent_repertoire_moves table (which already stores
position_key = first 4 FEN fields, the exact position-key convention used
by the repertoire/sparring code) for the target opponent, groups by
position_key, and measures per-position self-consistency across distinct
games. No production code is changed.
"""

import os
import sys
from collections import Counter, defaultdict

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # noqa: BLE001
    pass

UID = "user_3I3cNvpU6XgdUBTqEaYASS251nJ"
PROVIDER = "chesscom"
OPPONENT = sys.argv[1] if len(sys.argv) > 1 else "iaminspiredbroo"


def ply_bucket(ply: int) -> str:
    if ply <= 10:
        return "opening"
    if ply <= 30:
        return "middlegame"
    return "endgame"


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT position_key, move_uci, ply_index, opponent_game_id::text
        FROM opponent_repertoire_moves
        WHERE requested_by_user_id = %s
          AND provider = %s
          AND LOWER(opponent_username) = LOWER(%s)
        """,
        (UID, PROVIDER, OPPONENT),
    )
    rows = cur.fetchall()
    conn.close()

    total_moves = len(rows)

    # Group by position_key; within a game dedupe to the earliest occurrence.
    by_pos = defaultdict(list)
    for pk, move, ply, gid in rows:
        by_pos[pk].append((gid, ply, move))

    intra_game_mixed = 0  # same game, same position, different moves
    repeated = []  # per-position records
    repeated_positions = set()
    never_seen_positions = 0

    for pk, occ in by_pos.items():
        # earliest occurrence per game
        best = {}
        for gid, ply, move in occ:
            if gid not in best or ply < best[gid][0]:
                best[gid] = (ply, move)
        # detect intra-game multi-row (same position twice in one game)
        if len(best) < len(occ):
            # possible intra-game repetition; check for differing moves
            per_game_moves = defaultdict(set)
            for gid, ply, move in occ:
                per_game_moves[gid].add(move)
            for gid, moves in per_game_moves.items():
                if len(moves) > 1:
                    intra_game_mixed += 1

        n_games = len(best)
        if n_games < 2:
            never_seen_positions += 1
            continue

        repeated_positions.add(pk)
        move_counter = Counter(move for _, move in best.values())
        mode_move, mode_count = move_counter.most_common(1)[0]
        ply_counter = Counter(ply for _, ply, _ in occ)
        mode_ply = ply_counter.most_common(1)[0][0] + 1  # 1-indexed
        repeated.append({
            "pk": pk,
            "n_games": n_games,
            "mode_count": mode_count,
            "consistency": mode_count / n_games,
            "n_distinct_moves": len(move_counter),
            "ply": mode_ply,
        })

    n_repeated_positions = len(repeated)
    total_occ = sum(r["n_games"] for r in repeated)
    total_mode = sum(r["mode_count"] for r in repeated)
    overall_consistency = total_mode / total_occ

    # fraction of total moves that fall into a repeated position (raw rows)
    rows_in_repeated = sum(1 for pk, *_ in rows if pk in repeated_positions)
    fraction_covered = rows_in_repeated / total_moves

    # split by occurrence count
    def agg(sub):
        occ = sum(r["n_games"] for r in sub)
        if occ == 0:
            return None
        return sum(r["mode_count"] for r in sub) / occ

    twice = [r for r in repeated if r["n_games"] == 2]
    threeplus = [r for r in repeated if r["n_games"] >= 3]

    # split by ply bucket
    by_bucket = defaultdict(list)
    for r in repeated:
        by_bucket[ply_bucket(r["ply"])].append(r)

    # consistency distribution over positions
    perfect = [r for r in repeated if r["n_distinct_moves"] == 1]

    print("=" * 70)
    print(f"PLAYER SELF-CONSISTENCY — {OPPONENT} (chess.com)")
    print("=" * 70)
    print(f"total opponent moves (rows): {total_moves}")
    print(f"distinct positions faced:     {len(by_pos)}")
    print(f"positions faced once only:    {never_seen_positions}")
    print(f"repeated positions (>=2 distinct games): {n_repeated_positions}")
    print(f"total repeated-position occurrences:     {total_occ}")
    print(f"moves in repeated positions (raw rows): {rows_in_repeated}")
    print(f"fraction of all moves in a repeated position: "
          f"{fraction_covered * 100:.1f}%")
    print(f"intra-game repetitions with differing moves (caveat): "
          f"{intra_game_mixed}")

    print(f"\nOVERALL self-consistency: {overall_consistency * 100:.2f}% "
          f"({total_mode}/{total_occ})")

    print(f"\n--- by occurrence count ---")
    print(f"  faced 2x:  {len(twice)} positions, "
          f"consistency = {agg(twice) * 100:.2f}%")
    print(f"  faced 3+x: {len(threeplus)} positions, "
          f"consistency = {agg(threeplus) * 100:.2f}%")

    print(f"\n--- by ply bucket (position assigned by its modal ply) ---")
    for pb in ("opening", "middlegame", "endgame"):
        sub = by_bucket.get(pb, [])
        n = len(sub)
        if n == 0:
            print(f"  {pb:<12}: 0 positions")
            continue
        c = agg(sub)
        print(f"  {pb:<12}: {n} positions, "
              f"{sum(r['n_games'] for r in sub)} occurrences, "
              f"consistency = {c * 100:.2f}%")

    print(f"\n--- consistency distribution over repeated positions ---")
    print(f"  100% consistent positions: {len(perfect)} "
          f"({len(perfect) / n_repeated_positions * 100:.1f}%)")
    print(f"  <100% consistent positions: "
          f"{n_repeated_positions - len(perfect)} "
          f"({(n_repeated_positions - len(perfect)) / n_repeated_positions * 100:.1f}%)")
    # histogram of consistency among non-perfect positions
    hist = Counter()
    for r in repeated:
        if r["n_distinct_moves"] == 1:
            continue
        hist[round(r["consistency"], 1)] += 1
    print(f"  consistency histogram (non-perfect positions): "
          f"{dict(sorted(hist.items()))}")

    # how much of the "covered" signal is 2x (thin) vs 3+x
    occ_2x = sum(r["n_games"] for r in twice)
    print(f"\n  thin-sample share: {len(twice)}/{n_repeated_positions} positions "
          f"are faced exactly 2x ({occ_2x}/{total_occ} occurrences = "
          f"{occ_2x / total_occ * 100:.1f}% of repeated occurrences)")


if __name__ == "__main__":
    main()
