"""Loom Console API — production web console for the Loom knowledge pipeline."""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import os  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

import loom_bridge as bridge  # noqa: E402
from auth import ensure_indexes, seed_admin  # noqa: E402
from routers import auth_routes, content_routes, pipeline_routes, wiki_routes  # noqa: E402

app = FastAPI(title="Loom Console API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("FRONTEND_URL", "http://localhost:3000"),
                   "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router, prefix="/api")
app.include_router(pipeline_routes.router, prefix="/api")
app.include_router(wiki_routes.router, prefix="/api")
app.include_router(content_routes.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "loom-console"}


@app.on_event("startup")
async def startup():
    bridge.ensure_dirs()
    await ensure_indexes()
    await seed_admin()
