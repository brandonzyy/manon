"""POST /api/v1/repos/{repo_id}/analyze-structure — LLM-based directory relevance analysis."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import TenantContext, require_tenant
from ..db import get_db
from ..metering import record_usage
from ..services.llm import llm_chat, parse_json
from ..config import settings

log = logging.getLogger("saas.analyze")

router = APIRouter(prefix="/api/v1/repos/{repo_id}", tags=["analyze"])


class AnalyzeStructureRequest(BaseModel):
    signals: dict


_ANALYZE_SYSTEM = """你是代码索引优化专家。你的任务是判断项目中每个顶级目录是否值得建立代码知识图谱索引。

判断标准（按优先级）：
1. 语言匹配：目录内主要文件是否属于项目支持的解析语言？不支持的语言文件无法被解析，索引无意义。
2. 源码占比：目录内源码文件 (.ts/.js/.py/.go/.java/.rs 等) 占比是否足够？主要是文档 (.md)、配置 (.json/.yaml)、图片等非源码文件的目录不值得索引。
3. 业务关联：是否是核心业务代码、共享库、扩展模块？还是独立的工具脚本、静态资源、文档站？
4. 构建配置：有 package.json/tsconfig.json 等构建配置的目录通常是可构建的源码模块。

你必须输出严格的 JSON（不要 markdown 代码块），格式：
{"directories": [{"name": "目录名", "action": "index" 或 "skip", "reason": "一句话中文理由"}]}"""


def _build_analyze_user_prompt(dirs: dict, supported: list) -> str:
    """Build the LLM user prompt from directory signals."""
    dir_summaries = []
    for name, info in dirs.items():
        exts = info.get("extensions", {})
        ext_str = ", ".join(f"{k}: {v}" for k, v in list(exts.items())[:8])
        sample_str = ", ".join(info.get("sample_files", [])[:5])
        config_flag = " (有构建配置)" if info.get("has_build_config") else ""
        dir_summaries.append(
            f"- {name}/  文件数={info.get('total_files', 0)}{config_flag}\n"
            f"  扩展名: {ext_str}\n"
            f"  示例文件: {sample_str}"
        )
    return f"项目支持的解析语言: {', '.join(supported)}\n\n目录信号:\n" + "\n".join(dir_summaries)


def _validate_structure_directories(raw: list) -> list:
    """Validate and normalize directory analysis results."""
    validated = []
    for d in raw:
        if isinstance(d, dict) and "name" in d and "action" in d:
            d["action"] = d["action"] if d["action"] in ("index", "skip") else "index"
            validated.append({"name": d["name"], "action": d["action"], "reason": d.get("reason", "")})
    return validated


@router.post("/analyze-structure")
async def analyze_structure(
    repo_id: str,
    body: AnalyzeStructureRequest,
    ctx: TenantContext = Depends(require_tenant),
):
    """Analyze project directory structure using LLM to recommend index/skip per directory."""
    if not settings.llm_api_key:
        raise HTTPException(status_code=503, detail="LLM API key not configured")

    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM repos WHERE id = ? AND tenant_id = ?", (repo_id, ctx.tenant_id),
    )
    if not await cur.fetchone():
        raise HTTPException(404, "repo not found")

    signals = body.signals
    supported = signals.get("supported_languages", [])
    dirs = signals.get("directories", {})
    if not dirs:
        return {"directories": []}

    user_prompt = _build_analyze_user_prompt(dirs, supported)
    log.info("analyze-structure for repo %s: %d dirs, prompt %d chars", repo_id, len(dirs), len(user_prompt))

    try:
        analysis = await llm_chat([
            {"role": "system", "content": _ANALYZE_SYSTEM},
            {"role": "user", "content": user_prompt},
        ], max_tokens=4096, timeout=60.0)
        result = parse_json(analysis)
    except Exception as e:
        log.warning("analyze-structure LLM failed: %s", e)
        raise HTTPException(502, f"LLM analysis failed: {e}")

    await record_usage(ctx.tenant_id, "analyze.structure", repo_id)
    return {"directories": _validate_structure_directories(result.get("directories", []))}
