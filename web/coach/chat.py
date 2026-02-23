"""Coach chat handler — Manon conversational AI with graph-augmented context."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime

from shared import saas_client
from shared.ast_sync import find_project_by_repo_id

from .compact import _auto_compact, _microcompact, _history_chars
from .pipeline import (
    _send_dev, _send_thinking, _send_chat,
    _load_chat_history, _save_chat_history,
    get_session, Status,
)

log = logging.getLogger("manon.coach")

_MANON_SYSTEM = """你是 Manon（马浓），一个 AI 架构师助手。你可以：
- 回答关于项目代码的问题（基于知识图谱上下文）
- 分析代码结构、依赖关系、技术债务
- 讨论架构设计和最佳实践
- 提供重构建议、简化方案、替代设计
- 帮助理解代码逻辑和调用链

## 回答原则

你的回答应结合「项目知识图谱上下文」和你的专业知识。遵守以下规则：

1. **代码事实用上下文**：涉及项目中具体的函数实现、调用链、配置值、文件结构等事实性问题，必须基于知识图谱上下文回答。如果上下文中有明确信息，直接引用。
2. **架构建议用专业知识**：当用户询问设计方案、重构建议、简化思路、最佳实践、替代方案时，你应该结合上下文中的项目现状，运用你的软件工程专业知识给出建议。这类问题不需要拘泥于上下文。
3. **明确区分事实与建议**：回答中涉及项目代码的部分标注为事实（"根据代码..."），涉及你的建议的部分标注为建议（"建议..."或"可以考虑..."）。
4. **给出完整答案**：系统已经为你做了多轮检索确保上下文完整。你应该充分利用所有提供的上下文，给出全面、详尽的回答。不要遗漏上下文中已有的关键信息。
5. **不要编造代码事实**：不要假设项目中存在某个函数、配置或文件。如果上下文中没有，说明"当前上下文未覆盖"即可，但仍然可以基于已有信息给出架构层面的分析和建议。

回答简洁、专业，用中文。"""

_DEEPQUERY_SYSTEM = """你是一个代码知识图谱检索规划助手。你的任务是确保收集到的上下文能够完整回答用户的问题。

## 分析步骤

1. **拆解问题**：把用户的问题拆成具体的子问题/信息需求。例如用户问"审计报告是怎么生成的"，子问题可能包括：
   - 审计流程的入口函数是什么？
   - 报告生成用了哪些数据源？
   - 具体的 Prompt 是怎么构造的？
   - 输出格式是什么？

2. **逐项检查**：对每个子问题，检查已有上下文是否包含足够的代码细节（函数实现、调用链、参数、配置）。仅仅出现函数名不算覆盖，必须有实际的代码逻辑或实现细节。

3. **生成补充查询**：对未覆盖的子问题，提取最精确的查询词（函数名、类名、文件名、模块名）。优先使用已有上下文中出现但未展开的标识符。

## 关键规则

- **有 missing 就必须有 queries**：只要 missing 不为空，queries 也不能为空。从已有上下文中提取相关的函数名、类名、变量名作为查询词。
- **不要假设查不到**：即使你觉得知识图谱可能没有某个信息，也要尝试查询。宁可查了没结果，也不要跳过。
- **从上下文提取线索**：如果上下文中提到了某个类名/函数名但没有展开实现，用它作为查询词。

## 输出格式

只返回 JSON：
```json
{
  "sub_questions": ["子问题1", "子问题2", ...],
  "covered": ["已覆盖的子问题"],
  "missing": ["未覆盖的子问题"],
  "queries": ["查询词1", "查询词2"],
  "reason": "简要说明"
}
```

