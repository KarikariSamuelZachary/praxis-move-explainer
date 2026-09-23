"""
Syzygy tablebase probing for the Endgame Trainer.

probe_tablebase(fen) answers "what is the game-theoretic outcome of this
position, and how far is it from the next zeroing move?" for endgame trainer
positions. The session state machine (services/endgame_session.py) calls it
for every graded user move; this is the trainer's ground-truth oracle.

PROBING PATHS
=============
  1. LOCAL Syzygy files via python-chess's chess.syzygy -- PRIMARY for
     everything they cover. The trainer's positions are small (3-5 men;
     the deployed local set is the FULL 3-, 4- and 5-man Syzygy collection,
     ~940 MB), and probing is a local memory-mapped read: no network, no
     latency, no external failure mode during a training session. This
     matches how the codebase already treats self-hosted analysis assets
     (Stockfish ships via apt; the Maia-3 checkpoint is pre-warmed at
     build time instead of downloaded on first request).
  2. Lichess tablebase API fallback (tablebase.lichess.ovh -- the same
     service that verified the seed data) -- activates ONLY when local
     probing raises TablebaseUnavailableError, i.e. the position is legal
     but the installed files do not cover its material (6-7 men: the
     sourced content includes many 6-7-man drill starts, and 6-man local
     files would cost ~149 GB while 7-man is ~16 TB, so they are never
     installed). The fallback returns the SAME TablebaseResult shape, so
     callers (including the state machine) never need to know which source
     answered.

WHY THE FALLBACK IS NEEDED (and what it is NOT for)
===================================================
With the full 3-4-5-man set installed, every legal <=5-man position is
answered locally. 6-7-man positions (e.g. a rook endgame that captures
into KRPvKRP, 6 men, or the sourced 6-7-man drill starts) cannot be stored
locally at any sane deployment size (6-man ~149 GB, 7-man ~16 TB). The
fallback keeps those drillable and gradeable to actual checkmate. It is
NOT a general-purpose second opinion: whenever local files cover the
position, they answer, full stop. The step-2 seed-verification remains a
one-off offline use of the same service.

FAILURE CONTRACT (nothing fails silently)
=========================================
  * Malformed FEN (unparseable): ValueError, message includes the FEN.
  * Parseable but illegal position (e.g. adjacent kings, zero kings):
    ValueError -- a caller bug, not a tablebase gap. Never reaches either
    probing source.
  * LOCAL covers it -> TablebaseResult from local files (never touches the
    network).
  * Local cannot (material gap) -> Lichess API attempted:
      * API answers win/draw/loss (incl. cursed-win/blessed-loss) ->
        TablebaseResult, identical shape. outcome is the verdict for the
        side to move; wdl carries the raw tier so cursed wins (|wdl| == 1)
        stay detectable. dtz from the API's dtz field (None if not stored).
      * HTTP 429 (rate limit)            -> TablebaseUnavailableError
      * network timeout / refused / 5xx  -> TablebaseUnavailableError
      * category "unknown" (API ceiling is 7 men; also some exotic
        positions) -> TablebaseUnavailableError
      * unrecognized category / malformed body -> TablebaseUnavailableError
    A failed fallback RE-RAISES TablebaseUnavailableError whose message
    includes BOTH the local reason and the remote reason -- callers keep
    the exact same handling contract as the local-only module.
    NO retry loop inside this module: mid-session a single ~2s attempt is
    all a waiting user should pay; an admin/seed check can afford an
    explicit retry at the call site. The degraded mid-session behavior
    (route policy, a later step): surface a transient "could not verify"
    state and let the user retry the move -- do NOT silently downgrade a
    grade to a guess and do NOT mark the session failed on a network
    blip. Batch/admin callers just let the error surface.

  * A legal, covered position always returns a TablebaseResult; DTZ is None
    only when the answering source does not store DTZ for that exact
    position -- an answered probe, not an error.

DEGRADED-MODE DESIGN NOTE (mid-session vs one-off)
==================================================
When the fallback is also unavailable (offline deployment, API down,
rate-limited), the probe raises -- it never guesses. For a DRILL that means
the caller cannot grade the move; the sane route behavior is a retryable
"position temporarily ungradeable" response (the user waits a second, not
a forfeit). For ADMIN/seed work the same exception is simply fatal to that
check. Both behaviors live ABOVE this module; it only supplies the explicit
exception.

CACHING
=======
Two layers, both only on the fallback path (local probes are memory-speed
and never consult either):

  1. Short-lived in-memory cache keyed on the first 4 FEN fields (the
     repertoire_positions normalization -- counters don't change a verdict),
     TTL 300s, capped at 4096 entries (FIFO eviction), per process.
  2. OPTIONAL persistent cache (a PostgresProbeCache registered by the app
     via set_persistent_cache). Each distinct fallback position is paid to
     the Lichess API once per deployment; repeated positions -- across
     restarts, workers, and users hours apart -- are primary-key lookups.
     The cache is best-effort: a failure logs (throttled) and degrades to
     the old per-probe HTTP behavior, never to a guess, and never raises.
     With no cache registered (seed scripts, tests) behavior is unchanged.
     Tablebase verdicts are facts, so cached rows never go stale.

  Local probes never consult a cache (they are memory-speed already).

PER-MOVE PAYLOADS (probe_tablebase_moves)
=========================================
The opponent-reply generator needs one verdict PER LEGAL MOVE, not just the
root position's. Deriving that from probe_tablebase() means enumerating the
children and probing each one -- memory-speed locally, but for 6-7-man
positions that is one Lichess request per legal move (measured 2.6-6.7s
for 3-11 replies). The Lichess /standard response already carries the whole
`moves` array in that single request, so probe_tablebase_moves() exposes it:

  * local files answer -> `moves` is None and the caller enumerates
    children itself (fast, unchanged);
  * local cannot -> ONE Lichess request returns the root verdict AND every
    legal move's verdict, parsed into TablebaseMove entries.

The per-move payload gets its own short-lived in-memory cache (the
position-level caches above store only the root verdict). Callers still get
the plain position-level behavior from probe_tablebase(); its parser
deliberately keeps discarding the per-move array.

LOGGING
=======
Every fallback activation logs a WARNING with the FEN and the local reason;
every fallback answer logs INFO with the outcome; every fallback failure
logs ERROR. Grep for "tablebase fallback" to see how often real sessions
leave local coverage -- the input for deciding whether more local file
coverage is worth the disk.

DTZ SEMANTICS
=============
dtz is the distance-to-zeroing in plies as reported by the source, from the
point of view of the side to move (positive = side to move winning, negative
= losing), NOT adjusted for the current halfmove clock. Callers that care
about the 50-move rule must reconcile the clock themselves; the trainer's
seeded positions all have clock 0. The raw syzygy WDL value is exposed:
|wdl| == 1 marks a cursed win / blessed loss. Seeded trainer content
contains only clean outcomes (wdl +/-2 / 0), and the seed-verification
test asserts that.
"""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Literal, Optional

