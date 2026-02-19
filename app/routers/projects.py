"""Project CRUD — /api/v1/projects."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_api_key
from ..db import db_pool
from ..services.git import clone_or_pull

router = APIRouter(tags=["projects"], dependencies=[Depends(require_api_key)])


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
