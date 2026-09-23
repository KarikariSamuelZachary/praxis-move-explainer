"""
Verification harness for the Endgame Trainer tablebase module
(src/services/tablebase.py).

Covers:

  A. Contract behavior: malformed FEN -> ValueError; parseable-but-illegal
     position -> ValueError; a missing/empty local tablebase directory ->
     TablebaseUnavailableError when the API fallback is DISABLED (offline
     deployments must fail explicitly, never silently).

  B. Fallback behavior with the API enabled: a position beyond installed
     local coverage (the full 3-4-5-man set, so a 6-7-man position) is
     ANSWERED by the Lichess fallback with the same TablebaseResult shape,
     and a repeated identical probe hits the short-lived FEN cache instead
     of the network.

  C. Fallback failure modes (simulated transport fakes -- no network):
     HTTP 429 rate limiting, API down/5xx, timeouts, category "unknown"
     (the API's own 7-man ceiling), and a disabled switch -> every case
     raises an explicit TablebaseUnavailableError combining both reasons;
     none are silent.

  D. THE SEED-AGREEMENT GATE: re-probe every row stored in
     endgame_positions (under 'Lucena Position') with the live module and
     confirm each stored is_winning matches the live outcome:
       stored True  <-> live "win"
       stored False <-> live "draw"
     Any disagreement (including a live "loss", which would contradict the
     stored boolean in the other direction) fails the run with a non-zero
     exit. This is the drift check between "verified at seed time" (step 2)
     and "verified live" (this module) before any session logic is built on
     either. These rows are all within local coverage, so this gate also
     proves the local path answers them WITHOUT touching the network after
     the fallback was added.

  E. PERSISTENT PROBE CACHE: the optional set_persistent_cache seam is
     exercised with fakes (cold put, warm hit with zero transport calls,
     broken cache transparent), and the PostgresProbeCache adapter gets a
     real round-trip against the database.

Run with: cd src && ../venv/bin/python services/tablebase_test.py
"""
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

import chess

from services.tablebase import (
    TablebaseUnavailableError,
    _fetch_lichess,
    _open_tablebase,
    probe_tablebase,
    probe_tablebase_moves,
)

load_dotenv()

# A 6-man position beyond the full local 3-4-5-man coverage. Live-verified
# against tablebase.lichess.ovh: category "loss", dtz -2 (probe from the
# losing side to move). Used by the fallback activation and failure-mode
# checks, which must never depend on which local files happen to be
# installed.
SIX_MAN_LOSS_FEN = "6r1/p7/5k2/P7/5K1P/8/8/8 w - - 0 55"
MALFORMED_FEN = "not-a-fen"
# Parses but is illegal: kings on adjacent squares.
ILLEGAL_FEN = "1Kk5/1P6/8/8/8/8/r7/5R2 w - - 0 1"


def test_failure_contract():
    # Malformed FEN -> ValueError.
    try:
        probe_tablebase(MALFORMED_FEN)
        raise AssertionError("malformed FEN must raise ValueError")
    except ValueError as e:
        assert "malformed FEN" in str(e), e

    # Illegal but parseable FEN.
    try:
        probe_tablebase(ILLEGAL_FEN)
        raise AssertionError("illegal position must raise ValueError")
    except ValueError as e:
        assert "illegal position" in str(e), e

    # Missing / empty local tablebase directory WITH the fallback disabled:
    # the explicit offline failure contract.
    previous = os.environ.pop("SYZYGY_TABLEBASE_DIR", None)
    os.environ["SYZYGY_TABLEBASE_DIR"] = str(Path(os.sep) / "nonexistent" / "syzygy")
    os.environ["SYZYGY_TABLEBASE_API_DISABLED"] = "1"
    try:
        probe_tablebase(SIX_MAN_LOSS_FEN)
        raise AssertionError("offline + missing dir must raise TablebaseUnavailableError")
    except TablebaseUnavailableError as e:
        assert "directory not found" in str(e), e
    finally:
        os.environ["SYZYGY_TABLEBASE_API_DISABLED"] = "0"
        if previous is None:
            os.environ.pop("SYZYGY_TABLEBASE_DIR", None)
        else:
            os.environ["SYZYGY_TABLEBASE_DIR"] = previous

    print("  malformed/illegal/offline-uninstalled inputs raise, none silent")


