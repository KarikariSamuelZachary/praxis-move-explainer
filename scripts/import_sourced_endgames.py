#!/usr/bin/env python
"""
Import sourced, tablebase-verified endgame candidates into endgame_topics /
endgame_positions.

Consumes the survivors.jsonl produced by scripts/source_endgame_candidates.py
and maps the VERIFIED rows (verification != "tag-trusted-sampled") whose
drill position is <=7 men into the existing Endgame Trainer content tables.

WHAT GETS IMPORTED
==================
  * Row set: survivors with verification in {tablebase-verified,
    tablebase-verified-sampled, terminal-rule} -- i.e. the puzzle's solution
    line reaches a tablebase-verified decisive/drawn ending, or a rule-decided
    checkmate. tag-trusted-sampled rows are deliberately NOT imported.
  * Drill FEN: puzzle_fen (the position AFTER the opponent's setup move, with
    the solver to move), full six-field FEN. The raw start_fen is NOT stored:
    the drill starts after the setup move, matching the frontend fix in
    frontend/src/app/(app)/woodpecker/page.tsx.
  * is_winning: TRUE = tablebase win for the side to move, FALSE = draw.
    - <=5-man drill starts are re-probed HERE against local Syzygy at import
      time, so the stored flag is exact (any mismatch with the puzzle tag is
      excluded and counted, never force-fit).
    - 6-7-man drill starts cannot be probed locally; is_winning is derived
      from the puzzle track (win/equality). This is backed by the exhaustive
      <=5-man consistency check (7,901/7,901 agreed with the tag) and by the
      verified ending, but it is a derived, not probed, verdict -- reported
      as such in the import report.
  * halfmove clock: rows whose drill FEN starts with clock >= --max-halfmove
    (default 50) are excluded. The tablebase verdict ignores the clock, but
    a win drill that starts deep into the 50-move count can be unwinnable
    before the user errs (session resolution treats clock >= 100 as a draw).
    These rows are counted, not silently dropped.

TOPIC / CATEGORY RESOLUTION (the open schema question)
======================================================
The schema requires every position to hang off a topic_id, and topics carry
a category from the fixed 10-value CHECK set. Sourced puzzles are not named
theory, so:

  * CATEGORY is derived from the drill position's own material using the
    CHECK set's documented definitions (migrations.py):
        pure_pawn      kings + pawns only
        knight         knight(s) only, no other non-pawn pieces (NN vs K -> knight)
        bishop         exactly one bishop
        bishop_bishop  exactly two bishops, one per side (bishop vs bishop)
        bishop_knight  bishop(s) + knight(s)
        rook           one rook per side (incl. R+P vs R, Lucena/Philidor)
        bishop_rook    rook(s) + bishop(s)
        knight_rook    rook(s) + knight(s)
        queen          queens only, no other non-pawn pieces
        multi_piece    everything else (Q vs R, two rooks on one side, ...)
    Deriving from the drill material -- rather than the Lichess source theme,
    which describes the whole game's ending -- is exact and unambiguous.

  * TOPIC: one per UNORDERED material signature, named e.g.
    "KRP vs KR (sourced)". Mirrored signatures collapse into one topic
    (KR vs KRP == KRP vs KR), and win/draw variants coexist under it exactly
    like the curated "Lucena Position" topic mixes outcomes.
    difficulty_rating = median puzzle rating of the topic's rows (the schema
    stores difficulty at the topic level); sort_order is NULL so sourced
    topics sort after curated ones.

  No schema change is needed. The "(sourced)" suffix keeps generated topics
  distinct from curated ones, and the whole import is idempotent:
  endgame_topics upserts on name, endgame_positions uses
  ON CONFLICT (topic_id, fen) DO NOTHING.

USAGE
=====
    source venv/bin/activate
    python scripts/import_sourced_endgames.py            # dry run (default)
    python scripts/import_sourced_endgames.py --apply    # write to the DB

    # Regenerate the committed deploy seed artifact from the local survivor
    # run; src/services/endgame_seeding.py imports this file at first boot
    # when the sourced pool is empty (gzip is read transparently):
    python scripts/import_sourced_endgames.py \\
        --export-seed data/seed/endgame_sourced_seed.jsonl.gz
"""
import argparse
import collections
import gzip
import json
import logging
import os
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import chess
import chess.syzygy
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_SURVIVORS = (
    ROOT_DIR / "data" / "processed" / "endgame_candidates" / "survivors.jsonl"
)
DEFAULT_OUT_DIR = ROOT_DIR / "data" / "processed" / "endgame_candidates"
DEFAULT_TABLEBASE_DIR = ROOT_DIR / "data" / "syzygy" / "regular"

