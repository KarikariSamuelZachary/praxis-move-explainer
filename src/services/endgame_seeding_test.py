"""
Verification harness for services/endgame_seeding.py (boot-time endgame
content seeding). Runs WITHOUT a database: the DB handle and the seeder
subprocess runner are injected/patched, so every branch is exercised
deterministically.

Covers:

  A. auto_seed_enabled honors ENDGAME_AUTO_SEED, defaulting to on.

  B. sourced_pool_empty maps the EXISTS result to a bool.

  C. seed_missing_content:
       - always runs the curated seeder;
       - adds the sourced import + backfill only when the sourced pool is
         empty AND the seed artifacts are present;
       - skips the import when the pool already has sourced rows;
       - skips the import (and therefore the backfill) when artifacts are
         missing;
       - skips the backfill when the import failed.

  D. ensure_endgame_content:
       - disabled -> never connects;
       - lock not acquired -> no seeding, connection still closed;
       - lock acquired -> seeds, unlocks, closes;
       - unreachable DB -> swallowed, never raises.

Run with: cd src && ../venv/bin/python services/endgame_seeding_test.py
"""
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import endgame_seeding
from services.endgame_seeding import (
    CURATED_SCRIPT,
    SOURCED_SCRIPT,
    auto_seed_enabled,
    ensure_endgame_content,
    seed_missing_content,
    sourced_pool_empty,
)


class _FakeCursor:
    def __init__(self, fetch_value=None):
        self.fetch_value = fetch_value
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return (self.fetch_value,)


class _FakeConn:
    def __init__(self, fetch_value=None):
        self.cur = _FakeCursor(fetch_value)
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


class _Env:
    """Temporarily set (or clear, with value=None) one env var."""

    def __init__(self, name, value):
        self.name, self.value = name, value

    def __enter__(self):
        self.saved = os.environ.get(self.name)
        if self.value is None:
            os.environ.pop(self.name, None)
        else:
            os.environ[self.name] = self.value

    def __exit__(self, *exc):
        if self.saved is None:
            os.environ.pop(self.name, None)
        else:
            os.environ[self.name] = self.saved


class _Patched:
    """Temporarily replace module attributes on services.endgame_seeding."""

    def __init__(self, **attrs):
        self.attrs = attrs

    def __enter__(self):
        self.saved = {name: getattr(endgame_seeding, name) for name in self.attrs}
        for name, value in self.attrs.items():
            setattr(endgame_seeding, name, value)

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(endgame_seeding, name, value)


def test_auto_seed_enabled():
    with _Env("ENDGAME_AUTO_SEED", None):
        assert auto_seed_enabled() is True, "must default on"

    for value, expected in [
        ("on", True),
        ("1", True),
        ("yes", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        (" OFF ", False),
    ]:
        with _Env("ENDGAME_AUTO_SEED", value):
            assert auto_seed_enabled() is expected, value
    print("  default=on; off/0/no/false disable (whitespace tolerated)")


def test_sourced_pool_empty():
    assert sourced_pool_empty(_FakeConn(fetch_value=True)) is True
    assert sourced_pool_empty(_FakeConn(fetch_value=False)) is False
    print("  EXISTS result maps to bool")


def _capture_seed_runs(*, pool_empty, artifacts_ready, failing_labels=()):
    """Run seed_missing_content with fakes; return [(label, argv), ...]."""
    calls = []

    def runner(label, args, timeout_s):
        calls.append((label, list(args)))
        return label not in failing_labels

    input_path = Path("/fake/endgame_sourced_seed.jsonl.gz") if artifacts_ready else None
    with _Patched(
        sourced_pool_empty=lambda conn: pool_empty,
        _sourced_artifacts_ready=lambda: (input_path is not None, input_path),
    ):
        seed_missing_content(object(), runner=runner)
    return calls


def test_seed_missing_content():
    # Empty pool + artifacts: curated, then import, then backfill.
    calls = _capture_seed_runs(pool_empty=True, artifacts_ready=True)
    labels = [label for label, _ in calls]
    assert labels == ["curated", "sourced-import", "sourced-backfill"], labels
    assert str(CURATED_SCRIPT) in calls[0][1]
    assert str(SOURCED_SCRIPT) in calls[1][1]
    assert "--apply" in calls[1][1] and "--survivors" in calls[1][1]
    assert str(calls[1][1][calls[1][1].index("--survivors") + 1]) == "/fake/endgame_sourced_seed.jsonl.gz"
    assert "--backfill-source-ids" in calls[2][1]

    # Pool present: curated only.
    calls = _capture_seed_runs(pool_empty=False, artifacts_ready=True)
    assert [label for label, _ in calls] == ["curated"]

    # Artifacts missing: curated only (loud error, no subprocess).
    calls = _capture_seed_runs(pool_empty=True, artifacts_ready=False)
    assert [label for label, _ in calls] == ["curated"]

    # Import failure: no backfill (source ids must not be backfilled).
    calls = _capture_seed_runs(
        pool_empty=True, artifacts_ready=True, failing_labels={"sourced-import"}
    )
    assert [label for label, _ in calls] == ["curated", "sourced-import"]
    print(
        "  curated always; import+backfill only when empty+ready; "
        "missing artifacts and failed import both skip correctly"
    )


def test_ensure_endgame_content():
    # Disabled: must not even connect.
    connected = []
    with _Env("ENDGAME_AUTO_SEED", "off"), _Patched(
        _connect=lambda: connected.append(True) or _FakeConn()
    ):
        ensure_endgame_content()
    assert connected == [], "disabled seeding must not connect"

    # Lock denied: another replica is seeding -> no work, conn closed.
    conn = _FakeConn()
    seeded = []
    with _Patched(
        _connect=lambda: conn,
        _try_lock=lambda c: False,
        seed_missing_content=lambda c: seeded.append(True),
    ):
        ensure_endgame_content()
    assert seeded == []
    assert conn.closed

    # Lock acquired: seed, unlock, close.
    conn = _FakeConn()
    with _Patched(
        _connect=lambda: conn,
        _try_lock=lambda c: True,
        seed_missing_content=lambda c: seeded.append(True),
    ):
        ensure_endgame_content()
    assert seeded == [True]
    assert any("pg_advisory_unlock" in sql for sql, _ in conn.cur.executed)
    assert conn.closed

    # Unreachable DB: swallowed, never raises.
    def _boom():
        raise RuntimeError("no database")

    with _Patched(_connect=_boom):
        ensure_endgame_content()
    print(
        "  disabled -> no connect; lock denied -> skip; locked -> seed+unlock"
        "+close; connect failure swallowed"
    )


def main():
    logging.getLogger("services.endgame_seeding").setLevel(logging.CRITICAL)
    print("A. ENDGAME_AUTO_SEED parsing:")
    test_auto_seed_enabled()
    print("B. sourced_pool_empty:")
    test_sourced_pool_empty()
    print("C. seed_missing_content orchestration:")
    test_seed_missing_content()
    print("D. ensure_endgame_content boot path:")
    test_ensure_endgame_content()
    print("all endgame-seeding checks passed")


if __name__ == "__main__":
    main()
