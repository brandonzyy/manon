"""Coach pipeline state machine — orchestrates clarify → spec → design → decompose → execute.

Mirrors donnie/agent/lib/coach-feature.js but in async Python.
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
    REVIEWING = "reviewing"
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
    evaluation: dict | None = None
    _failed_phase: str | None = None  # "spec" | "decompose" — retryable phase
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


async def _load_chat_history(dev_id: str) -> list[dict]:
    """Load chat history from DB, cache in memory."""
    if dev_id in _chat_history:
        return _chat_history[dev_id]
    try:
        from ..db import db_pool
        async with db_pool() as db:
            row = await db.execute_fetchone(
                "SELECT messages FROM conversations WHERE id = ?", (dev_id,),
            )
        if row and row["messages"]:
            msgs = json.loads(row["messages"])
            _chat_history[dev_id] = msgs
            return msgs
    except Exception as exc:
        log.debug("Failed to load chat history for %s: %s", dev_id, exc)
    _chat_history[dev_id] = []
    return _chat_history[dev_id]


async def _save_chat_history(dev_id: str, project_id: str = "") -> None:
    """Persist chat history to DB."""
    history = _chat_history.get(dev_id, [])
    try:
        from ..db import db_pool
        async with db_pool() as db:
            await db.execute(
                "INSERT INTO conversations (id, project_id, messages, updated_at) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(id) DO UPDATE SET messages=excluded.messages, "
                "project_id=COALESCE(NULLIF(excluded.project_id,''), project_id), "
                "updated_at=datetime('now')",
                (dev_id, project_id or "default", json.dumps(history, ensure_ascii=False)),
            )
            await db.commit()
    except Exception as exc:
        log.debug("Failed to save chat history for %s: %s", dev_id, exc)

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

_COMPACT_PROMPT = """请将以下对话历史压缩为结构化摘要（800字以内），严格按以下格式输出：

## 讨论主题
- 列出讨论过的主要话题（每个一行）

## 关键结论
- 已达成的结论和决策

## 提到的代码
- 文件名、函数名、类名（原样保留，不要改写或翻译）

## 待处理
- 未完成的事项或用户提出但未解决的问题