VERIFIED_KINDS = {
    "tablebase-verified",
    "tablebase-verified-sampled",
    "terminal-rule",
}

# Same DB config shape as src/seed_endgames.py: discrete DB_* vars first,
# then the app's DATABASE_URL when they are not all set.
DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
}
if not all([DB_CONFIG["dbname"], DB_CONFIG["user"], DB_CONFIG["password"]]):
    DB_CONFIG = {"dsn": os.getenv("DATABASE_URL")}

log = logging.getLogger("import_sourced_endgames")


def tablebase_dir() -> Path:
    configured = os.getenv("SYZYGY_TABLEBASE_DIR")
    return Path(configured) if configured else DEFAULT_TABLEBASE_DIR


# ---------------------------------------------------------------------------
# Material classification
# ---------------------------------------------------------------------------

def board_sides(board: chess.Board) -> Tuple[List[str], List[str]]:
    """Uppercase piece letters (kings included) for white and black."""
    white: List[str] = []
    black: List[str] = []
    for piece in board.piece_map().values():
        (white if piece.color else black).append(piece.symbol().upper())
    return white, black


def material_key(board: chess.Board) -> Tuple[str, str]:
    """Unordered material signature, e.g. (\"KRP\", \"KR\") for both color
    orientations of rook+pawn vs rook. Sides are ordered stronger-first so
    mirrored signatures collapse to one key."""
    rank = "KQRBNP"

    def render(pieces: List[str]) -> str:
        return "".join(sorted(pieces, key=rank.index))

    def strength(s: str) -> Tuple[int, List[int]]:
        # More material first, then strongest piece first (rank index
        # increases as pieces get weaker, so negate it).
        return len(s), [-rank.index(c) for c in s]

    white_pieces, black_pieces = board_sides(board)
    white, black = render(white_pieces), render(black_pieces)
    pair = sorted([white, black], key=strength, reverse=True)
    return pair[0], pair[1]


def category_for(board: chess.Board) -> str:
    """Map a drill position to one of the schema's 10 category values."""
    white, black = board_sides(board)
    pieces = collections.Counter(
        p for p in white + black if p not in ("K", "P")
    )
    if not pieces:
        return "pure_pawn"
    kinds = set(pieces)
    if kinds == {"Q"}:
        return "queen"
    if kinds == {"N"}:
        return "knight"
    if kinds == {"B"}:
        if pieces["B"] == 1:
            return "bishop"
        if pieces["B"] == 2 and white.count("B") == 1 and black.count("B") == 1:
            return "bishop_bishop"
        return "multi_piece"  # two bishops on one side: not covered by the pair
    if kinds == {"B", "N"}:
        return "bishop_knight"
    if kinds == {"R"}:
        if pieces["R"] == 1:
            return "rook"
        if pieces["R"] == 2 and white.count("R") == 1 and black.count("R") == 1:
            return "rook"
        return "multi_piece"  # double-rook endings per the schema comment
    if kinds == {"B", "R"}:
        return "bishop_rook"
    if kinds == {"N", "R"}:
        return "knight_rook"
    return "multi_piece"


