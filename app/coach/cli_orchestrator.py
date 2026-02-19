"""CLI Orchestrator — MatrixoneGraph → task decomposition → auto-fix agent → review.

Replaces the simple CLI subprocess approach with an intelligent pipeline:
1. MatrixoneGraph hybrid query (already done by caller)
2. LLM decomposes user request into sub-tasks
3. For each task: query graph → build context → assign to agent → review
4. Report results back to user
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..services.llm import call_glm5, parse_json_from_llm
from matrixone_graph import MatrixoneGraph
from ..ws_hub import hub
from .pipeline import FeatureState, Status, _send_chat, _send_dev, _send_thinking

log = logging.getLogger("manon.coach.cli_orchestrator")

TASK_TIMEOUT = 10 * 60  # 10 min per task
MAX_RETRIES = 2
MAX_CONTEXT_CHARS = 300_000  # ≈100K tokens

DECOMPOSE_SYSTEM = """你是 Manon 的任务拆解引擎。根据用户需求和代码知识图谱上下文，拆解为可独立执行的子任务。
规则：
1. 每个子任务涉及 3-5 个文件，有明确验收标准
2. 按依赖顺序排列（先基础后上层）
3. 每个子任务的指令+代码上下文控制在 100K token 以内
4. 渐进式披露：后续任务可引用前序产出
5. 指令具体到函数/类级别
6. graph_queries 字段指定需要从图谱补充查询的关键词
7. 如果需求简单（单文件修改、简单问答），只输出 1 个任务即可

输出严格 JSON 数组（不要 markdown 代码块包裹）：
[{"id":1,"title":"任务标题","instruction":"详细开发指令","files":["path/..."],"criteria":"验收标准","graph_queries":["关键词1","关键词2"],"order":1}]"""

REVIEW_SYSTEM = """你是 Manon 的代码审查引擎。审查 auto-fix agent 的任务执行结果。
审查维度：
1. 是否满足验收标准
2. 是否引入 bug 或安全问题
3. 代码风格是否一致

