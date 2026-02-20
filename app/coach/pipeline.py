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

    # If pipeline is active, route as user-response
    state = get_session(dev_id)
    if state and state.status not in (Status.IDLE, Status.DONE, Status.FAILED):
        await _handle_user_response(dev_id, {"content": prompt})
        return

    # Query graph for context
    graph_context = ""
    context_tokens = 0
    if project_id:
        from ..db import db_pool
        async with db_pool() as db:
            row = await db.execute_fetchone(
                "SELECT local_path FROM projects WHERE id = ?", (project_id,),
            )
        if row and row["local_path"]:
            try:
                await _send_thinking(dev_id, True, "查询知识图谱...")
                t0 = time.monotonic()
                mg = MatrixoneGraph.get(row["local_path"])
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
    if graph_context:
        system += f"\n\n## 项目知识图谱上下文\n\n{graph_context}"

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
    elif msg_type in ("claude-chat", "codebuddy-chat"):
        await _handle_cli_chat(dev_id, msg)
    elif msg_type == "cli-init":
        await _handle_cli_init(dev_id, msg)
    elif msg_type == "pty-start":
        await _handle_pty_start(dev_id, msg)
    elif msg_type == "pty-input":
        await _handle_pty_input(dev_id, msg)
    elif msg_type == "pty-resize":
        await _handle_pty_resize(dev_id, msg)
    elif msg_type == "pty-stop":
        await _handle_pty_stop(dev_id)
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


async def _handle_cli_init(dev_id: str, msg: dict) -> None:
    """Check CLI availability + MatrixoneGraph status when user switches to CLI mode."""
    import json as _json
    import shutil
    from matrixone_graph import MatrixoneGraph

    cli = msg.get("cli", "claude")
    project_id = msg.get("projectId", "")

    # Check CLI binary
    bin_name = "claude" if cli == "claude" else "codebuddy"
    cli_available = shutil.which(bin_name) is not None

    # Check project graph status
    graph_status = "no_project"
    graph_stats = {}
    workspace = None
    local_path = None
    if project_id:
        from ..db import db_pool
        async with db_pool() as db:
            row = await db.execute_fetchone(
                "SELECT workspace, local_path, index_stats FROM projects WHERE id = ?",
                (project_id,),
            )
            if row:
                workspace = row["workspace"]
                local_path = row["local_path"]
                if row["index_stats"]:
                    try:
                        graph_stats = _json.loads(row["index_stats"])
                    except Exception:
                        pass
                if local_path:
                    try:
                        mg = MatrixoneGraph.get(local_path)
                        s = mg.status()
                        graph_status = "connected" if s.get("indexed") else "disconnected"
                        log.info("Graph status for %s: %s", local_path, graph_status)
                    except Exception as exc:
                        graph_status = "disconnected"
                        log.warning("Graph status check failed for %s: %s", local_path, exc)
                else:
                    graph_status = "not_indexed"

    await _send_dev(dev_id, {
        "type": "cli-ready",
        "cli": cli,
        "available": cli_available,
        "graphStatus": graph_status,
        "workspace": workspace or "",
        "entities": graph_stats.get("entities", 0),
        "relations": graph_stats.get("relations", 0),
    })


_CLI_SYSTEM = {
    "claude": """You are Claude Code, an AI coding assistant by Anthropic. You help developers understand code, debug issues, and answer technical questions.
Answer concisely and professionally. Use the project context provided when relevant.""",
    "codebuddy": """你是 CodeBuddy，一个 AI 编程助手。帮助开发者理解代码、调试问题、回答技术问题。
回答简洁、专业，用中文。""",
}


