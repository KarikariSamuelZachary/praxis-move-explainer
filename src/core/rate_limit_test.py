"""
Verification harness for core/rate_limit.py after the backend split.

Covers:

  A. MEMORY backend (the default): the fixed window allows exactly `limit`
     requests, rejects the next, keeps users/routes independent, and rolls
     over when the window elapses. The periodic sweep prunes expired keys.

  B. BACKEND DISPATCH: RATE_LIMIT_BACKEND=redis routes through the same
     incr + conditional-expire seam as before (fake Redis, no network);
     unset/unknown values use the in-process backend and never touch
     get_redis.

  C. DEPENDENCY KEYS: limit_by_clerk_user_id builds the documented
     "rate_limit:import:<route>:<user>" key and raises 429 through
     HTTPException; limit_by_ip keys on the forwarded client IP.

Run with: cd src && ../venv/bin/python core/rate_limit_test.py
"""
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException
from starlette.requests import Request

from core import rate_limit


class FakeRedis:
    """Records the calls the redis backend makes; no network."""

    def __init__(self, incr_value=1):
        self.incr_value = incr_value
        self.keys = []
        self.expires = []

    def incr(self, key):
        self.keys.append(key)
        return self.incr_value

    def expire(self, key, window):
        self.expires.append((key, window))
        return True


def _request(headers=None, path="/api/endgames/move"):
    raw = [
        (key.lower().encode(), value.encode())
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": raw,
            "query_string": b"",
            "client": ("203.0.113.7", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
            "root_path": "",
        }
    )


def _with_backend(value):
    previous = os.environ.get("RATE_LIMIT_BACKEND")
    if value is None:
        os.environ.pop("RATE_LIMIT_BACKEND", None)
    else:
        os.environ["RATE_LIMIT_BACKEND"] = value
    return previous


def _restore_backend(previous):
    if previous is None:
        os.environ.pop("RATE_LIMIT_BACKEND", None)
    else:
        os.environ["RATE_LIMIT_BACKEND"] = previous


def test_memory_window():
    rate_limit._memory_counters.clear()
    key = "test:memory:" + uuid.uuid4().hex
    assert [rate_limit.is_over_limit(key, 3, 60) for _ in range(3)] == [
        False,
        False,
        False,
    ], "the first `limit` requests must pass"
    assert rate_limit.is_over_limit(key, 3, 60) is True, "the 4th must reject"
    assert rate_limit.is_over_limit(key, 3, 60) is True, "rejection persists"

    other = "test:memory:" + uuid.uuid4().hex
    assert rate_limit.is_over_limit(other, 3, 60) is False, "keys are independent"
    print("  memory: fixed window allows limit, rejects the next, keys independent")


def test_memory_rollover_and_sweep():
    rate_limit._memory_counters.clear()
    key = "test:rollover:" + uuid.uuid4().hex
    # A stale entry (started long ago) is treated as a fresh window.
    rate_limit._memory_counters[key] = (time.monotonic() - 120, 99, 60)
    assert rate_limit.is_over_limit(key, 3, 60) is False, "window must roll over"

    # The periodic sweep drops entries whose own window expired.
    expired = "test:expired:" + uuid.uuid4().hex
    rate_limit._memory_counters[expired] = (time.monotonic() - 120, 5, 60)
    rate_limit._memory_ops = rate_limit._MEMORY_SWEEP_EVERY - 1
    rate_limit._memory_counters["test:sweep:" + uuid.uuid4().hex] = (
        time.monotonic(),
        1,
        60,
    )
    rate_limit.is_over_limit("test:sweep-new:" + uuid.uuid4().hex, 3, 60)
    assert expired not in rate_limit._memory_counters, "expired keys are swept"
    print("  memory: window rollover works and expired keys are swept")


def test_backend_dispatch():
    previous = _with_backend("redis")
    fake = FakeRedis(incr_value=16)
    original_get_redis = rate_limit.get_redis
    rate_limit.get_redis = lambda: fake
    try:
        assert rate_limit.is_over_limit("k", 15, 60) is True
        assert fake.keys == ["k"], fake.keys
        assert fake.expires == [], "expire only fires on the first hit"

        fake2 = FakeRedis(incr_value=1)
        rate_limit.get_redis = lambda: fake2
        rate_limit.is_over_limit("k2", 15, 60)
        assert fake2.expires == [("k2", 60)], fake2.expires
    finally:
        rate_limit.get_redis = original_get_redis
        _restore_backend(previous)

    previous = _with_backend(None)
    original_get_redis = rate_limit.get_redis

    def must_not_call():
        raise AssertionError("memory backend must not touch get_redis")

    rate_limit.get_redis = must_not_call
    try:
        # Any branch that consults Redis would raise here.
        rate_limit.is_over_limit("test:dispatch:" + uuid.uuid4().hex, 2, 60)
    finally:
        rate_limit.get_redis = original_get_redis
        _restore_backend(previous)
    print("  dispatch: redis seam used only when selected; memory never calls it")


def test_dependency_keys():
    previous = _with_backend(None)
    rate_limit._memory_counters.clear()
    original_get_redis = rate_limit.get_redis
    rate_limit.get_redis = lambda: FakeRedis(incr_value=1)
    try:
        check = rate_limit.limit_by_clerk_user_id(limit=2, window=60)
        request = _request({"X-Clerk-User-Id": "user-abc"})
        check(request)
        check(request)
        try:
            check(request)
            raise AssertionError("third call must raise HTTPException 429")
        except HTTPException as exc:
            assert exc.status_code == 429, exc
        # Different user has their own budget.
        check(_request({"X-Clerk-User-Id": "user-xyz"}))

        expected = "rate_limit:import:/api/endgames/move:user-abc"
        assert expected in rate_limit._memory_counters, rate_limit._memory_counters

        ip_check = rate_limit.limit_by_ip(limit=1, window=60)
        ip_request = _request({"X-Forwarded-For": "198.51.100.9, 10.0.0.1"})
        ip_check(ip_request)
        try:
            ip_check(ip_request)
            raise AssertionError("second IP call must raise")
        except HTTPException as exc:
            assert exc.status_code == 429, exc
        assert "rate_limit:review:198.51.100.9" in rate_limit._memory_counters
    finally:
        rate_limit.get_redis = original_get_redis
        rate_limit._memory_counters.clear()
        _restore_backend(previous)
    print("  dependencies: key shape and 429 behavior match the old limiter")


def main():
    test_memory_window()
    test_memory_rollover_and_sweep()
    test_backend_dispatch()
    test_dependency_keys()
    print("all rate limiter checks passed")


if __name__ == "__main__":
    main()