def load_survivors(path: Path) -> Iterator[Dict[str, Any]]:
    """Yield survivor records from a plain or gzipped JSONL file.

    Gzip is accepted because the committed deploy seed artifact
    (data/seed/endgame_sourced_seed.jsonl.gz, produced by --export-seed)
    ships compressed; src/services/endgame_seeding.py points this loader
    at it on first boot.
    """
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def export_seed(survivor_path: Path, out_path: Path) -> int:
    """Write the rows the importer would consider as gzipped JSONL.

    The filter is deliberately identical to build_plan's early skips
    (verified kinds, <=7 men), so applying the exported file produces the
    same library as applying the full survivors run. Used to regenerate
    the committed data/seed artifact after a new sourcing run.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    with gzip.open(out_path, "wt", encoding="utf-8", compresslevel=9) as out:
        for record in load_survivors(survivor_path):
            if record.get("verification") not in VERIFIED_KINDS:
                continue
            if (record.get("puzzle_men") or 99) > 7:
                continue
            out.write(json.dumps(record, separators=(",", ":")) + "\n")
            kept += 1
    print(f"exported {kept} verified <=7-man rows -> {out_path}")
    return 0


class TopicPlan:
    def __init__(self, name: str, category: str) -> None:
        self.name = name
        self.category = category
        self.rows: List[Tuple[str, bool]] = []
        self.seen_fens = set()
        self.ratings: List[int] = []
        self.wins = 0
        self.draws = 0
        self.probed = 0
        self.derived = 0

    def add(self, fen: str, is_winning: bool, rating: int, probed: bool) -> bool:
        if fen in self.seen_fens:
            return False
        self.seen_fens.add(fen)
        self.rows.append((fen, is_winning))
        self.ratings.append(rating)
        self.probed += 1 if probed else 0
        self.derived += 0 if probed else 1
        if is_winning:
            self.wins += 1
        else:
            self.draws += 1
        return True

    def description(self) -> str:
        median = int(statistics.median(self.ratings)) if self.ratings else 0
        return (
            f"Sourced from tablebase-verified Lichess endgame puzzles. "
            f"Material: {self.name.replace(' (sourced)', '')}. "
            f"{len(self.rows)} drill variants: {self.wins} winning, "
            f"{self.draws} drawn for the side to move. "
            f"Median puzzle rating {median}."
        )

    def difficulty(self) -> int:
        return int(statistics.median(self.ratings)) if self.ratings else 1200


def build_plan(
    survivor_path: Path,
    tablebase: chess.syzygy.Tablebase,
    max_halfmove: int,
) -> Tuple[Dict[str, TopicPlan], collections.Counter, int]:
    plan: Dict[str, TopicPlan] = {}
    excluded: collections.Counter = collections.Counter()
    considered = 0

    for record in load_survivors(survivor_path):
        if record.get("verification") not in VERIFIED_KINDS:
            excluded["not-verified(tag-trusted)"] += 1
            continue
        if (record.get("puzzle_men") or 99) > 7:
            excluded["drill-position-beyond-7-men"] += 1
            continue
        considered += 1
        board = chess.Board(record["puzzle_fen"])
        if board.halfmove_clock >= max_halfmove:
            excluded[f"halfmove-clock->={max_halfmove}"] += 1
            continue

        expected = 2 if record["track"] == "win" else 0
        if len(board.piece_map()) <= 5:
            try:
                wdl = tablebase.probe_wdl(board)
            except KeyError:
                excluded["local-probe-unavailable"] += 1
                continue
            if wdl != expected:
                # A tag/start disagreement: never force-fit, always count.
                excluded[f"start-probe-mismatch(wdl={wdl})"] += 1
                continue
            is_winning = wdl == 2
            probed = True
        else:
            is_winning = record["track"] == "win"
            probed = False

        topic_name = " vs ".join(material_key(board)) + " (sourced)"
        topic = plan.get(topic_name)
        if topic is None:
            topic = TopicPlan(topic_name, category_for(board))
            plan[topic_name] = topic
        if not topic.add(record["puzzle_fen"], is_winning, record["rating"], probed):
            excluded["duplicate-fen-within-topic"] += 1

    return plan, excluded, considered


def write_plan(conn, plan: Dict[str, TopicPlan]) -> Dict[str, int]:
    cur = conn.cursor()
    topics_upserted = 0
    positions_inserted = 0
    for name, topic in sorted(plan.items()):
        cur.execute(
            """
            INSERT INTO endgame_topics
                (name, category, description, difficulty_rating, sort_order)
            VALUES (%s, %s, %s, %s, NULL)
            ON CONFLICT (name) DO UPDATE
                SET category          = EXCLUDED.category,
                    description       = EXCLUDED.description,
                    difficulty_rating = EXCLUDED.difficulty_rating,
                    updated_at        = NOW()
            RETURNING id
            """,
            (name, topic.category, topic.description(), topic.difficulty()),
        )
        topic_id = cur.fetchone()[0]
        topics_upserted += 1
        before = positions_inserted
        if topic.rows:
            # One statement so cur.rowcount is the real insert count
            # (execute_values with a smaller page_size only reports the
            # last page).
            execute_values(
                cur,
                """
                INSERT INTO endgame_positions (topic_id, fen, is_winning)
                VALUES %s
                ON CONFLICT (topic_id, fen) DO NOTHING
                """,
                [(topic_id, fen, win) for fen, win in topic.rows],
                page_size=max(len(topic.rows), 1),
            )
            positions_inserted += cur.rowcount
        log.info(
            "topic %-24s category=%-14s rows=%d inserted=%d",
            name, topic.category, len(topic.rows), positions_inserted - before,
        )
    conn.commit()
    return {"topics_upserted": topics_upserted, "positions_inserted": positions_inserted}


def backfill_source_ids(survivor_path: Path) -> int:
    """Set endgame_positions.source_puzzle_id for every sourced row from the
    survivors file (FEN -> first puzzle id, same filters/order as the
    import). Applies pending migrations first so the column exists."""
    if str(ROOT_DIR / "src") not in sys.path:
        sys.path.insert(0, str(ROOT_DIR / "src"))
    from core.database import init_db
    from core.migrations import run_migrations

    init_db()
    run_migrations()

    mapping: Dict[str, str] = {}
    for record in load_survivors(survivor_path):
        if record.get("verification") not in VERIFIED_KINDS:
            continue
        if (record.get("puzzle_men") or 99) > 7:
            continue
        mapping.setdefault(record["puzzle_fen"], record["puzzle_id"])

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                UPDATE endgame_positions p
                SET source_puzzle_id = v.puzzle_id,
                    updated_at        = NOW()
                FROM (VALUES %s) AS v(fen, puzzle_id)
                WHERE p.fen = v.fen
                  AND p.topic_id IN (
                      SELECT id FROM endgame_topics WHERE name LIKE '%%(sourced)'
                  )
                """,
                list(mapping.items()),
                page_size=max(len(mapping), 1),
            )
            updated = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    print(f"source_puzzle_id: mapped {len(mapping)} FENs, updated {updated} rows")
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--survivors", type=Path, default=DEFAULT_SURVIVORS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--apply", action="store_true",
                        help="write to the DB (default is a dry run)")
    parser.add_argument("--max-halfmove", type=int, default=50,
                        help="exclude drill FENs whose halfmove clock is >= N")
    parser.add_argument("--backfill-source-ids", action="store_true",
                        help="only set endgame_positions.source_puzzle_id, then exit")
    parser.add_argument(
        "--export-seed", type=Path, default=None,
        help="write the verified <=7-man survivor rows as gzipped JSONL to "
             "this path and exit (regenerates the committed deploy seed "
             "artifact, data/seed/endgame_sourced_seed.jsonl.gz)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.backfill_source_ids:
        return backfill_source_ids(args.survivors)

    if args.export_seed is not None:
        return export_seed(args.survivors, args.export_seed)

    tablebase = chess.syzygy.open_tablebase(str(tablebase_dir()))
    try:
        plan, excluded, considered = build_plan(
            args.survivors, tablebase, args.max_halfmove
        )
    finally:
        tablebase.close()

    total_rows = sum(len(t.rows) for t in plan.values())
    total_probed = sum(t.probed for t in plan.values())
    total_derived = sum(t.derived for t in plan.values())
    by_category: Dict[str, Dict[str, int]] = {}
    for topic in plan.values():
        cat = by_category.setdefault(topic.category, {"topics": 0, "rows": 0})
        cat["topics"] += 1
        cat["rows"] += len(topic.rows)

    print(f"considered verified rows: {considered}")
    print(f"topics: {len(plan)}   positions: {total_rows}")
    print(f"  drill starts probed locally (<=5 men): {total_probed}")
    print(f"  drill starts derived from track (6-7 men): {total_derived}")
    print("by category:")
    for cat, stats in sorted(by_category.items(), key=lambda kv: -kv[1]["rows"]):
        print(f"  {cat:14s} topics={stats['topics']:3d} rows={stats['rows']}")
    print("excluded:")
    for reason, count in excluded.most_common():
        print(f"  {reason}: {count}")

    report = {
        "considered_verified_rows": considered,
        "topics": len(plan),
        "positions": total_rows,
        "probed_locally": total_probed,
        "derived_from_track": total_derived,
        "by_category": by_category,
        "excluded": dict(excluded),
        "topics_detail": {
            name: {
                "category": t.category,
                "rows": len(t.rows),
                "wins": t.wins,
                "draws": t.draws,
                "probed": t.probed,
                "derived": t.derived,
                "difficulty_rating": t.difficulty(),
            }
            for name, t in plan.items()
        },
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.out_dir / "import_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"report: {report_path}")

    if not args.apply:
        print("DRY RUN - nothing written. Re-run with --apply to import.")
        return 0

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        written = write_plan(conn, plan)
    finally:
        conn.close()
    log.info(
        "imported topics=%d positions_inserted=%d (existing rows untouched)",
        written["topics_upserted"], written["positions_inserted"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
