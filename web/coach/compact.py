"""Coach context compaction — microcompact (truncation) + auto-compact (LLM summary)."""
from __future__ import annotations

import logging

log = logging.getLogger("manon.coach")

# ── Compact model — always use glm-4.7-fp8 regardless of coach model config ──
_COMPACT_MODEL = "glm-4.7-fp8"

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

# ── Thresholds (characters) ──────────────────────────
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
            continue
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
    """Layer 2 (+ layer 1): auto-compaction with LLM structured summary."""
    from .pipeline import _send_thinking, _save_chat_history

    total = _history_chars(history)

    n_trunc = _microcompact(history)
    if n_trunc:
        log.info("Microcompact: truncated %d old assistant messages", n_trunc)

    total = _history_chars(history)

    if not force and total <= _COMPACT_TRIGGER:
        return
    if not force and total <= _COMPACT_TARGET:
        return

    if len(history) <= _AUTO_KEEP_RECENT:
        return

    from ..services.llm import llm_chat

    keep_recent = history[-_AUTO_KEEP_RECENT:]
    old_messages = history[:-_AUTO_KEEP_RECENT]

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
        if len(history) > _AUTO_FALLBACK_KEEP:
            history[:] = history[-_AUTO_FALLBACK_KEEP:]
            log.info("Fallback: kept last %d messages", _AUTO_FALLBACK_KEEP)