import chess
import chess.syzygy
from pydantic import BaseModel

log = logging.getLogger(__name__)

DEFAULT_TABLEBASE_DIR = (
    Path(__file__).resolve().parents[2] / "data" / "syzygy" / "regular"
)

# Raw syzygy WDL -> outcome for the side to move. |wdl| == 1 is the cursed
# win / blessed loss tier (outcome only holds when the 50-move counter is
# taken as 0); it still maps to win/loss because that is what the tablebase
# asserts, with `wdl` exposed so callers can single those positions out.
_WDL_OUTCOME = {2: "win", 1: "win", 0: "draw", -1: "loss", -2: "loss"}

# Lichess API categories are a superset of syzygy WDL tiers.
_LICHESS_CATEGORY_WDL = {
    "win": 2,
    "cursed-win": 1,
    "draw": 0,
    "blessed-loss": -1,
    "loss": -2,
}


class TablebaseUnavailableError(RuntimeError):
    """Raised when the probe cannot be answered by either source: no/empty
    local tablebase, a legal position whose material is beyond installed
    coverage, a fallback failure, or a position beyond the API's own
    ceiling. Callers must handle this explicitly; it is never a
    win/draw/loss verdict."""


class TablebaseResult(BaseModel):
    """Outcome of one tablebase probe, from the side to move's perspective."""

    outcome: Literal["win", "draw", "loss"]
    # Plies to the next zeroing move (pawn move or capture), as stored by the
    # DTZ source; None when the source does not store DTZ for this position.
    dtz: Optional[int]
    # Raw syzygy WDL value (-2..2) for callers that need to detect cursed
    # wins / blessed losses (|wdl| == 1). Identical semantics on both paths.
    wdl: int
    # Informational ONLY (grading callers ignore it): which source answered.
    # Kept so test harnesses and ops tooling can pace around external calls.
    source: Literal["local", "lichess"]