对话历史：
"""

# Compact model — always use glm-4.7-fp8 regardless of coach model config
_COMPACT_MODEL = "glm-4.7-fp8"

# Thresholds (characters)
_COMPACT_TRIGGER = 100_000   # auto-compact check threshold
_COMPACT_TARGET = 80_000     # if still above this after microcompact → LLM summarize
_MICRO_KEEP_RECENT = 5       # assistant messages in hot tail (full)
_MICRO_TRUNCATE_AT = 2000    # truncate older assistant msgs longer than this
_MICRO_KEEP_CHARS = 1500     # keep first N chars when truncating
_AUTO_KEEP_RECENT = 10       # messages kept intact during LLM compaction
_AUTO_FALLBACK_KEEP = 20     # fallback: keep recent N if LLM fails


def _history_chars(history: list[dict]) -> int:
    """Total character count of all messages in history."""
    return sum(len(m.get("content", "")) for m in history)


def _microcompact(history: list[dict]) -> int:
    """Layer 1: truncate old long assistant messages, return number truncated."""
    truncated = 0
    # Count assistant messages from the end to find the hot-tail boundary
    assistant_count = 0
    hot_indices: set[int] = set()
    for i in range(len(history) - 1, -1, -1):
        if history[i]["role"] == "assistant":
            assistant_count += 1
            if assistant_count <= _MICRO_KEEP_RECENT:
                hot_indices.add(i)

    for i, m in enumerate(history):
        if m["role"] != "assistant":
            continue
        if i in hot_indices:
            continue  # hot tail — keep full
        content = m.get("content", "")
        if len(content) > _MICRO_TRUNCATE_AT:
            history[i] = {
                "role": "assistant",
                "content": content[:_MICRO_KEEP_CHARS]
                + f"\n...[已压缩，原文 {len(content)} 字符]",
            }
            truncated += 1
    return truncated


async def _auto_compact(
    dev_id: str,
    history: list[dict],
    *,
    force: bool = False,
    focus_hint: str = "",
) -> None:
    """Layer 2 (+ layer 1): auto-compaction with LLM structured summary.

    Args:
        force: if True, skip threshold check (used by /compact).
        focus_hint: optional focus instruction appended to summary prompt.
    """
    total = _history_chars(history)

    # Always run microcompact first
    n_trunc = _microcompact(history)
    if n_trunc:
        log.info("Microcompact: truncated %d old assistant messages", n_trunc)

    total = _history_chars(history)

    # Check if LLM compaction is needed
    if not force and total <= _COMPACT_TRIGGER:
        return
    if not force and total <= _COMPACT_TARGET:
        return

    # Not enough messages to compact
    if len(history) <= _AUTO_KEEP_RECENT:
        return

    from ..services.llm import llm_chat

    keep_recent = history[-_AUTO_KEEP_RECENT:]
    old_messages = history[:-_AUTO_KEEP_RECENT]

    # Build text for LLM
    lines = []
    for m in old_messages:
        if m["role"] == "system" and m.get("content", "").startswith("[历史摘要]"):
            lines.append(f"[之前的摘要]: {m['content']}")
        else:
            role = "用户" if m["role"] == "user" else "Manon"
            lines.append(f"{role}: {m['content']}")
    old_text = "\n".join(lines)

    prompt = _COMPACT_PROMPT + old_text
    if focus_hint:
        prompt += f"\n\n重点保留以下方面的信息：{focus_hint}"

    try:
        await _send_thinking(dev_id, True, "压缩对话历史...")
        result = await llm_chat(
            [{"role": "user", "content": prompt}],
            model=_COMPACT_MODEL,
            max_tokens=1200,
            timeout=30.0,
        )
        summary = result.get("content", "").strip()
        if summary:
            history[:] = [
                {"role": "system", "content": f"[历史摘要] {summary}"},
            ] + keep_recent
            old_chars = sum(len(m.get("content", "")) for m in old_messages)
            new_chars = _history_chars(history)
            log.info(
                "Auto-compact: %d msgs → summary + %d recent (chars %d → %d)",
                len(old_messages), len(keep_recent), old_chars, new_chars,
            )
            await _save_chat_history(dev_id)
        await _send_thinking(dev_id, False)
    except Exception as exc:
        log.warning("Auto-compact LLM failed (%s), falling back to truncation", exc)
        await _send_thinking(dev_id, False)
        # Fallback: microcompact already done, just keep recent N
        if len(history) > _AUTO_FALLBACK_KEEP:
            history[:] = history[-_AUTO_FALLBACK_KEEP:]
            log.info("Fallback: kept last %d messages", _AUTO_FALLBACK_KEEP)


async def _iterative_graph_query(
    dev_id: str, prompt: str, mg, initial_context: str, max_rounds: int = 3,
) -> str:
    """Perform iterative graph queries — LLM analyzes gaps and triggers follow-up searches."""
    import json as _json
    from ..services.llm import llm_chat

    accumulated = initial_context
    for round_idx in range(max_rounds):
        # Ask LLM what additional info is needed
        plan_messages = [
            {"role": "system", "content": _DEEPQUERY_SYSTEM},
            {"role": "user", "content": f"## 用户问题\n{prompt}\n\n## 已有知识图谱上下文\n{accumulated}"},
        ]
        try:
            await _send_thinking(dev_id, True, f"完整性检查：第 {round_idx + 1} 轮自检...")
            result = await llm_chat(plan_messages, max_tokens=2048, timeout=30.0)
            text = result.get("content", "").strip()
            log.info("Deep query LLM response (round %d): %s", round_idx + 1, text[:1000])
            # Parse JSON from response
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
            # Fallback: if LLM reports missing but no queries, use missing items as queries
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

        # Execute follow-up queries
        for q in follow_ups[:3]:
            try:
                import time as _time
                t0 = _time.monotonic()
                r = await mg.query(q, top_k=5, depth=1)
                q_ms = int((_time.monotonic() - t0) * 1000)
                if r.context:
                    accumulated += f"\n\n## 补充查询: {q}\n{r.context}"
                    ctx_tok = len(r.context) // 2
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
    # Explicit pipeline trigger
    if p.startswith("/feature") or p.startswith("/pipeline") or p.startswith("/do"):
        return True
    # Chinese keywords that signal code modification intent (startswith)
    action_starts = (
        "实现", "添加", "新增", "开发", "创建", "搭建", "构建",
        "修改", "改一下", "改成", "改为", "重构", "优化",
        "删除", "移除", "去掉",
        "修复", "修bug", "修一下", "fix",
        "写一个", "写个", "帮我写", "帮我实现", "帮我添加", "帮我开发",
        "帮我修改", "帮我修复", "帮我重构", "帮我优化", "帮我创建",
        "请实现", "请添加", "请修改", "请开发", "请创建",
        "把这个", "把它", "开始实现", "开始开发", "开始执行", "开始做",
    )
    for kw in action_starts:
        if p.startswith(kw):
            return True
    # Chinese keywords that can appear anywhere (contains) — confirmation & action
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
    # English confirmation keywords (contains)
    en_contains = ("let's do it", "go ahead", "start coding", "do it", "let's implement")
    for kw in en_contains:
        if kw in p_lower:
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
            await _send_dev(dev_id, {"type": "coach-stage", "stage": "idle"})
            return
        await _send_chat(dev_id, "当前没有进行中的 pipeline。", role="system")
        return

    # /do — manual pipeline trigger from chat context
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
            return
        await _start_feature(dev_id, {
            "description": context_desc,
            "projectId": project_id,
            "prompt": extra or None,
        })
        return

    # /compact — manual compaction (layer 3)
    if prompt.lower().startswith("/compact"):
        hint = prompt[8:].strip()
        history = await _load_chat_history(dev_id)
        if not history:
            await _send_chat(dev_id, "当前没有对话历史。", role="system")
            return
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
                # Always send query record so user sees graph activity
                context_tokens = len(result.context) // 2 if result.context else 0
                await _send_dev(dev_id, {
                    "type": "llm-query", "caller": "manon",
                    "command": "matrixone_graph.query(hybrid)",
                    "query": prompt[:120], "ts": datetime.now().isoformat(),
                    "duration_ms": graph_ms,
                    "context_tokens": context_tokens,
                })
                if result.context:
                    graph_context = result.context
                    await _send_thinking(dev_id, True, f"知识图谱返回 ~{context_tokens/1000:.1f}k tokens ({graph_ms}ms)")
                    # Iterative deep retrieval: LLM analyzes gaps → follow-up queries
                    try:
                        graph_context = await _iterative_graph_query(
                            dev_id, prompt, mg, graph_context, max_rounds=2,
                        )
                        context_tokens = len(graph_context) // 2
                    except Exception as exc:
                        log.warning("Iterative graph query failed: %s", exc)
                else:
                    await _send_thinking(dev_id, True, f"知识图谱无匹配结果 ({graph_ms}ms)")
                await _send_thinking(dev_id, False)
            except Exception as exc:
                await _send_thinking(dev_id, False)
                log.warning("MatrixoneGraph query failed: %s", exc)
                await _send_chat(dev_id, f"⚠ 知识图谱查询失败: {exc}", role="system")

    # Classify intent: explicit task request → pipeline, otherwise → chat
    if _is_feature_request(prompt):
        # Include recent chat history so pipeline LLM understands context
        history = await _load_chat_history(dev_id)
        context_desc = prompt
        if history:
            recent = history[-10:]
            lines = []
            for h in recent:
                role = "用户" if h["role"] == "user" else "Manon"
                lines.append(f"{role}: {h['content']}")
            context_desc = "## 对话上下文\n" + "\n".join(lines) + f"\n\n## 当前需求\n{prompt}"
        await _start_feature(dev_id, {
            "description": context_desc,
            "projectId": project_id,
            "prompt": prompt,
        })
        return

    # Chat mode (default) — answer directly with graph context
    history = await _load_chat_history(dev_id)
    history.append({"role": "user", "content": prompt})
    await _save_chat_history(dev_id, project_id)

    # Three-layer compaction: auto-compact before LLM call
    await _auto_compact(dev_id, history)

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
            await asyncio.sleep(0)  # flush WS between chunks
        llm_ms = int((time.monotonic() - t0) * 1000)
        log.info("Stream done: reasoning=%d chars, content=%d chars, %.1fs", len(full_reasoning), len(full_content), llm_ms/1000)
        # Append timing info to the end of the response
        time_suffix = f"\n\n---\n*⏱ {llm_ms/1000:.1f}s*"
        await _send_dev(dev_id, {"type": "coach-content-delta", "delta": time_suffix})
        full_content += time_suffix
        history.append({"role": "assistant", "content": full_content})
        _microcompact(history)  # Layer 1: truncate old long assistant replies
        await _save_chat_history(dev_id, project_id)
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
pre{background:#f5f7fa;padding:10px;border-radius:6px;overflow-x:auto;font-size:12px;white-space:pre-wrap;word-break:break-all}
.meta{color:#888;font-size:12px;margin-bottom:20px}
.diff-add{color:#22863a;background:#e6ffec}.diff-del{color:#cb2431;background:#ffeef0}
.eval-score{font-size:28px;font-weight:800;display:inline-block;padding:4px 16px;border-radius:8px;margin:6px 0}
.eval-pass{background:#e6ffec;color:#22863a}.eval-fail{background:#ffeef0;color:#cb2431}
.token-table td:last-child{text-align:right;font-family:monospace}
"""


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_diff_html(diff_text: str) -> str:
    """Render diff text with colored add/del lines."""
    lines = []
    for line in diff_text.split("\n"):
        escaped = _esc(line)
        if line.startswith("+"):
            lines.append(f'<div class="diff-add">{escaped}</div>')
        elif line.startswith("-"):
            lines.append(f'<div class="diff-del">{escaped}</div>')
        else:
            lines.append(f"<div>{escaped}</div>")
    return "".join(lines)


