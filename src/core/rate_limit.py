"""
Backend rate limiter backed by Upstash Redis (REST).

Mirrors the exact incr + conditional expire pattern used by the frontend
in frontend/src/app/api/puzzles/route.ts so the two layers stay consistent.

Upstash credentials use the REST API (UPSTASH_REDIS_REST_URL +
UPSTASH_REDIS_REST_TOKEN), not a redis:// URL — so upstash_redis.Redis
is used directly rather than redis-py or slowapi.

The Redis client is constructed lazily on first use (not at import time)
so it picks up env vars loaded by src/main.py's load_dotenv(...) calls,
which run after this module's import due to import ordering in main.py.
"""
import os
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


def is_over_limit(key: str, limit: int, window_seconds: int) -> bool:
    """
    Fixed-window counter, same shape as the frontend limiter.

    Increments a Redis counter and, on the very first hit in the window,
    sets its TTL so the window rolls forward cleanly. Returns True if the
    caller has exceeded `limit` within `window_seconds`.
    """
    redis = get_redis()
    count = redis.incr(key)
    if count == 1:
        redis.expire(key, window_seconds)
    return count > limit


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
    """
    def _check(request: Request) -> None:
        identifier = request.headers.get("x-clerk-user-id") or get_client_ip(request)
        key = f"rate_limit:import:{identifier}"
        if is_over_limit(key, limit, window):
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please slow down.",
            )
    return _check