async def _handle_cli_chat(dev_id: str, msg: dict) -> None:
    """Handle claude-chat / codebuddy-chat.

    Questions → streaming LLM chat (same UX as Manon).
    Feature requests → native CLI subprocess.
    """
    import time
    from datetime import datetime
    from matrixone_graph import MatrixoneGraph
    from ..services.llm import llm_chat_stream

    cli = "claude" if msg.get("type") == "claude-chat" else "codebuddy"
    prompt = msg.get("content", "").strip()
    if not prompt:
        return

    # Resolve project cwd
    cwd = None
    project_id = msg.get("projectId", "")
    if project_id:
        from ..db import db_pool
        async with db_pool() as db:
            row = await db.execute_fetchone(
                "SELECT local_path FROM projects WHERE id = ?", (project_id,),
            )
            if row:
                cwd = row["local_path"]

    # Graph context query
    graph_context = ""
    context_tokens = 0
    if cwd:
        try:
            await _send_thinking(dev_id, True, "查询知识图谱...")
            t0 = time.monotonic()
            mg = MatrixoneGraph.get(cwd)
            result = await mg.query(prompt, top_k=10, depth=1)
            graph_ms = int((time.monotonic() - t0) * 1000)
            if result.context:
                graph_context = result.context
                context_tokens = len(graph_context) // 2
                await _send_thinking(dev_id, True, f"知识图谱返回 ~{context_tokens/1000:.1f}k tokens ({graph_ms}ms)")
                await _send_dev(dev_id, {
                    "type": "llm-query", "caller": f"{cli}-chat",
                    "command": "matrixone_graph.query(hybrid)",
                    "query": prompt[:120], "ts": datetime.now().isoformat(),
                    "duration_ms": graph_ms, "context_tokens": context_tokens,
                })
            await _send_thinking(dev_id, False)
        except Exception as exc:
            await _send_thinking(dev_id, False)
            log.warning("MatrixoneGraph query failed for %s chat: %s", cli, exc)

    # ── Default: chat → streaming LLM ──
    if not _is_feature_request(prompt):
        # Default: question / chat → streaming LLM chat (same as Manon)
        history = _chat_history.setdefault(dev_id, [])
        history.append({"role": "user", "content": prompt})
        if len(history) > 40:
            history[:] = history[-40:]

        system = _CLI_SYSTEM.get(cli, _MANON_SYSTEM)
        if graph_context:
            system += f"\n\n## 项目知识图谱上下文\n\n{graph_context}"

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
                await asyncio.sleep(0)
            llm_ms = int((time.monotonic() - t0) * 1000)
            history.append({"role": "assistant", "content": full_content})
            await _send_thinking(dev_id, True, f"LLM 响应完成 ({llm_ms/1000:.1f}s)")
            await _send_thinking(dev_id, False)
            await _send_dev(dev_id, {"type": "coach-stream-end"})
        except Exception as exc:
            await _send_thinking(dev_id, False)
            await _send_dev(dev_id, {"type": "coach-stream-end"})
            await _send_chat(dev_id, f"LLM 调用失败：{exc}", role="system")
        return

    # ── Feature request → native CLI subprocess ──
    from ..services.claude_cli import run_cli_chat

    full_prompt = prompt
    if graph_context:
        full_prompt = f"## 项目知识图谱上下文\n\n{graph_context}\n\n## 用户需求\n{prompt}"

    await _send_thinking(dev_id, True, f"调用 {cli} CLI...")

    async def on_output(line: str) -> None:
        await _send_dev(dev_id, {"type": "cli-stream", "content": line, "cli": cli})

    try:
        result = await run_cli_chat(cli, full_prompt, cwd=cwd, on_output=on_output)
        await _send_thinking(dev_id, False)
        if not result.strip():
            await _send_chat(dev_id, f"{cli} 未返回内容。", role="system")
    except Exception as exc:
        await _send_thinking(dev_id, False)
        log.error("%s CLI failed: %s", cli, exc)
        await _send_chat(dev_id, f"{cli} 执行失败：{exc}", role="system")


# ---- PTY handlers (native CLI terminal) ----

async def _handle_pty_start(dev_id: str, msg: dict) -> None:
    """Spawn an interactive CLI in a PTY and stream output to the frontend."""
    from ..services.pty_manager import pty_mgr

    cli = msg.get("cli", "claude")
    cols = msg.get("cols", 120)
    rows = msg.get("rows", 40)

    # Resolve project cwd
    cwd = None
    project_id = msg.get("projectId", "")
    if project_id:
        from ..db import db_pool
        async with db_pool() as db:
            row = await db.execute_fetchone(
                "SELECT local_path FROM projects WHERE id = ?", (project_id,),
            )
            if row:
                cwd = row["local_path"]

    async def on_output(data: str) -> None:
        await _send_dev(dev_id, {"type": "pty-output", "data": data})

    try:
        await pty_mgr.spawn(dev_id, cli, cwd=cwd, cols=cols, rows=rows, on_output=on_output)
        await _send_dev(dev_id, {"type": "pty-started", "cli": cli})
        log.info("PTY started for %s: cli=%s cwd=%s", dev_id, cli, cwd)
    except FileNotFoundError:
        await _send_dev(dev_id, {"type": "pty-error", "message": f"CLI not found: {cli}"})
    except Exception as exc:
        log.error("PTY spawn failed: %s", exc)
        await _send_dev(dev_id, {"type": "pty-error", "message": str(exc)})


async def _handle_pty_input(dev_id: str, msg: dict) -> None:
    """Forward user keystrokes to the PTY."""
    from ..services.pty_manager import pty_mgr

    session = pty_mgr.get(dev_id)
    if session:
        session.write(msg.get("data", ""))


async def _handle_pty_resize(dev_id: str, msg: dict) -> None:
    """Resize the PTY to match the frontend terminal dimensions."""
    from ..services.pty_manager import pty_mgr

    session = pty_mgr.get(dev_id)
    if session:
        session.resize(msg.get("cols", 120), msg.get("rows", 40))


async def _handle_pty_stop(dev_id: str) -> None:
    """Kill the PTY session."""
    from ..services.pty_manager import pty_mgr

    await pty_mgr.kill(dev_id)
    await _send_dev(dev_id, {"type": "pty-stopped"})
