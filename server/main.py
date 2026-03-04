"""
main.py — FastAPI application entry point.

Mounts all routes, serves the static frontend, and manages startup/shutdown.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from server import deps
from server.routes import health, chats, messages, files


# ── Lifespan (startup / shutdown) ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("[SERVER] Initializing dependencies...")
    deps.init_all()
    print("[SERVER] Ready.")
    yield
    # Shutdown
    print("[SERVER] Shutting down...")
    deps.shutdown_all()


# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="LH Genie",
    description="Lakehouse Genie — natural language interface to Starburst Trino",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend dev server if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Routes ─────────────────────────────────────────────────────────────

app.include_router(health.router)
app.include_router(chats.router)
app.include_router(messages.router)
app.include_router(files.router)

# ── Static Frontend ────────────────────────────────────────────────────────

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# Serve CSS and JS as static files
if (FRONTEND_DIR / "css").exists():
    app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
if (FRONTEND_DIR / "js").exists():
    app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")


@app.get("/")
async def serve_frontend():
    """Serve the single-page frontend."""
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "Frontend not found. Place index.html in frontend/"}