输出严格 JSON（不要 markdown 代码块包裹）：
{"passed":true/false,"summary":"一句话总结","issues":["问题1"],"suggestions":["建议1"]}"""


async def orchestrate_cli_request(
    state: FeatureState,
    prompt: str,
    graph_context: str,
    cwd: str | None,
    workspace: str | None,
) -> None:
    """Main entry — decompose → execute each task → report."""
    dev_id = state.dev_id

    try:
        # Step 1: Decompose
        await _send_thinking(dev_id, True, "拆解任务...")
        await _send_dev(dev_id, {
            "type": "cli-task-progress",
            "phase": "decomposing",
            "message": "正在分析需求并拆解任务...",
        })
        tasks = await _decompose_with_graph(state, prompt, graph_context)
        await _send_thinking(dev_id, False)

        if not tasks:
            await _send_chat(dev_id, "无法拆解任务，请尝试更具体的描述。", role="system")
            return

        state.tasks = [{"status": "pending", **t} for t in tasks]
        state.current_task_idx = -1

        # Notify frontend
        await _send_dev(dev_id, {
            "type": "cli-task-progress",
            "phase": "tasks_ready",
            "message": f"已拆解为 {len(tasks)} 个子任务",
            "tasks": [{"id": t["id"], "title": t["title"]} for t in tasks],
        })
        task_titles = "\n".join(f"  {t['id']}. {t['title']}" for t in tasks)
        await _send_chat(dev_id, f"已拆解为 {len(tasks)} 个子任务：\n{task_titles}", role="manon")

        # Step 2: Execute each task
        for i, task in enumerate(state.tasks):
            state.current_task_idx = i
            task["status"] = "in_progress"

            await _send_dev(dev_id, {
                "type": "cli-task-progress",
                "phase": "executing",
                "taskId": task["id"],
                "taskIndex": i + 1,
                "taskTotal": len(state.tasks),
                "title": task.get("title", ""),
            })

            success = await _assign_and_review(state, task, cwd, cwd)

            if success:
                task["status"] = "completed"
                await _send_dev(dev_id, {
                    "type": "feature-task-status",
                    "featureId": state.feature_id,
                    "taskId": task["id"],
                    "status": "completed",
                })
            else:
                task["status"] = "failed"
                await _send_dev(dev_id, {
                    "type": "feature-task-status",
                    "featureId": state.feature_id,
                    "taskId": task["id"],
                    "status": "failed",
                })
                await _send_chat(
                    dev_id,
                    f"任务「{task.get('title', '')}」执行失败，继续下一个任务。",
                    role="system",
                )

        # Step 3: Summary
        done = sum(1 for t in state.tasks if t["status"] == "completed")
        failed = sum(1 for t in state.tasks if t["status"] == "failed")
        await _send_dev(dev_id, {
            "type": "cli-task-progress",
            "phase": "done",
            "message": f"全部完成：{done} 成功，{failed} 失败",
        })
        await _send_chat(
            dev_id,
            f"任务执行完毕：{done}/{len(state.tasks)} 成功" +
            (f"，{failed} 个失败" if failed else "") + "。",
            role="manon",
        )

    except Exception as exc:
        await _send_thinking(dev_id, False)
        log.error("CLI orchestration failed: %s", exc, exc_info=True)
        await _send_chat(dev_id, f"编排执行失败：{exc}", role="system")


async def _decompose_with_graph(
    state: FeatureState,
    prompt: str,
    graph_context: str,
) -> list[dict]:
    """LLM decomposes user request into sub-tasks using graph context."""
    user_prompt = f"## 用户需求\n{prompt}\n"
    if graph_context:
        user_prompt += f"\n{graph_context}\n"
    user_prompt += "\n请拆解为子任务列表。"

    raw = await call_glm5(DECOMPOSE_SYSTEM, user_prompt, max_tokens=8192, timeout=90.0)
    tasks = parse_json_from_llm(raw)
    if isinstance(tasks, dict):
        tasks = [tasks]
    log.info("Decomposed CLI request into %d tasks", len(tasks))
    return tasks


async def _query_graph_for_task(
    task: dict,
    repo_path: str | None,
) -> str:
    """Query MatrixoneGraph for task-specific context using graph_queries."""
    queries = task.get("graph_queries", [])
    if not queries or not repo_path:
        return ""

    parts: list[str] = []
    for q in queries[:5]:  # limit to 5 queries per task
        try:
            mg = MatrixoneGraph.get(repo_path)
            result = await mg.query(q, top_k=10, depth=1)
            if result.context:
                parts.append(f"### {q}\n{result.context}")
        except Exception as exc:
            log.debug("Graph query '%s' failed: %s", q, exc)

    return "\n\n".join(parts) if parts else ""


async def _build_task_context(
    task: dict,
    graph_context: str,
    state: FeatureState,
    repo_path: str | None,
) -> str:
    """Assemble full context for a single task, capped at MAX_CONTEXT_CHARS."""
    parts: list[str] = []

    # Task instruction
    parts.append(f"## 当前任务\n标题：{task.get('title', '')}\n指令：{task.get('instruction', '')}")
    parts.append(f"验收标准：{task.get('criteria', '')}")
    if task.get("files"):
        parts.append(f"涉及文件：{', '.join(task['files'])}")

    # Previous task summaries
    completed = [t for t in state.tasks if t.get("status") == "completed"]
    if completed:
        summaries = "\n".join(
            f"  - {t.get('title', '')}: {t.get('review_summary', '已完成')}"
            for t in completed
        )
        parts.append(f"## 前序任务产出\n{summaries}")

    # Task-specific graph context
    task_graph = await _query_graph_for_task(task, repo_path)
    if task_graph:
        parts.append(f"## 任务相关图谱上下文\n{task_graph}")

    # Original graph context (truncated if needed)
    if graph_context:
        parts.append(f"## 项目知识图谱上下文\n{graph_context}")

    context = "\n\n".join(parts)
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n[上下文已截断]"
    return context


async def _assign_and_review(
    state: FeatureState,
    task: dict,
    repo_path: str | None,
    cwd: str | None,
) -> bool:
    """Assign task to agent, wait for result, review. Retry up to MAX_RETRIES."""
    dev_id = state.dev_id

    # Build context
    graph_context = ""
    # Reconstruct from state if available
    completed = [t for t in state.tasks if t.get("status") == "completed"]
    context = await _build_task_context(task, graph_context, state, repo_path)

    # Resolve repo info
    repo_path = ""
    test_command = ""
    if state.project_id:
        from ..db import db_pool
        async with db_pool() as db:
            row = await db.execute_fetchone(
                "SELECT local_path, test_command FROM projects WHERE id = ?",
                (state.project_id,),
            )
            if row:
                repo_path = row["local_path"] or ""
                test_command = row["test_command"] or ""

    assign_msg = {
        "type": "feature-task-assign",
        "featureId": state.feature_id,
        "taskId": task["id"],
        "instruction": task.get("instruction") or task.get("title", ""),
        "scopedFiles": task.get("files", []),
        "context": context,
        "repoPath": repo_path or cwd or "",
        "workspace": "",
        "testCommand": test_command,
        "skipGraph": True,
    }

    for attempt in range(MAX_RETRIES + 1):
        if not hub.has_agents:
            await _send_chat(dev_id, "等待 auto-fix agent 连接...", role="system")
            for _ in range(60):
                if hub.has_agents:
                    break
                await asyncio.sleep(1)
            if not hub.has_agents:
                log.error("No agents connected after 60s")
                return False

        await _send_thinking(dev_id, True, f"执行任务 {task.get('title', '')}...")
        await hub.broadcast_to_agents(assign_msg)
        result = await _wait_for_result(state, task["id"])
        await _send_thinking(dev_id, False)

        if result is None:
            log.warning("Task %s timed out (attempt %d)", task["id"], attempt + 1)
            if attempt < MAX_RETRIES:
                assign_msg["instruction"] += "\n\n## 上次超时\n请简化方案重试。"
            continue

        if result.get("type") == "feature-task-done":
            agent_output = result.get("output", result.get("reason", ""))
            # Review
            review = await _review_result(state, task, agent_output)
            await _send_dev(dev_id, {
                "type": "cli-review-result",
                "taskId": task["id"],
                "passed": review.get("passed", True),
                "summary": review.get("summary", ""),
                "issues": review.get("issues", []),
            })

            if review.get("passed", True):
                task["review_summary"] = review.get("summary", "通过")
                return True

            # Review failed — retry
            if attempt < MAX_RETRIES:
                issues = "\n".join(f"- {i}" for i in review.get("issues", []))
                assign_msg["instruction"] += f"\n\n## Review 不通过\n{review.get('summary', '')}\n{issues}\n请修复后重试。"
                await _send_chat(
                    dev_id,
                    f"任务「{task.get('title', '')}」review 不通过：{review.get('summary', '')}，重试中...",
                    role="system",
                )
            continue

        # Task failed
        reason = result.get("reason", "unknown")
        log.warning("Task %s failed: %s (attempt %d)", task["id"], reason, attempt + 1)
        if attempt < MAX_RETRIES:
            assign_msg["instruction"] += f"\n\n## 上次失败\n原因：{reason}\n请换一种方式重试。"

    return False


async def _review_result(
    state: FeatureState,
    task: dict,
    agent_output: str,
) -> dict:
    """LLM reviews agent output against task criteria."""
    user_prompt = (
        f"## 任务\n标题：{task.get('title', '')}\n"
        f"验收标准：{task.get('criteria', '')}\n\n"
        f"## Agent 输出\n{agent_output[:8000]}\n\n"
        "请审查并输出 JSON。"
    )
    try:
        raw = await call_glm5(REVIEW_SYSTEM, user_prompt, max_tokens=2048, timeout=60.0)
        return parse_json_from_llm(raw)
    except Exception as exc:
        log.warning("Review LLM call failed: %s", exc)
        return {"passed": True, "summary": "Review 跳过（LLM 调用失败）", "issues": []}


async def _wait_for_result(state: FeatureState, task_id: int) -> dict | None:
    """Wait for task result from agent with timeout."""
    loop = asyncio.get_event_loop()
    state._task_result_future = loop.create_future()
    try:
        return await asyncio.wait_for(state._task_result_future, timeout=TASK_TIMEOUT)
    except asyncio.TimeoutError:
        return None
    finally:
        state._task_result_future = None