如果所有子问题都已覆盖：
```json
{"sub_questions": [...], "covered": [...], "missing": [], "queries": [], "reason": "上下文已完整覆盖所有子问题"}
```"""


async def _iterative_graph_query(
    dev_id: str, prompt: str, repo_id: str, initial_context: str, max_rounds: int = 3,
) -> str:
    """Perform iterative graph queries via saas/ — LLM analyzes gaps and triggers follow-up searches."""
    import json as _json
    from ..services.llm import llm_chat

    accumulated = initial_context
    for round_idx in range(max_rounds):
        plan_messages = [
            {"role": "system", "content": _DEEPQUERY_SYSTEM},
            {"role": "user", "content": f"## 用户问题\n{prompt}\n\n## 已有知识图谱上下文\n{accumulated}"},
        ]
        try:
            await _send_thinking(dev_id, True, f"完整性检查：第 {round_idx + 1} 轮自检...")
            result = await llm_chat(plan_messages, max_tokens=2048, timeout=30.0)
            text = result.get("content", "").strip()
            log.info("Deep query LLM response (round %d): %s", round_idx + 1, text[:1000])
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = _json.loads(text[start:end])
            else:
                log.warning("Deep query: no JSON found in response")
                break
            log.info("Deep query parsed: missing=%s, queries=%s", parsed.get("missing", []), parsed.get("queries", []))
            follow_ups = parsed.get("queries", [])
            missing = parsed.get("missing", [])
            if not follow_ups and missing:
                follow_ups = [m.split("（")[0].split("(")[0].strip() for m in missing[:3]]
                log.info("Auto-generated queries from missing: %s", follow_ups)
            if not follow_ups:
                await _send_thinking(dev_id, True, "完整性检查：上下文已完整覆盖所有子问题")
                break
            reason = parsed.get("reason", "")
            missing_str = "、".join(missing[:3]) if missing else reason
            await _send_thinking(dev_id, True, f"完整性检查：缺失 [{missing_str}]，补充查询 {follow_ups}")
        except Exception as exc:
            log.warning("Deep query planning failed: %s", exc)
            break

        for q in follow_ups[:3]:
            try:
                import time as _time
                t0 = _time.monotonic()
                r = await saas_client.search(repo_id, q, top_k=5, depth=1)
                q_ms = int((_time.monotonic() - t0) * 1000)
                ctx = r.get("context", "")
                if ctx:
                    accumulated += f"\n\n## 补充查询: {q}\n{ctx}"
                    ctx_tok = len(ctx) // 2
                    await _send_dev(dev_id, {
                        "type": "llm-query", "caller": "manon.deep",
                        "command": f"补充检索 (round {round_idx + 1})",
                        "query": q, "ts": datetime.now().isoformat(),
                        "duration_ms": q_ms,
                        "context_tokens": ctx_tok,
                    })
            except Exception as exc:
                log.warning("Follow-up query '%s' failed: %s", q, exc)

    return accumulated

def _is_feature_request(prompt: str) -> bool:
    """Heuristic: detect if the prompt is an explicit code-change / feature request."""
    p = prompt.strip()
    if p.startswith("/feature") or p.startswith("/pipeline") or p.startswith("/do"):
        return True
    action_starts = (
        "实现", "添加", "新增", "开发", "创建", "搭建", "构建",
        "修改", "改一下", "改成", "改为", "重构", "优化",
        "删除", "移除", "去掉",
        "修复", "修bug", "修一下", "fix",
        "写一个", "写个", "帮我写", "帮我实现", "帮我添加", "帮我开发", "帮我给",
        "帮我修改", "帮我修复", "帮我重构", "帮我优化", "帮我创建",
        "请实现", "请添加", "请修改", "请开发", "请创建",
        "把这个", "把它", "开始实现", "开始开发", "开始执行", "开始做",
    )
    for kw in action_starts:
        if p.startswith(kw):
            return True
    action_contains = (
        "帮我实现", "帮我做", "帮我开发", "帮我写", "帮我修改",
        "帮我添加", "帮我创建", "帮我修复", "帮我重构", "帮我优化",
        "按方案", "按这个方案", "按你说的", "就这样做", "就这么做",
        "那就做", "那就实现", "那就开发", "那就写",
        "来实现", "去实现", "去做", "来做",
        "开始吧", "做吧", "实现吧", "开发吧", "写吧",
        "动手吧", "搞吧", "干吧", "整吧",
        "进入开发", "进入pipeline", "启动pipeline",
    )
    for kw in action_contains:
        if kw in p:
            return True
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
    en_contains = ("let's do it", "go ahead", "start coding", "do it", "let's implement")
    for kw in en_contains:
        if kw in p_lower:
            return True
    return False

async def _handle_chat_commands(dev_id: str, prompt: str, project_id: str) -> bool:
    """Handle slash commands (/reset, /do, /compact). Returns True if handled."""
    # /reset or /cancel
    if prompt.lower() in ("/reset", "/cancel", "取消", "重置"):
        state = get_session(dev_id)
        if state and state.status not in (Status.IDLE, Status.DONE, Status.FAILED):
            state.status = Status.IDLE
            await _send_chat(dev_id, "Pipeline 已重置，回到正常对话模式。", role="system")
            await _send_dev(dev_id, {"type": "coach-thinking", "active": False})
            await _send_dev(dev_id, {"type": "coach-stage", "stage": "idle"})
            return True
        await _send_chat(dev_id, "当前没有进行中的 pipeline。", role="system")
        return True

    # /do — manual pipeline trigger
    if prompt.lower().startswith("/do"):
        extra = prompt[3:].strip()
        history = await _load_chat_history(dev_id)
        context_desc = extra if extra else ""
        if history:
            recent = history[-10:]
            lines = []
            for h in recent:
                role = "用户" if h["role"] == "user" else "Manon"
                lines.append(f"{role}: {h['content']}")
            history_block = "\n".join(lines)
            if context_desc:
                context_desc = f"## 对话上下文\n{history_block}\n\n## 当前需求\n{context_desc}"
            else:
                context_desc = f"## 对话上下文\n{history_block}\n\n## 当前需求\n请根据以上对话内容，自动识别需要执行的任务，进入工作流程。"
        elif not context_desc:
            await _send_chat(dev_id, "没有对话上下文，请先描述你的需求，或使用 `/do <需求描述>`。", role="system")
            return True
        from .pipeline import _start_feature
        await _start_feature(dev_id, {
            "description": context_desc,
            "projectId": project_id,
            "prompt": extra or None,
        })
        return True

    # /compact — manual compaction
    if prompt.lower().startswith("/compact"):
        hint = prompt[8:].strip()
        history = await _load_chat_history(dev_id)
        if not history:
            await _send_chat(dev_id, "当前没有对话历史。", role="system")
            return True
        before_chars = _history_chars(history)
        before_msgs = len(history)
        await _auto_compact(dev_id, history, force=True, focus_hint=hint)
        after_chars = _history_chars(history)
        after_msgs = len(history)
        await _save_chat_history(dev_id, project_id)
        await _send_chat(
            dev_id,
            f"对话历史已压缩：{before_msgs} 条消息 ({before_chars:,} 字符) → "
            f"{after_msgs} 条 ({after_chars:,} 字符)"
            + (f"\n重点保留：{hint}" if hint else ""),
            role="system",
        )
        return True

    return False


async def _query_graph_context(
    dev_id: str, prompt: str, project_id: str,
) -> tuple[str, int, str, str, str]:
    """Query knowledge graph for chat context. Returns (graph_context, context_tokens, project_name, project_path, repo_id)."""
    graph_context = ""
    context_tokens = 0
    project_name = ""
    project_path = ""
    repo_id = ""
    if not project_id:
        return graph_context, context_tokens, project_name, project_path, repo_id

    found = find_project_by_repo_id(project_id)
    if found:
        project_path, proj_info = found
        project_name = proj_info.get("name", "")
        repo_id = project_id
    else:
        repo_id = project_id
        try:
            repo = await saas_client.repos_get(repo_id)
            project_name = repo.get("name", "")
        except Exception:
            pass

    if not repo_id:
        return graph_context, context_tokens, project_name, project_path, repo_id

    try:
        await _send_thinking(dev_id, True, "查询知识图谱...")
        t0 = time.monotonic()
        result = await saas_client.search(repo_id, prompt, top_k=10, depth=1)
        graph_ms = int((time.monotonic() - t0) * 1000)
        ctx = result.get("context", "")
        context_tokens = len(ctx) // 2 if ctx else 0
        await _send_dev(dev_id, {
            "type": "llm-query", "caller": "manon",
            "command": "saas_client.search(hybrid)",
            "query": prompt[:120], "ts": datetime.now().isoformat(),
            "duration_ms": graph_ms,
            "context_tokens": context_tokens,
        })
        if ctx:
            graph_context = ctx
            await _send_thinking(dev_id, True, f"知识图谱返回 ~{context_tokens/1000:.1f}k tokens ({graph_ms}ms)")
            try:
                graph_context = await _iterative_graph_query(
                    dev_id, prompt, repo_id, graph_context, max_rounds=2,
                )
                context_tokens = len(graph_context) // 2
            except Exception as exc:
                log.warning("Iterative graph query failed: %s", exc)
        else:
            await _send_thinking(dev_id, True, f"知识图谱无匹配结果 ({graph_ms}ms)")
        await _send_thinking(dev_id, False)
    except Exception as exc:
        await _send_thinking(dev_id, False)
        log.warning("saas_client.search failed: %s", exc)
        await _send_chat(dev_id, f"⚠ 知识图谱查询失败: {exc}", role="system")

    return graph_context, context_tokens, project_name, project_path, repo_id


async def _stream_chat_response(
    dev_id: str, project_id: str, history: list[dict],
    graph_context: str, context_tokens: int, project_name: str, project_path: str,
) -> None:
    """Build system prompt, stream LLM response, save history."""
    from ..services.llm import llm_chat_stream

    system = _MANON_SYSTEM
    if project_name or project_path:
        system += f"\n\n## 当前工作项目\n你正在为「{project_name}」项目提供服务。\n项目路径: {project_path}\n\n请始终记住你当前所处的项目是「{project_name}」，你的所有回答都应该围绕这个项目。当用户提问时，默认是在问关于「{project_name}」项目的问题。"
    if graph_context:
        system += f"\n\n## 项目知识图谱上下文\n\n{graph_context}"
    elif project_path:
        system += "\n\n（知识图谱未返回相关上下文。请告知用户当前查询未匹配到项目代码信息，建议用更具体的关键词（如函数名、类名、文件名）重新提问。不要基于假设回答项目相关问题。）"

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
        log.info("Stream done: reasoning=%d chars, content=%d chars, %.1fs", len(full_reasoning), len(full_content), llm_ms/1000)
        time_suffix = f"\n\n---\n*⏱ {llm_ms/1000:.1f}s*"
        await _send_dev(dev_id, {"type": "coach-content-delta", "delta": time_suffix})
        full_content += time_suffix
        history.append({"role": "assistant", "content": full_content})
        _microcompact(history)
        await _save_chat_history(dev_id, project_id)
        await _send_thinking(dev_id, True, f"LLM 响应完成 ({llm_ms/1000:.1f}s)")
        await _send_thinking(dev_id, False)
        await _send_dev(dev_id, {"type": "coach-stream-end"})
    except Exception as exc:
        await _send_thinking(dev_id, False)
        await _send_dev(dev_id, {"type": "coach-stream-end"})
        await _send_chat(dev_id, f"LLM 调用失败：{exc}", role="system")


async def _handle_manon_chat(dev_id: str, msg: dict) -> None:
    """Unified Manon handler — answers questions or auto-starts pipeline."""
    prompt = msg.get("content", "").strip()
    if not prompt:
        return
    project_id = msg.get("projectId", "")

    # Slash commands
    if await _handle_chat_commands(dev_id, prompt, project_id):
        return

    # If pipeline is active, route as user-response
    state = get_session(dev_id)
    if state and state.status not in (Status.IDLE, Status.DONE, Status.FAILED):
        from .pipeline import _handle_user_response
        await _handle_user_response(dev_id, {"content": prompt})
        return

    # Query graph for context
    graph_context, context_tokens, project_name, project_path, repo_id = await _query_graph_context(
        dev_id, prompt, project_id,
    )

    # Classify intent: explicit task request → pipeline, otherwise → chat
    if _is_feature_request(prompt):
        history = await _load_chat_history(dev_id)
        context_desc = prompt
        if history:
            recent = history[-10:]
            lines = []
            for h in recent:
                role = "用户" if h["role"] == "user" else "Manon"
                lines.append(f"{role}: {h['content']}")
            context_desc = "## 对话上下文\n" + "\n".join(lines) + f"\n\n## 当前需求\n{prompt}"
        from .pipeline import _start_feature
        await _start_feature(dev_id, {
            "description": context_desc,
            "projectId": project_id,
            "prompt": prompt,
        })
        return

    # Chat mode — answer directly with graph context
    history = await _load_chat_history(dev_id)
    history.append({"role": "user", "content": prompt})
    await _save_chat_history(dev_id, project_id)
    await _auto_compact(dev_id, history)
    await _stream_chat_response(
        dev_id, project_id, history,
        graph_context, context_tokens, project_name, project_path,
    )