"""Pipeline service — REST-friendly state machine for code task planning.

Stages: clarify → spec → confirm → design → decompose → execute → review → done

Each session stores messages in a buffer for REST polling. User interaction
points pause the pipeline and set a pending_action; the respond endpoint
resumes it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .llm import llm_chat, parse_json

log = logging.getLogger("saas.pipeline")


class Stage(str, Enum):
    IDLE = "idle"
    CLARIFYING = "clarifying"
    SPEC = "spec"
    CONFIRMING = "confirming"
    DESIGNING = "designing"
    DECOMPOSING = "decomposing"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"


@dataclass
class PipelineSession:
    pipeline_id: str
    tenant_id: str
    repo_id: str
    repo_path: str
    description: str
    auto_execute: bool = False
    stage: Stage = Stage.IDLE
    messages: list[dict] = field(default_factory=list)
    # Artifacts
    conversation_history: list[dict] = field(default_factory=list)
    spec: dict | None = None
    design: dict | None = None
    tasks: list[dict] = field(default_factory=list)
    report_url: str = ""
    # Interaction
    pending_action: str | None = None  # "answer" | "confirm_plan" | "confirm_review"
    _response_event: asyncio.Event = field(default_factory=asyncio.Event)
    _response_value: str = ""
    _task: asyncio.Task | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def add_msg(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content, "ts": datetime.now().isoformat()})

    async def wait_user(self, action: str) -> str:
        """Pause pipeline and wait for user response."""
        self.pending_action = action
        self._response_event.clear()
        await self._response_event.wait()
        self.pending_action = None
        val = self._response_value
        self._response_value = ""
        return val

    def resume(self, content: str) -> None:
        """Resume pipeline with user response."""
        self._response_value = content
        self._response_event.set()


# Session store: pipeline_id → PipelineSession
_sessions: dict[str, PipelineSession] = {}


def get_session(pid: str) -> PipelineSession | None:
    return _sessions.get(pid)


def list_sessions(tenant_id: str, repo_id: str) -> list[PipelineSession]:
    return [s for s in _sessions.values() if s.tenant_id == tenant_id and s.repo_id == repo_id]


# ── LLM Prompts ──────────────────────────────────────

_CLARIFY_SYSTEM = """你是 Manon 的产品经理。用户提交了需求，你需要通过提问确认需求边界。
规则：
1. 每轮最多问 2-3 个问题
2. 问题要具体、可快速回答
3. 如果已有足够信息（通常 1-2 轮），输出 "READY" 表示可以生成 spec
4. 用中文，语气友好专业"""

_SPEC_SYSTEM = """你是 Manon 的产品经理。根据需求描述和用户确认，生成结构化的任务规格。
输出严格 JSON 格式（不要 markdown 代码块包裹）：
{"title":"任务标题","scope":"任务范围描述","requirements":[{"id":"R1","title":"需求标题","priority":"MUST","scenarios":[{"title":"场景名","condition":"条件","expected":"预期结果"}]}]}
priority 取值：MUST | SHOULD | MAY"""

_DESIGN_SYSTEM = """你是 Manon 的技术架构师。根据任务规格设计技术方案。
输出严格 JSON 格式（不要 markdown 代码块包裹）：
{"approach":"技术方案概述","decisions":[{"title":"决策标题","rationale":"理由"}],"fileChanges":[{"file":"路径","action":"new|modify","description":"说明"}]}"""

_DECOMPOSE_SYSTEM = """你是 Manon 的技术架构师。将需求拆分为可独立执行和验证的子任务。
每个 task 应该：对应一个可独立验证的工作项，涉及 3-5 个文件，有明确验收标准。
每个 task 应有 order 字段表示执行顺序。相同 order 的任务可以并行执行。
输出严格 JSON 数组（不要 markdown 代码块包裹）：
[{"id":1,"title":"任务标题","instruction":"详细开发指令","files":["path/..."],"criteria":"验收标准","order":1}]"""


# ── Pipeline Runner ───────────────────────────────────

async def start_pipeline(
    tenant_id: str, repo_id: str, repo_path: str,
    description: str, auto_execute: bool = False,
) -> PipelineSession:
    pid = uuid.uuid4().hex[:8]
    session = PipelineSession(
        pipeline_id=pid, tenant_id=tenant_id, repo_id=repo_id,
        repo_path=repo_path, description=description, auto_execute=auto_execute,
    )
    _sessions[pid] = session
    session._task = asyncio.create_task(_run_pipeline(session))
    return session


async def _run_pipeline(s: PipelineSession) -> None:
    """Main pipeline coroutine — runs through all stages."""
    try:
        await _phase_clarify(s)
        await _phase_spec(s)
        if not s.auto_execute:
            await _phase_confirm(s)
        await _phase_design(s)
        await _phase_decompose(s)
        await _phase_review(s)
        s.stage = Stage.DONE
        s.add_msg("system", "Pipeline 完成。")
    except asyncio.CancelledError:
        s.stage = Stage.FAILED
        s.add_msg("system", "Pipeline 已取消。")
    except Exception as exc:
        log.error("Pipeline %s failed: %s", s.pipeline_id, exc)
        s.stage = Stage.FAILED
        s.add_msg("system", f"Pipeline 失败: {exc}")


# ── Phase implementations ─────────────────────────────

_MAX_CLARIFY_ROUNDS = 3


async def _phase_clarify(s: PipelineSession) -> None:
    s.stage = Stage.CLARIFYING
    s.add_msg("system", "开始需求澄清...")

    for round_num in range(_MAX_CLARIFY_ROUNDS):
        msgs = [{"role": "system", "content": _CLARIFY_SYSTEM}]
        msgs.append({"role": "user", "content": f"## 需求描述\n{s.description}\n\n请生成追问问题，或输出 READY 表示信息已足够。"})
        for h in s.conversation_history:
            msgs.append({"role": "assistant", "content": h["question"]})
            msgs.append({"role": "user", "content": h["answer"]})
        if round_num >= 2:
            msgs.append({"role": "user", "content": "已经问了足够多的问题，请输出 READY 或最后一轮追问。"})

        response = await llm_chat(msgs, timeout=60.0)

        if "READY" in response or round_num >= _MAX_CLARIFY_ROUNDS - 1:
            s.add_msg("system", f"需求澄清完成（{round_num + 1} 轮）。")
            return

        s.add_msg("manon", response)

        if s.auto_execute:
            s.conversation_history.append({"question": response, "answer": "(auto-skip)"})
            continue

        answer = await s.wait_user("answer")
        s.add_msg("user", answer)
        s.conversation_history.append({"question": response, "answer": answer})


async def _phase_spec(s: PipelineSession) -> None:
    s.stage = Stage.SPEC
    s.add_msg("system", "正在生成任务规格...")

    history_text = ""
    if s.conversation_history:
        pairs = [f"Q: {h['question']}\nA: {h['answer']}" for h in s.conversation_history]
        history_text = "\n\n## 用户确认\n" + "\n\n".join(pairs)

    response = await llm_chat([
        {"role": "system", "content": _SPEC_SYSTEM},
        {"role": "user", "content": f"## 需求描述\n{s.description}{history_text}"},
    ], timeout=90.0)

    s.spec = parse_json(response)
    title = s.spec.get("title", "")
    scope = s.spec.get("scope", "")
    reqs = s.spec.get("requirements", [])
    summary = f"**{title}**\n> {scope}\n\n"
    for r in reqs:
        summary += f"- [{r.get('priority','MUST')}] {r.get('title','')}\n"
    s.add_msg("manon", f"## 任务规格\n{summary}")


async def _phase_confirm(s: PipelineSession) -> None:
    s.stage = Stage.CONFIRMING
    s.add_msg("system", "请确认执行计划。回复 \"confirm\" 确认，或提出修改意见。")
    answer = await s.wait_user("confirm_plan")
    if answer.lower() not in ("confirm", "确认", "ok", "yes"):
        s.description += f"\n\n## 用户补充\n{answer}"
        s.add_msg("user", answer)
        await _phase_spec(s)
        await _phase_confirm(s)
    else:
        s.add_msg("user", "确认")


async def _phase_design(s: PipelineSession) -> None:
    s.stage = Stage.DESIGNING
    s.add_msg("system", "正在生成技术设计...")

    spec = s.spec or {}
    req_summary = "\n".join(
        f"{r['id']}. [{r.get('priority','MUST')}] {r['title']}"
        for r in spec.get("requirements", [])
    )
    # Include graph context if available
    graph_ctx = ""
    try:
        from matrixone_graph import MatrixoneGraph
        mg = MatrixoneGraph.get(s.repo_path)
        result = await mg.query(s.description, top_k=10, depth=1)
        if result.context:
            graph_ctx = f"\n\n## 代码知识图谱上下文\n{result.context}"
    except Exception as exc:
        log.warning("Graph query failed during design: %s", exc)

    response = await llm_chat([
        {"role": "system", "content": _DESIGN_SYSTEM},
        {"role": "user", "content": f"## 任务规格\n标题：{spec.get('title','')}\n范围：{spec.get('scope','')}\n\n## 需求\n{req_summary}{graph_ctx}"},
    ], max_tokens=4096, timeout=90.0)

    s.design = parse_json(response)
    approach = s.design.get("approach", "")
    files = s.design.get("fileChanges", [])
    file_lines = "\n".join(f"- `{f['file']}` ({f['action']}): {f['description']}" for f in files)
    s.add_msg("manon", f"## 技术设计\n**方案：** {approach}\n\n**文件变更：**\n{file_lines}")


async def _phase_decompose(s: PipelineSession) -> None:
    s.stage = Stage.DECOMPOSING
    s.add_msg("system", "正在拆解子任务...")

    spec = s.spec or {}
    design = s.design or {}
    req_text = "\n".join(
        f"{r['id']}. [{r.get('priority','MUST')}] {r['title']}"
        for r in spec.get("requirements", [])
    )
    design_ctx = ""
    if design:
        files = "; ".join(f"{f['file']} ({f['action']}): {f['description']}" for f in design.get("fileChanges", []))
        design_ctx = f"\n\n## 技术设计\n方案：{design.get('approach','')}\n文件变更：{files}"

    response = await llm_chat([
        {"role": "system", "content": _DECOMPOSE_SYSTEM},
        {"role": "user", "content": f"## 任务规格\n标题：{spec.get('title','')}\n范围：{spec.get('scope','')}\n需求：\n{req_text}{design_ctx}"},
    ], max_tokens=8192, timeout=90.0)

    tasks = parse_json(response)
    s.tasks = [{"status": "planned", **t} for t in tasks]
    task_lines = "\n".join(f"  {t['id']}. {t.get('title','')} (order={t.get('order',1)})" for t in s.tasks)
    s.add_msg("manon", f"## 子任务\n{task_lines}")


async def _phase_review(s: PipelineSession) -> None:
    s.stage = Stage.REVIEWING
    # Build summary for review
    spec = s.spec or {}
    design = s.design or {}
    task_lines = "\n".join(f"- {t.get('title','')}: {t.get('instruction','')[:100]}" for t in s.tasks)

    review_prompt = (
        f"## 原始需求\n{s.description}\n\n"
        f"## 任务规格\n{json.dumps(spec, ensure_ascii=False)[:2000]}\n\n"
        f"## 技术设计\n{design.get('approach','')}\n\n"
        f"## 子任务\n{task_lines}\n\n"
        f"## 评估要求\n"
        f"评估这个计划的完整性和可行性。输出严格 JSON：\n"
        f'{{"score": 1-10, "summary": "总结", "suggestions": ["建议1", ...]}}'
    )
    response = await llm_chat([
        {"role": "system", "content": "你是代码审查专家，评估任务计划的完整性和可行性。"},
        {"role": "user", "content": review_prompt},
    ], max_tokens=2048, timeout=60.0)

    try:
        evaluation = parse_json(response)
    except Exception:
        evaluation = {"score": 7, "summary": response[:200], "suggestions": []}

    score = evaluation.get("score", 7)
    summary = evaluation.get("summary", "")
    s.add_msg("manon", f"## 计划评审\n评分: {score}/10\n{summary}")

    # Generate report
    s.report_url = _generate_report(s, evaluation)
    s.add_msg("system", f"报告已生成: {s.report_url}")


def _generate_report(s: PipelineSession, evaluation: dict) -> str:
    """Generate HTML report and return URL path."""
    reports_dir = Path(__file__).resolve().parents[1] / "static" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    safe_name = f"pipeline_{s.pipeline_id}_{now.strftime('%Y%m%d_%H%M')}"
    title = (s.spec or {}).get("title", s.description[:40])

    parts = [f"<h1>{title}</h1>"]
    parts.append(f'<div style="color:#888;font-size:12px">Pipeline #{s.pipeline_id} · {now.strftime("%Y-%m-%d %H:%M")}</div>')
    parts.append(f"<h2>需求描述</h2><p>{s.description}</p>")

    if s.spec:
        parts.append("<h2>任务规格</h2>")
        for r in s.spec.get("requirements", []):
            parts.append(f"<p><strong>[{r.get('priority','MUST')}]</strong> {r.get('title','')}</p>")

    if s.design:
        parts.append(f"<h2>技术设计</h2><p>{s.design.get('approach','')}</p>")
        fc = s.design.get("fileChanges", [])
        if fc:
            parts.append("<table><tr><th>文件</th><th>操作</th><th>说明</th></tr>")
            for f in fc:
                parts.append(f"<tr><td>{f.get('file','')}</td><td>{f.get('action','')}</td><td>{f.get('description','')}</td></tr>")
            parts.append("</table>")

    if s.tasks:
        parts.append("<h2>子任务</h2><table><tr><th>#</th><th>任务</th><th>指令</th><th>文件</th></tr>")
        for t in s.tasks:
            files = ", ".join(t.get("files", []))
            parts.append(f"<tr><td>{t.get('id','')}</td><td>{t.get('title','')}</td><td>{t.get('instruction','')[:200]}</td><td>{files}</td></tr>")
        parts.append("</table>")

    score = evaluation.get("score", 0)
    parts.append(f"<h2>评审</h2><p>评分: <strong>{score}/10</strong></p><p>{evaluation.get('summary','')}</p>")

    css = "body{font-family:'Segoe UI',sans-serif;max-width:860px;margin:40px auto;padding:0 24px;line-height:1.7}h1{border-bottom:2px solid #4a90e2;padding-bottom:8px}h2{color:#4a90e2;border-left:4px solid #4a90e2;padding-left:10px}table{width:100%;border-collapse:collapse}th,td{padding:6px 10px;border:1px solid #ddd;text-align:left}th{background:#f5f7fa}"
    html = f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><title>{title}</title><style>{css}</style></head><body>{''.join(parts)}</body></html>"

    html_path = reports_dir / f"{safe_name}.html"
    html_path.write_text(html, encoding="utf-8")
    return f"/static/reports/{safe_name}.html"