class TablebaseMove(BaseModel):
    """One legal move's tablebase verdict, from the CHILD side to move's
    perspective -- the same side the child's own probe would report (the
    mover's opponent), so callers invert exactly as they do for a child
    probe. Only produced by the remote path; the local path enumerates
    children itself."""

    uci: str
    outcome: Literal["win", "draw", "loss"]
    dtz: Optional[int]
    wdl: int


class TablebaseMovesProbe(BaseModel):
    """Result of probe_tablebase_moves(): the root verdict plus the per-move
    payload when the local files could not answer.

    `moves` is None when the LOCAL files answered -- every child of a covered
    position is covered too, so the caller enumerates them itself at memory
    speed (see services/endgame_reply.py). Otherwise it is the full legal-move
    list from ONE Lichess request, sorted by the API (callers must not rely on
    that order; they iterate the board's moves).
    """

    result: TablebaseResult
    moves: Optional[List[TablebaseMove]] = None


_tablebase: Optional[chess.syzygy.Tablebase] = None
_tablebase_lock = threading.Lock()

# Short-lived fallback cache: {(first-4-fen-fields): (monotonic_time, result)}.
_fallback_cache: dict[str, tuple[float, TablebaseResult]] = {}
_fallback_cache_lock = threading.Lock()
_FALLBACK_CACHE_TTL_SECONDS = 300.0
_FALLBACK_CACHE_MAX_ENTRIES = 4096

# Short-lived per-move payload cache, same policy as the fallback cache. Kept
# separate because the position-level caches above store only the root
# verdict; this one holds the full TablebaseMovesProbe (root + every move).
_moves_cache: dict[str, tuple[float, TablebaseMovesProbe]] = {}
_moves_cache_lock = threading.Lock()

# Optional persistent cache (see CACHING above). None until the app
# registers one at startup; the fallback path works unchanged without it.
_persistent_cache = None
_persistent_cache_lock = threading.Lock()


def set_persistent_cache(cache) -> None:
    """Register (or clear, with None) the persistent fallback cache. The
    object must expose get(fen_key) -> dict|None and put(fen_key, dict)."""
    global _persistent_cache
    with _persistent_cache_lock:
        _persistent_cache = cache
    log.info(
        "tablebase persistent probe cache %s",
        "registered" if cache is not None else "cleared",
    )


def persistent_cache_enabled() -> bool:
    return _persistent_cache is not None


def _persistent_cache_get(key: str) -> Optional[TablebaseResult]:
    cache = _persistent_cache
    if cache is None:
        return None
    try:
        payload = cache.get(key)
    except Exception as exc:  # noqa: BLE001 - a broken cache is a miss, never a failure
        log.warning("tablebase persistent cache get failed (ignoring): %s", exc)
        return None
    if payload is None:
        return None
    try:
        return TablebaseResult(**payload)
    except Exception:  # noqa: BLE001 - a malformed row is a miss, not a crash
        log.warning("tablebase persistent cache row for %s is malformed; ignoring", key)
        return None


def _persistent_cache_put(key: str, result: TablebaseResult) -> None:
    cache = _persistent_cache
    if cache is None:
        return
    try:
        cache.put(key, result.model_dump())
    except Exception as exc:  # noqa: BLE001 - cache writes never break a probe
        log.warning("tablebase persistent cache put failed (ignoring): %s", exc)


def _remember_fallback(key: str, result: TablebaseResult) -> None:
    with _fallback_cache_lock:
        while len(_fallback_cache) >= _FALLBACK_CACHE_MAX_ENTRIES:
            _fallback_cache.pop(next(iter(_fallback_cache)))
        _fallback_cache[key] = (time.monotonic(), result)


def _tablebase_dir() -> Path:
    configured = os.getenv("SYZYGY_TABLEBASE_DIR")
    if configured:
        return Path(configured)
    return DEFAULT_TABLEBASE_DIR


