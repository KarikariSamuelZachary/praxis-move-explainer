#!/usr/bin/env python
"""
Endgame Trainer data sourcing: Lichess puzzle CSV -> tablebase-verified
endgame candidate pool.

REPORT-ONLY. This script never touches the database and never writes to
endgame_positions: its outputs are the files under --out-dir (a JSONL row
per candidate plus a survivors file and a report). A later, separate step
decides how survivors map into the schema.

WHAT IT DOES
============
  1. Cheap filter over the FULL local Lichess puzzle CSV (the same file
     src/seed_puzzles.py loads: data/raw/lichess_db_puzzle.csv.zst,
     standard Lichess headers, Themes SPACE-separated). Keep rows tagged
     "endgame" AND at least one of rookEndgame / bishopEndgame /
     knightEndgame / queenEndgame / pawnEndgame / queenRookEndgame. Split
     into WIN track (no "equality" theme) and DRAW track ("equality").
     Per-category raw counts are logged and saved BEFORE any probing.
  2. Replay each candidate's FULL Moves sequence with python-chess from the
     CSV FEN to reconstruct the final ("ending") position. The Lichess
     puzzle convention -- already handled by the frontend fix in
     frontend/src/app/(app)/woodpecker/page.tsx (moves[0] is the opponent's
     setup move; the user solves moves[1:]) -- is applied by replaying from
     the raw FEN, so the setup move is part of the sequence. The replayed
     position after moves[0] is recorded as puzzle_fen (the actual drill
     position); the position after the last move is ending_fen.
  3. Probe EVERY ending position with services/tablebase.py (local Syzygy
     first, Lichess API fallback -- both already rate-limit-aware in that
     module). A candidate PASSES when the ending position is a genuine
     tablebase result for the track:
       * WIN track: clean win for the side that just delivered the solution
         (probe from the opponent's side to move: wdl == -2). A final
         checkmate is a special case of this and passes; the ending does
         NOT need to already be mate. Reasoning: the trainer wants
         ground-truth decided positions, and requiring mate would throw
         away every line that stops at a resolved material win. Cursed wins
         (wdl == -1) are NOT "genuine" under the 50-move rule (see
         services/tablebase.py DTZ SEMANTICS) and are discarded separately.
       * DRAW track: genuine draw (wdl == 0).
     Everything else is a mismatch, discarded and counted. Positions >7 men
     (beyond the API's ceiling -- locally unprobeable AND remotely
     unknowable) and positions with castling rights (not tablebase
     material) are never probed: they are discarded as unprobeable and
     counted, with terminal-checkmate specifics reported.
  4. Checkpoints continuously: results.jsonl is append-only, one line per
     candidate, flushed/fsynced every N candidates, so a crash loses at
     most the current batch and a re-run resumes by skipping processed
     puzzle ids. A persistent probe cache deduplicates identical ending
     positions across puzzles and across runs.

USAGE
=====
    source venv/bin/activate
    python scripts/source_endgame_candidates.py            # full run, resume-safe
    python scripts/source_endgame_candidates.py --limit 200  # smoke test
    python scripts/source_endgame_candidates.py --report-only  # regenerate outputs
    python scripts/source_endgame_candidates.py --fresh        # start over

Pacing: the Lichess API policy is one request at a time and ~1s between
requests; --api-interval (default 1.0s) enforces it, and the existing
fallback's HTTP 429 maps to a 60s+ exponential backoff via --max-retries.
"""
import argparse
import csv
import hashlib
import io
import json
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional, Tuple

import chess
import zstandard as zstd

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from services.tablebase import (  # noqa: E402
    TablebaseResult,
    TablebaseUnavailableError,
    probe_tablebase,
)

DEFAULT_CSV = ROOT_DIR / "data" / "raw" / "lichess_db_puzzle.csv.zst"
DEFAULT_OUT_DIR = ROOT_DIR / "data" / "processed" / "endgame_candidates"

ENDGAME_CATEGORIES = (
    "rookEndgame",
    "bishopEndgame",
    "knightEndgame",
    "queenEndgame",
    "pawnEndgame",
    "queenRookEndgame",
)

# The Lichess tablebase service answers up to 7 men; anything larger cannot
# be verified by either source, so it is discarded without a wasted API call.
API_MEN_CEILING = 7
# With the full 3-4-5-man Syzygy set installed, every legal <=5-man ending
# is answered locally. 6-7-man endings need the API (or a local capture
# resolution); castling positions are not tablebase material at all.
LOCAL_MEN_CEILING = 5
# Deterministic hash-sampling seed for the WIN-track 6-7-man shortcut. The
# DRAW track is never sampled: it is small and its labels are the
# higher-stakes ground truth, so it stays exhaustively probed.
DEFAULT_SAMPLE_SEED = "endgame-candidates-2026"

VERIFICATION_TABLEBASE = "tablebase-verified"
VERIFICATION_TABLEBASE_SAMPLED = "tablebase-verified-sampled"
VERIFICATION_TERMINAL = "terminal-rule"
VERIFICATION_TAG_TRUSTED = "tag-trusted-sampled"
VERIFICATION_UNPROBEABLE = "unprobeable"
VERIFICATION_REPLAY_ERROR = "replay-error"

log = logging.getLogger("source_endgame_candidates")


# ---------------------------------------------------------------------------
# Step 1: cheap theme filtering
# ---------------------------------------------------------------------------

