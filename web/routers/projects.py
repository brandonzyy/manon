"""Project CRUD — /api/v1/projects.

All data operations go through saas/ API via saas_client.
Local AST extraction + sync via ast_sync.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_api_key
from shared import saas_client
from shared.ast_sync import (
    scan_and_parse, count_scannable_files, sync_to_server,
    get_project, set_project, find_project_by_repo_id,
)
from ..services.git import clone_or_pull

log = logging.getLogger("manon.projects")

router = APIRouter(tags=["projects"], dependencies=[Depends(require_api_key)])

INLINE_SCAN_LIMIT = 200  # max files to scan inline

# Track background indexing tasks (in-memory, for live progress)
_index_tasks: dict[str, dict] = {}


class ProjectSetup(BaseModel):
    source: str  # "local" | "git"
    path: str    # local dir path or git URL
    name: str | None = None
    branch: str = "main"


class ProjectOut(BaseModel):
    id: str
    name: str
    git_url: str = ""
    branch: str = "main"
    local_path: str | None = None
    source_type: str = "local"
    index_status: str = "pending"


class PushUpdate(BaseModel):
    files: list[str] | None = None
    commit: str | None = None


@router.post("/projects/setup")
async def setup_project(body: ProjectSetup):
    """One-click project setup: create repo on saas/ + start AST sync."""
    local_path = ""
    git_url = ""

    if body.source == "local":
        local_path = body.path.replace("\\", "/")
        if not Path(local_path).is_dir():
            raise HTTPException(400, f"Path does not exist: {local_path}")
    elif body.source == "git":
        if not body.path:
            raise HTTPException(400, "Git URL is required")
        git_url = body.path
    else:
        raise HTTPException(400, f"Invalid source: {body.source}")

    name = body.name or Path(local_path or git_url.rstrip("/").split("/")[-1]).stem or "project"
    # Deduplicate: check local project registry
    if body.source == "local":
        existing = get_project(local_path)
        if existing:
            repo_id = existing["repo_id"]
            log.info("Reusing existing project %s for path %s", repo_id, local_path)
            try:
                repo = await saas_client.repos_get(repo_id)
                return {
                    "status": "ok",
                    "project": {"id": repo_id, "name": repo["name"], "local_path": local_path,
                                "source_type": "local", "index_status": repo.get("index_status", "pending")},
                    "indexing": "existing",
                }
            except Exception:
                pass  # stale entry, create new

    # Create repo on saas/
    if body.source == "local":
        repo = await saas_client.repos_create(name, source_type="local", branch=body.branch)
    else:
        repo = await saas_client.repos_create(name, git_url=git_url, branch=body.branch)

    repo_id = repo["id"]

    # For git repos, clone locally for worker file access
    if body.source == "git":
        local_path = await clone_or_pull(repo_id, git_url, body.branch)

    # Register in local project cache
    set_project(local_path, {
        "repo_id": repo_id, "name": name,
        "last_sync": "", "file_hashes": {},
    })

    # Background AST sync for local projects
    if body.source == "local":
        async def _bg_ast_sync():
            try:
                _index_tasks[repo_id] = {"status": "indexing", "result": {}}
                file_count = count_scannable_files(local_path)
                file_results, deleted, new_hashes = scan_and_parse(local_path, {})
                if file_results:
                    await sync_to_server(repo_id, file_results, deleted, full_reindex=True)
                set_project(local_path, {
                    "repo_id": repo_id, "name": name,
                    "last_sync": datetime.now().isoformat(),
                    "file_hashes": new_hashes,
                })
                _index_tasks[repo_id] = {"status": "done", "result": {
                    "files_synced": len(file_results), "files_total": len(new_hashes),
                }}
                log.info("AST sync done for %s: %d/%d files", repo_id, len(file_results), len(new_hashes))
            except Exception as exc:
                _index_tasks[repo_id] = {"status": "error", "result": {"error": str(exc)}}
                log.error("AST sync failed for %s: %s", repo_id, exc)

        asyncio.create_task(_bg_ast_sync())

    return {
        "status": "ok",
        "project": {"id": repo_id, "name": name, "git_url": git_url, "branch": body.branch,
                     "local_path": local_path, "source_type": body.source},
        "indexing": "background",
    }


@router.get("/projects/{project_id}/index-status")
async def index_status(project_id: str):
    """Poll indexing status — check in-memory first, then saas/."""
    info = _index_tasks.get(project_id)
    if info and info["status"] == "indexing":
        return {"status": "indexing"}
    try:
        return await saas_client.index_status(project_id)
    except Exception as exc:
        log.warning("index_status from saas/ failed: %s", exc)
        if info:
            return info
        return {"status": "unknown", "error": str(exc)}


@router.get("/projects")
async def list_projects():
    """List all repos from saas/."""
    repos = await saas_client.repos_list()
    return [ProjectOut(
        id=r["id"], name=r["name"],
        git_url=r.get("git_url", ""), branch=r.get("branch", "main"),
        local_path=r.get("local_path"),
        source_type=r.get("source_type", "local"),
        index_status=r.get("index_status", "pending"),
    ) for r in repos]


@router.get("/projects/{project_id}")
async def get_project_detail(project_id: str):
    """Get repo details from saas/."""
    try:
        repo = await saas_client.repos_get(project_id)
    except Exception:
        raise HTTPException(404, "Project not found")
    return ProjectOut(
        id=repo["id"], name=repo["name"],
        git_url=repo.get("git_url", ""), branch=repo.get("branch", "main"),
        local_path=repo.get("local_path"),
        source_type=repo.get("source_type", "local"),
        index_status=repo.get("index_status", "pending"),
    )


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str):
    """Delete repo from saas/ + clean local project cache."""
    # Clean local cache
    found = find_project_by_repo_id(project_id)
    if found:
        from shared.ast_sync import load_projects, save_projects
        local_path, _ = found
        data = load_projects()
        data["projects"].pop(local_path, None)
        save_projects(data)
    await saas_client.repos_delete(project_id)


@router.post("/projects/{project_id}/push-update")
async def push_update(project_id: str, body: PushUpdate | None = None):
    """Incremental AST sync — scan local changes and upload to saas/."""
    found = find_project_by_repo_id(project_id)
    if found:
        local_path, info = found
        old_hashes = info.get("file_hashes", {})

        async def _bg_sync():
            try:
                _index_tasks[project_id] = {"status": "updating", "result": {}}
                file_results, deleted, new_hashes = scan_and_parse(local_path, old_hashes)
                if not file_results and not deleted:
                    _index_tasks[project_id] = {"status": "done", "result": {"message": "no changes"}}
                    return
                await sync_to_server(project_id, file_results, deleted)
                info["file_hashes"] = new_hashes
                info["last_sync"] = datetime.now().isoformat()
                set_project(local_path, info)
                _index_tasks[project_id] = {"status": "done", "result": {
                    "files_synced": len(file_results), "deleted": len(deleted),
                }}
                log.info("Push-update done for %s: %d synced, %d deleted", project_id, len(file_results), len(deleted))
            except Exception as exc:
                _index_tasks[project_id] = {"status": "error", "result": {"error": str(exc)}}
                log.error("Push-update failed for %s: %s", project_id, exc)

        asyncio.create_task(_bg_sync())
        return {"status": "accepted", "project_id": project_id}

    # Fallback: server-side push-update (git repos)
    result = await saas_client.push_update(project_id)
    return {"status": "accepted", "project_id": project_id, "result": result}


@router.post("/projects/{project_id}/sync")
async def sync_project(project_id: str):
    """Git sync — pull latest and re-index via saas/."""
    return await saas_client.push_update(project_id)

