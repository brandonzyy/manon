"""FastAPI application — lifespan, routers, admin seed endpoint."""
from __future__ import annotations

import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ensure project root importable
_project_root = str(Path(__file__).resolve().parents[1])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from matrixone_graph import MatrixoneGraph  # noqa: E402

from .config import settings
from .db import init_db, close_db, get_db
from .models import TenantCreate, TenantOut, RegisterRequest
from .routers import health, repos, indexing, query, usage, embedding, pipeline, config, account


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    await init_db(settings.db_path)
    MatrixoneGraph.configure(
        embedding_url=settings.embedding_url,
        data_dir=settings.index_dir,
    )
    yield
    await MatrixoneGraph.shutdown_all()
    from .routers.embedding import close_embedding_client
    await close_embedding_client()
    from .services.llm import close_llm_client
    await close_llm_client()
    await close_db()


app = FastAPI(
    title="Manon SaaS API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# routers
app.include_router(health.router)
app.include_router(repos.router)
app.include_router(indexing.router)
app.include_router(query.router)
app.include_router(usage.router)
app.include_router(embedding.router)
app.include_router(pipeline.router)
app.include_router(config.router)
app.include_router(account.router)

# static console
_static_dir = Path(__file__).parent / "static"


@app.get("/console", include_in_schema=False)
async def console():
    return FileResponse(_static_dir / "console.html")


# ── Admin: seed tenant ─────────────────────────────────
@app.post("/admin/tenants", status_code=201, tags=["admin"])
async def create_tenant(body: TenantCreate):
    db = await get_db()
    tenant_id = uuid.uuid4().hex[:8]
    api_key = f"msk_{uuid.uuid4().hex}"

    await db.execute(
        "INSERT INTO tenants (id, name, tier) VALUES (?, ?, ?)",
        (tenant_id, body.name, body.tier),
    )
    await db.execute(
        "INSERT INTO api_keys (key, tenant_id, label) VALUES (?, ?, ?)",
        (api_key, tenant_id, "default"),
    )
    await db.commit()
    return TenantOut(id=tenant_id, name=body.name, tier=body.tier, api_key=api_key)


# ── Public: self-service registration ─────────────────
@app.post("/api/v1/register", status_code=201, tags=["public"])
async def register(body: RegisterRequest):
    """Public registration — creates a free-tier tenant and returns an API key."""
    db = await get_db()
    tenant_id = uuid.uuid4().hex[:8]
    api_key = f"msk_{uuid.uuid4().hex}"

    await db.execute(
        "INSERT INTO tenants (id, name, tier) VALUES (?, ?, ?)",
        (tenant_id, body.name, "free"),
    )
    await db.execute(
        "INSERT INTO api_keys (key, tenant_id, label) VALUES (?, ?, ?)",
        (api_key, tenant_id, "auto-register"),
    )
    await db.commit()
    return {"api_key": api_key, "tenant_id": tenant_id, "tier": "free"}