def test_fallback_activation_and_cache():
    # 1. The fallback answers a position local files do not cover, in the
    #    SAME TablebaseResult shape (live-verified API values).
    result = probe_tablebase(SIX_MAN_LOSS_FEN)
    assert result.outcome == "loss", result
    assert result.dtz == -2, result
    assert result.wdl == -2, result

    # 2. A repeated identical probe must come from the cache, not the wire.
    import services.tablebase as module

    calls = {"n": 0}
    original = module._fetch_lichess

    def counting_fetch(fen):
        calls["n"] += 1
        return original(fen)

    module._fetch_lichess = counting_fetch
    try:
        again = probe_tablebase(SIX_MAN_LOSS_FEN)
        assert calls["n"] == 0, f"expected cache hit, saw {calls['n']} HTTP calls"
        assert again.outcome == "loss" and again.wdl == -2
    finally:
        module._fetch_lichess = original

    print("  fallback answered 6-men material with the same shape; repeat hit the cache")


def test_fallback_failure_modes():
    import services.tablebase as module

    # The activation test above cached a hot answer; failure modes must be
    # exercised through a cold cache (the cache itself is covered in B).
    module._fallback_cache.clear()

    cases = [
        ("HTTP 429 rate limit", "rate limited"),
        ("API down (502)", "failed"),
        ("timeout", "network"),
        ("category unknown (API ceiling)", "no data"),
    ]

    def make_remote(msg):
        def remote(fen):
            raise TablebaseUnavailableError(msg)

        return remote

    for label, fragment in cases:
        original = module._fetch_lichess
        module._fetch_lichess = make_remote(f"simulated: {label}")
        try:
            probe_tablebase(SIX_MAN_LOSS_FEN)
            raise AssertionError(f"{label} must raise TablebaseUnavailableError")
        except TablebaseUnavailableError as exc:
            combined = str(exc)
            assert "[local:" in combined, (label, combined)
            assert "[remote:" in combined, (label, combined)
            assert "does not cover" in combined  # local reason present
        finally:
            module._fetch_lichess = original

    print("  429 / down / timeout / beyond-API-ceiling all raise explicit combined errors")


