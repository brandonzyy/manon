"""Coach pipeline state machine — orchestrates clarify → spec → design → decompose → execute.

Mirrors donnie/agent/lib/coach-feature.js but in async Python.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..ws_hub import hub

log = logging.getLogger("manon.coach")


class Status(str, Enum):
    IDLE = "idle"
    CLARIFYING = "clarifying"
    SPEC_READY = "spec-ready"
    USER_CONFIRMING = "user-confirming"
    DESIGNING = "designing"
    DECOMPOSING = "decomposing"
    EXECUTING = "executing"
    DONE = "done"
    FAILED = "failed"


@dataclass
class FeatureState:
    feature_id: str = ""
    dev_id: str = ""
    project_id: str = ""
    status: Status = Status.IDLE
    description: str = ""
    conversation_history: list[dict] = field(default_factory=list)
    spec: dict | None = None
    design: dict | None = None
    tasks: list[dict] = field(default_factory=list)
    current_task_idx: int = -1
    failed_attempts: int = 0
    _task_result_future: asyncio.Future | None = None


# Active sessions: dev_id → FeatureState
_sessions: dict[str, FeatureState] = {}


def get_session(dev_id: str) -> FeatureState | None:
    return _sessions.get(dev_id)


def _ensure_session(dev_id: str) -> FeatureState:
    if dev_id not in _sessions:
        _sessions[dev_id] = FeatureState(dev_id=dev_id)
    return _sessions[dev_id]


async def _send_dev(dev_id: str, data: dict) -> None:
    await hub.send_to_dev(dev_id, data)


async def _send_thinking(dev_id: str, active: bool, label: str = "") -> None:
    await _send_dev(dev_id, {"type": "coach-thinking", "active": active, "label": label})


async def _send_chat(dev_id: str, content: str, role: str = "manon") -> None:
    await _send_dev(dev_id, {"type": "coach-chat", "role": role, "content": content})


# Manon chat history per dev (separate from pipeline state)
_chat_history: dict[str, list[dict]] = {}

_MANON_SYSTEM = """你是 Manon（马浓），一个 AI 架构师助手。你可以：
- 回答关于项目代码的问题（基于知识图谱上下文）
- 分析代码结构、依赖关系、技术债务
- 讨论架构设计和最佳实践
- 帮助理解代码逻辑和调用链
回答简洁、专业，用中文。"""


def _is_feature_request(prompt: str) -> bool:
    """Heuristic: detect if the prompt is an explicit code-change / feature request."""
    p = prompt.strip()
    # Explicit pipeline trigger
    if p.startswith("/feature") or p.startswith("/pipeline"):
        return True
    # Chinese keywords that signal code modification intent
    action_starts = (
        "实现", "添加", "新增", "开发", "创建", "搭建", "构建",
        "修改", "改一下", "改成", "改为", "重构", "优化",
        "删除", "移除", "去掉",
        "修复", "修bug", "修一下", "fix",
        "写一个", "写个", "帮我写", "帮我实现", "帮我添加", "帮我开发",
        "帮我修改", "帮我修复", "帮我重构", "帮我优化", "帮我创建",
        "请实现", "请添加", "请修改", "请开发", "请创建",
        "把这个", "把它",
    )
    for kw in action_starts:
        if p.startswith(kw):
            return True
    # English keywords for code changes
    p_lower = p.lower()
    en_starts = (
        "implement", "add ", "create ", "build ", "develop ",
        "modify ", "change ", "update ", "refactor ", "optimize ",
        "delete ", "remove ",
        "fix ", "write ",
    )
    for kw in en_starts:
        if p_lower.startswith(kw):
            return True
    return False


async def _handle_manon_chat(dev_id: str, msg: dict) -> None:
    """Unified Manon handler — answers questions or auto-starts pipeline."""
    import time
    from datetime import datetime
    from matrixone_graph import MatrixoneGraph
    from ..services.llm import llm_chat_stream

    prompt = msg.get("content", "").strip()
    if not prompt:
        return
    project_id = msg.get("projectId", "")

    # /reset or /cancel — force-reset pipeline state
    if prompt.lower() in ("/reset", "/cancel", "取消", "重置"):
        state = get_session(dev_id)
        if state and state.status not in (Status.IDLE, Status.DONE, Status.FAILED):
            state.status = Status.IDLE
            await _send_chat(dev_id, "Pipeline 已重置，回到正常对话模式。", role="system")
            await _send_dev(dev_id, {"type": "coach-thinking", "active": False})
            return
        await _send_chat(dev_id, "当前没有进行中的 pipeline。", role="system")
        return

    # If pipeline is active, route as user-response
    state = get_session(dev_id)
    if state and state.status not in (Status.IDLE, Status.DONE, Status.FAILED):
        await _handle_user_response(dev_id, {"content": prompt})
        return

    # Query graph for context
    graph_context = ""
    context_tokens = 0
    project_name = ""
    project_path = ""
    if project_id:
        from ..db import db_pool
        async with db_pool() as db:
            row = await db.execute_fetchone(
                "SELECT name, local_path FROM projects WHERE id = ?", (project_id,),
            )
        if row:
            project_name = row["name"] or ""
            project_path = row["local_path"] or ""
        if project_path:
            try:
                await _send_thinking(dev_id, True, "查询知识图谱...")
                t0 = time.monotonic()
                mg = MatrixoneGraph.get(project_path)
                result = await mg.query(prompt, top_k=10, depth=1)
                graph_ms = int((time.monotonic() - t0) * 1000)
                if result.context:
                    graph_context = result.context
                    context_tokens = len(graph_context) // 2  # rough CJK estimate
                    await _send_thinking(dev_id, True, f"知识图谱返回 ~{context_tokens/1000:.1f}k tokens ({graph_ms}ms)")
                    await _send_dev(dev_id, {
                        "type": "llm-query", "caller": "manon",
                        "command": "matrixone_graph.query(hybrid)",
                        "query": prompt[:120], "ts": datetime.now().isoformat(),
                        "duration_ms": graph_ms,
                        "context_tokens": context_tokens,
                    })
                else:
                    await _send_thinking(dev_id, True, f"知识图谱无匹配结果 ({graph_ms}ms)")
                await _send_thinking(dev_id, False)
            except Exception as exc:
                await _send_thinking(dev_id, False)
                log.warning("MatrixoneGraph query failed: %s", exc)
                await _send_chat(dev_id, f"⚠ 知识图谱查询失败: {exc}", role="system")

    # Classify intent: explicit feature request → pipeline, otherwise → chat
    if _is_feature_request(prompt):
        # Include recent chat history so pipeline LLM understands context
        history = _chat_history.get(dev_id, [])
        context_desc = prompt
        if history:
            recent = history[-6:]  # last 3 turns
            lines = []
            for h in recent:
                role = "用户" if h["role"] == "user" else "Manon"
                lines.append(f"{role}: {h['content'][:300]}")
            context_desc = "## 对话上下文\n" + "\n".join(lines) + f"\n\n## 当前需求\n{prompt}"
        await _start_feature(dev_id, {
            "description": context_desc,
            "projectId": project_id,
        })
        return

    # Chat mode (default) — answer directly with graph context
    history = _chat_history.setdefault(dev_id, [])
    history.append({"role": "user", "content": prompt})
    if len(history) > 40:
        history[:] = history[-40:]

    system = _MANON_SYSTEM
    if project_name or project_path:
        system += f"\n\n## 当前项目\n项目名称: {project_name}\n项目路径: {project_path}"
    if graph_context:
        system += f"\n\n## 项目知识图谱上下文\n\n{graph_context}"
    elif project_path:
        system += "\n\n（知识图谱未返回相关上下文，请基于你的通用知识回答）"

    messages = [{"role": "system", "content": system}] + history

    await _send_thinking(dev_id, True, f"调用 LLM (~{context_tokens/1000:.1f}k tokens 上下文)...")
    try:
        t0 = time.monotonic()
        await _send_dev(dev_id, {"type": "coach-stream-start"})
        full_reasoning = ""
        full_content = ""
        async for chunk in llm_chat_stream(messages, max_tokens=4096):
            if chunk["type"] == "reasoning":
                full_reasoning += chunk["delta"]
                await _send_dev(dev_id, {"type": "coach-reasoning-delta", "delta": chunk["delta"]})
            else:
                full_content += chunk["delta"]
                await _send_dev(dev_id, {"type": "coach-content-delta", "delta": chunk["delta"]})
            await asyncio.sleep(0)  # flush WS between chunks
        llm_ms = int((time.monotonic() - t0) * 1000)
        history.append({"role": "assistant", "content": full_content})
        await _send_thinking(dev_id, True, f"LLM 响应完成 ({llm_ms/1000:.1f}s)")
        await _send_thinking(dev_id, False)
        await _send_dev(dev_id, {"type": "coach-stream-end"})
    except Exception as exc:
        await _send_thinking(dev_id, False)
        await _send_dev(dev_id, {"type": "coach-stream-end"})
        await _send_chat(dev_id, f"LLM 调用失败：{exc}", role="system")


# ---- Report generation ----

REPORTS_DIR = Path(__file__).resolve().parent.parent / "static" / "reports"

_REPORT_CSS = """
body{font-family:'Segoe UI','Noto Sans SC',sans-serif;max-width:860px;margin:40px auto;padding:0 24px;color:#1a1a1a;line-height:1.7}
h1{font-size:22px;border-bottom:2px solid #4a90e2;padding-bottom:8px;margin-bottom:20px}
h2{font-size:16px;color:#4a90e2;margin:24px 0 10px;border-left:4px solid #4a90e2;padding-left:10px}
table{width:100%;border-collapse:collapse;margin:10px 0;font-size:13px}
th,td{padding:6px 10px;border:1px solid #ddd;text-align:left}
th{background:#f5f7fa;font-weight:600}
ul{padding-left:20px}
li{margin:3px 0}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;color:#fff}
.badge-ok{background:#34c759}.badge-fail{background:#ff3b30}.badge-skip{background:#f5a623}
pre{background:#f5f7fa;padding:10px;border-radius:6px;overflow-x:auto;font-size:12px}
.meta{color:#888;font-size:12px;margin-bottom:20px}
"""


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def generate_report(state: FeatureState) -> None:
    """Build an HTML report from pipeline state and notify the frontend."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    date_str = now.strftime("%Y%m%d_%H%M")
    title = (state.spec or {}).get("title", state.description[:40]) or state.feature_id
    safe_name = f"{state.feature_id}_{date_str}"

    # ── Build HTML sections ──
    parts: list[str] = []
    parts.append(f"<h1>{_esc(title)}</h1>")
    parts.append(f'<div class="meta">Feature #{_esc(state.feature_id)} · {now.strftime("%Y-%m-%d %H:%M")}</div>')

    # Description
    parts.append("<h2>需求描述</h2>")
    parts.append(f"<p>{_esc(state.description)}</p>")

    # Clarification Q&A
    if state.conversation_history:
        parts.append("<h2>需求对齐</h2>")
        parts.append("<table><tr><th>问题</th><th>回答</th></tr>")
        for qa in state.conversation_history:
            parts.append(f"<tr><td>{_esc(qa.get('question',''))}</td><td>{_esc(qa.get('answer',''))}</td></tr>")
        parts.append("</table>")

    # Spec
    spec = state.spec or {}
    if spec:
        parts.append("<h2>功能规格</h2>")
        parts.append(f"<p><strong>范围:</strong> {_esc(spec.get('scope',''))}</p>")
        reqs = spec.get("requirements", [])
        if reqs:
            parts.append("<ul>")
            for r in reqs:
                parts.append(f"<li>[{_esc(r.get('priority','MUST'))}] {_esc(r.get('title',''))}</li>")
            parts.append("</ul>")

    # Design
    design = state.design or {}
    if design:
        parts.append("<h2>架构设计</h2>")
        parts.append(f"<p><strong>方案:</strong> {_esc(design.get('approach',''))}</p>")
        fc = design.get("fileChanges", [])
        if fc:
            parts.append("<table><tr><th>文件</th><th>操作</th><th>说明</th></tr>")
            for f in fc:
                parts.append(f"<tr><td>{_esc(f.get('file',''))}</td><td>{_esc(f.get('action',''))}</td><td>{_esc(f.get('description',''))}</td></tr>")
            parts.append("</table>")

    # Tasks
    if state.tasks:
        done = sum(1 for t in state.tasks if t.get("status") == "completed")
        failed = sum(1 for t in state.tasks if t.get("status") == "failed")
        skipped = sum(1 for t in state.tasks if t.get("status") == "skipped")
        total = len(state.tasks)

        parts.append("<h2>任务执行</h2>")
        parts.append(f"<p>完成: {done} / 总计: {total} · 失败: {failed} · 跳过: {skipped}</p>")
        parts.append("<table><tr><th>#</th><th>任务</th><th>状态</th><th>涉及文件</th></tr>")
        for t in state.tasks:
            st = t.get("status", "pending")
            badge_cls = "badge-ok" if st == "completed" else "badge-fail" if st == "failed" else "badge-skip"
            files_str = ", ".join(t.get("files", []))
            parts.append(
                f'<tr><td>{t.get("id","")}</td><td>{_esc(t.get("title",""))}</td>'
                f'<td><span class="badge {badge_cls}">{_esc(st)}</span></td>'
                f'<td>{_esc(files_str)}</td></tr>'
            )
        parts.append("</table>")

    # Result summary
    parts.append("<h2>执行结果</h2>")
    parts.append(f"<p><strong>状态:</strong> {state.status.value}</p>")

    body = "\n".join(parts)
    html = f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><title>{_esc(title)}</title><style>{_REPORT_CSS}</style></head><body>{body}</body></html>"

    # Write file
    report_path = REPORTS_DIR / f"{safe_name}.html"
    report_path.write_text(html, encoding="utf-8")
    report_url = f"/static/reports/{safe_name}.html"
    log.info("Report generated: %s", report_path)

    # Notify frontend
    await _send_dev(state.dev_id, {
        "type": "feature-report",
        "featureId": state.feature_id,
        "title": title,
        "url": report_url,
        "status": state.status.value,
        "ts": now.isoformat(),
    })


# ---- Entry points called from main.py ----

async def handle_dev_message(dev_id: str, msg: dict) -> None:
    """Route incoming developer WebSocket messages."""
    msg_type = msg.get("type", "")

    if msg_type == "feature-request":
        await _start_feature(dev_id, msg)
    elif msg_type == "manon-chat":
        await _handle_manon_chat(dev_id, msg)
    elif msg_type == "user-response":
        await _handle_user_response(dev_id, msg)
    elif msg_type == "feature-plan-approved":
        await _handle_plan_approved(dev_id)
    elif msg_type == "feature-plan-rejected":
        await _handle_plan_rejected(dev_id, msg)
    else:
        await _send_dev(dev_id, {"type": "error", "message": f"Unknown message type: {msg_type}"})


async def handle_agent_result(msg: dict) -> None:
    """Handle task results from auto-fix agent (replaces handle_upstream_message)."""
    msg_type = msg.get("type", "")
    if msg_type in ("feature-task-done", "feature-task-failed"):
        feature_id = msg.get("featureId", "")
        for state in _sessions.values():
            if state.feature_id == feature_id and state._task_result_future:
                if not state._task_result_future.done():
                    state._task_result_future.set_result(msg)
                break


async def handle_upstream_message(msg: dict) -> None:
    """Legacy — redirects to handle_agent_result."""
    await handle_agent_result(msg)


# ---- Internal handlers ----

async def _start_feature(dev_id: str, msg: dict) -> None:
    from .clarify import clarify_intent

    state = _ensure_session(dev_id)
    if state.status not in (Status.IDLE, Status.DONE, Status.FAILED):
        await _send_chat(dev_id, "当前有进行中的任务，请等待完成或取消后再提交新需求。", role="system")
        return

    state.feature_id = msg.get("featureId") or str(uuid.uuid4())[:8]
    state.project_id = msg.get("projectId", "")
    state.description = msg.get("description", "")
    state.status = Status.CLARIFYING
    state.conversation_history = []
    state.spec = None
    state.design = None
    state.tasks = []
    state.current_task_idx = -1
    state.failed_attempts = 0

    await _send_chat(dev_id, f"收到您的功能需求：「{state.description}」\n\n让我确认几个细节，以便更好地实现...")
    await clarify_intent(state)


async def _handle_user_response(dev_id: str, msg: dict) -> None:
    from .clarify import clarify_intent

    state = get_session(dev_id)
    if not state:
        return

    if state.status == Status.CLARIFYING:
        state.conversation_history.append({
            "question": getattr(state, "_last_question", ""),
            "answer": msg.get("content", ""),
        })
        await clarify_intent(state)

    elif state.status == Status.EXECUTING:
        # User guidance for failed task
        content = msg.get("content", "").strip()
        if content == "跳过":
            await _skip_current_task(state)
        elif content == "取消":
            await _cancel_feature(state)
        else:
            await _retry_with_guidance(state, content)


async def _handle_plan_approved(dev_id: str) -> None:
    from .design import generate_design

    state = get_session(dev_id)
    if not state or state.status != Status.USER_CONFIRMING:
        return
    await _send_chat(dev_id, "方案已确认，开始技术设计...")
    await generate_design(state)


async def _handle_plan_rejected(dev_id: str, msg: dict) -> None:
    from .spec import finalize_spec

    state = get_session(dev_id)
    if not state or state.status != Status.USER_CONFIRMING:
        return
    reason = msg.get("reason", "")
    state.description += f"\n\n用户补充：{reason}"
    await _send_chat(dev_id, "收到修改意见，正在重新生成规格...")
    await finalize_spec(state)


async def _skip_current_task(state: FeatureState) -> None:
    from .decompose import execute_task_loop

    idx = state.current_task_idx
    if 0 <= idx < len(state.tasks):
        state.tasks[idx]["status"] = "skipped"
        await _send_dev(state.dev_id, {
            "type": "feature-task-status",
            "featureId": state.feature_id,
            "taskId": state.tasks[idx]["id"],
            "status": "skipped",
        })
    await execute_task_loop(state)


async def _cancel_feature(state: FeatureState) -> None:
    state.status = Status.FAILED
    await _send_dev(state.dev_id, {"type": "feature-failed", "featureId": state.feature_id, "reason": "User cancelled"})
    await _send_chat(state.dev_id, "功能开发已取消。", role="system")
    await generate_report(state)
    state.status = Status.IDLE


async def _retry_with_guidance(state: FeatureState, guidance: str) -> None:
    from .decompose import assign_task

    idx = state.current_task_idx
    if 0 <= idx < len(state.tasks):
        task = state.tasks[idx]
        task["instruction"] = f"{task.get('instruction', '')}\n\n## 用户补充指导\n{guidance}"
        state.failed_attempts = 0
        await assign_task(state, task)

