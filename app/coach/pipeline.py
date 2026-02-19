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


# ---- Entry points called from main.py ----

async def handle_dev_message(dev_id: str, msg: dict) -> None:
    """Route incoming developer WebSocket messages."""
    msg_type = msg.get("type", "")

    if msg_type == "feature-request":
        await _start_feature(dev_id, msg)
    elif msg_type == "user-response":
        await _handle_user_response(dev_id, msg)
    elif msg_type == "feature-plan-approved":
        await _handle_plan_approved(dev_id)
    elif msg_type == "feature-plan-rejected":
        await _handle_plan_rejected(dev_id, msg)
    elif msg_type in ("claude-chat", "codebuddy-chat"):
        await _handle_cli_chat(dev_id, msg)
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


async def _handle_cli_chat(dev_id: str, msg: dict) -> None:
    """Handle claude-chat / codebuddy-chat — spawn CLI, stream output back."""
    import asyncio
    from ..services.claude_cli import run_cli_chat

    cli = "claude" if msg.get("type") == "claude-chat" else "codebuddy"
    prompt = msg.get("content", "")
    if not prompt:
        return

    # Resolve project cwd
    cwd = None
    project_id = msg.get("projectId", "")
    if project_id:
        from ..db import db_pool
        async with db_pool() as db:
            row = await db.execute_fetchone("SELECT local_path FROM projects WHERE id = ?", (project_id,))
            if row:
                cwd = row["local_path"]

    await _send_thinking(dev_id, True, f"{cli} 正在处理...")

    async def on_line(line: str):
        await _send_dev(dev_id, {"type": "cli-stream", "cli": cli, "content": line})

    try:
        result = await run_cli_chat(cli, prompt, cwd=cwd, max_turns=10, on_output=on_line, timeout=300.0)
        await _send_thinking(dev_id, False)
        if result.strip():
            await _send_chat(dev_id, result.strip(), role=cli)
    except Exception as exc:
        await _send_thinking(dev_id, False)
        await _send_chat(dev_id, f"{cli} 执行失败：{exc}", role="system")