def iter_candidates(csv_path: Path) -> Iterator[Dict[str, Any]]:
    """Yield unique filtered puzzle rows in CSV order (first occurrence wins)."""
    seen = set()
    with open(csv_path, "rb") as fh:
        decompressed = zstd.ZstdDecompressor().stream_reader(fh)
        text = io.TextIOWrapper(decompressed, encoding="utf-8")
        for row in csv.DictReader(text):
            themes = row["Themes"].split() if row["Themes"] else []
            theme_set = set(themes)
            if "endgame" not in theme_set:
                continue
            categories = [c for c in ENDGAME_CATEGORIES if c in theme_set]
            if not categories:
                continue
            puzzle_id = row["PuzzleId"]
            if puzzle_id in seen:
                continue
            seen.add(puzzle_id)
            yield {
                "puzzle_id": puzzle_id,
                "fen": row["FEN"],
                "moves": row["Moves"].split(),
                "rating": int(row["Rating"]),
                "themes": themes,
                "categories": categories,
                "track": "draw" if "equality" in theme_set else "win",
                "game_url": row["GameUrl"] or None,
            }


def cheap_dataset_summary(csv_path: Path) -> Dict[str, Any]:
    """One full scan: dataset totals + raw per-category per-track counts."""
    summary = {
        "rows_total": 0,
        "endgame_rows": 0,
        "multi_category_rows": 0,
        "raw_counts": {
            c: {"win": 0, "draw": 0, "total": 0} for c in ENDGAME_CATEGORIES
        },
    }
    with open(csv_path, "rb") as fh:
        decompressed = zstd.ZstdDecompressor().stream_reader(fh)
        text = io.TextIOWrapper(decompressed, encoding="utf-8")
        for row in csv.DictReader(text):
            summary["rows_total"] += 1
            themes = row["Themes"].split() if row["Themes"] else []
            theme_set = set(themes)
            if "endgame" not in theme_set:
                continue
            summary["endgame_rows"] += 1
            categories = [c for c in ENDGAME_CATEGORIES if c in theme_set]
            if not categories:
                continue
            if len(categories) > 1:
                summary["multi_category_rows"] += 1
            track = "draw" if "equality" in theme_set else "win"
            for c in categories:
                summary["raw_counts"][c][track] += 1
                summary["raw_counts"][c]["total"] += 1
    return summary


# ---------------------------------------------------------------------------
# Step 2: replay the full move sequence
# ---------------------------------------------------------------------------

class ReplayError(Exception):
    pass