def _open_tablebase(directory: Path) -> chess.syzygy.Tablebase:
    if not directory.is_dir():
        raise TablebaseUnavailableError(
            f"Syzygy tablebase directory not found: {directory}. "
            "Install the files (see scripts/prewarm_syzygy.py) or set "
            "SYZYGY_TABLEBASE_DIR."
        )
    if not list(directory.glob("*.rtbw")):
        raise TablebaseUnavailableError(
            f"Syzygy tablebase directory {directory} contains no *.rtbw files. "
            "Install the files (see scripts/prewarm_syzygy.py)."
        )
    return chess.syzygy.open_tablebase(str(directory))


def _get_tablebase() -> chess.syzygy.Tablebase:
    global _tablebase
    if _tablebase is None:
        with _tablebase_lock:
            if _tablebase is None:
                # Failures are not cached: a transient deployment fix
                # (dropping the files in) takes effect on the next call.
                _tablebase = _open_tablebase(_tablebase_dir())
                log.info(
                    "Syzygy tablebase opened from %s (%d material files)",
                    _tablebase_dir(),
                    len(list(_tablebase_dir().glob("*.rtbw"))),
                )
    return _tablebase


def _api_enabled() -> bool:
    return os.getenv("SYZYGY_TABLEBASE_API_DISABLED", "").lower() not in (
        "1",
        "true",
        "yes",
    )


def _api_base() -> str:
    return os.getenv(
        "SYZYGY_TABLEBASE_API_BASE", "https://tablebase.lichess.ovh/standard"
    )


def _api_timeout() -> float:
    try:
        return float(os.getenv("SYZYGY_TABLEBASE_API_TIMEOUT", "2.0"))
    except ValueError:
        return 2.0


def _cache_key(fen: str) -> str:
    # First 4 fields (board/turn/castling/ep): the repertoire_positions
    # normalization. Halfmove/fullmove counters cannot change a tablebase
    # verdict (DTZ is table-derived, not clock-derived).
    return " ".join(fen.split()[:4])


def _probe_local(board: chess.Board) -> TablebaseResult:
    tablebase = _get_tablebase()
    try:
        wdl = tablebase.probe_wdl(board)
    except KeyError as e:
        raise TablebaseUnavailableError(
            f"local Syzygy tablebase ({_tablebase_dir()}) does not cover this "
            f"position's material ({len(board.piece_map())} men). "
            "Install the matching 3-4-5 files or extend coverage."
        ) from e

    try:
        dtz = tablebase.probe_dtz(board)
    except KeyError as e:
        raise TablebaseUnavailableError(
            f"local Syzygy tablebase ({_tablebase_dir()}) has the WDL file for "
            f"this position but no DTZ coverage. Install the matching "
            "*.rtbz files."
        ) from e

    return TablebaseResult(
        outcome=_WDL_OUTCOME[wdl], dtz=dtz, wdl=wdl, source="local"
    )


def _fetch_lichess_payload(fen: str) -> dict:
    """One live HTTP attempt against tablebase.lichess.ovh, returning the
    parsed JSON payload (the raw body: root category/dtz AND the per-move
    `moves` array used by probe_tablebase_moves).

    The only HTTP seam: tests monkeypatch this (or _fetch_lichess, which
    delegates here) to simulate 429s, outages and malformed responses
    without a network."""
    url = f"{_api_base()}?fen={urllib.parse.quote(fen)}"
    try:
        with urllib.request.urlopen(url, timeout=_api_timeout()) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise TablebaseUnavailableError(
                "tablebase fallback rate limited by tablebase.lichess.ovh (HTTP 429)"
            ) from exc
        raise TablebaseUnavailableError(
            f"tablebase fallback failed: tablebase.lichess.ovh returned HTTP {exc.code}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TablebaseUnavailableError(
            f"tablebase fallback failed: could not reach tablebase.lichess.ovh: {exc}"
        ) from exc

    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise TablebaseUnavailableError(
            f"tablebase fallback failed: malformed response body: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise TablebaseUnavailableError(
            f"tablebase fallback failed: unexpected response type {type(payload).__name__}"
        )
    return payload


