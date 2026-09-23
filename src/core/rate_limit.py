"""
Backend rate limiter for the FastAPI routes.

Two backends, selected per call by RATE_LIMIT_BACKEND:

  * "memory" (DEFAULT) -- a thread-safe in-process fixed-window counter. A
    limiter check is a dict operation (sub-microsecond): it adds ZERO
    network latency to the request path. This matters because a probe of
    the old default measured the Upstash round trip at ~350-1400 ms per
    request on the live endgame routes -- more than the grading itself.
    Trade-off: the budget is per process. With one uvicorn worker (the
    Dockerfile runs one) and one replica it is exactly as strict as before;
    with N replicas a client effectively gets N x the configured budget.
    The route limits here are generous abuse guards (e.g. 120/min per user
    on the move routes), not billing meters, so this is the right default.

  * "redis" -- the original Upstash Redis (REST) fixed-window counter,
    unchanged: a global budget shared across every process and replica.
    Set RATE_LIMIT_BACKEND=redis to opt back in when the deployment runs
    multiple replicas AND the strict global budget is wanted. Each check
    then pays a synchronous HTTP round trip, so this is a deliberate
    latency/correctness trade, not an oversight.

Mirrors the incr + conditional expire pattern used by the frontend in
frontend/src/app/api/puzzles/route.ts so the two layers stay consistent
where both are active.

Upstash credentials use the REST API (UPSTASH_REDIS_REST_URL +
UPSTASH_REDIS_REST_TOKEN), not a redis:// URL -- so upstash_redis.Redis
is used directly rather than redis-py or slowapi.

The Redis client is constructed lazily on first use (not at import time)
so it picks up env vars loaded by src/main.py's load_dotenv(...) calls,
which run after this module's import due to import ordering in main.py.
"""
import os
import threading
import time
from typing import Optional

from fastapi import HTTPException, Request
from upstash_redis import Redis

_redis_client: Optional[Redis] = None


def get_redis() -> Redis:
    """Return a process-wide Upstash Redis client, built on first use."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis(
            url=os.getenv("UPSTASH_REDIS_REST_URL", ""),
            token=os.getenv("UPSTASH_REDIS_REST_TOKEN", ""),
        )
    return _redis_client


def get_client_ip(request: Request) -> str:
    """
    Return the caller's IP, preferring the first hop of X-Forwarded-For
    (the convention in the Next.js proxy and on Fly.io) and falling back
    to request.client.host, then to "unknown".
    """
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# --- in-memory backend ---------------------------------------------------
# key -> (window_started_monotonic, count, window_seconds). The window is
# stored per entry so the sweep can expire each key by its OWN window
# (routes use different windows: 5/min imports, 120/min moves).
_memory_counters: dict[str, tuple[float, int, int]] = {}
_memory_lock = threading.Lock()
# Hard cap so a hostile/buggy key space cannot grow the dict without bound;
# on overflow the oldest tenth is dropped (their windows expire anyway).
_MEMORY_MAX_KEYS = 100_000
# Entries older than their own window are pruned every this many checks.
_MEMORY_SWEEP_EVERY = 4096
_memory_ops = 0


def _memory_is_over_limit(key: str, limit: int, window_seconds: int) -> bool:
    """Fixed-window counter in process memory. Thread-safe; the check is a
    dict read/write under one lock, no I/O."""
    global _memory_ops
    now = time.monotonic()
    with _memory_lock:
        entry = _memory_counters.get(key)
        if entry is None or now - entry[0] >= window_seconds:
            _memory_counters[key] = (now, 1, window_seconds)
            count = 1
        else:
            count = entry[1] + 1
            _memory_counters[key] = (entry[0], count, window_seconds)

        _memory_ops += 1
        if _memory_ops % _MEMORY_SWEEP_EVERY == 0:
            for existing_key, (started, _count, window) in list(
                _memory_counters.items()
            ):
                if now - started >= window:
                    _memory_counters.pop(existing_key, None)
        if len(_memory_counters) > _MEMORY_MAX_KEYS:
            oldest = sorted(
                _memory_counters, key=lambda k: _memory_counters[k][0]
            )[: _MEMORY_MAX_KEYS // 10]
            for existing_key in oldest:
                _memory_counters.pop(existing_key, None)

    return count > limit


def _backend() -> str:
    """Which backend is active: "memory" (default) or "redis". Read per call
    so tests and ops can flip it without reimporting the module."""
    return os.getenv("RATE_LIMIT_BACKEND", "memory").strip().lower()


def is_over_limit(key: str, limit: int, window_seconds: int) -> bool:
    """
    Fixed-window counter. Returns True if the caller has exceeded `limit`
    within `window_seconds`.

    "memory": in-process counter, no I/O (see module docstring).
    "redis": Upstash REST incr + conditional expire, shared globally.
    """
    if _backend() == "redis":
        redis = get_redis()
        count = redis.incr(key)
        if count == 1:
            redis.expire(key, window_seconds)
        return count > limit
    return _memory_is_over_limit(key, limit, window_seconds)


def limit_by_ip(limit: int = 5, window: int = 60):
    """
    FastAPI dependency factory: limit per-client-IP, default 5/minute.

    Use on routes with no Clerk user id available (the backend
    POST /api/review endpoint is reached via the Next.js proxy, which
    does not currently forward X-Clerk-User-Id).
    """
    def _check(request: Request) -> None:
        key = f"rate_limit:review:{get_client_ip(request)}"
        if is_over_limit(key, limit, window):
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please slow down.",
            )
    return _check


def limit_by_clerk_user_id(limit: int = 5, window: int = 60):
    """
    FastAPI dependency factory: limit per X-Clerk-User-Id, default 5/minute.

    Use on user-scoped routes (chess.com / lichess import) where the Clerk
    header is already required. Falls back to client IP if the header is
    somehow missing, so a missing header cannot bypass the limiter.

    The counter is scoped per route (route template, not raw path) rather
    than shared across every endpoint. A single shared key per user was a
    bug: the import-status endpoint is polled every 1.5s (40/min), which
    burned the whole 30/min budget and 429'd unrelated train endpoints.
    """
    def _check(request: Request) -> None:
        identifier = request.headers.get("x-clerk-user-id") or get_client_ip(request)
        # Scope by the route template (e.g. /api/train/opponent-import/{job_id})
        # rather than the raw path, so dynamic segments (job ids) cannot mint a
        # fresh budget per id. Falls back to the path if the route is unknown.
        route = request.scope.get("route")
        scope = getattr(route, "path", None) or request.url.path
        key = f"rate_limit:import:{scope}:{identifier}"
        if is_over_limit(key, limit, window):
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please slow down.",
            )
    return _check
