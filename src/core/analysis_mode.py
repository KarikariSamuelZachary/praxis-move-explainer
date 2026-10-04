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
    """Content hash of the loaded opening book (not a load timestamp)."""
    from services import opening_book

    return opening_book.get_book_revision() or "unloaded"


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