def _lichess_wdl(payload: dict, fen: str) -> int:
    """The payload's root category as a raw syzygy WDL value. Raises
    TablebaseUnavailableError for the API's own non-answers."""
    category = payload.get("category")
    if category == "unknown":
        # The service answers HTTP 200 with category "unknown" when it has
        # no data (its ceiling is 7 men) -- not an HTTP error.
        raise TablebaseUnavailableError(
            f"tablebase.lichess.ovh has no data for this position "
            f"(its ceiling is 7 men): {fen!r}"
        )
    wdl = _LICHESS_CATEGORY_WDL.get(category)
    if wdl is None:
        raise TablebaseUnavailableError(
            f"tablebase fallback failed: unrecognized category {category!r}"
        )
    return wdl


def _lichess_result_from_payload(payload: dict, fen: str) -> TablebaseResult:
    wdl = _lichess_wdl(payload, fen)
    return TablebaseResult(
        outcome=_WDL_OUTCOME[wdl],
        dtz=payload.get("dtz"),
        wdl=wdl,
        source="lichess",
    )


def _lichess_moves_from_payload(
    payload: dict, fen: str
) -> List[TablebaseMove]:
    """Parse the payload's per-move `moves` array. Every returned entry is
    from the CHILD side to move, exactly like a child probe. Raises
    TablebaseUnavailableError when the array is missing/empty or an entry is
    unusable: the caller (the reply generator) must then fall back to
    per-child probing rather than rank an incomplete move set."""
    moves = payload.get("moves")
    if not isinstance(moves, list) or not moves:
        raise TablebaseUnavailableError(
            f"tablebase fallback returned no per-move data for {fen!r}"
        )
    parsed: List[TablebaseMove] = []
    for entry in moves:
        if not isinstance(entry, dict) or not isinstance(entry.get("uci"), str):
            raise TablebaseUnavailableError(
                f"tablebase fallback returned a malformed move entry for {fen!r}"
            )
        wdl = _LICHESS_CATEGORY_WDL.get(entry.get("category"))
        if wdl is None:
            raise TablebaseUnavailableError(
                f"tablebase fallback returned an unrecognized move category "
                f"{entry.get('category')!r} for {fen!r}"
            )
        dtz = entry.get("dtz")
        parsed.append(
            TablebaseMove(
                uci=entry["uci"],
                outcome=_WDL_OUTCOME[wdl],
                dtz=int(dtz) if dtz is not None else None,
                wdl=wdl,
            )
        )
    return parsed


def _fetch_lichess(fen: str) -> TablebaseResult:
    """One live HTTP attempt against tablebase.lichess.ovh. The only test
    seam for failure modes: tests monkeypatch this to simulate 429s,
    outages and malformed responses without a network."""
    payload = _fetch_lichess_payload(fen)
    return _lichess_result_from_payload(payload, fen)


def _probe_lichess(fen: str) -> TablebaseResult:
    """Lichess API fallback with the in-memory cache in front and the
    optional persistent cache behind it (in-memory -> persistent -> HTTP)."""
    key = _cache_key(fen)
    now = time.monotonic()
    with _fallback_cache_lock:
        hit = _fallback_cache.get(key)
        if hit is not None:
            cached_at, cached_result = hit
            if now - cached_at < _FALLBACK_CACHE_TTL_SECONDS:
                log.debug("tablebase fallback cache hit: %s", key)
                return cached_result.model_copy()
            _fallback_cache.pop(key, None)  # expired

    persisted = _persistent_cache_get(key)
    if persisted is not None:
        log.debug("tablebase fallback persistent cache hit: %s", key)
        _remember_fallback(key, persisted)
        return persisted.model_copy()

    result = _fetch_lichess(fen)

    _persistent_cache_put(key, result)
    _remember_fallback(key, result)
    return result.model_copy()