async def generate_report(state: FeatureState) -> None:
    """Build a PDF report from pipeline state and notify the frontend."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    date_str = now.strftime("%Y%m%d_%H%M")
    title = (state.spec or {}).get("title", state.description[:40]) or state.feature_id
    safe_name = f"{state.feature_id}_{date_str}"

    # ── Build HTML sections ──
    parts: list[str] = []
    parts.append(f"<h1>{_esc(title)}</h1>")
    parts.append(f'<div class="meta">Task #{_esc(state.feature_id)} · {now.strftime("%Y-%m-%d %H:%M")}</div>')

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
        parts.append("<h2>任务规格</h2>")
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

    # Tasks with diffs, self-test, and token usage
    total_prompt = 0
    total_completion = 0
    if state.tasks:
        done = sum(1 for t in state.tasks if t.get("status") == "completed")
        failed = sum(1 for t in state.tasks if t.get("status") == "failed")
        skipped = sum(1 for t in state.tasks if t.get("status") == "skipped")
        total = len(state.tasks)

        parts.append("<h2>任务执行</h2>")
        parts.append(f"<p>完成: {done} / 总计: {total} · 失败: {failed} · 跳过: {skipped}</p>")
        parts.append("<table><tr><th>#</th><th>任务</th><th>状态</th><th>涉及文件</th><th>Token</th></tr>")
        for t in state.tasks:
            st = t.get("status", "pending")
            badge_cls = "badge-ok" if st == "completed" else "badge-fail" if st == "failed" else "badge-skip"
            files_str = ", ".join(t.get("files", []))
            tu = t.get("tokenUsage") or {}
            tok_str = f'{tu.get("total", 0):,}' if tu else "—"
            total_prompt += tu.get("prompt", 0)
            total_completion += tu.get("completion", 0)
            parts.append(
                f'<tr><td>{t.get("id","")}</td><td>{_esc(t.get("title",""))}</td>'
                f'<td><span class="badge {badge_cls}">{_esc(st)}</span></td>'
                f'<td>{_esc(files_str)}</td><td>{tok_str}</td></tr>'
            )
        parts.append("</table>")

        # Per-task diffs and self-test
        for t in state.tasks:
            task_diffs = t.get("diffs") or {}
            task_st = t.get("selfTest") or {}
            if task_diffs or task_st:
                parts.append(f'<h2>任务 #{t.get("id","")} — {_esc(t.get("title",""))}</h2>')
                # Self-test result
                if task_st:
                    passed = task_st.get("passed", True)
                    badge = '<span class="badge badge-ok">PASS</span>' if passed else '<span class="badge badge-fail">FAIL</span>'
                    parts.append(f"<p>自测结果: {badge}</p>")
                    issues = task_st.get("issues", [])
                    if issues:
                        parts.append("<ul>")
                        for iss in issues:
                            parts.append(f"<li>{_esc(str(iss))}</li>")
                        parts.append("</ul>")
                # Diffs
                if task_diffs:
                    for fp, diff_content in task_diffs.items():
                        parts.append(f"<p><strong>{_esc(fp)}</strong></p>")
                        parts.append(f"<pre>{_render_diff_html(diff_content)}</pre>")

    # Evaluation section
    ev = state.evaluation
    if ev:
        parts.append("<h2>整体评价</h2>")
        score = ev.get("score", 0)
        score_cls = "eval-pass" if score >= 5 else "eval-fail"
        parts.append(f'<div class="eval-score {score_cls}">{score}/10</div>')
        parts.append(f"<p>{_esc(ev.get('summary', ''))}</p>")
        ev_issues = ev.get("issues", [])
        if ev_issues:
            parts.append("<ul>")
            for iss in ev_issues:
                parts.append(f"<li>{_esc(str(iss))}</li>")
            parts.append("</ul>")

    # Token statistics
    total_all = total_prompt + total_completion
    parts.append("<h2>Token 统计</h2>")
    parts.append('<table class="token-table"><tr><th>类型</th><th>数量</th></tr>')
    parts.append(f"<tr><td>Prompt Tokens</td><td>{total_prompt:,}</td></tr>")
    parts.append(f"<tr><td>Completion Tokens</td><td>{total_completion:,}</td></tr>")
    parts.append(f"<tr><td><strong>Total</strong></td><td><strong>{total_all:,}</strong></td></tr>")
    parts.append("</table>")

    # Result summary
    parts.append("<h2>执行结果</h2>")
    parts.append(f"<p><strong>状态:</strong> {state.status.value}</p>")

    body = "\n".join(parts)
    html = f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><title>{_esc(title)}</title><style>{_REPORT_CSS}</style></head><body>{body}</body></html>"

    # Write PDF (fallback to HTML if xhtml2pdf unavailable)
    report_url = ""
    try:
        from xhtml2pdf import pisa
        import io
        pdf_path = REPORTS_DIR / f"{safe_name}.pdf"
        with open(pdf_path, "wb") as f:
            pisa_status = pisa.CreatePDF(io.StringIO(html), dest=f)
        if pisa_status.err:
            raise RuntimeError(f"xhtml2pdf errors: {pisa_status.err}")
        report_url = f"/static/reports/{safe_name}.pdf"
        log.info("PDF report generated: %s", pdf_path)
    except Exception as pdf_exc:
        log.warning("PDF generation failed (%s), falling back to HTML", pdf_exc)
        html_path = REPORTS_DIR / f"{safe_name}.html"
        html_path.write_text(html, encoding="utf-8")
        report_url = f"/static/reports/{safe_name}.html"
        log.info("HTML report generated: %s", html_path)

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
    elif msg_type == "feature-approved":
        await handle_feature_approved(dev_id)
    elif msg_type == "feature-rejected":
        await handle_feature_rejected(dev_id, msg.get("reason", ""))
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


# ---- Feature review handlers ----

async def handle_feature_approved(dev_id: str) -> None:
    """Handle report approval — git add + commit + push."""
    state = get_session(dev_id)
    if not state or state.status != Status.REVIEWING:
        return

    title = (state.spec or {}).get("title", state.description[:40]) or "feature"
    repo_path = ""
    if state.project_id:
        from ..db import db_pool
        async with db_pool() as db:
            row = await db.execute_fetchone("SELECT local_path FROM projects WHERE id = ?", (state.project_id,))
            if row:
                repo_path = row["local_path"] or ""

    if repo_path:
        import asyncio as _asyncio
        try:
            await _send_thinking(dev_id, True, "正在提交代码...")
            proc = await _asyncio.create_subprocess_shell(
                f'git add -A && git commit -m "feat: {title}" && git push',
                cwd=repo_path,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=60)
            out = stdout.decode(errors="replace")
            err = stderr.decode(errors="replace")
            await _send_thinking(dev_id, False)
            if proc.returncode == 0:
                await _send_chat(dev_id, f"代码已提交并推送。\n{out[:300]}", role="system")
            else:
                await _send_chat(dev_id, f"Git 操作失败: {(err or out)[:500]}", role="system")
        except Exception as exc:
            await _send_thinking(dev_id, False)
            await _send_chat(dev_id, f"Git 操作异常: {exc}", role="system")
    else:
        await _send_chat(dev_id, "审核通过（未配置项目路径，跳过 git 提交）。", role="system")

    state.status = Status.IDLE
    await _send_dev(dev_id, {"type": "coach-stage", "stage": "idle"})


async def handle_feature_rejected(dev_id: str, reason: str) -> None:
    """Handle report rejection — go back to executing with user feedback."""
    state = get_session(dev_id)
    if not state or state.status != Status.REVIEWING:
        return

    state.status = Status.EXECUTING
    await _send_dev(dev_id, {"type": "coach-stage", "stage": "executing"})
    await _send_chat(dev_id, f"收到驳回意见：{reason}\n正在重新调整...", role="system")

    # Inject feedback and re-run task loop
    state.description += f"\n\n## 用户驳回意见\n{reason}"
    # Reset failed tasks to pending for re-execution
    for t in state.tasks:
        if t.get("status") in ("completed", "failed"):
            t["status"] = "pending"

    from .decompose import execute_task_loop
    await execute_task_loop(state)


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
    state.evaluation = None

    await _send_dev(dev_id, {"type": "coach-stage", "stage": "clarifying"})
    user_prompt = msg.get("prompt")
    if user_prompt:
        await _send_chat(dev_id, "收到需求，进入工作流程。")
    else:
        await _send_chat(dev_id, "已根据对话上下文进入工作流程...")
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
        # User guidance for failed task or failed phase
        content = msg.get("content", "").strip()
        if content == "重试":
            if state._failed_phase:
                await _retry_failed_phase(state)
            else:
                await _retry_failed_tasks(state)
        elif content == "跳过":
            await _skip_failed_tasks(state)
        elif content == "取消":
            await _cancel_feature(state)
        else:
            if state._failed_phase:
                await _retry_failed_phase(state)
            else:
                await _retry_with_guidance(state, content)

    elif state.status == Status.REVIEWING:
        # User can type feedback during review
        content = msg.get("content", "").strip()
        if content in ("通过", "approve", "ok"):
            await handle_feature_approved(dev_id)
        else:
            await handle_feature_rejected(dev_id, content)


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


async def _skip_failed_tasks(state: FeatureState) -> None:
    """Skip all failed tasks and continue the execution loop."""
    from .decompose import execute_task_loop

    for task in state.tasks:
        if task.get("status") == "failed":
            task["status"] = "skipped"
            await _send_dev(state.dev_id, {
                "type": "feature-task-status",
                "featureId": state.feature_id,
                "taskId": task["id"],
                "status": "skipped",
            })
    await execute_task_loop(state)


async def _cancel_feature(state: FeatureState) -> None:
    state._failed_phase = None
    state.status = Status.FAILED
    await _send_dev(state.dev_id, {"type": "feature-failed", "featureId": state.feature_id, "reason": "User cancelled"})
    await _send_chat(state.dev_id, "任务已取消。", role="system")
    await generate_report(state)
    state.status = Status.IDLE


async def _retry_failed_phase(state: FeatureState) -> None:
    """Retry a failed pipeline phase (spec or decompose)."""
    phase = state._failed_phase
    state._failed_phase = None
    await _send_chat(state.dev_id, f"正在重试 {phase} 阶段...", role="system")
    if phase == "spec":
        from .spec import finalize_spec
        await finalize_spec(state)
    elif phase == "decompose":
        from .decompose import decompose_to_tasks
        await decompose_to_tasks(state)
    else:
        log.warning("Unknown failed phase: %s", phase)


async def _retry_failed_tasks(state: FeatureState) -> None:
    """Reset all failed tasks to pending and re-enter the execution loop."""
    from .decompose import execute_task_loop

    for task in state.tasks:
        if task.get("status") == "failed":
            task["status"] = "pending"
            await _send_dev(state.dev_id, {
                "type": "feature-task-status",
                "featureId": state.feature_id,
                "taskId": task["id"],
                "status": "pending",
            })
    state.failed_attempts = 0
    await _send_chat(state.dev_id, "正在重试失败的任务...", role="system")
    await execute_task_loop(state)


async def _retry_with_guidance(state: FeatureState, guidance: str) -> None:
    """Inject user guidance into all failed tasks, then re-enter the execution loop."""
    from .decompose import execute_task_loop

    for task in state.tasks:
        if task.get("status") == "failed":
            task["instruction"] = f"{task.get('instruction', '')}\n\n## 用户补充指导\n{guidance}"
            task["status"] = "pending"
            await _send_dev(state.dev_id, {
                "type": "feature-task-status",
                "featureId": state.feature_id,
                "taskId": task["id"],
                "status": "pending",
            })
    state.failed_attempts = 0
    await _send_chat(state.dev_id, "收到指导，正在重试...", role="system")
    await execute_task_loop(state)

