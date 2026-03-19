"""POST /api/v1/classify-scripts — LLM-based script vs source-code classification."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import TenantContext, require_tenant
from ..metering import record_usage
from ..services.llm import llm_chat, parse_json
from ..config import settings

log = logging.getLogger("saas.classify")

router = APIRouter(prefix="/api/v1", tags=["classify"])


class FileSummary(BaseModel):
    path: str
    imports: list[str] = []
    exports: list[str] = []
    docstring: str = ""
    lines: int = 0
    has_main: bool = False


class ClassifyScriptsRequest(BaseModel):
    files: list[FileSummary]


_CLASSIFY_SYSTEM = """你是代码分类专家。判断每个 Python 文件是"工具脚本"还是"源代码模块"。

工具脚本特征：
- 独立运行的入口脚本（有 if __name__ == "__main__" 且很少被其他模块导入）
- 命名含 deploy/setup/install/migrate/seed/admin/run/start/stop/init/bootstrap/cleanup/reset/update/helper
- 只有少量公开 API（≤2个公开函数/类）
- docstring 描述的是"使用方式"而非 API

源代码模块特征：
- 被其他模块导入使用
- 提供多个公开函数/类作为 API
- 命名是功能名词（parser, classifier, analyzer, client, models, utils 等）

输出严格 JSON（不要 markdown 代码块），格式：
{"results": {"文件路径": "tool_script" 或 "source_code"}}
只对不确定的文件做分类，每个文件必须有结果。"""


def _build_classify_prompt(files: list[FileSummary]) -> str:
    parts = []
    for f in files:
        parts.append(
            f"路径: {f.path}\n"
            f"行数: {f.lines}, 有__main__: {f.has_main}\n"
            f"导入: {', '.join(f.imports[:8]) or '(无)'}\n"
            f"公开API: {', '.join(f.exports[:8]) or '(无)'}\n"
            f"文档: {f.docstring[:120] or '(无)'}"
        )
    return "请分类以下文件：\n\n" + "\n\n".join(parts)


@router.post("/classify-scripts")
async def classify_scripts(
    body: ClassifyScriptsRequest,
    ctx: TenantContext = Depends(require_tenant),
):
    """Classify uncertain Python files as tool_script or source_code using LLM."""
    if not settings.llm_api_key:
        raise HTTPException(status_code=503, detail="LLM API key not configured")

    if not body.files:
        return {"results": {}}

    user_prompt = _build_classify_prompt(body.files)
    log.info("classify-scripts: %d files, prompt %d chars", len(body.files), len(user_prompt))

    try:
        response = await llm_chat(
            [
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1024,
            timeout=30.0,
        )
        result = parse_json(response)
    except Exception as e:
        log.warning("classify-scripts LLM failed: %s", e)
        raise HTTPException(502, f"LLM classification failed: {e}")

    raw = result.get("results", {})
    # Normalize: only accept valid values
    normalized = {
        k: v for k, v in raw.items()
        if v in ("tool_script", "source_code")
    }

    await record_usage(ctx.tenant_id, "classify.scripts", None)
    return {"results": normalized}