def probe_tablebase(fen: str) -> TablebaseResult:
    """Probe the position's outcome: local Syzygy primary, Lichess API
    fallback (same TablebaseResult either way).

    Raises ValueError for malformed/illegal FENs and
    TablebaseUnavailableError when neither source can answer (never a
    silent non-answer).
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"malformed FEN {fen!r}: {e}") from e
    if not board.is_valid():
        raise ValueError(f"illegal position {fen!r}: failed python-chess validity check")

    try:
        return _probe_local(board)
    except TablebaseUnavailableError as local_error:
        if not _api_enabled():
            raise
        log.warning(
            "tablebase fallback: local files cannot answer %s (%s); "
            "asking tablebase.lichess.ovh",
            fen,
            local_error,
        )
        try:
            result = _probe_lichess(fen)
        except TablebaseUnavailableError as remote_error:
            log.error(
                "tablebase fallback failed for %s: %s", fen, remote_error
            )
            raise TablebaseUnavailableError(
                f"tablebase probe unavailable for {fen!r} "
                f"[local: {local_error}] [remote: {remote_error}]"
            ) from local_error
        log.info(
            "tablebase fallback answered %s: outcome=%s dtz=%s wdl=%d",
            fen,
            result.outcome,
            result.dtz,
            result.wdl,
        )
        return result


def probe_tablebase_moves(fen: str) -> TablebaseMovesProbe:
    """Root verdict + per-move verdicts, one network request at most.

    The reply generator's entry point (services/endgame_reply.py). The local
    files answer the root probe -> `moves` is None and the caller enumerates
    children itself (memory-speed, unchanged behavior). Otherwise ONE Lichess
    request returns the root verdict plus every legal move's verdict.

    Caching: the per-move payload has its own short-lived in-memory cache
    (TTL/cap shared with the position-level fallback cache). A payload fetch
    also warms the position-level caches with the root verdict (and the
    persistent cache with the root only -- writing N children would add N DB
    round trips to the request path; children are warmed in memory instead,
    which covers the drill's next moves in this process). Raises the same
    TablebaseUnavailableError contract as probe_tablebase."""
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"malformed FEN {fen!r}: {e}") from e
    if not board.is_valid():
        raise ValueError(f"illegal position {fen!r}: failed python-chess validity check")

    try:
        return TablebaseMovesProbe(result=_probe_local(board))
    except TablebaseUnavailableError as local_error:
        if not _api_enabled():
            raise
        key = _cache_key(fen)
        now = time.monotonic()
        with _moves_cache_lock:
            hit = _moves_cache.get(key)
            if hit is not None:
                cached_at, cached_probe = hit
                if now - cached_at < _FALLBACK_CACHE_TTL_SECONDS:
                    log.debug("tablebase per-move cache hit: %s", key)
                    return cached_probe.model_copy(deep=True)
                _moves_cache.pop(key, None)  # expired

        log.warning(
            "tablebase fallback: local files cannot answer %s (%s); asking "
            "tablebase.lichess.ovh for the per-move payload",
            fen,
            local_error,
        )
        try:
            payload = _fetch_lichess_payload(fen)
            result = _lichess_result_from_payload(payload, fen)
            moves = _lichess_moves_from_payload(payload, fen)
        except TablebaseUnavailableError as remote_error:
            log.error(
                "tablebase fallback failed for %s: %s", fen, remote_error
            )
            raise TablebaseUnavailableError(
                f"tablebase probe unavailable for {fen!r} "
                f"[local: {local_error}] [remote: {remote_error}]"
            ) from local_error

        # Warm the position-level caches exactly as probe_tablebase would
        # have (the root is the position that gets graded first).
        _persistent_cache_put(key, result)
        _remember_fallback(key, result)
        # Children: in-memory only. The user's next graded fen_before is one
        # of these positions, and 300s of in-process warmth covers the
        # immediate continuation at zero I/O. Children still inside local
        # coverage are skipped -- they never consult the fallback cache.
        for move in moves:
            try:
                child = board.copy(stack=False)
                child.push(chess.Move.from_uci(move.uci))
            except ValueError:
                continue
            if len(child.piece_map()) <= 5:
                continue
            _remember_fallback(
                _cache_key(child.fen()),
                TablebaseResult(
                    outcome=move.outcome, dtz=move.dtz, wdl=move.wdl, source="lichess"
                ),
            )

        probe = TablebaseMovesProbe(result=result, moves=moves)
        with _moves_cache_lock:
            while len(_moves_cache) >= _FALLBACK_CACHE_MAX_ENTRIES:
                _moves_cache.pop(next(iter(_moves_cache)))
            _moves_cache[key] = (time.monotonic(), probe)
        log.info(
            "tablebase fallback answered %s with %d per-move entries: "
            "outcome=%s dtz=%s wdl=%d",
            fen,
            len(moves),
            result.outcome,
            result.dtz,
            result.wdl,
        )
        return probe.model_copy(deep=True)
