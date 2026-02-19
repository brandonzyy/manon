"""Coach pipeline state machine — orchestrates clarify → spec → design → decompose → execute.

Mirrors donnie/agent/lib/coach-feature.js but in async Python.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
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


def _is_question(prompt: str) -> bool:
    """Heuristic: detect if the prompt is a question rather than a feature request."""
    p = prompt.strip()
    if p.endswith("?") or p.endswith("？"):
        return True
    q_starts = (
        "什么", "为什么", "怎么", "如何", "哪", "是不是", "能不能", "有没有",
        "请问", "请解释", "请说明", "请分析", "解释", "说明", "分析", "介绍",
        "看看", "查看", "了解", "告诉我", "帮我看",
    )
    for q in q_starts:
        if p.startswith(q):
            return True
    return False


async def _handle_manon_chat(dev_id: str, msg: dict) -> None:
    """Unified Manon handler — answers questions or auto-starts pipeline."""
    import time
    from datetime import datetime
    from matrixone_graph import MatrixoneGraph
    from ..services.llm import call_glm5

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
                    await _send_thinking(dev_id, True, f"知识图谱返回 ~{context_tokens} tokens ({graph_ms}ms)")
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

    # Classify intent: question → chat, otherwise → feature pipeline
    if not _is_question(prompt):
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

    # Chat mode — answer directly with graph context
    history = _chat_history.setdefault(dev_id, [])
    history.append({"role": "user", "content": prompt})
    if len(history) > 40:
        history[:] = history[-40:]

    system = _MANON_SYSTEM
    if graph_context:
        system += f"\n\n## 项目知识图谱上下文\n\n{graph_context}"

    messages = [{"role": "system", "content": system}] + history

    await _send_thinking(dev_id, True, f"调用 LLM (~{context_tokens} tokens 上下文)...")
    try:
        t0 = time.monotonic()
        reply = await call_glm5(None, None, messages=messages, max_tokens=4096)
        llm_ms = int((time.monotonic() - t0) * 1000)
        history.append({"role": "assistant", "content": reply})
        await _send_thinking(dev_id, True, f"LLM 完成 ({llm_ms}ms)")
        await _send_thinking(dev_id, False)
        await _send_chat(dev_id, reply)
    except Exception as exc:
        await _send_thinking(dev_id, False)
        await _send_chat(dev_id, f"LLM 调用失败：{exc}", role="system")


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
                    except Exception:
                        graph_status = "disconnected"
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


async def _handle_cli_chat(dev_id: str, msg: dict) -> None:
    """Handle claude-chat / codebuddy-chat — orchestrated pipeline.

    Flow: LoomGraph query → task decomposition → agent execution → review.
    """
    from datetime import datetime
    from matrixone_graph import MatrixoneGraph
    from .cli_orchestrator import orchestrate_cli_request

    cli = "claude" if msg.get("type") == "claude-chat" else "codebuddy"
    prompt = msg.get("content", "")
    if not prompt:
        return

    # Resolve project cwd + workspace
    cwd = None
    workspace = None
    project_id = msg.get("projectId", "")
    if project_id:
        from ..db import db_pool
        async with db_pool() as db:
            row = await db.execute_fetchone(
                "SELECT local_path, workspace FROM projects WHERE id = ?", (project_id,),
            )
            if row:
                cwd = row["local_path"]
                workspace = row["workspace"]

    # ── Mandatory MatrixoneGraph query ──
    graph_context = ""
    if cwd:
        try:
            await _send_thinking(dev_id, True, "查询知识图谱...")
            mg = MatrixoneGraph.get(cwd)
            result = await mg.query(prompt, top_k=10, depth=1)
            if result.context:
                graph_context = f"\n\n## 项目知识图谱上下文 (MatrixoneGraph)\n\n{result.context}\n"
                await _send_dev(dev_id, {
                    "type": "llm-query",
                    "caller": f"{cli}-chat",
                    "command": "matrixone_graph.query(hybrid)",
                    "query": prompt[:120],
                    "ts": datetime.now().isoformat(),
                })
            await _send_thinking(dev_id, False)
        except Exception as exc:
            await _send_thinking(dev_id, False)
            log.warning("MatrixoneGraph query failed for %s chat: %s", cli, exc)

    # ── Orchestrated pipeline ──
    state = _ensure_session(dev_id)
    state.feature_id = state.feature_id or str(uuid.uuid4())[:8]
    state.project_id = project_id
    state.status = Status.EXECUTING

    await orchestrate_cli_request(state, prompt, graph_context, cwd, workspace)
    state.status = Status.IDLE
