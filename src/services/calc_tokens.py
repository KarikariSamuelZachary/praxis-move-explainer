"""Exercise tokens for the Blind Calculation trainer.

GET /next issues a token; POST /submit redeems it. The token binds
{user_id, segment_id, offset, n, nonce, issued_at} with HMAC-SHA256 so the
client can never alter what it is solving.

Signing reuses the repo's only signing-adjacent pattern: the INTERNAL_SECRET
bearer check in main.py (hmac.compare_digest). No JWT/itsdangerous-style
signed-value infra exists in the repo, so this module is deliberately small
and dependency-free: HMAC-SHA256 over canonical JSON, base64url, compare_digest.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

TOKEN_TTL_SECONDS = 24 * 3600

# Domain-separated subkey: the token signer never uses INTERNAL_SECRET
# directly, so a token signature is useless for any other purpose the
# secret serves (e.g. the X-Internal-Secret bearer check).
TOKEN_SUBKEY_INFO = b"praxis/calc-token/v1"


class TokenError(Exception):
    """Base class; status mapping lives in the router (no line info leaks)."""


class BadTokenError(TokenError):
    pass


class ExpiredTokenError(TokenError):
    pass


class ForeignTokenError(TokenError):
    pass


def _secret() -> bytes:
    secret = os.environ.get("INTERNAL_SECRET")
    if not secret:
        raise RuntimeError("INTERNAL_SECRET is not set")
    return secret.encode()


def _signing_key() -> bytes:
    return hmac.new(_secret(), TOKEN_SUBKEY_INFO, hashlib.sha256).digest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_token(user_id: str, segment_id: str, offset: int, n: int) -> dict:
    """Mint a solve token. Returns {"token", "nonce", "issued_at"}."""
    payload = {
        "kind": "solve",
        "user_id": user_id,
        "segment_id": segment_id,
        "offset": offset,
        "n": n,
        "nonce": secrets.token_bytes(16).hex(),
        "issued_at": int(time.time()),
    }
    body = _b64e(json.dumps(payload, sort_keys=True,
                             separators=(",", ":")).encode())
    sig = _b64e(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest())
    return {"token": f"{body}.{sig}", "nonce": payload["nonce"],
            "issued_at": payload["issued_at"]}


def issue_review_token(user_id: str, entry_id: str) -> dict:
    """Mint a single-use review token bound to a woodpecker entry."""
    payload = {
        "kind": "review",
        "user_id": user_id,
        "entry_id": entry_id,
        "nonce": secrets.token_bytes(16).hex(),
        "issued_at": int(time.time()),
    }
    body = _b64e(json.dumps(payload, sort_keys=True,
                             separators=(",", ":")).encode())
    sig = _b64e(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest())
    return {"token": f"{body}.{sig}", "nonce": payload["nonce"],
            "issued_at": payload["issued_at"]}


def verify_token(token: str, user_id: str, now: float | None = None) -> dict:
    """Verify a solve token: signature, kind, user match, 24 h expiry."""
    return _verify(token, user_id, expect_kind="solve", now=now)


def verify_review_token(token: str, user_id: str,
                        now: float | None = None) -> dict:
    """Verify a review token: signature, kind, user match, 24 h expiry."""
    return _verify(token, user_id, expect_kind="review", now=now)


def _verify(token: str, user_id: str, expect_kind: str,
            now: float | None = None) -> dict:
    """Shared verifier. Raises BadTokenError (malformed/bad signature/
    wrong kind/future-issued), ExpiredTokenError, or ForeignTokenError."""
    try:
        body, sig = token.split(".", 1)
        blob = _b64d(body)
        given = _b64d(sig)
    except (ValueError, base64.binascii.Error):
        raise BadTokenError("malformed token")
    want = hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(given, want):
        raise BadTokenError("bad signature")
    try:
        payload = json.loads(blob.decode())
    except (ValueError, UnicodeDecodeError):
        raise BadTokenError("bad payload")
    if not isinstance(payload, dict):
        raise BadTokenError("bad payload")
    if payload.get("kind") != expect_kind:
        raise BadTokenError("wrong token kind")
    want_keys = {"kind", "user_id", "nonce", "issued_at"} | (
        {"segment_id", "offset", "n"} if expect_kind == "solve"
        else {"entry_id"})
    if set(payload) != want_keys:
        raise BadTokenError("bad payload")
    if payload["user_id"] != user_id:
        raise ForeignTokenError("token belongs to another user")
    now = time.time() if now is None else now
    age = now - payload["issued_at"]
    if age > TOKEN_TTL_SECONDS:
        raise ExpiredTokenError("token expired")
    if age < -60:
        raise BadTokenError("token issued in the future")
    return payload