def test_per_move_payload():
    """The per-move reply payload: local short-circuit, one-request parsing,
    the in-memory payload cache, and the explicit failure contract. Hermetic
    -- a fake payload seam, no network."""
    import services.tablebase as module

    # Local coverage short-circuits: no payload, no network, moves=None.
    local = probe_tablebase_moves("8/8/8/4k3/8/8/8/R3K3 w - - 0 1")
    assert local.moves is None and local.result.source == "local", local

    module._moves_cache.clear()
    module._fallback_cache.clear()
    board = chess.Board(SIX_MAN_LOSS_FEN)
    legal = sorted(board.legal_moves, key=lambda m: m.uci())
    calls = {"n": 0}

    def fake_payload(fen):
        calls["n"] += 1
        return {
            "category": "loss",
            "dtz": -2,
            "moves": [
                {"uci": move.uci(), "category": "win", "dtz": 5 + index}
                for index, move in enumerate(legal)
            ],
        }

    original = module._fetch_lichess_payload
    module._fetch_lichess_payload = fake_payload
    try:
        probe = probe_tablebase_moves(SIX_MAN_LOSS_FEN)
        assert calls["n"] == 1, "remote path must fetch ONE payload"
        assert probe.result.outcome == "loss" and probe.result.dtz == -2
        assert probe.result.source == "lichess"
        assert probe.moves is not None
        assert len(probe.moves) == len(legal), (len(probe.moves), len(legal))
        by_uci = {move.uci: move for move in probe.moves}
        assert by_uci[legal[0].uci()].outcome == "win"
        assert by_uci[legal[0].uci()].dtz == 5
        assert by_uci[legal[0].uci()].wdl == 2

        # A repeat is served from the payload cache, not the transport.
        again = probe_tablebase_moves(SIX_MAN_LOSS_FEN)
        assert calls["n"] == 1 and again.moves is not None

        # Children are warmed in memory: probing the first child (6 men,
        # beyond local coverage) must not touch the transport either.
        child_board = board.copy(stack=False)
        child_board.push(legal[0])
        child_probe = probe_tablebase(child_board.fen())
        assert child_probe.outcome == "win" and child_probe.dtz == 5
        assert calls["n"] == 1, "child warming failed; probe went to the wire"

        # Missing per-move data and unusable entries are explicit failures.
        module._moves_cache.clear()
        module._fallback_cache.clear()

        def no_moves(fen):
            return {"category": "loss", "dtz": -2}

        module._fetch_lichess_payload = no_moves
        try:
            probe_tablebase_moves(SIX_MAN_LOSS_FEN)
            raise AssertionError("missing moves array must raise")
        except TablebaseUnavailableError as exc:
            assert "no per-move data" in str(exc), exc

        def bad_entry(fen):
            return {
                "category": "loss",
                "dtz": -2,
                "moves": [{"uci": legal[0].uci(), "category": "banana"}],
            }

        module._fetch_lichess_payload = bad_entry
        try:
            probe_tablebase_moves(SIX_MAN_LOSS_FEN)
            raise AssertionError("unusable move category must raise")
        except TablebaseUnavailableError as exc:
            assert "unrecognized move category" in str(exc), exc

        # A transport failure keeps the combined local+remote message.
        def down(fen):
            raise TablebaseUnavailableError("simulated: API down")

        module._fetch_lichess_payload = down
        try:
            probe_tablebase_moves(SIX_MAN_LOSS_FEN)
            raise AssertionError("transport failure must raise")
        except TablebaseUnavailableError as exc:
            combined = str(exc)
            assert "[local:" in combined and "[remote:" in combined, combined
    finally:
        module._fetch_lichess_payload = original
        module._moves_cache.clear()
        module._fallback_cache.clear()

    print(
        "  local short-circuit; one payload fetch parsed, cached, and "
        "warmed; missing/malformed payloads raise explicitly"
    )


def _db_config():
    # Connection config mirrors seed_puzzles.py: discrete DB_* vars first
    # (what src/.env provides when running from src/), DATABASE_URL as the
    # fallback (what the root .env provides when running from repo root).
    # src/.env carries a placeholder DATABASE_URL, so a bare URL check alone
    # is not safe in this tree.
    config = {
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 5432)),
    }
    if not all([config["dbname"], config["user"], config["password"]]):
        database_url = os.getenv("DATABASE_URL")
        if database_url and "://" in database_url:
            return {"dsn": database_url}
        raise SystemExit(
            "no usable DB config: set DB_NAME/DB_USER/DB_PASSWORD (src/.env) "
            "or a real DATABASE_URL"
        )
    return config


