import hmac
import logging
import os
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from core.database import init_db
from routers import puzzles

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

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
    init_db()

# --- Routers ---
app.include_router(puzzles.router, prefix="/api")

# --- App Running? ---
@app.get("/praxis")
def praxis():
    return {"status": "ok"}
