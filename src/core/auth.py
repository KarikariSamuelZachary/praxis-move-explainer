"""
Shared Clerk identity dependency for FastAPI routes.

The Next.js proxy authenticates with Clerk and forwards the signed-in user
as ``X-Clerk-User-Id`` alongside the internal secret. Routes that spend
engine, LLM or heavy DB work must depend on ``require_clerk_user_id`` as the
FIRST dependency, so an internal caller without a user is rejected before
``limit_by_clerk_user_id`` runs -- that limiter falls back to the client IP
when the header is missing, which is the wrong bucket for an anonymous
caller.
"""
from typing import Optional

from fastapi import Header, HTTPException


def require_clerk_user_id(
    x_clerk_user_id: Optional[str] = Header(
        default=None, alias="X-Clerk-User-Id"
    ),
) -> str:
    """Return the forwarded Clerk user id, or reject the request with 400.

    Kept as a dependency (not an inline body check) because FastAPI resolves
    dependencies before the endpoint body runs; an inline check would let the
    per-user rate limiter's IP fallback apply first.
    """
    if not x_clerk_user_id:
        raise HTTPException(
            status_code=400, detail="Missing X-Clerk-User-Id header"
        )
    return x_clerk_user_id
