"""Process-local sparring caches shared by the train router and services.

Lives here (not in routers/train.py) so background services can invalidate:
a successful import/analysis/index makes cached style/traps/opening data
stale, and only services see those completions. Same process-local TTL
pattern throughout: module scope, no cross-worker sharing, TTL expiry plus
explicit invalidation on data changes.
"""
from typing import Any, Dict, Optional


# --- chess.com profile (avatar + verified) ----------------------------------
# 1 hour: short enough to pick up an avatar change, long enough that
# re-mounting within a session doesn't re-hit chess.com.
CHESSCOM_PROFILE_TTL_SECONDS = 3600
chesscom_profile_cache: dict[tuple[str, str], tuple[float, Dict[str, Any]]] = {}


# --- Weak Openings representative-game caches -------------------------------
# Listing a bucket re-derives family/color per PGN; single-game fetches feed
# the replay board. 10 minutes. Negative results cached too.
OPENING_GAME_CACHE_TTL_SECONDS = 600
opening_games_cache: dict[
    tuple[str, str, str, str, str], tuple[float, Optional[Dict[str, Any]]]
] = {}
opening_game_cache: dict[
    tuple[str, str], tuple[float, Optional[Dict[str, Any]]]
] = {}


def opening_games_cache_key(
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    family: str,
    color: str,
) -> tuple[str, str, str, str, str]:
    return (
        requested_by_user_id,
        provider,
        opponent_username.strip().lower(),
        family.strip().lower(),
        color,
    )


def opening_game_cache_key(
    requested_by_user_id: str,
    game_id: str,
) -> tuple[str, str]:
    return (requested_by_user_id, game_id)


# --- style + traps (sparring hot path) --------------------------------------
# compute_opponent_style (~2.4s cold for a 500-game corpus) and
# compute_exploitable_traps (~6ms) are cached together per (user, provider,
# username, time_control) and reused until the TTL expires. Entry shape:
# (cached_at_unix, style, exploitable_trap_keys, traps_ok).
SPARRING_STYLE_TRAPS_TTL_SECONDS = 1800  # 30 minutes
sparring_style_traps_cache: dict[
    tuple[str, str, str, str],
    tuple[float, Optional[Dict[str, Any]], Optional[set], bool],
] = {}


def sparring_style_traps_cache_key(
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    time_control: Optional[str] = None,
) -> tuple[str, str, str, str]:
    """Canonical key. Username lowercased to match the SQL LOWER() the
    style/traps queries use; raw TC string (not resolved bucket) as the 4th
    element so equivalent spellings are conservative misses, not stale hits."""
    return (
        requested_by_user_id,
        provider,
        (opponent_username or "").strip().lower(),
        (time_control or "").strip().lower(),
    )


# --- ensure_opponent_repertoire throttle ------------------------------------
# ensure's anti-join scan runs at most once per opponent per TTL window.
SPARRING_ENSURE_REPERTOIRE_TTL_SECONDS = 60
sparring_ensure_repertoire_cache: dict[
    tuple[str, str, str],
    float,
] = {}


def sparring_ensure_repertoire_cache_key(
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
) -> tuple[str, str, str]:
    return (
        requested_by_user_id,
        provider,
        (opponent_username or "").strip().lower(),
    )


def drop_ensure_throttle(
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
) -> None:
    """Drop one opponent's ensure throttle so the next sparring move
    re-indexes fresh games. Called when repertoire indexing completes —
    nothing else changed (style/traps/opening lists don't read
    repertoire_moves), so the wider caches are left warm."""
    sparring_ensure_repertoire_cache.pop(
        sparring_ensure_repertoire_cache_key(
            requested_by_user_id, provider, opponent_username
        ),
        None,
    )


def invalidate_opponent_caches(
    requested_by_user_id: str,
    provider: Optional[str] = None,
    opponent_username: Optional[str] = None,
    *,
    drop_single_game: bool = False,
) -> None:
    """Drop cached sparring data after the underlying corpus changed.

    Call after a successful import, analysis run, repertoire index, or data
    clear. With provider+username only that opponent's entries go (all
    time-control variants); without, the whole user's entries go.
    `drop_single_game` additionally drops per-game PGN entries (keyed by
    game id, unmappable to an opponent without a query): pass True only on
    data clears, where deleted ids would otherwise be served stale. Imports
    and analysis leave single-game entries alone — stored PGNs are immutable
    and new games get new ids.
    """
    if provider and opponent_username:
        lowered = (opponent_username or "").strip().lower()

        def matches(entry_user: str, entry_provider: str, entry_name: str) -> bool:
            return (
                entry_user == requested_by_user_id
                and entry_provider == provider
                and entry_name == lowered
            )

        for key in [k for k in sparring_style_traps_cache if matches(*k[:3])]:
            sparring_style_traps_cache.pop(key, None)
        for key in [k for k in opening_games_cache if matches(*k[:3])]:
            opening_games_cache.pop(key, None)
        sparring_ensure_repertoire_cache.pop(
            (requested_by_user_id, provider, lowered), None
        )
        if drop_single_game:
            for key in [
                k for k in opening_game_cache if k[0] == requested_by_user_id
            ]:
                opening_game_cache.pop(key, None)
        return

    for key in [
        k for k in sparring_style_traps_cache if k[0] == requested_by_user_id
    ]:
        sparring_style_traps_cache.pop(key, None)
    for key in [
        k for k in opening_games_cache if k[0] == requested_by_user_id
    ]:
        opening_games_cache.pop(key, None)
    for key in [
        k for k in sparring_ensure_repertoire_cache if k[0] == requested_by_user_id
    ]:
        sparring_ensure_repertoire_cache.pop(key, None)
    if drop_single_game:
        for key in [
            k for k in opening_game_cache if k[0] == requested_by_user_id
        ]:
            opening_game_cache.pop(key, None)
