"""
Deterministic review analysis mode (REVIEW_DETERMINISTIC).

When enabled, review runs fixed-nodes searches with a fresh game token per
position (python-chess sends ucinewgame, clearing the transposition table).
Fixed nodes without a cleared TT is not reproducible: a warm-vs-fresh sweep
on SF19 showed 290/300 positions changing score/best move and 77/301 move
labels changing, with a warm-vs-warm reversed-order floor of 80/301.

Background jobs (opponent analysis, weakness profile) construct GameAnalyzer
without this flag and keep their historical time+depth behavior.
"""
import hashlib
import os
from typing import Optional

DEFAULT_REVIEW_NODES = 100_000
DEFAULT_NODES_BACKSTOP_SECONDS = 10.0
# Frontend proxy abort (REVIEW_TIMEOUT_MS in the analyze route).
REVIEW_TIMEOUT_SECONDS = 240.0
# Engine time must fit in half the remaining budget (variance/overhead).
REVIEW_HEADROOM = 2.0
# Placeholder until the container nps is measured: p99 of the frozen gate
# sets (data/gate_sets.json: A 05538f9d..., B 8394f937...).
GATE_P99_PLIES = 157

# Fixed in review and live; background jobs keep GameAnalyzer's default of 1:
# opponent_game_analysis.py and weakness_profile.py construct GameAnalyzer
# without multipv, and analyze_game.py (CLI) does too.
REVIEW_MULTIPV = 2
assert REVIEW_MULTIPV == 2, "review/live analysis requires MultiPV=2"
# Bump when the analysis algorithm changes without its constants changing.
MODE_VERSION = "rev-det-v1"

# Live sandbox (explore) analysis: progressive deepening to a fixed depth,
# not fixed nodes. The stream emits one snapshot per depth; the settled
# label is the depth-N evaluation. Deliberately different from the batch
# review's fixed-nodes budget: explore is its own deeper analysis, like
# chess.com's analysis board. Compatibility (below) keeps the parts that
# must match for labels to be comparable: engine, classifier and book.
LIVE_MODE_VERSION = "rev-live-v1"
REVIEW_LIVE_DEPTH = 20
REVIEW_LIVE_MULTIPV = 1
REVIEW_LIVE_PREWARM_MULTIPV = 2
# Prewarm (explore arrow + before-eval seed) searches to this depth instead of
# the settle depth: depth 14 is ~5-10x fewer nodes than depth 20, so the first
# arrow lands in a few hundred ms. The stream still settles the after-position
# to REVIEW_LIVE_DEPTH, and prefers a depth-20 before-eval when cached.
DEFAULT_REVIEW_LIVE_PREWARM_DEPTH = 14
# Deepening snapshots below this depth are not streamed as info lines: early
# depths flicker (best move changes almost every ply) and each line costs a
# classifier pass. Depth 0 (exact terminal synthesis) is always emitted.
DEFAULT_REVIEW_LIVE_INFO_MIN_DEPTH = 8
# Pathological-position guard: with depth and time both set, Stockfish
# stops at whichever comes first.
REVIEW_LIVE_TIME_BACKSTOP = 15.0
# Mode keys that must be identical between a review and a live explore for
# the stale-review gate to pass (search budget may differ by design).
PARITY_MODE_KEYS = ("engine", "classifier", "book")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def review_deterministic_enabled() -> bool:
    """True when the review path should use the deterministic eval mode."""
    return _env_flag("REVIEW_DETERMINISTIC")


def review_nodes() -> int:
    """Nodes budget per position.

    Deterministic mode has no default: REVIEW_NODES must be set explicitly to
    the container-measured old-mode median (the old-mode p10 is the floor).
    Flag off keeps the historical default for callers that never read it.
    """
    try:
        value = int(os.getenv("REVIEW_NODES", ""))
    except (TypeError, ValueError):
        value = 0
    if value > 0:
        return value
    if review_deterministic_enabled():
        raise RuntimeError(
            "REVIEW_DETERMINISTIC is on but REVIEW_NODES is not a positive "
            "integer; set it to the container-measured old-mode median "
            "(old-mode p10 is the floor)."
        )
    return DEFAULT_REVIEW_NODES


def review_max_plies() -> int | None:
    """Longest game the deterministic review accepts.

    None when the flag is off (old behavior: no ply cap). When the flag is on
    the cap comes from the container speed and the chosen N:
        max_plies = (REVIEW_TIMEOUT - REVIEW_LLM_SECONDS) / REVIEW_HEADROOM
                    * REVIEW_CONTAINER_NPS / REVIEW_NODES - 1
    Until REVIEW_CONTAINER_NPS is measured it falls back to GATE_P99_PLIES.
    """
    if not review_deterministic_enabled():
        return None
    try:
        nps = float(os.getenv("REVIEW_CONTAINER_NPS", ""))
    except (TypeError, ValueError):
        nps = 0.0
    if nps <= 0:
        return GATE_P99_PLIES
    try:
        llm_seconds = float(os.getenv("REVIEW_LLM_SECONDS", ""))
    except (TypeError, ValueError):
        llm_seconds = 0.0
    budget = max(0.0, (REVIEW_TIMEOUT_SECONDS - llm_seconds) / REVIEW_HEADROOM)
    return max(1, int(budget * nps / review_nodes()) - 1)


def review_nodes_backstop_seconds() -> float:
    """Wall-clock backstop for nodes searches; logs via backstop_fired."""
    try:
        value = float(os.getenv("REVIEW_NODES_BACKSTOP", ""))
    except (TypeError, ValueError):
        value = DEFAULT_NODES_BACKSTOP_SECONDS
    return value if value > 0 else DEFAULT_NODES_BACKSTOP_SECONDS


