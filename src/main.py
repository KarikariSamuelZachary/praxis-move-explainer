import glob
import hmac
import logging
import os
import platform
import shutil
import time
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from core.database import init_db
from core.migrations import run_migrations
from engines.maia_engine import close_maia3, is_maia_available, start_maia3
from engines.stockfish_engine import STOCKFISH_CANDIDATE_PATHS
from routers import import_games, maia_debug, onboarding, puzzles, review, train, user, webhooks, woodpecker

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / "src" / ".env")

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Maia-3 health flag.
#
# `app.state` is the FastAPI-recommended place for process-local application
# state. We mirror it into a module-level `_maia_available` so that code paths
# that have the `app` object (request handlers) and code paths that do not
# (utilities, tests, scripts) can both read the same source of truth. Both
# are written together by `_record_maia_health()` and read by the
# `/api/debug/maia-health` endpoint and by any code path that wants to
# short-circuit before attempting a Maia call.
#
# Three states matter:
#   Unknown  -> boot hasn't completed yet (transient; only during startup).
#   True     -> Maia started successfully; inference is expected to work.
#   False    -> boot-time start_maia3() raised; Maia calls WILL fail with
#               MaiaUnavailableError. Other features (Game Review, Puzzles,
#               Woodpecker) keep working — the failure must be loud but
#               contained to Maia-dependent paths.
# ---------------------------------------------------------------------------
_maia_available: Optional[bool] = None


def _record_maia_health(ok: bool) -> None:
    global _maia_available
    _maia_available = ok
    try:
        app.state.maia_available = ok
    except Exception:  # noqa: BLE001
        # app.state is attached to the FastAPI instance and is always
        # available by the time we call this from startup(); the guard is
        # only here so unit-test imports of main don't blow up before app
        # construction in exotic import orders.
        pass


def maia_available() -> Optional[bool]:
    """Public accessor: True/False after startup, None before."""
    return _maia_available


def get_stockfish_debug_info():
    workspace_matches = glob.glob("/workspace/**/stockfish", recursive=True)
    return {
        "shutil_which": shutil.which("stockfish"),
        "common_paths": {
            path: os.path.exists(path)
            for path in STOCKFISH_CANDIDATE_PATHS
        },
        "workspace_matches": workspace_matches,
        "stockfish_path_env": os.environ.get("STOCKFISH_PATH"),
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

    # Maia-3 (human-like chess model). We attempt to start it eagerly at
    # boot so a missing checkpoint surfaces immediately rather than on the
    # first sparring request. Other features (Game Review, Puzzles,
    # Woodpecker) do NOT depend on Maia, so a startup failure must NOT
    # take the app down — but it must be loud, not swallowed as a quiet
    # warning. We log the full traceback at ERROR and record an explicit
    # health flag that is readable by the /api/debug/maia-health endpoint
    # and by any code path that wants to short-circuit a Maia call.
    try:
        start_maia3()
        _record_maia_health(True)
        log.info("Maia-3 engine started successfully")
    except Exception as exc:  # noqa: BLE001
        _record_maia_health(False)
        # log.exception → ERROR level + full traceback. This is the loud
        # signal that replaces the previous swallowed warning. Downstream
        # callers will get a typed MaiaUnavailableError if they try to use
        # Maia, not a confusing generic failure.
        log.exception("Maia-3 engine failed to start at boot: %s", exc)


@app.on_event("shutdown")
def shutdown():
    close_maia3()

# --- Routers ---
app.include_router(onboarding.router, prefix="/onboarding")
app.include_router(puzzles.router, prefix="/api")
app.include_router(review.router, prefix="/api")
app.include_router(import_games.router, prefix="/api")
app.include_router(train.router, prefix="/api")
app.include_router(user.router, prefix="/api/user")
app.include_router(webhooks.router, prefix="/webhooks")
app.include_router(woodpecker.router, prefix="/api/woodpecker")
app.include_router(maia_debug.router, prefix="/api")

# --- App Running? ---
@app.get("/praxis")
def praxis():
    return {"status": "ok"}


@app.get("/debug/stockfish")
def debug_stockfish():
    return get_stockfish_debug_info()
