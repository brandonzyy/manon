"""Task decomposition + execution loop — split spec into tasks, assign to worker pool.

Replaces the old WebSocket-based auto-fix dispatch with direct worker_pool.submit() calls.
Supports order-based parallel scheduling: same-order tasks run concurrently via asyncio.gather().
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from ..services.llm import call_glm5, parse_json_from_llm, llm_chat
from .pipeline import FeatureState, Status, _send_chat, _send_dev, _send_thinking, generate_report

log = logging.getLogger("manon.coach.decompose")

SYSTEM_PROMPT = """你是 Manon 的技术架构师。将需求拆分为可独立执行和验证的子任务。

每个 task 应该：对应一个可独立验证的工作项，涉及 3-5 个文件，有明确验收标准。

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
        "\n".join(f"  - {s.get('title','')}: {s.get('condition', s.get('given',''))} → {s.get('expected', s.get('then',''))}" for s in r.get("scenarios", []))
        for r in spec.get("requirements", [])
    )
    design_ctx = ""
    if state.design:
        d = state.design
        files = "; ".join(f"{f['file']} ({f['action']}): {f['description']}" for f in d.get("fileChanges", []))
        design_ctx = f"\n\n## 技术设计\n方案：{d.get('approach','')}\n文件变更：{files}"

    user_prompt = f"## 任务规格\n标题：{spec.get('title','')}\n范围：{spec.get('scope','')}\n需求与场景：\n{req_text}{design_ctx}"

    try:
        await _send_thinking(state.dev_id, True, "正在拆解子任务...")
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
        group = [t for t in groups[order] if t.get("status") not in ("skipped", "completed")]
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
                    f"任务「{task.get('title','')}」执行失败，需要人工介入。\n"
                    "回复\"重试\"重新执行 / \"跳过\"跳过此任务 / \"取消\"取消整个任务 / 或提供额外指导",
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
                    "回复\"重试\"重新执行失败任务 / \"跳过\"跳过失败任务继续 / \"取消\"取消整个任务 / 或提供额外指导",
                    role="system",
                )
                return

            state.failed_attempts = 0

    # All tasks done — run overall evaluation
    log.info("All %d tasks completed for feature #%s", len(state.tasks), state.feature_id)

    # 1. Project-level test
    test_result_text = ""
    if state.project_id:
        from ..db import db_pool
        async with db_pool() as db:
            row = await db.execute_fetchone("SELECT test_command, local_path, workspace FROM projects WHERE id = ?", (state.project_id,))
        if row and row["test_command"]:
            test_cmd = row["test_command"]
            test_cwd = row["workspace"] or row["local_path"] or ""
            try:
                await _send_thinking(state.dev_id, True, f"运行项目测试: {test_cmd}")
                proc = await asyncio.create_subprocess_shell(
                    test_cmd, cwd=test_cwd,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                out = stdout.decode(errors="replace")
                err = stderr.decode(errors="replace")
                await _send_thinking(state.dev_id, False)
                if proc.returncode == 0:
                    test_result_text = f"项目测试通过\n{out[:500]}"
                    await _send_chat(state.dev_id, "项目测试通过 ✓", role="system")
                else:
                    test_result_text = f"项目测试失败 (exit {proc.returncode})\n{(err or out)[:1000]}"
                    await _send_chat(state.dev_id, f"项目测试失败: {(err or out)[:200]}", role="system")
            except Exception as exc:
                await _send_thinking(state.dev_id, False)
                test_result_text = f"测试执行异常: {exc}"
        else:
            test_result_text = "未配置测试命令"

    # 2. LLM overall evaluation
    try:
        await _send_thinking(state.dev_id, True, "LLM 整体评价中...")
        # Build evaluation context
        task_summaries = []
        for t in state.tasks:
            diffs_preview = ""
            for fp, d in (t.get("diffs") or {}).items():
                diffs_preview += f"\n--- {fp} ---\n{d[:300]}"
            st = t.get("selfTest") or {}
            task_summaries.append(
                f"### 任务 #{t.get('id')}: {t.get('title','')}\n"
                f"状态: {t.get('status','')}\n"
                f"自测: {'通过' if st.get('passed', True) else '未通过'}\n"
                f"变更:\n{diffs_preview[:600]}"
            )

        eval_prompt = (
            f"## 原始需求\n{state.description}\n\n"
            f"## 设计方案\n{(state.design or {}).get('approach', '无')}\n\n"
            f"## 各任务执行结果\n" + "\n\n".join(task_summaries) + "\n\n"
            f"## 项目测试结果\n{test_result_text}\n\n"
            f"## 评估要求\n"
            f"评估这次任务执行的完成度和质量。输出严格 JSON（不要 markdown 包裹）：\n"
            f'{{\"score\": 1-10, \"summary\": \"总结\", \"issues\": [\"问题1\", ...], \"passed\": true/false}}'
        )
        eval_messages = [
            {"role": "system", "content": "你是代码审查专家，评估任务执行的完成度和质量。"},
            {"role": "user", "content": eval_prompt},
        ]
        eval_result = await llm_chat(eval_messages, max_tokens=2048, timeout=60.0)
        eval_text = eval_result.get("content", "").strip()
        await _send_thinking(state.dev_id, False)

        # Parse evaluation JSON
        import json as _json
        start = eval_text.find("{")
        end = eval_text.rfind("}") + 1
        if start >= 0 and end > start:
            evaluation = _json.loads(eval_text[start:end])
        else:
            evaluation = {"score": 5, "summary": eval_text[:200], "issues": [], "passed": True}

        state.evaluation = evaluation
        score = evaluation.get("score", 5)
        log.info("Evaluation: score=%d, passed=%s", score, evaluation.get("passed"))
        await _send_chat(
            state.dev_id,
            f"整体评价: {score}/10 — {evaluation.get('summary', '')[:200]}",
            role="system",
        )

        if score < 5:
            state.status = Status.FAILED
            await _send_dev(state.dev_id, {"type": "feature-failed", "featureId": state.feature_id, "reason": f"评价分数 {score}/10"})
        else:
            state.status = Status.DONE
            await _send_dev(state.dev_id, {"type": "feature-done", "featureId": state.feature_id})

    except Exception as eval_exc:
        await _send_thinking(state.dev_id, False)
        log.warning("Evaluation failed: %s", eval_exc)
        state.evaluation = {"score": 0, "summary": f"评价失败: {eval_exc}", "issues": [], "passed": True}
        state.status = Status.DONE
        await _send_dev(state.dev_id, {"type": "feature-done", "featureId": state.feature_id})

    # 3. Generate report and enter REVIEWING state
    await generate_report(state)
    state.status = Status.REVIEWING
    await _send_dev(state.dev_id, {"type": "coach-stage", "stage": "reviewing"})
    await _send_chat(state.dev_id, "报告已生成，请审核后点击「审核通过」或「驳回」。", role="system")


async def assign_task(state: FeatureState, task: dict) -> bool:
    """Submit task to worker pool, wait for result. Retry up to MAX_RETRIES."""
    log.info("Assigning task %s to worker: %s", task.get("id"), task.get("title", ""))

    # Build context from spec + design + completed tasks
    parts: list[str] = []
    if state.spec:
        parts.append(f"Task: {state.spec.get('title','')}\nScope: {state.spec.get('scope','')}")
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

    # Query knowledge graph for task-specific context
    graph_context = ""
    if repo_path:
        try:
            import time as _time
            from datetime import datetime as _dt
            from matrixone_graph import MatrixoneGraph
            mg = MatrixoneGraph.get(repo_path)
            instruction = task.get("instruction") or task.get("title", "")
            scoped_files = task.get("files", [])

            # Primary query: instruction + file hints
            query_text = instruction
            if scoped_files:
                query_text += "\n涉及文件: " + ", ".join(scoped_files[:10])
            t0 = _time.monotonic()
            result = await mg.query(query_text, top_k=10, depth=1)
            q_ms = int((_time.monotonic() - t0) * 1000)
            if result.context:
                graph_context = result.context
                ctx_tok = len(graph_context) // 2
                log.info("Graph context for task %s: %d chars (%dms)", task.get("id"), len(graph_context), q_ms)
                task_group_key = f"Task #{task.get('id')}: {task.get('title', '')}"
                await _send_dev(state.dev_id, {
                    "type": "llm-query", "caller": "manon.task",
                    "groupKey": task_group_key,
                    "command": f"任务上下文检索 (Task #{task.get('id')})",
                    "query": query_text[:120], "ts": _dt.now().isoformat(),
                    "duration_ms": q_ms,
                    "context_tokens": ctx_tok,
                })
        except Exception as exc:
            log.warning("Graph query for task %s failed: %s", task.get("id"), exc)

    task_config = {
        "featureId": state.feature_id,
        "taskId": task["id"],
        "instruction": task.get("instruction") or task.get("title", ""),
        "scopedFiles": task.get("files", []),
        "context": context,
        "graphContext": graph_context,
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

        # Forward worker's graph queries to frontend (grouped by task)
        task_group_key = f"Task #{task.get('id')}: {task.get('title', '')}"
        for wq in result.get("queries") or []:
            from datetime import datetime as _dt
            await _send_dev(state.dev_id, {
                "type": "llm-query", "caller": wq.get("caller", "worker"),
                "groupKey": task_group_key,
                "command": wq.get("command", "search_code"),
                "query": wq.get("query", ""), "ts": _dt.now().isoformat(),
                "duration_ms": wq.get("duration_ms", 0),
                "context_tokens": wq.get("context_tokens", 0),
            })

        if result.get("type") == "feature-task-done":
            task["output"] = result.get("output", "")
            task["changes"] = result.get("changes", "")
            task["diffs"] = result.get("diffs") or {}
            task["selfTest"] = result.get("selfTest") or {}
            task["tokenUsage"] = result.get("tokenUsage") or {}
            return True

        # Failed — retry with feedback if attempts remain
        reason = result.get("reason", "unknown")
        task["reason"] = reason
        log.warning("Task %s failed: %s (attempt %d)", task["id"], reason, attempt + 1)
        if attempt < MAX_RETRIES:
            task_config["instruction"] += f"\n\n## Previous attempt failed\nReason: {reason}\nPlease try a different approach."

    return False
