import glob
import hmac
import logging
import os
import platform
import shutil
import threading
import time
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from core.database import init_db
from core.migrations import run_migrations
from services import endgame_seeding
from services.tablebase import set_persistent_cache
from services.tablebase_cache import PostgresProbeCache
from engines.maia_engine import close_maia3, start_maia3, verify_maia3_patch
from engines.stockfish_engine import (
    STOCKFISH_CANDIDATE_PATHS,
    close_endgame_stockfish,
    close_review_stockfish,
    close_stockfish_singleton,
    resolve_stockfish_path,
    singleton_status,
    start_endgame_stockfish,
    start_review_stockfish,
    start_stockfish_singleton,
)
from routers import endgame_hint, endgame_playout, endgame_practice, endgame_woodpecker, endgames, import_games, maia_debug, onboarding, puzzles, repertoire, review, train, user, webhooks, woodpecker

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / "src" / ".env")

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# Persistent tablebase probe cache, owned by the app (registered on
# services.tablebase at startup, closed on shutdown).
_persistent_probe_cache = None


def get_stockfish_debug_info():
    workspace_matches = glob.glob("/workspace/**/stockfish", recursive=True)
    return {
        "resolved_path": resolve_stockfish_path(),
        "shutil_which": shutil.which("stockfish"),
        "common_paths": {
            path: os.path.exists(path)
            for path in STOCKFISH_CANDIDATE_PATHS
        },
        "workspace_matches": workspace_matches,
        "stockfish_path_env": os.environ.get("STOCKFISH_PATH"),
        # Which engine revision each long-lived process actually booted
        # ('Stockfish 19'), so a deploy is verifiable without a shell.
        "singletons": singleton_status(),
    }

# --- App ---
app = FastAPI(title="Praxis API")

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Internal API Protection ---
@app.middleware("http")
async def require_internal_secret(request: Request, call_next):
    if request.url.path == "/webhooks/clerk":
        return await call_next(request)

    expected_secret = os.environ.get("INTERNAL_SECRET")
    provided_secret = request.headers.get("X-Internal-Secret")

    if (
        not expected_secret
        or not provided_secret
        or not hmac.compare_digest(provided_secret, expected_secret)
    ):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    return await call_next(request)


# --- Request Logging Middleware ---
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = (time.time() - start) * 1000
    log.info("%s %s %.2fms", request.method, request.url.path, duration)
    return response