def review_live_prewarm_depth() -> int:
    """Depth target for the explore prewarm (suggestion + before-eval seed).

    Defaults below the settle depth for a fast first arrow; clamped into
    [1, REVIEW_LIVE_DEPTH]. Set REVIEW_LIVE_PREWARM_DEPTH=20 to restore the
    old always-deep prewarm.
    """
    try:
        value = int(os.getenv("REVIEW_LIVE_PREWARM_DEPTH", ""))
    except (TypeError, ValueError):
        return DEFAULT_REVIEW_LIVE_PREWARM_DEPTH
    if value <= 0:
        return DEFAULT_REVIEW_LIVE_PREWARM_DEPTH
    return max(1, min(REVIEW_LIVE_DEPTH, value))


def review_live_info_min_depth() -> int:
    """Deepening snapshots below this depth are not streamed as info lines.

    Depth 0 (exact terminal synthesis) is always emitted regardless.
    """
    try:
        value = int(os.getenv("REVIEW_LIVE_INFO_MIN_DEPTH", ""))
    except (TypeError, ValueError):
        return DEFAULT_REVIEW_LIVE_INFO_MIN_DEPTH
    if value <= 0:
        return DEFAULT_REVIEW_LIVE_INFO_MIN_DEPTH
    return max(1, value)


def classifier_fingerprint() -> str:
    """CLASSIFIER_VERSION plus a hash of the tuned classifier constants."""
    from core import game_analyzer as ga

    constants = (
        ga.EP_LOGISTIC_K_REFERENCE,
        ga.EP_RATING_REFERENCE,
        ga.EP_RATING_SENSITIVITY,
        ga.EP_K_FACTOR_MIN,
        ga.EP_K_FACTOR_MAX,
        ga.EP_EXCELLENT_MAX,
        ga.EP_GOOD_MAX,
        ga.EP_INACCURACY_MAX,
        ga.EP_MISTAKE_MAX,
        ga.BRILLIANT_MAX_EP_BEFORE,
        ga.BRILLIANT_MIN_EP_AFTER,
        ga.BRILLIANT_NEAR_BEST_EP,
        ga.BRILLIANT_MIN_SACRIFICE_CP,
        ga.BRILLIANT_PAWN_SAC_CP,
        ga.BRILLIANT_PAWN_SAC_MAX_RATING,
        ga.GREAT_ONLY_GOOD_GAP_EP,
        ga.MISS_OPPONENT_EP_LOSS,
        ga.MISS_MIN_EP_LOSS,
        ga.MISS_MATE_MIN_EP_LOSS,
        ga.MISS_WINNING_EP_MIN,
        ga.MISS_CONCRETE_LOSS_CP,
        ga.MISS_LINE_PLIES,
        ga.MISS_TACTIC_GAIN_CP,
        ga.MISS_TACTIC_GAP_CP,
        ga.MISS_TACTIC_MIN_EP_LOSS,
        ga.MISS_TACTIC_MAX_PLAYED_LOSS_CP,
        ga.MISS_TACTIC_PLIES,
    )
    digest = hashlib.sha256(repr(constants).encode()).hexdigest()[:12]
    return f"{ga.CLASSIFIER_VERSION}:{digest}"


def book_fingerprint() -> str:
    """Content hash of the loaded opening book (not a load timestamp).

    Forces the lazy load: the review computes its mode before the analysis
    runs, and without this the first review after boot would record
    book=unloaded while its own labels already used the loaded book (the
    live route would then reject every explore as stale).
    """
    from services import opening_book

    return opening_book.ensure_book_revision() or "unloaded"


def current_mode_string(engine_name: str, multipv: int, nodes: int) -> str:
    """Auditable label-parity fingerprint for the review/live paths."""
    return "|".join(
        [
            MODE_VERSION,
            f"engine={engine_name}",
            "threads=1",
            "hash=16",
            f"nodes={nodes}",
            f"multipv={multipv}",
            f"classifier={classifier_fingerprint()}",
            f"book={book_fingerprint()}",
        ]
    )


def current_live_mode_string(
    engine_name: str, depth: int = REVIEW_LIVE_DEPTH
) -> str:
    """Fingerprint for the progressive live/explore analysis.

    No nodes/multipv component: the live stream searches to `depth` with a
    varying MultiPV width (1 for the deepening stream, 2 for prewarm), and
    the mode is only compared against a review on the parity keys.
    """
    return "|".join(
        [
            LIVE_MODE_VERSION,
            f"engine={engine_name}",
            "threads=1",
            "hash=16",
            f"depth={depth}",
            f"classifier={classifier_fingerprint()}",
            f"book={book_fingerprint()}",
        ]
    )


def mode_components(mode: str) -> dict:
    """Parse a mode string into its key=value parts (version key skipped)."""
    parts: dict = {}
    for chunk in mode.split("|"):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        parts[key.strip()] = value.strip()
    return parts


def live_mode_compatible(expected_mode: Optional[str], live_mode: str) -> bool:
    """True when a live explore may proceed against `expected_mode`.

    The live search budget intentionally differs from the batch review
    (depth 20 vs fixed nodes), so only the parity keys are compared:
    engine, classifier and book. A mode missing any parity key (an old or
    foreign fingerprint) is rejected.
    """
    if not expected_mode:
        return True
    expected = mode_components(expected_mode)
    live = mode_components(live_mode)
    for key in PARITY_MODE_KEYS:
        if not expected.get(key) or expected[key] != live.get(key):
            return False
    return True
