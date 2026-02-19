"""Project CRUD — /api/v1/projects."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_api_key
from ..db import db_pool
from ..services.git import clone_or_pull
from matrixone_graph import MatrixoneGraph

log = logging.getLogger("manon.projects")

router = APIRouter(tags=["projects"], dependencies=[Depends(require_api_key)])

# Track background indexing tasks
_index_tasks: dict[str, dict] = {}  # project_id -> {task, status, result}


class ProjectCreate(BaseModel):
    name: str
    git_url: str
    branch: str = "main"
    workspace: str | None = None
    test_command: str | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    git_url: str
    branch: str
    workspace: str | None
    local_path: str | None
    test_command: str | None


class ProjectSetup(BaseModel):
    source: str  # "local" | "git"
    path: str    # local dir path or git URL
    name: str | None = None
    branch: str = "main"


@router.post("/projects/setup")
async def setup_project(body: ProjectSetup):
    """One-click project setup: create project + start background indexing."""
    pid = str(uuid.uuid4())[:8]

    if body.source == "local":
        local_path = body.path.replace("\\", "/")
        if not Path(local_path).is_dir():
            raise HTTPException(400, f"Path does not exist: {local_path}")
        git_url = ""
    elif body.source == "git":
        if not body.path:
            raise HTTPException(400, "Git URL is required")
        git_url = body.path
        local_path = await clone_or_pull(pid, git_url, body.branch)
    else:
        raise HTTPException(400, f"Invalid source: {body.source}")

    name = body.name or Path(local_path).name
    workspace = name  # Use project name as workspace identifier

    async with db_pool() as db:
        await db.execute(
            "INSERT INTO projects (id,name,git_url,branch,workspace,local_path) VALUES (?,?,?,?,?,?)",
            (pid, name, git_url, body.branch, workspace, local_path),
        )
        await db.commit()

    # Start indexing in background
    async def _bg_index():
        try:
            # Mark indexing in DB
            async with db_pool() as db:
                await db.execute("UPDATE projects SET index_stats=? WHERE id=?",
                                 (json.dumps({"status": "indexing"}), pid))
                await db.commit()
            _index_tasks[pid]["status"] = "indexing"
            result = await MatrixoneGraph.get(local_path).index_report()
            _index_tasks[pid]["status"] = "done"
            _index_tasks[pid]["result"] = result
            # Save stats to DB
            stats_json = json.dumps({
                "status": "done",
                "entities": result.get("entities", 0),
                "relations": result.get("relations", 0),
                "files": result.get("files", 0),
                "chunks": result.get("chunks", 0),
                "skipped": result.get("skipped", 0),
                "errors_count": len(result.get("errors", [])),
                "entityTypes": result.get("entityTypes", {}),
                "score": result.get("health"),
                "lastUpdate": datetime.now(timezone.utc).isoformat(),
            })
            async with db_pool() as db:
                await db.execute("UPDATE projects SET index_stats=?, updated_at=datetime('now') WHERE id=?", (stats_json, pid))
                await db.commit()
            log.info("Background indexing done for %s: %s files, %s entities", pid, result.get("files"), result.get("entities"))
        except Exception as exc:
            _index_tasks[pid]["status"] = "error"
            _index_tasks[pid]["result"] = {"error": str(exc)}
            async with db_pool() as db:
                await db.execute("UPDATE projects SET index_stats=? WHERE id=?",
                                 (json.dumps({"status": "error", "error": str(exc)}), pid))
                await db.commit()
            log.error("Background indexing failed for %s: %s", pid, exc)

    _index_tasks[pid] = {"status": "started", "result": {}}
    asyncio.create_task(_bg_index())

    return {
        "status": "ok",
        "project": {"id": pid, "name": name, "git_url": git_url, "branch": body.branch,
                     "workspace": workspace, "local_path": local_path},
        "indexing": "background",
    }


@router.get("/projects/{project_id}/index-status")
async def index_status(project_id: str):
    """Poll background indexing status — reads from DB (survives restart)."""
    # In-memory task has priority (live progress)
    info = _index_tasks.get(project_id)
    if info and info["status"] == "indexing":
        return {"status": "indexing"}

    # Fall back to DB
    async with db_pool() as db:
        row = await db.execute_fetchone("SELECT index_stats FROM projects WHERE id=?", (project_id,))
    if not row or not row["index_stats"]:
        return {"status": "not_started"}
    stats = json.loads(row["index_stats"])
    db_status = stats.pop("status", "done")
    if db_status == "indexing":
        return {"status": "interrupted"}  # was indexing when Manon restarted
    if db_status == "error":
        return {"status": "error", "result": {"error": stats.get("error", "unknown")}}
    return {"status": "done", "result": stats, "stats": stats}


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectCreate):
    pid = str(uuid.uuid4())[:8]
    local_path = await clone_or_pull(pid, body.git_url, body.branch)
    workspace = body.workspace or pid
    async with db_pool() as db:
        await db.execute(
            "INSERT INTO projects (id,name,git_url,branch,workspace,local_path,test_command) VALUES (?,?,?,?,?,?,?)",
            (pid, body.name, body.git_url, body.branch, workspace, local_path, body.test_command),
        )
        await db.commit()
    return ProjectOut(id=pid, name=body.name, git_url=body.git_url, branch=body.branch,
                      workspace=workspace, local_path=local_path, test_command=body.test_command)


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects():
    async with db_pool() as db:
        cursor = await db.execute("SELECT id,name,git_url,branch,workspace,local_path,test_command FROM projects")
        rows = await cursor.fetchall()
    return [ProjectOut(**dict(r)) for r in rows]


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str):
    async with db_pool() as db:
        row = await db.execute_fetchone(
            "SELECT id,name,git_url,branch,workspace,local_path,test_command FROM projects WHERE id=?", (project_id,)
        )
    if not row:
        raise HTTPException(404, "Project not found")
    return ProjectOut(**dict(row))


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str):
    async with db_pool() as db:
        await db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        await db.commit()


@router.post("/projects/{project_id}/sync")
async def sync_project(project_id: str):
    async with db_pool() as db:
        row = await db.execute_fetchone("SELECT git_url, branch FROM projects WHERE id=?", (project_id,))
    if not row:
        raise HTTPException(404, "Project not found")
    local_path = await clone_or_pull(project_id, row["git_url"], row["branch"])
    async with db_pool() as db:
        await db.execute("UPDATE projects SET local_path=?, updated_at=datetime('now') WHERE id=?", (local_path, project_id))
        await db.commit()
    return {"status": "synced", "local_path": local_path}


class PushUpdate(BaseModel):
    files: list[str] | None = None  # changed file paths (relative to repo root)
    commit: str | None = None       # commit hash (for logging)


@router.post("/projects/{project_id}/push-update")
async def push_update(project_id: str, body: PushUpdate | None = None):
    """Post-push incremental update — file-level chunk refresh + entity update.

    Call after git push to keep the index fresh.
    Accepts optional changed file list; auto-detects via git diff if omitted.
    """
    async with db_pool() as db:
        row = await db.execute_fetchone(
            "SELECT local_path, workspace FROM projects WHERE id=?", (project_id,)
        )
    if not row:
        raise HTTPException(404, "Project not found")

    local_path = row["local_path"]
    workspace = row["workspace"]
    changed_files = body.files if body else None

    # Auto-detect changed files via git diff if not provided
    if not changed_files and local_path:
        import subprocess
        try:
            out = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
                cwd=local_path, capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0 and out.stdout.strip():
                changed_files = [f.strip() for f in out.stdout.strip().split("\n") if f.strip()]
        except Exception:
            pass

    # Run incremental update in background
    async def _bg_update():
        try:
            async with db_pool() as db:
                await db.execute("UPDATE projects SET index_stats=json_set(COALESCE(index_stats,'{}'),'$.status','updating') WHERE id=?", (project_id,))
                await db.commit()
            _index_tasks[project_id] = {"status": "updating", "result": {}}

            result = await MatrixoneGraph.get(local_path).index_report(incremental=True)

            # Merge result into existing stats
            async with db_pool() as db:
                existing = await db.execute_fetchone("SELECT index_stats FROM projects WHERE id=?", (project_id,))
                stats = json.loads(existing["index_stats"]) if existing and existing["index_stats"] else {}
                stats["status"] = "done"
                stats["lastUpdate"] = datetime.now(timezone.utc).isoformat()
                stats["lastPushUpdate"] = {
                    "strategy": result.get("strategy", "full"),
                    "files": result.get("files", 0),
                    "chunks_cleared": result.get("chunks_cleared", 0),
                    "chunks": result.get("chunks", 0),
                    "entities": result.get("entities", 0),
                    "commit": body.commit if body else None,
                }
                # Accumulate totals
                if result.get("strategy") == "hot-update":
                    stats["chunks"] = (stats.get("chunks", 0) or 0) + result.get("chunks", 0)
                else:
                    stats.update({
                        "entities": result.get("entities", 0),
                        "relations": result.get("relations", 0),
                        "files": result.get("files", 0),
                        "chunks": result.get("chunks", 0),
                    })
                await db.execute("UPDATE projects SET index_stats=?, updated_at=datetime('now') WHERE id=?",
                                 (json.dumps(stats), project_id))
                await db.commit()

            _index_tasks[project_id] = {"status": "done", "result": result}
            log.info("Push-update done for %s: %s", project_id, result.get("strategy", "full"))
        except Exception as exc:
            _index_tasks[project_id] = {"status": "error", "result": {"error": str(exc)}}
            log.error("Push-update failed for %s: %s", project_id, exc)

    asyncio.create_task(_bg_update())

    return {
        "status": "accepted",
        "project_id": project_id,
        "changed_files": len(changed_files) if changed_files else "auto-detect",
    }