def _warm_engines() -> None:
    """Best-effort pre-warm of the long-lived engines, OFF the boot path.

    Spawning Maia-3 plus the three Stockfish singletons at boot keeps the
    per-request process spawns off the user path, but it must not delay the
    app becoming ready: a scale-to-zero replica pays the whole startup
    before it can answer even a puzzle request, and Stockfish UCI
    handshakes grow with the binary (multi-second on a cold page cache).
    This runs on a daemon thread; every request path still starts any
    engine that is not up yet (get_maia3 / get_stockfish_singleton /
    get_review_stockfish / get_endgame_stockfish), so the worst case is the
    same as a lazy boot.

    Every failure is logged loudly and swallowed: none of these engines is
    required for non-engine features (puzzles, repertoire, endgame
    library), so none may take the app down.
    """
    # Maia-3 (human-like chess model). Starting it here still surfaces a
    # missing checkpoint in the boot logs (liveness is owned by
    # engines.maia_engine and read by /api/debug/maia-health). The
    # throwaway inference pre-loads the model so the user's first
    # out-of-book sparring move does not pay that latency, and doubles as a
    # policy-patch self-test (verify_maia3_patch logs at ERROR if the patch
    # chain is broken).
    try:
        start_maia3()
        log.info("Maia-3 engine started successfully")
        if verify_maia3_patch():
            log.info("Maia-3 prewarm inference OK")
    except Exception as exc:  # noqa: BLE001
        # log.exception → ERROR level + full traceback. Downstream callers
        # get a typed MaiaUnavailableError if they try to use Maia.
        log.exception("Maia-3 engine failed to start at boot: %s", exc)

    # Stockfish singleton for the sparring safety check. Failure is
    # non-fatal: get_stockfish_singleton starts it lazily.
    try:
        engine = start_stockfish_singleton()
        log.info(
            "Stockfish singleton started from: %s (%s)",
            engine.stockfish_path,
            engine.name,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Stockfish singleton failed to start at boot: %s", exc)

    # Separate full-strength Stockfish singleton for game review (strength
    # isolation from the sparring engine; see engines.stockfish_engine).
    # Non-fatal: get_review_stockfish starts it lazily.
    try:
        review_engine = start_review_stockfish(depth=int(os.getenv("REVIEW_DEPTH", "18")))
        log.info(
            "Review Stockfish singleton started from: %s (%s, %.2fs/position)",
            review_engine.stockfish_path,
            review_engine.name,
            review_engine.analysis_time,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Review Stockfish singleton failed to start at boot: %s", exc)

    # Separate full-strength Stockfish singleton for Endgame Trainer opponent
    # replies (tablebase-miss fallback). Own process for failure-domain and
    # latency isolation from review. Non-fatal: get_endgame_stockfish starts
    # it lazily.
    try:
        endgame_engine = start_endgame_stockfish(
            depth=int(os.getenv("ENDGAME_REPLY_DEPTH", "18"))
        )
        log.info(
            "Endgame reply Stockfish singleton started from: %s (%s)",
            endgame_engine.stockfish_path,
            endgame_engine.name,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Endgame reply Stockfish singleton failed to start at boot: %s", exc)


@app.on_event("startup")
def startup():
    log.info("Platform: %s", platform.machine())
    stockfish_debug_info = get_stockfish_debug_info()
    log.info("Stockfish executable from PATH: %s", stockfish_debug_info["shutil_which"])
    log.info("Stockfish found at: %s", stockfish_debug_info["workspace_matches"])
    for path, exists in stockfish_debug_info["common_paths"].items():
        log.info("Stockfish candidate exists: %s=%s", path, exists)

    init_db()
    run_migrations()

    # Endgame content seeding. The library is DATA, not schema (migrations
    # create empty tables), and had only ever been seeded by hand -- which is
    # exactly how production booted with an empty library and answered "no
    # endgame drill positions in rating range ..." (2026-09-21). Runs on a
    # daemon thread: a fresh database imports its 22k sourced positions in
    # the background without delaying boot; normal boots only re-apply the
    # tiny curated set. See services/endgame_seeding.py for the full policy.
    threading.Thread(
        target=endgame_seeding.ensure_endgame_content,
        name="endgame-seeding",
        daemon=True,
    ).start()

    # Persistent tablebase probe cache: every distinct Lichess fallback
    # position is paid once per deployment instead of once per user/move.
    # Non-fatal on failure (the fallback degrades to uncached HTTP), but
    # loud, matching the Maia/Stockfish boot policy below.
    global _persistent_probe_cache
    try:
        _persistent_probe_cache = PostgresProbeCache()
        set_persistent_cache(_persistent_probe_cache)
        log.info("Tablebase persistent probe cache registered")
    except Exception as exc:  # noqa: BLE001
        _persistent_probe_cache = None
        log.exception("Tablebase persistent probe cache unavailable: %s", exc)

    # Long-lived engines (Maia-3 + the three Stockfish singletons) warm on a
    # background thread: a scale-to-zero replica must be able to answer HTTP
    # requests immediately instead of waiting on multi-second engine spawns.
    # See _warm_engines for the full policy.
    threading.Thread(
        target=_warm_engines,
        name="engine-warmup",
        daemon=True,
    ).start()


@app.on_event("shutdown")
def shutdown():
    global _persistent_probe_cache
    if _persistent_probe_cache is not None:
        set_persistent_cache(None)
        _persistent_probe_cache.close()
        _persistent_probe_cache = None
    close_maia3()
    close_stockfish_singleton()
    close_review_stockfish()
    close_endgame_stockfish()

# --- Routers ---
app.include_router(onboarding.router, prefix="/onboarding")
app.include_router(puzzles.router, prefix="/api")
app.include_router(endgames.router, prefix="/api/endgames")
# "Get solution" is shared by all three surfaces (rated, review, practice),
# so it lives beside them at /api/endgames/hint.
app.include_router(endgame_hint.router, prefix="/api/endgames")
app.include_router(
    endgame_woodpecker.router, prefix="/api/endgames/woodpecker"
)
app.include_router(
    endgame_practice.router, prefix="/api/endgames/practice"
)
app.include_router(
    endgame_playout.router, prefix="/api/endgames/playout"
)
app.include_router(review.router, prefix="/api")
app.include_router(import_games.router, prefix="/api")
app.include_router(train.router, prefix="/api")
app.include_router(user.router, prefix="/api/user")
app.include_router(webhooks.router, prefix="/webhooks")
app.include_router(woodpecker.router, prefix="/api/woodpecker")
app.include_router(repertoire.router, prefix="/api/repertoires")
app.include_router(maia_debug.router, prefix="/api")

# --- App Running? ---
@app.get("/praxis")
def praxis():
    return {"status": "ok"}


@app.get("/debug/stockfish")
def debug_stockfish():
    return get_stockfish_debug_info()
