"""Task decomposition + execution loop — split spec into tasks, assign to Manus worker pool.

Replaces the old WebSocket-based auto-fix dispatch with direct worker_pool.submit() calls.
Supports order-based parallel scheduling: same-order tasks run concurrently via asyncio.gather().
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from ..services.llm import call_glm5, parse_json_from_llm
from .pipeline import FeatureState, Status, _send_chat, _send_dev, _send_thinking, generate_report

log = logging.getLogger("manon.coach.decompose")

SYSTEM_PROMPT = """你是 Manon 的技术架构师。将功能需求拆分为可独立开发和验证的子任务。

每个 task 应该：对应一个可独立验证的功能点，涉及 3-5 个文件，有明确验收标准。

每个 task 应有 order 字段表示执行顺序。相同 order 的任务可以并行执行，不同 order 按顺序执行。
例如：order=1 的任务都是独立的基础工作，order=2 的任务依赖 order=1 的结果。

输出严格 JSON 数组（不要 markdown 代码块包裹）：
[{"id":1,"title":"任务标题","instruction":"详细开发指令","files":["path/..."],"criteria":"验收标准","order":1}]"""

TASK_TIMEOUT = 10 * 60  # 10 min per task
MAX_RETRIES = 2


async def decompose_to_tasks(state: FeatureState) -> None:
    log.info("Decomposing feature #%s into tasks", state.feature_id)
    state.status = Status.DECOMPOSING

    spec = state.spec or {}
    req_text = "\n".join(
        f"{r['id']}. [{r.get('priority','MUST')}] {r['title']}\n" +
        "\n".join(f"  - {s['title']}: Given {s['given']} / When {s['when']} / Then {s['then']}" for s in r.get("scenarios", []))
        for r in spec.get("requirements", [])
    )
    design_ctx = ""
    if state.design:
        d = state.design
        files = "; ".join(f"{f['file']} ({f['action']}): {f['description']}" for f in d.get("fileChanges", []))
        design_ctx = f"\n\n## 技术设计\n方案：{d.get('approach','')}\n文件变更：{files}"

    user_prompt = f"## 功能规格\n标题：{spec.get('title','')}\n范围：{spec.get('scope','')}\n需求与场景：\n{req_text}{design_ctx}"

    try:
        await _send_thinking(state.dev_id, True, "正在拆解开发任务...")
        raw = await call_glm5(SYSTEM_PROMPT, user_prompt, max_tokens=8192, timeout=90.0)
        await _send_thinking(state.dev_id, False)
        tasks = parse_json_from_llm(raw)
        state.tasks = [{"status": "pending", **t} for t in tasks]
        state.current_task_idx = -1
        log.info("Decomposed into %d tasks", len(tasks))
        await _send_dev(state.dev_id, {"type": "feature-tasks-ready", "featureId": state.feature_id, "tasks": state.tasks})
        await execute_task_loop(state)
    except Exception as exc:
        await _send_thinking(state.dev_id, False)
        log.error("Task decomposition failed: %s", exc)
        state.status = Status.FAILED
        await _send_dev(state.dev_id, {"type": "feature-failed", "featureId": state.feature_id, "reason": str(exc)})
        await generate_report(state)
        state.status = Status.IDLE


def _group_tasks_by_order(tasks: list[dict]) -> dict[int, list[dict]]:
    """Group tasks by their 'order' field. Tasks without order default to their index."""
    groups: dict[int, list[dict]] = defaultdict(list)
    for i, task in enumerate(tasks):
        order = task.get("order", i + 1)
        groups[order].append(task)
    return dict(groups)


async def execute_task_loop(state: FeatureState) -> None:
    """Execute tasks by order groups — same-order tasks run in parallel."""
    log.info("Starting task loop for feature #%s", state.feature_id)
    state.status = Status.EXECUTING

    # Group tasks by order
    groups = _group_tasks_by_order(state.tasks)

    for order in sorted(groups.keys()):
        group = [t for t in groups[order] if t.get("status") != "skipped"]
        if not group:
            continue

        # Mark all tasks in this group as in_progress
        for task in group:
            task["status"] = "in_progress"
            state.current_task_idx = state.tasks.index(task)
            await _send_dev(state.dev_id, {
                "type": "feature-task-status", "featureId": state.feature_id,
                "taskId": task["id"], "status": "in_progress",
            })

        log.info("Executing order=%d group: %d task(s): %s",
                 order, len(group), ", ".join(t.get("title", "") for t in group))

        if len(group) == 1:
            # Single task — run directly
            task = group[0]
            success = await assign_task(state, task)
            if not success:
                task["status"] = "failed"
                await _send_dev(state.dev_id, {
                    "type": "feature-task-status", "featureId": state.feature_id,
                    "taskId": task["id"], "status": "failed",
                })
                await _send_chat(
                    state.dev_id,
                    f"任务「{task.get('title','')}」开发失败，需要人工介入。\n"
                    "回复\"跳过\"跳过此任务 / \"取消\"取消整个功能 / 或提供额外指导",
                    role="system",
                )
                return
            task["status"] = "completed"
            await _send_dev(state.dev_id, {
                "type": "feature-task-status", "featureId": state.feature_id,
                "taskId": task["id"], "status": "completed",
            })
            state.failed_attempts = 0
        else:
            # Multiple tasks — run in parallel
            results = await asyncio.gather(
                *[assign_task(state, t) for t in group],
                return_exceptions=True,
            )
            any_failed = False
            for task, result in zip(group, results):
                if isinstance(result, Exception) or not result:
                    task["status"] = "failed"
                    await _send_dev(state.dev_id, {
                        "type": "feature-task-status", "featureId": state.feature_id,
                        "taskId": task["id"], "status": "failed",
                    })
                    any_failed = True
                else:
                    task["status"] = "completed"
                    await _send_dev(state.dev_id, {
                        "type": "feature-task-status", "featureId": state.feature_id,
                        "taskId": task["id"], "status": "completed",
                    })

            if any_failed:
                failed_names = ", ".join(t.get("title", "") for t in group if t.get("status") == "failed")
                await _send_chat(
                    state.dev_id,
                    f"并行任务组中有失败：「{failed_names}」，需要人工介入。\n"
                    "回复\"跳过\"跳过失败任务继续 / \"取消\"取消整个功能 / 或提供额外指导",
                    role="system",
                )
                return

            state.failed_attempts = 0

    # All tasks done
    log.info("All %d tasks completed for feature #%s", len(state.tasks), state.feature_id)
    state.status = Status.DONE
    await _send_dev(state.dev_id, {"type": "feature-done", "featureId": state.feature_id})
    await _send_chat(state.dev_id, "所有任务已完成！", role="system")
    await generate_report(state)


async def assign_task(state: FeatureState, task: dict) -> bool:
    """Submit task to Manus worker pool, wait for result. Retry up to MAX_RETRIES."""
    log.info("Assigning task %s to Manus worker: %s", task.get("id"), task.get("title", ""))

    # Build context from spec + design + completed tasks
    parts: list[str] = []
    if state.spec:
        parts.append(f"Feature: {state.spec.get('title','')}\nScope: {state.spec.get('scope','')}")
    if state.design:
        parts.append(f"Design: {state.design.get('approach','')}")
    completed = [t for t in state.tasks if t.get("status") == "completed"]
    if completed:
        parts.append(f"Completed tasks: {', '.join(t.get('title','') for t in completed)}")
    context = "\n".join(parts)

    # Look up project for repoPath/workspace/testCommand
    repo_path = ""
    workspace = ""
    test_command = ""
    if state.project_id:
        from ..db import db_pool
        async with db_pool() as db:
            row = await db.execute_fetchone("SELECT local_path, workspace, test_command FROM projects WHERE id = ?", (state.project_id,))
            if row:
                repo_path = row["local_path"] or ""
                workspace = row["workspace"] or ""
                test_command = row["test_command"] or ""

    task_config = {
        "featureId": state.feature_id,
        "taskId": task["id"],
        "instruction": task.get("instruction") or task.get("title", ""),
        "scopedFiles": task.get("files", []),
        "context": context,
        "repoPath": repo_path,
        "workspace": workspace,
        "testCommand": test_command,
    }

    from ..worker import worker_pool

    for attempt in range(MAX_RETRIES + 1):
        # Wait between retries
        if attempt > 0:
            wait_secs = 5 * attempt
            log.info("Waiting %ds before retry attempt %d", wait_secs, attempt + 1)
            await _send_thinking(state.dev_id, True, f"等待 {wait_secs}s 后重试 (第 {attempt + 1} 次)...")
            await asyncio.sleep(wait_secs)
            await _send_thinking(state.dev_id, False)

        result = await worker_pool.submit(task_config)

        if result.get("type") == "feature-task-done":
            task["output"] = result.get("output", "")
            return True

        # Failed — retry with feedback if attempts remain
        reason = result.get("reason", "unknown")
        task["reason"] = reason
        log.warning("Task %s failed: %s (attempt %d)", task["id"], reason, attempt + 1)
        if attempt < MAX_RETRIES:
            task_config["instruction"] += f"\n\n## Previous attempt failed\nReason: {reason}\nPlease try a different approach."

    return False
