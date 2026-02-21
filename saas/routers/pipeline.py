"""Pipeline endpoints — start, poll, respond, report."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import TenantContext, require_tenant
from ..db import get_db
from ..metering import record_usage
from ..models import PipelineStart, PipelineRespond, PipelineStatusOut
from ..services.pipeline import start_pipeline, get_session, list_sessions

router = APIRouter(prefix="/api/v1/repos/{repo_id}", tags=["pipeline"])


async def _get_repo_row(repo_id: str, tenant_id: str):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM repos WHERE id = ? AND tenant_id = ?", (repo_id, tenant_id),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repo not found")
    return row


@router.post("/pipeline", status_code=201)
async def create_pipeline(
    repo_id: str,
    body: PipelineStart,
    ctx: TenantContext = Depends(require_tenant),
):
    """Start a new pipeline for task planning."""
    row = await _get_repo_row(repo_id, ctx.tenant_id)
    if not row["local_path"]:
        raise HTTPException(400, "repo has no local path — index first")

    session = await start_pipeline(
        tenant_id=ctx.tenant_id,
        repo_id=repo_id,
        repo_path=row["local_path"],
        description=body.description,
        auto_execute=body.auto_execute,
    )
    await record_usage(ctx.tenant_id, "pipeline.start", repo_id)
    return {"pipeline_id": session.pipeline_id, "stage": session.stage.value}


@router.get("/pipeline")
async def list_pipelines(
    repo_id: str,
    ctx: TenantContext = Depends(require_tenant),
):
    """List all pipeline sessions for this repo."""
    await _get_repo_row(repo_id, ctx.tenant_id)
    sessions = list_sessions(ctx.tenant_id, repo_id)
    return [
        {
            "pipeline_id": s.pipeline_id,
            "stage": s.stage.value,
            "description": s.description[:100],
            "created_at": s.created_at,
            "pending_action": s.pending_action,
        }
        for s in sessions
    ]


@router.get("/pipeline/{pid}/status")
async def pipeline_status(
    repo_id: str,
    pid: str,
    since: int = 0,
    ctx: TenantContext = Depends(require_tenant),
):
    """Poll pipeline status. Use `since` param to get only new messages."""
    await _get_repo_row(repo_id, ctx.tenant_id)
    session = get_session(pid)
    if not session or session.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "pipeline not found")

    return PipelineStatusOut(
        pipeline_id=pid,
        stage=session.stage.value,
        messages=session.messages[since:],
        pending_action=session.pending_action,
        spec=session.spec,
        design=session.design,
        tasks=session.tasks,
        report_url=session.report_url,
    )


@router.post("/pipeline/{pid}/respond")
async def pipeline_respond(
    repo_id: str,
    pid: str,
    body: PipelineRespond,
    ctx: TenantContext = Depends(require_tenant),
):
    """Send user response to a waiting pipeline."""
    await _get_repo_row(repo_id, ctx.tenant_id)
    session = get_session(pid)
    if not session or session.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "pipeline not found")
    if not session.pending_action:
        raise HTTPException(400, "pipeline is not waiting for user input")

    session.resume(body.content)
    await record_usage(ctx.tenant_id, "pipeline.respond", repo_id)
    return {"pipeline_id": pid, "stage": session.stage.value, "accepted": True}


@router.get("/pipeline/{pid}/report")
async def pipeline_report(
    repo_id: str,
    pid: str,
    ctx: TenantContext = Depends(require_tenant),
):
    """Get the pipeline report URL."""
    await _get_repo_row(repo_id, ctx.tenant_id)
    session = get_session(pid)
    if not session or session.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "pipeline not found")
    if not session.report_url:
        raise HTTPException(404, "report not yet generated")
    return {"pipeline_id": pid, "report_url": session.report_url}
