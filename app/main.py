"""
The FastAPI application.

One process serves both the JSON API (under /api) and the built React frontend
(everything else). That keeps deployment to a single container with no CORS
configuration to get wrong.
"""

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config, routes_executions, routes_skills
from app.db import SessionLocal, create_tables
from app.logging_setup import app_log, log_event, setup_logging
from app.seed import seed_if_empty

# Where the built frontend lands. Created by the Docker build; absent during
# local backend-only development, which is handled gracefully below.
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup work: logging, tables, starter data."""
    setup_logging()
    create_tables()

    db = SessionLocal()
    try:
        seed_if_empty(db)
    finally:
        db.close()

    log_event(
        app_log,
        "startup_complete",
        llm_configured=config.llm_is_configured(),
        model=config.GEMINI_MODEL,
        frontend_bundled=STATIC_DIR.exists(),
    )
    yield


app = FastAPI(
    title="Dynamic Skills Agent Platform",
    description="Define reusable AI skills, run them with bounded tools, and approve write actions.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """One structured log line per request, including how long it took."""
    started = time.monotonic()
    response = await call_next(request)
    duration_ms = int((time.monotonic() - started) * 1000)

    # Static asset requests would drown out everything useful.
    if request.url.path.startswith("/api"):
        log_event(
            app_log,
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

    return response


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """Last resort. Logs the full traceback but returns a plain message, so we
    never leak internals to the browser."""
    app_log.exception(
        "unhandled_error",
        extra={"context": {"path": request.url.path, "method": request.method}},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on the server. The error has been logged."},
    )


app.include_router(routes_skills.router)
app.include_router(routes_executions.router)


@app.get("/api/health")
def health():
    """Used by the frontend to warn about missing configuration, and by uptime
    checks to keep the free-tier container awake."""
    return {
        "status": "ok",
        "llm_configured": config.llm_is_configured(),
        "model": config.GEMINI_MODEL,
        "model_chain": config.model_chain(),
    }


# --- frontend ----------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        """Serves the React app.

        Any path that is not an API route returns index.html, so client-side
        routes like /skills/3 work when opened directly or refreshed.
        """
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")

else:

    @app.get("/")
    def no_frontend():
        """Shown when running the backend without building the frontend."""
        return {
            "message": "API is running. The frontend bundle is not present.",
            "docs": "/docs",
            "health": "/api/health",
        }