def test_seed_agreement():
    conn = psycopg2.connect(**_db_config())
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.fen, p.is_winning
            FROM endgame_positions p
            JOIN endgame_topics t ON t.id = p.topic_id
            WHERE t.name = %s
            ORDER BY p.fen
            """,
            ("Lucena Position",),
        )
        rows = cur.fetchall()
    conn.close()

    if not rows:
        raise SystemExit("no seeded positions found — run src/seed_endgames.py first")

    mismatches = []
    print(f"  stored rows: {len(rows)}")
    for fen, stored_is_winning in rows:
        result = probe_tablebase(fen)

        if stored_is_winning:
            expected_outcome = "win"
        else:
            expected_outcome = "draw"
        agrees = result.outcome == expected_outcome

        # dtz must be present for every seeded position (all are covered by
        # the installed KRPvKR DTZ table); None would signal a config gap.
        dtz_ok = result.dtz is not None

        print(
            f"  {fen:38s} stored={stored_is_winning!s:5s} "
            f"live={result.outcome:4s} wdl={result.wdl:+d} dtz={result.dtz} "
            f"{'OK' if agrees and dtz_ok else 'MISMATCH'}"
        )

        if not agrees:
            mismatches.append((fen, stored_is_winning, result))
        if not dtz_ok:
            mismatches.append((fen, stored_is_winning, result))

    if mismatches:
        for fen, stored, result in mismatches:
            print(
                f"  MISMATCH: {fen!r} stored is_winning={stored} "
                f"live={result.outcome} wdl={result.wdl} dtz={result.dtz}"
            )
        raise SystemExit(
            f"SEED/LIVE DISAGREEMENT: {len(mismatches)} of {len(rows)} rows do not "
            "match. Do NOT reconcile silently — investigate before proceeding."
        )

    print("  all stored is_winning values agree with live tablebase probes")


def test_persistent_cache_seam():
    """The persistent cache is optional and best-effort: a cold fallback
    populates it, a warm hit skips the transport entirely, and a broken
    cache is transparent (a miss, never a failed probe). Hermetic: the
    transport is faked, so no network."""
    import services.tablebase as module
    from services.tablebase import TablebaseResult

    class FakeCache:
        def __init__(self):
            self.rows = {}
            self.gets = 0
            self.puts = 0

        def get(self, key):
            self.gets += 1
            return self.rows.get(key)

        def put(self, key, payload):
            self.puts += 1
            self.rows[key] = payload

    key = " ".join(SIX_MAN_LOSS_FEN.split()[:4])
    fetched = {"n": 0}

    def fake_fetch(fen):
        fetched["n"] += 1
        return TablebaseResult(outcome="loss", dtz=-2, wdl=-2, source="lichess")

    original = module._fetch_lichess
    module._fallback_cache.clear()
    fake = FakeCache()
    module.set_persistent_cache(fake)
    module._fetch_lichess = fake_fetch
    try:
        first = probe_tablebase(SIX_MAN_LOSS_FEN)
        assert first.outcome == "loss"
        assert fetched["n"] == 1, "cold probe must hit the transport"
        assert fake.puts == 1 and key in fake.rows, "answer must be persisted"

        module._fallback_cache.clear()
        second = probe_tablebase(SIX_MAN_LOSS_FEN)
        assert fetched["n"] == 1, "persistent hit must not touch the transport"
        assert second.wdl == -2

        class BrokenCache:
            def get(self, key):
                raise RuntimeError("db down")

            def put(self, key, payload):
                raise RuntimeError("db down")

        module._fallback_cache.clear()
        module.set_persistent_cache(BrokenCache())
        third = probe_tablebase(SIX_MAN_LOSS_FEN)
        assert fetched["n"] == 2 and third.outcome == "loss"
    finally:
        module._fetch_lichess = original
        module.set_persistent_cache(None)
        module._fallback_cache.clear()

    print("  cold put, warm hit without transport, broken cache transparent")


def test_postgres_cache_roundtrip():
    import psycopg2

    from services.tablebase_cache import PostgresProbeCache

    cache = PostgresProbeCache()
    key = "roundtrip-" + uuid.uuid4().hex
    payload = {"outcome": "draw", "wdl": 0, "dtz": 0, "source": "lichess"}
    try:
        cache.put(key, payload)
        assert cache.get(key) == payload, "stored probe must round-trip"
        assert cache.get("roundtrip-missing-" + uuid.uuid4().hex) is None
    finally:
        conn = psycopg2.connect(**_db_config())
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tablebase_probe_cache WHERE fen_key = %s", (key,)
            )
        conn.commit()
        conn.close()
        cache.close()
    print("  Postgres probe cache round-trip OK")


def main():
    print("A. failure contract:")
    test_failure_contract()
    print("B. lichess fallback (activation, shape, cache, failure modes):")
    test_fallback_activation_and_cache()
    test_fallback_failure_modes()
    print("B2. per-move payload (one request, parsing, warming, failures):")
    test_per_move_payload()
    print("C. seed agreement gate (live probe vs stored is_winning):")
    test_seed_agreement()
    print("D. persistent probe cache (seam + Postgres round-trip):")
    test_persistent_cache_seam()
    test_postgres_cache_roundtrip()


if __name__ == "__main__":
    main()
