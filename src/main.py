import glob
import hmac
import logging
import os
import platform
import shutil
import time
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from core.database import init_db
from core.migrations import run_migrations
from engines.stockfish_engine import STOCKFISH_CANDIDATE_PATHS
from routers import onboarding, puzzles, review, webhooks

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / "src" / ".env")

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


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

# --- Routers ---
app.include_router(onboarding.router, prefix="/onboarding")
app.include_router(puzzles.router, prefix="/api")
app.include_router(review.router, prefix="/api")
app.include_router(webhooks.router, prefix="/webhooks")

# --- App Running? ---
@app.get("/praxis")
def praxis():
    return {"status": "ok"}


@app.get("/debug/stockfish")
def debug_stockfish():
    return get_stockfish_debug_info()
