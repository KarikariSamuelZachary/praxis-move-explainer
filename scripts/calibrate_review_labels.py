"""
Calibrate the Game Review classifier against Chess.com's own labels.

Input is a labels file in the format recorded from Chess.com's Game Review
(see "Game 1.txt" in the repo root), with one or more games per file:

    Game 1
    URL: https://www.chess.com/game/live/184551359070?username=...
    Labels: book, book, best, excellent, ...
    Accuracy: White 62.3 / Black 53.4

    Game 2
    URL: https://www.chess.com/analysis/game/live/184566098588/review
    Labels: ...

PGN resolution per game, in order:
  1. `--pgn PATH` when exactly one game is in the file,
  2. `Game {N}.pgn` next to the labels file,
  3. the local `opponent_games` table by game URL.

The production classifier (Expected Points model, real opening book,
MultiPV=2) replays each PGN; every ply is compared against Chess.com's
label, printing per-game mismatches and an aggregate confusion matrix.

"only move" is a Chess.com label our classifier does not emit. It remains a
strict-label mismatch, and a second semantic score counts it as `best` because
both indicate the engine's forced choice.

Usage:
    venv/bin/python scripts/calibrate_review_labels.py "Game 1.txt"
        [--pgn path.pgn] [--depth 18] [--time 0.5] [--show-matches]
        [--json out.json]
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # noqa: BLE001 -- dotenv is optional
    pass

from core import database  # noqa: E402
from core.game_analyzer import GameAnalyzer  # noqa: E402
from engines.stockfish_engine import StockfishEngine  # noqa: E402
from llms.mock_explainer import MockExplainer  # noqa: E402
from services.opening_book import is_book_move  # noqa: E402

LABELS = {
    "book",
    "brilliant",
    "great",
    "best",
    "excellent",
    "good",
    "inaccuracy",
    "mistake",
    "miss",
    "blunder",
    # Chess.com emits this one; our classifier has no equivalent label.
    "only move",
}
UNMAPPED_LABELS = {"only move"}
LABEL_ALIASES = {"ex": "excellent"}
# Game-ending markers the user may write where Chess.com classified nothing.
NOTE_TOKENS = {
    "timeout",
    "time out",
    "flag",
    "flagged",
    "resign",
    "resigned",
    "resignation",
    "draw",
    "stalemate",
}

# SAN-like trailing notes ("Qxg3", "Kxh5", "O-O", "e8=Q+") the user may
# write instead of a label for the final move; ignored with a warning.
SAN_NOTE_RE = re.compile(
    r"^(?:[KQRBN][a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?"
    r"|[a-h]x?[a-h][1-8](?:=[QRBN])?[+#]?"
    r"|O-O(?:-O)?[+#]?)$",
    re.IGNORECASE,
)


def _parse_label_phrase(phrase):
    """Split one comma-separated phrase into labels.

    Known multi-word labels ("only move") win first; otherwise a phrase like
    "best excellent" (missing comma) is split into its known tokens.
    """
    phrase = re.sub(r"\([^)]*\)", "", phrase).strip().lower()
    phrase = re.sub(r"\s+", " ", phrase)
    if not phrase:
        return []
    phrase = LABEL_ALIASES.get(phrase, phrase)
    if phrase in LABELS:
        return [phrase]
    tokens = [LABEL_ALIASES.get(token, token) for token in phrase.split(" ")]
    if all(token in LABELS for token in tokens):
        return tokens
    return [phrase]


def parse_labels_file(path):
    """Return [{number, url, labels}] from a hand-recorded labels file."""
    games = []
    current = None
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            game_match = re.match(r"^game\s+(\d+)\s*$", stripped, re.IGNORECASE)
            if game_match:
                current = {
                    "number": int(game_match.group(1)),
                    "url": None,
                    "labels": None,
                }
                games.append(current)
                continue
            lower = stripped.lower()
            if current is None:
                continue
            if lower.startswith("url:"):
                current["url"] = stripped.split(":", 1)[1].strip()
            elif lower.startswith("labels:"):
                text = stripped.split(":", 1)[1].strip()
                # Tolerate periods used as separators ("miss. blunder").
                text = text.replace(".", ",")
                labels = []
                for phrase in text.split(","):
                    parsed = _parse_label_phrase(phrase)
                    if parsed and all(token in LABELS for token in parsed):
                        labels.extend(parsed)
                    elif len(parsed) == 1 and parsed[0] in NOTE_TOKENS:
                        print(
                            f"  Game {current['number']}: ignoring game-end "
                            f"marker {phrase.strip()!r}"
                        )
                    elif len(parsed) == 1 and SAN_NOTE_RE.match(parsed[0]):
                        # Trailing move notation instead of a label
                        # (e.g. "Qxg3" for the last move) — ignore it.
                        print(
                            f"  Game {current['number']}: ignoring move note "
                            f"{phrase.strip()!r}"
                        )
                    elif len(phrase.split()) > 1:
                        # Free-text note at the end of the list (e.g.
                        # "game ends on last move Qxh5") — ignore it.
                        print(
                            f"  Game {current['number']}: ignoring note "
                            f"{phrase.strip()!r}"
                        )
                    else:
                        labels.extend(parsed)  # single unknown -> error below
                current["labels"] = labels

    if not games:
        raise SystemExit(f"No 'Game N' blocks found in {path}")
    for game in games:
        if not game["labels"]:
            raise SystemExit(f"Game {game['number']} has no Labels line")
        unknown = [label for label in game["labels"] if label not in LABELS]
        if unknown:
            raise SystemExit(
                f"Game {game['number']} has unknown labels: {sorted(set(unknown))}"
            )
    return games


def resolve_pgn(game, labels_path, explicit_pgn, game_count):
    if explicit_pgn and game_count == 1:
        with open(explicit_pgn, encoding="utf-8") as handle:
            return handle.read()

    beside = os.path.join(
        os.path.dirname(os.path.abspath(labels_path)), f"Game {game['number']}.pgn"
    )
    if os.path.exists(beside):
        with open(beside, encoding="utf-8") as handle:
            return handle.read()

    url = game.get("url")
    if not url:
        return None
    base = url.split("?")[0].rstrip("/")
    base = base.replace("/review", "")
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pgn FROM opponent_games
                WHERE game_url = %s
                ORDER BY end_time DESC
                LIMIT 1
                """,
                (base,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        database.connection_pool.putconn(conn)


def move_label(row):
    color = "w" if row["color"] == "white" else "b"
    return f"{row['move_number']}{color}.{row['san']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels_file", help="Hand-recorded labels file.")
    parser.add_argument(
        "--pgn",
        default=None,
        help="PGN file (only when the labels file holds one game).",
    )
    parser.add_argument("--depth", type=int, default=18, help="Stockfish depth.")
    parser.add_argument(
        "--time",
        type=float,
        default=0.0,
        help="Seconds per position; 0 (default) = deterministic depth-only search.",
    )
    parser.add_argument(
        "--show-matches", action="store_true", help="Print matching plies too."
    )
    parser.add_argument("--json", default=None, help="Dump the comparison to JSON.")
    args = parser.parse_args()

    games = parse_labels_file(args.labels_file)
    database.init_db()

    analysis_time = args.time if args.time > 0 else None
    all_rows = []
    all_labels = []
    for game in games:
        pgn = resolve_pgn(game, args.labels_file, args.pgn, len(games))
        if not pgn:
            print(
                f"\nGame {game['number']}: NO PGN (expected "
                f"'Game {game['number']}.pgn' beside the labels file or the "
                f"game in the DB)"
            )
            continue

        # Fresh engine per game: Stockfish's transposition table persists
        # across searches, so reusing one process makes results depend on
        # analysis order. A fresh process keeps each game reproducible.
        engine = StockfishEngine(depth=args.depth, analysis_time=analysis_time)
        if analysis_time is None:
            # The constructor defaults analysis_time to FAST_ANALYSIS_TIME
            # (0.1s), which would make the search time-limited and noisy.
            # Force a depth-only search for reproducible calibration.
            engine.analysis_time = None
        engine.start()
        try:
            analyzer = GameAnalyzer(
                engine=engine,
                explainer=MockExplainer(),
                book_lookup=is_book_move,
                multipv=2,
            )
            rows = analyzer.analyze_full_game(pgn)[1:]
        finally:
            engine.close()

        print(f"\n{'=' * 72}")
        print(f"Game {game['number']}: {game.get('url') or '(no url)'}")
        labels = game["labels"]
        if abs(len(labels) - len(rows)) > 1:
            print(
                f"  SKIPPING: {len(labels)} labels vs {len(rows)} plies — "
                f"the list looks incomplete or misaligned; re-check it."
            )
            continue
        if len(labels) != len(rows):
            print(
                f"  WARNING: {len(labels)} labels vs {len(rows)} plies — "
                f"comparing the first {min(len(labels), len(rows))}"
            )
        count = min(len(labels), len(rows))
        mismatches = []
        semantic_matches = 0
        for i in range(count):
            theirs, ours = labels[i], rows[i]["classification"]
            if theirs != ours:
                mismatches.append((i, theirs, ours))
                if theirs == "only move" and ours == "best":
                    semantic_matches += 1
            elif args.show_matches:
                print(
                    f"  {i + 1:>3} {move_label(rows[i]):>12} {theirs:>10} == {ours}"
                )

        print(f"  Agreement: {count - len(mismatches)}/{count} = "
              f"{100.0 * (count - len(mismatches)) / count:.1f}%")
        print(
            f"  Semantic agreement (only move = best): "
            f"{count - len(mismatches) + semantic_matches}/{count} = "
            f"{100.0 * (count - len(mismatches) + semantic_matches) / count:.1f}%"
        )
        for i, theirs, ours in mismatches:
            row = rows[i]
            print(
                f"    {i + 1:>3} {move_label(row):>12}  chess.com={theirs:<10} "
                f"ours={ours:<10} cp={row['cp_loss']:>5} ep={row['ep_loss']:.3f}"
            )

        for special in ("brilliant", "great", "miss", "only move"):
            theirs_idx = [i for i in range(count) if labels[i] == special]
            ours_idx = [
                i for i in range(count) if rows[i]["classification"] == special
            ]
            hit = len(set(theirs_idx) & set(ours_idx))
            if theirs_idx or ours_idx:
                print(
                    f"    {special:<9} chess.com={len(theirs_idx):>2} "
                    f"ours={len(ours_idx):>2} both={hit}"
                )

        all_rows.extend(rows[:count])
        all_labels.extend(labels[:count])

    if not all_rows:
        return 1

    count = len(all_rows)
    confusion = {}
    mismatch_count = 0
    semantic_match_count = 0
    for theirs, row in zip(all_labels, all_rows):
        ours = row["classification"]
        confusion.setdefault(theirs, {}).setdefault(ours, 0)
        confusion[theirs][ours] += 1
        if theirs != ours:
            mismatch_count += 1
            if theirs == "only move" and ours == "best":
                semantic_match_count += 1

    print(f"\n{'=' * 72}")
    print(f"AGGREGATE: {count - mismatch_count}/{count} = "
          f"{100.0 * (count - mismatch_count) / count:.1f}% exact agreement")
    print(
        f"SEMANTIC (only move = best): "
        f"{count - mismatch_count + semantic_match_count}/{count} = "
        f"{100.0 * (count - mismatch_count + semantic_match_count) / count:.1f}%"
    )
    print("\nConfusion (rows = chess.com, cols = ours):")
    our_labels = sorted({row["classification"] for row in all_rows} | set(all_labels))
    print("theirs \\ ours".ljust(12) + "".join(f"{c[:9]:>10}" for c in our_labels))
    for theirs in sorted(confusion):
        cells = "".join(
            f"{confusion[theirs].get(ours, 0):>10}" for ours in our_labels
        )
        print(f"{theirs:<12}{cells}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(
                [
                    {
                        "ply": i + 1,
                        "move": move_label(all_rows[i]),
                        "chesscom": all_labels[i],
                        "comparison_label": (
                            "best" if all_labels[i] == "only move" else all_labels[i]
                        ),
                        "ours": all_rows[i]["classification"],
                        "cp_loss": all_rows[i]["cp_loss"],
                        "ep_loss": all_rows[i]["ep_loss"],
                        "ep_best": all_rows[i].get("ep_best"),
                        "second_best_cp": all_rows[i].get("second_best_cp"),
                        "player_rating": all_rows[i].get("player_rating"),
                        "sacrifice_cp": all_rows[i].get("sacrifice_cp"),
                        "played_line_loss": all_rows[i].get("played_line_loss"),
                        "missed_tactic": all_rows[i].get("missed_tactic"),
                        "fen_before": all_rows[i].get("fen_before"),
                        "best_move_uci": all_rows[i].get("best_move_uci"),
                        "best_move_san": all_rows[i].get("best_move_san"),
                    }
                    for i in range(count)
                ],
                handle,
                indent=2,
            )
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