def replay_ending(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Play the full CSV move sequence from the raw FEN.

    Returns the drill-start position (after the setup move), the solver
    color, the final position and some terminal flags. Raises ReplayError
    on an illegal/unparseable line (counted as unprobeable, never silently
    dropped).
    """
    moves = candidate["moves"]
    if not moves:
        raise ReplayError("empty Moves field")
    try:
        board = chess.Board(candidate["fen"])
    except ValueError as exc:
        raise ReplayError(f"malformed FEN: {exc}") from exc

    solution_san = []
    pushed = 0
    for uci in moves:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError as exc:
            raise ReplayError(f"malformed move {uci!r}: {exc}") from exc
        if move not in board.legal_moves:
            raise ReplayError(
                f"illegal move {uci!r} at ply {pushed} from {board.fen()!r}"
            )
        if pushed > 0:
            solution_san.append(board.san(move))
        board.push(move)
        pushed += 1

    setup_move = moves[0]
    puzzle_board = chess.Board(candidate["fen"])
    puzzle_board.push(chess.Move.from_uci(setup_move))

    return {
        "puzzle_fen": puzzle_board.fen(),
        "puzzle_men": len(puzzle_board.piece_map()),
        "setup_move": setup_move,
        "solver_color": "white" if puzzle_board.turn else "black",
        "solution_moves": moves[1:],
        "solution_ply_count": len(moves) - 1,
        "solution_san": solution_san,
        "ending_fen": board.fen(),
        "ending_men": len(board.piece_map()),
        "ending_checkmate": board.is_checkmate(),
        "ending_stalemate": board.is_stalemate(),
        "ending_halfmove_clock": board.halfmove_clock,
        "ending_castling_rights": bool(board.castling_rights),
    }


# ---------------------------------------------------------------------------
# Step 3: tablebase probing with pacing, retries and a persistent cache
# ---------------------------------------------------------------------------

def normalize_fen(fen: str) -> str:
    return " ".join(fen.split()[:4])


class Unprobeable(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def classify_failure(message: str) -> str:
    if "has no data for this position" in message:
        return "api-no-data"
    if "rate limited" in message or "HTTP 429" in message:
        return "remote-rate-limited"
    if (
        "could not reach" in message
        or "malformed response" in message
        or "HTTP 4" in message
        or "HTTP 5" in message
    ):
        return "remote-unavailable"
    return "probe-error"


class Prober:
    """services.tablebase.probe_tablebase with batch-runner behavior:

      * persistent FEN cache (normalized to 4 fields) -- a repeated ending
        costs zero probes, including across resumed runs;
      * one remote request at a time with --api-interval spacing;
      * the module's deliberate no-retry policy is honored in-session, but
        this offline batch caller retries transient failures with backoff
        (429 -> 60s * 2^n, network -> 2s * 2^n) up to --max-retries, then
        reports the position as unprobeable instead of guessing.
    """

    def __init__(
        self,
        cache: Dict[str, Any],
        api_interval: float,
        max_retries: int,
        stats: Counter,
    ):
        self.cache = cache
        self.api_interval = api_interval
        self.max_retries = max_retries
        self.stats = stats
        self.last_remote_attempt: Optional[float] = None

    def _pace(self) -> None:
        if self.last_remote_attempt is None:
            return
        remaining = self.api_interval - (time.monotonic() - self.last_remote_attempt)
        if remaining > 0:
            time.sleep(remaining)

    def _mark_remote(self) -> None:
        self.last_remote_attempt = time.monotonic()

    def probe(self, fen: str, men: int) -> TablebaseResult:
        key = normalize_fen(fen)
        cached = self.cache.get(key)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return TablebaseResult(**cached)

        retry = 0
        while True:
            if men > LOCAL_MEN_CEILING:
                self._pace()
            try:
                result = probe_tablebase(fen)
            except TablebaseUnavailableError as exc:
                message = str(exc)
                self._mark_remote()
                self.stats["remote_attempts"] += 1
                reason = classify_failure(message)
                if reason == "probe-error" or reason == "api-no-data":
                    raise Unprobeable(reason, message) from exc
                if retry >= self.max_retries:
                    raise Unprobeable(reason + "-persistent", message) from exc
                if reason == "remote-rate-limited":
                    self.stats["rate_limit_hits"] += 1
                    wait = min(60.0 * (2 ** retry), 1800.0)
                else:
                    wait = min(2.0 * (2 ** retry), 120.0)
                self.stats["retries"] += 1
                log.warning(
                    "probe retry %d/%d in %.0fs (%s): %s",
                    retry + 1,
                    self.max_retries,
                    wait,
                    reason,
                    fen,
                )
                time.sleep(wait)
                retry += 1
                continue
            except ValueError as exc:
                raise Unprobeable("illegal-ending-position", str(exc)) from exc

            if result.source == "lichess":
                self._mark_remote()
                self.stats["remote_attempts"] += 1
                self.stats["lichess_results"] += 1
            else:
                self.stats["local_results"] += 1
            self.cache[key] = result.model_dump()
            return result


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

def evaluate(track: str, probe: TablebaseResult) -> Tuple[str, Optional[str]]:
    """Map a probe (from the final side to move = solver's opponent) to a
    verdict. Returns (status, discard_reason); status is "survived" or
    "mismatch"."""
    if track == "win":
        if probe.wdl == -2:
            return "survived", None
        if probe.wdl == 0:
            return "mismatch", "win-tagged-but-tablebase-draw"
        if probe.wdl == -1:
            return "mismatch", "win-tagged-but-cursed-win-only"
        return "mismatch", "win-tagged-but-tablebase-loss"
    if probe.wdl == 0:
        return "survived", None
    return "mismatch", "equality-tagged-but-tablebase-decisive"


def terminal_probe(outcome: str) -> Dict[str, Any]:
    """A probe-shaped record for a rule-decided terminal ending (checkmate or
    stalemate), from the final side-to-move's perspective. No tablebase
    lookup is needed: mate is the strongest possible loss, stalemate is a
    draw by rule. source="terminal-rule" keeps this distinguishable from a
    real local/lichess probe."""
    wdl = {"win": 2, "draw": 0, "loss": -2}[outcome]
    return {
        "outcome": outcome,
        "dtz": -1 if outcome == "loss" else 0,
        "wdl": wdl,
        "source": "terminal-rule",
    }


def win67_in_sample(puzzle_id: str, per_10000: int, seed: str) -> bool:
    """Deterministic per-row hash sampling (stable across resumes).

    per_10000=0 disables sampling (probe every WIN 6-7-man ending);
    per_10000=10000 samples all of them."""
    if per_10000 <= 0:
        return False
    if per_10000 >= 10000:
        return True
    digest = hashlib.sha256(f"{seed}:{puzzle_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:2], "big") % 10000 < per_10000


# ---------------------------------------------------------------------------
# Checkpointing / resume
# ---------------------------------------------------------------------------

def atomic_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def load_results(path: Path, on_record) -> Tuple[set, int]:
    """Read the append-only results file, repairing a torn trailing line.

    Records are streamed to on_record (for counter rebuild / survivor
    collection) instead of all being kept in memory: a full run has ~150k
    records and only the survivors need to stay around.
    """
    processed = set()
    if not path.exists():
        return processed, 0
    good_bytes = 0
    with open(path, "rb") as fh:
        for raw in fh:
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                break
            if not isinstance(record, dict) or "puzzle_id" not in record:
                break
            processed.add(record["puzzle_id"])
            on_record(record)
            good_bytes += len(raw)
    size = path.stat().st_size
    if good_bytes != size:
        log.warning(
            "results file has a torn tail (%d bytes); truncating to last good record",
            size - good_bytes,
        )
        with open(path, "r+b") as fh:
            fh.truncate(good_bytes)
    return processed, good_bytes


class RunStats:
    def __init__(self) -> None:
        self.status_by_track = {"win": Counter(), "draw": Counter()}
        self.reasons = Counter()
        self.per_category: Dict[str, Dict[str, Counter]] = {
            c: {"win": Counter(), "draw": Counter()} for c in ENDGAME_CATEGORIES
        }
        self.men_hist = {"win": Counter(), "draw": Counter()}
        self.mate_by_status = Counter()
        self.probe = Counter()
        self.survivor_puzzle_men = Counter()
        self.verification = Counter()
        self.status_verification = Counter()
        self.track_status_verification = Counter()
        # (track, size-bucket, status) -> count; bucket is le5 / 67 / gt7.
        self.bucket_status = Counter()
        # (track, bucket, verification, status) -> count.
        self.bucket_verification = Counter()
        self.sampled = Counter()

    def add(self, record: Dict[str, Any]) -> None:
        track = record["track"]
        status = record["status"]
        verification = record.get("verification", "unknown")
        self.status_by_track[track][status] += 1
        self.verification[verification] += 1
        self.status_verification[(status, verification)] += 1
        self.track_status_verification[(track, status, verification)] += 1
        if record.get("discard_reason"):
            self.reasons[record["discard_reason"]] += 1
        for category in record["categories"]:
            self.per_category[category][track][status] += 1
        men = record.get("ending_men")
        if men is not None:
            self.men_hist[track][men] += 1
            bucket = "le5" if men <= LOCAL_MEN_CEILING else (
                "67" if men <= API_MEN_CEILING else "gt7"
            )
            self.bucket_status[(track, bucket, status)] += 1
            self.bucket_verification[(track, bucket, verification, status)] += 1
        if record.get("ending_checkmate"):
            self.mate_by_status[status] += 1
        if status == "survived" and record.get("puzzle_men") is not None:
            self.survivor_puzzle_men[record["puzzle_men"]] += 1
        if verification == VERIFICATION_TABLEBASE_SAMPLED:
            self.sampled["probed"] += 1
            if status == "mismatch":
                self.sampled["mismatch"] += 1

    def merge_prober_stats(self, prober_stats: Counter) -> None:
        self.probe.update(prober_stats)


def make_record(
    candidate: Dict[str, Any],
    replay: Optional[Dict[str, Any]],
    status: str,
    reason: Optional[str],
    probe: Optional[Dict[str, Any]],
    solver_wdl: Optional[int],
    verification: str,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "puzzle_id": candidate["puzzle_id"],
        "status": status,
        "verification": verification,
        "discard_reason": reason,
        "track": candidate["track"],
        "categories": candidate["categories"],
        "rating": candidate["rating"],
        "themes": candidate["themes"],
        "game_url": candidate["game_url"],
        "start_fen": candidate["fen"],
        "moves": candidate["moves"],
    }
    if replay is not None:
        record.update(
            {
                "setup_move": replay["setup_move"],
                "puzzle_fen": replay["puzzle_fen"],
                "puzzle_men": replay["puzzle_men"],
                "solver_color": replay["solver_color"],
                "solution_moves": replay["solution_moves"],
                "solution_ply_count": replay["solution_ply_count"],
                "solution_san": replay["solution_san"],
                "ending_fen": replay["ending_fen"],
                "ending_normalized_fen": normalize_fen(replay["ending_fen"]),
                "ending_men": replay["ending_men"],
                "ending_checkmate": replay["ending_checkmate"],
                "ending_stalemate": replay["ending_stalemate"],
                "ending_halfmove_clock": replay["ending_halfmove_clock"],
                "ending_castling_rights": replay["ending_castling_rights"],
            }
        )
    record["probe"] = probe
    record["solver_wdl"] = solver_wdl
    return record


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def build_report(
    csv_path: Path,
    summary: Dict[str, Any],
    stats: RunStats,
    unique_candidates: int,
    elapsed: float,
    stopped_early: bool,
    win67_sample_per_10000: int,
    sample_seed: str,
) -> Dict[str, Any]:
    per_category = {}
    for category in ENDGAME_CATEGORIES:
        per_category[category] = {}
        for track in ("win", "draw"):
            counters = stats.per_category[category][track]
            per_category[category][track] = {
                "candidates_raw": summary["raw_counts"][category][track],
                "survived": counters["survived"],
                "discarded_mismatch": counters["mismatch"],
                "discarded_unprobeable": counters["unprobeable"],
            }
    funnel = {}
    for track in ("win", "draw"):
        counters = stats.status_by_track[track]
        tsv = stats.track_status_verification
        funnel[track] = {
            "candidates": counters["survived"] + counters["mismatch"] + counters["unprobeable"],
            "survived": counters["survived"],
            "survived_tablebase_verified": (
                tsv[(track, "survived", VERIFICATION_TABLEBASE)]
                + tsv[(track, "survived", VERIFICATION_TABLEBASE_SAMPLED)]
            ),
            "survived_terminal_rule": tsv[(track, "survived", VERIFICATION_TERMINAL)],
            "survived_tag_trusted": tsv[(track, "survived", VERIFICATION_TAG_TRUSTED)],
            "discarded_mismatch": counters["mismatch"],
            "discarded_unprobeable": counters["unprobeable"],
        }
    total = {
        "candidates": funnel["win"]["candidates"] + funnel["draw"]["candidates"],
        "survived": funnel["win"]["survived"] + funnel["draw"]["survived"],
        "survived_tablebase_verified": (
            funnel["win"]["survived_tablebase_verified"]
            + funnel["draw"]["survived_tablebase_verified"]
        ),
        "survived_terminal_rule": (
            funnel["win"]["survived_terminal_rule"]
            + funnel["draw"]["survived_terminal_rule"]
        ),
        "survived_tag_trusted": (
            funnel["win"]["survived_tag_trusted"]
            + funnel["draw"]["survived_tag_trusted"]
        ),
        "discarded_mismatch": (
            funnel["win"]["discarded_mismatch"] + funnel["draw"]["discarded_mismatch"]
        ),
        "discarded_unprobeable": (
            funnel["win"]["discarded_unprobeable"] + funnel["draw"]["discarded_unprobeable"]
        ),
    }

    def exhaustive(bucket_track: str, bucket: str) -> Tuple[int, int]:
        """(probed, mismatches) for genuinely probed rows in a bucket
        (excludes sampled / tag-trusted / terminal-rule rows)."""
        bv = stats.bucket_verification
        probed = (
            bv[(bucket_track, bucket, VERIFICATION_TABLEBASE, "survived")]
            + bv[(bucket_track, bucket, VERIFICATION_TABLEBASE, "mismatch")]
        )
        mismatches = bv[(bucket_track, bucket, VERIFICATION_TABLEBASE, "mismatch")]
        return probed, mismatches

    win_le5_probed, win_le5_mismatch = exhaustive("win", "le5")
    win67_pre_probed, win67_pre_mismatch = exhaustive("win", "67")
    draw67_probed, draw67_mismatch = exhaustive("draw", "67")
    draw_le5_probed, draw_le5_mismatch = exhaustive("draw", "le5")
    sampling = {
        "win67_sample_per_10000": win67_sample_per_10000,
        "sample_seed": sample_seed,
        "win67_sampled_probed": stats.sampled["probed"],
        "win67_sampled_mismatches": stats.sampled["mismatch"],
        "win67_sampled_error_rate": _rate(stats.sampled["mismatch"], stats.sampled["probed"]),
        "win67_tag_trusted": stats.verification[VERIFICATION_TAG_TRUSTED],
        "win67_terminal_rule": stats.verification[VERIFICATION_TERMINAL],
        "win_le5_exhaustive_probed": win_le5_probed,
        "win_le5_exhaustive_mismatches": win_le5_mismatch,
        "win_le5_exhaustive_error_rate": _rate(win_le5_mismatch, win_le5_probed),
        "win67_pre_switch_exhaustive_probed": win67_pre_probed,
        "win67_pre_switch_exhaustive_mismatches": win67_pre_mismatch,
        "win67_pre_switch_exhaustive_error_rate": _rate(win67_pre_mismatch, win67_pre_probed),
        "draw67_exhaustive_probed": draw67_probed,
        "draw67_exhaustive_mismatches": draw67_mismatch,
        "draw67_exhaustive_error_rate": _rate(draw67_mismatch, draw67_probed),
        "draw_le5_exhaustive_probed": draw_le5_probed,
        "draw_le5_exhaustive_mismatches": draw_le5_mismatch,
        "draw_le5_exhaustive_error_rate": _rate(draw_le5_mismatch, draw_le5_probed),
    }
    return {
        "csv_path": str(csv_path),
        "dataset": summary,
        "unique_candidates": unique_candidates,
        "funnel": {"win": funnel["win"], "draw": funnel["draw"], "total": total},
        "per_category": per_category,
        "discard_reasons": dict(stats.reasons.most_common()),
        "ending_men_histogram": {
            track: dict(sorted(stats.men_hist[track].items()))
            for track in ("win", "draw")
        },
        "survivor_puzzle_men_histogram": dict(sorted(stats.survivor_puzzle_men.items())),
        "survivors_with_drill_position_within_api_ceiling": sum(
            count for men, count in stats.survivor_puzzle_men.items()
            if men <= API_MEN_CEILING
        ),
        "final_checkmate_by_status": dict(stats.mate_by_status),
        "verification_counts": dict(stats.verification.most_common()),
        "sampling": sampling,
        "probe_stats": dict(stats.probe),
        "elapsed_seconds": round(elapsed, 1),
        "stopped_early_with_limit": stopped_early,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Endgame candidate sourcing report")
    lines.append("")
    lines.append(f"- CSV: `{report['csv_path']}`")
    dataset = report["dataset"]
    lines.append(
        f"- Dataset rows: {dataset['rows_total']:,} "
        f"(endgame-tagged: {dataset['endgame_rows']:,})"
    )
    lines.append(f"- Unique filtered candidates: {report['unique_candidates']:,}")
    lines.append(
        f"- Elapsed: {report['elapsed_seconds']:.0f}s"
        + (" (limited run -- NOT the full dataset)" if report["stopped_early_with_limit"] else "")
    )
    lines.append("")
    lines.append("## Raw counts per material category per track (step 1, before probing)")
    lines.append("")
    lines.append("| category | win candidates | draw candidates | total |")
    lines.append("|---|---:|---:|---:|")
    for category, raw in dataset["raw_counts"].items():
        lines.append(
            f"| {category} | {raw['win']:,} | {raw['draw']:,} | {raw['total']:,} |"
        )
    lines.append("")
    lines.append("## Funnel (unique candidates)")
    lines.append("")
    lines.append(
        "| track | candidates | survived | verified | terminal-rule | tag-trusted | "
        "discarded mismatch | discarded unprobeable |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for track in ("win", "draw", "total"):
        f = report["funnel"][track]
        lines.append(
            f"| {track} | {f['candidates']:,} | {f['survived']:,} | "
            f"{f['survived_tablebase_verified']:,} | {f['survived_terminal_rule']:,} | "
            f"{f['survived_tag_trusted']:,} | "
            f"{f['discarded_mismatch']:,} | {f['discarded_unprobeable']:,} |"
        )
    lines.append("")
    lines.append("## Provenance taxonomy")
    lines.append("")
    lines.append(
        "- `tablebase-verified`: ending probed through services/tablebase.py "
        "(local Syzygy or Lichess API) - full verification."
    )
    lines.append(
        "- `tablebase-verified-sampled`: WIN-track 6-7-man row drawn into the "
        "random sample and probed - full verification; sample denominator."
    )
    lines.append(
        "- `terminal-rule`: ending is checkmate or stalemate; decided by the "
        "rules, no tablebase lookup needed."
    )
    lines.append(
        "- `tag-trusted-sampled`: WIN-track 6-7-man row NOT probed; status is "
        "the Lichess tag, quality inferred from the sample. NOT independently "
        "verified - re-probe before seeding if that matters."
    )
    lines.append("- `unprobeable`: >7 men, castling rights, or probe failure.")
    lines.append("- `replay-error`: the CSV line could not be replayed.")
    lines.append("")
    for verification, count in report["verification_counts"].items():
        lines.append(f"- count `{verification}`: {count:,}")
    lines.append("")
    lines.append("## Sampling and measured error rates")
    lines.append("")
    sampling = report["sampling"]
    lines.append(
        f"- WIN 6-7-man sampling rate: {sampling['win67_sample_per_10000']} per 10,000 "
        f"(seed `{sampling['sample_seed']}`)"
    )
    lines.append(
        f"- WIN 6-7-man sample probed: {sampling['win67_sampled_probed']:,}, "
        f"mismatches: {sampling['win67_sampled_mismatches']:,}, "
        f"error rate: {sampling['win67_sampled_error_rate']}"
    )
    lines.append(
        f"- WIN 6-7-man tag-trusted (not probed): {sampling['win67_tag_trusted']:,}"
    )
    lines.append(
        f"- WIN 6-7-man decided by terminal rule: {sampling['win67_terminal_rule']:,}"
    )
    lines.append(
        f"- WIN <=5-man exhaustive: {sampling['win_le5_exhaustive_probed']:,} probed, "
        f"{sampling['win_le5_exhaustive_mismatches']:,} mismatches, "
        f"error rate: {sampling['win_le5_exhaustive_error_rate']}"
    )
    lines.append(
        f"- WIN 6-7-man exhaustive pre-switch (from the first run phase): "
        f"{sampling['win67_pre_switch_exhaustive_probed']:,} probed, "
        f"{sampling['win67_pre_switch_exhaustive_mismatches']:,} mismatches, "
        f"error rate: {sampling['win67_pre_switch_exhaustive_error_rate']}"
    )
    lines.append(
        f"- DRAW 6-7-man exhaustive: {sampling['draw67_exhaustive_probed']:,} probed, "
        f"{sampling['draw67_exhaustive_mismatches']:,} mismatches, "
        f"error rate: {sampling['draw67_exhaustive_error_rate']}"
    )
    lines.append(
        f"- DRAW <=5-man exhaustive: {sampling['draw_le5_exhaustive_probed']:,} probed, "
        f"{sampling['draw_le5_exhaustive_mismatches']:,} mismatches, "
        f"error rate: {sampling['draw_le5_exhaustive_error_rate']}"
    )
    lines.append("")
    lines.append("## Per category per track")
    lines.append("")
    lines.append("| category | track | candidates (raw) | survived | mismatch | unprobeable |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for category, tracks in report["per_category"].items():
        for track in ("win", "draw"):
            row = tracks[track]
            lines.append(
                f"| {category} | {track} | {row['candidates_raw']:,} | "
                f"{row['survived']:,} | {row['discarded_mismatch']:,} | "
                f"{row['discarded_unprobeable']:,} |"
            )
    lines.append("")
    lines.append("## Discard reasons")
    lines.append("")
    for reason, count in report["discard_reasons"].items():
        lines.append(f"- {reason}: {count:,}")
    lines.append("")
    lines.append("## Ending men histogram (unique candidates)")
    lines.append("")
    for track in ("win", "draw"):
        hist = report["ending_men_histogram"][track]
        rendered = ", ".join(f"{men}m:{count:,}" for men, count in hist.items())
        lines.append(f"- {track}: {rendered or 'none'}")
    lines.append("")
    lines.append("## Final positions that are checkmate (by status)")
    lines.append("")
    for status, count in report["final_checkmate_by_status"].items():
        lines.append(f"- {status}: {count:,}")
    lines.append("")
    lines.append("## Survivors: drill-position (after setup move) size")
    lines.append("")
    rendered = ", ".join(
        f"{men}m:{count:,}"
        for men, count in report["survivor_puzzle_men_histogram"].items()
    )
    lines.append(f"- puzzle_men: {rendered or 'none'}")
    lines.append(
        "- survivors whose drill position is <= 7 men "
        f"(directly tablebase-verifiable): "
        f"{report['survivors_with_drill_position_within_api_ceiling']:,}"
    )
    lines.append("")
    lines.append("## Probe stats")
    lines.append("")
    for key, value in report["probe_stats"].items():
        lines.append(f"- {key}: {value:,}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--limit", type=int, default=None, help="process at most N candidates")
    parser.add_argument("--api-interval", type=float, default=1.0,
                        help="min seconds between remote probes (Lichess asks ~1s)")
    parser.add_argument("--max-retries", type=int, default=6)
    parser.add_argument("--checkpoint-every", type=int, default=100)
    parser.add_argument("--log-every", type=int, default=250)
    parser.add_argument("--fresh", action="store_true", help="discard previous progress")
    parser.add_argument("--report-only", action="store_true",
                        help="regenerate survivors/report from results.jsonl; no probing")
    parser.add_argument(
        "--win67-sample-per-10000",
        type=int,
        default=146,
        help=(
            "hash-sampling rate for WIN-track 6-7-man endings (expected "
            "sample ~400 of ~27k). 0 disables the shortcut (probe all); "
            "10000 samples all. DRAW-track 6-7-man is always probed."
        ),
    )
    parser.add_argument("--sample-seed", default=DEFAULT_SAMPLE_SEED)
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    survivors_path = out_dir / "survivors.jsonl"
    cache_path = out_dir / "probe_cache.json"
    progress_path = out_dir / "progress.json"
    summary_path = out_dir / "candidates_summary.json"

    if args.fresh:
        for path in (results_path, survivors_path, cache_path, progress_path):
            path.unlink(missing_ok=True)
        log.info("--fresh: previous progress discarded")

    t0 = time.time()
    log.info("Scanning %s for candidates (cheap filter, full dataset)...", args.csv)
    summary = cheap_dataset_summary(args.csv)
    atomic_write_json(summary_path, summary)
    for category in ENDGAME_CATEGORIES:
        raw = summary["raw_counts"][category]
        log.info(
            "  %s: %d win-candidates, %d draw-candidates",
            category, raw["win"], raw["draw"],
        )
    log.info(
        "Dataset: %d rows, %d endgame-tagged, %d multi-category endgame rows",
        summary["rows_total"], summary["endgame_rows"],
        summary["multi_category_rows"],
    )

    stats = RunStats()
    survivor_records: Dict[str, Dict[str, Any]] = {}

    def _consume_existing(record: Dict[str, Any]) -> None:
        # Records written by earlier phases may predate the provenance field.
        if "verification" not in record:
            if record.get("probe") is not None:
                record["verification"] = VERIFICATION_TABLEBASE
            elif record.get("discard_reason") == "replay-error":
                record["verification"] = VERIFICATION_REPLAY_ERROR
            else:
                record["verification"] = VERIFICATION_UNPROBEABLE
        stats.add(record)
        if record["status"] == "survived":
            survivor_records[record["puzzle_id"]] = record

    if args.fresh:
        processed_ids: set = set()
    else:
        processed_ids, _ = load_results(results_path, _consume_existing)
    if processed_ids:
        log.info("Resuming: %d candidates already processed", len(processed_ids))

    # Probe-side counters are cumulative: pick them up from the last
    # checkpoint so resumed/report-only runs report the full picture.
    prober_stats: Counter = Counter()
    if progress_path.exists():
        try:
            saved = json.loads(progress_path.read_text(encoding="utf-8"))
            prober_stats.update(saved.get("probe_stats", {}))
        except (json.JSONDecodeError, OSError):
            pass

    if args.report_only:
        stats.merge_prober_stats(prober_stats)
        return finish(
            args, out_dir, results_path, survivors_path, summary,
            stats, len(processed_ids), t0, records=survivor_records,
        )

    cache: Dict[str, Any] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            log.info("Loaded %d cached probe results", len(cache))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("ignoring unreadable probe cache: %s", exc)
            cache = {}

    prober = Prober(cache, args.api_interval, args.max_retries, prober_stats)

    results_fh = open(results_path, "a", encoding="utf-8")
    if survivor_records:
        # Rewrite survivors from the authoritative results file so a resume
        # never duplicates or drops survivor lines.
        with open(survivors_path, "w", encoding="utf-8") as fh:
            for record in survivor_records.values():
                fh.write(json.dumps(record, sort_keys=True) + "\n")
    survivors_fh = open(survivors_path, "a", encoding="utf-8")

    processed = len(processed_ids)
    total = 0
    batch_count = 0
    stopped_early = False

    try:
        for candidate in iter_candidates(args.csv):
            total += 1
            if args.limit is not None and processed >= args.limit:
                stopped_early = True
                break
            if candidate["puzzle_id"] in processed_ids:
                continue

            replay = None
            probe = None
            solver_wdl = None
            try:
                replay = replay_ending(candidate)
            except ReplayError as exc:
                status, reason = "unprobeable", "replay-error"
                verification = VERIFICATION_REPLAY_ERROR
                log.warning("replay failed for %s: %s", candidate["puzzle_id"], exc)
            else:
                men = replay["ending_men"]
                track = candidate["track"]
                six_or_seven = men > LOCAL_MEN_CEILING
                if men > API_MEN_CEILING:
                    status, reason = "unprobeable", "beyond-7-man-api-ceiling"
                    verification = VERIFICATION_UNPROBEABLE
                elif replay["ending_castling_rights"]:
                    status, reason = "unprobeable", "castling-rights-unsupported"
                    verification = VERIFICATION_UNPROBEABLE
                elif track == "win" and six_or_seven and replay["ending_checkmate"]:
                    # Rule-decided terminal: no API call needed, and mate is
                    # exactly the clean win the WIN track demands.
                    probe = terminal_probe("loss")
                    solver_wdl = 2
                    status, reason = "survived", None
                    verification = VERIFICATION_TERMINAL
                elif track == "win" and six_or_seven and replay["ending_stalemate"]:
                    probe = terminal_probe("draw")
                    solver_wdl = 0
                    status, reason = "mismatch", "win-tagged-but-stalemate"
                    verification = VERIFICATION_TERMINAL
                elif (
                    track == "win"
                    and six_or_seven
                    and not win67_in_sample(
                        candidate["puzzle_id"],
                        args.win67_sample_per_10000,
                        args.sample_seed,
                    )
                ):
                    # WIN-track 6-7-man shortcut: tag-trusted, clearly marked.
                    # The DRAW track is never short-circuited.
                    status, reason = "survived", None
                    verification = VERIFICATION_TAG_TRUSTED
                else:
                    sampled = (
                        args.win67_sample_per_10000 > 0
                        and track == "win"
                        and six_or_seven
                    )
                    try:
                        result = prober.probe(replay["ending_fen"], men)
                    except Unprobeable as exc:
                        status, reason = "unprobeable", exc.reason
                        verification = VERIFICATION_UNPROBEABLE
                    else:
                        status, reason = evaluate(track, result)
                        probe = result.model_dump()
                        solver_wdl = -result.wdl
                        verification = (
                            VERIFICATION_TABLEBASE_SAMPLED
                            if sampled
                            else VERIFICATION_TABLEBASE
                        )

            record = make_record(
                candidate, replay, status, reason, probe, solver_wdl, verification
            )
            results_fh.write(json.dumps(record, sort_keys=True) + "\n")
            processed_ids.add(candidate["puzzle_id"])
            stats.add(record)
            if status == "survived":
                survivor_records[candidate["puzzle_id"]] = record
                survivors_fh.write(json.dumps(record, sort_keys=True) + "\n")
            processed += 1
            batch_count += 1

            if batch_count >= args.checkpoint_every:
                batch_count = 0
                checkpoint(
                    results_fh, survivors_fh, cache, cache_path,
                    progress_path, stats, prober_stats, processed, total,
                    args, t0,
                )

            if processed % args.log_every == 0:
                log_progress(stats, prober_stats, processed, t0, args)
    except KeyboardInterrupt:
        log.warning("Interrupted; checkpointing partial progress")
    finally:
        checkpoint(
            results_fh, survivors_fh, cache, cache_path, progress_path,
            stats, prober_stats, processed, total, args, t0,
        )
        results_fh.close()
        survivors_fh.close()

    stats.merge_prober_stats(prober_stats)
    return finish(
        args, out_dir, results_path, survivors_path, summary,
        stats, processed, t0, records=survivor_records,
    )


def checkpoint(
    results_fh, survivors_fh, cache, cache_path, progress_path,
    stats: RunStats, prober_stats: Counter, processed: int, total: int,
    args, t0: float,
) -> None:
    results_fh.flush()
    os.fsync(results_fh.fileno())
    survivors_fh.flush()
    os.fsync(survivors_fh.fileno())
    atomic_write_json(cache_path, cache)
    statuses = stats.status_by_track
    atomic_write_json(
        progress_path,
        {
            "processed": processed,
            "dataset_candidates_scanned": total,
            "survived": statuses["win"]["survived"] + statuses["draw"]["survived"],
            "mismatch": statuses["win"]["mismatch"] + statuses["draw"]["mismatch"],
            "unprobeable": statuses["win"]["unprobeable"] + statuses["draw"]["unprobeable"],
            "probe_stats": dict(prober_stats),
            "elapsed_seconds": round(time.time() - t0, 1),
        },
    )


def log_progress(stats: RunStats, prober_stats: Counter, processed: int,
                 t0: float, args) -> None:
    statuses = stats.status_by_track
    survived = statuses["win"]["survived"] + statuses["draw"]["survived"]
    mismatch = statuses["win"]["mismatch"] + statuses["draw"]["mismatch"]
    unprobeable = statuses["win"]["unprobeable"] + statuses["draw"]["unprobeable"]
    rate = processed / max(time.time() - t0, 0.001)
    log.info(
        "progress processed=%d survived=%d mismatch=%d unprobeable=%d "
        "local=%d lichess=%d cache_hits=%d retries=%d rate=%.1f/s",
        processed, survived, mismatch, unprobeable,
        prober_stats["local_results"], prober_stats["lichess_results"],
        prober_stats["cache_hits"], prober_stats["retries"], rate,
    )


def finish(
    args, out_dir: Path, results_path: Path, survivors_path: Path,
    summary: Dict[str, Any], stats: RunStats, processed: int, t0: float,
    records: Dict[str, Dict[str, Any]],
) -> int:
    elapsed = time.time() - t0
    report = build_report(
        args.csv, summary, stats, processed, elapsed,
        stopped_early=args.limit is not None and processed >= args.limit,
        win67_sample_per_10000=args.win67_sample_per_10000,
        sample_seed=args.sample_seed,
    )
    atomic_write_json(out_dir / "report.json", report)
    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")

    # Regenerate the survivors file from the authoritative record set.
    with open(survivors_path, "w", encoding="utf-8") as fh:
        for record in records.values():
            if record["status"] == "survived":
                fh.write(json.dumps(record, sort_keys=True) + "\n")

    funnel = report["funnel"]
    log.info(
        "DONE candidates=%d survived=%d mismatch=%d unprobeable=%d in %.0fs",
        funnel["total"]["candidates"], funnel["total"]["survived"],
        funnel["total"]["discarded_mismatch"],
        funnel["total"]["discarded_unprobeable"], elapsed,
    )
    log.info("Results:   %s", results_path)
    log.info("Survivors: %s", survivors_path)
    log.info("Report:    %s", out_dir / "report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
