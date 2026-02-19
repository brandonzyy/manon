"""Intent clarification — multi-turn dialogue to refine requirements.

Mirrors coach-feature.js clarifyIntent() (lines 34-81).
"""

from __future__ import annotations

import logging

from ..services.llm import call_glm5
from .pipeline import FeatureState, Status, _send_chat, _send_thinking

log = logging.getLogger("manon.coach.clarify")

MAX_ROUNDS = 3

SYSTEM_PROMPT = """你是 Manon 的产品经理。用户提交了功能需求，你需要通过提问确认需求边界。
规则：
1. 每轮最多问 2-3 个问题
2. 问题要具体、可快速回答
3. 如果已有足够信息（通常 1-2 轮），输出 "READY" 表示可以生成 spec
4. 用中文，语气友好专业"""


async def clarify_intent(state: FeatureState) -> None:
    """Run one round of clarification. Called repeatedly as user responds."""
    from .spec import finalize_spec

    round_num = len(state.conversation_history)
    log.info("Clarifying feature #%s (round %d)", state.feature_id, round_num + 1)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({
        "role": "user",
        "content": f"## 功能需求\n{state.description}\n\n请生成追问问题，或输出 READY 表示信息已足够。",
    })
    for h in state.conversation_history:
        messages.append({"role": "assistant", "content": h["question"]})
        messages.append({"role": "user", "content": h["answer"]})
    if round_num >= 2:
        messages.append({"role": "user", "content": "已经问了足够多的问题，请输出 READY 或最后一轮追问。"})

    try:
        await _send_thinking(state.dev_id, True, "正在分析您的需求...")
        response = await call_glm5(None, None, messages=messages, timeout=60.0)
        await _send_thinking(state.dev_id, False)

        if "READY" in response or round_num >= MAX_ROUNDS:
            log.info("Clarification complete after %d rounds", round_num)
            await finalize_spec(state)
            return

        await _send_chat(state.dev_id, response)
        state._last_question = response

    except Exception as exc:
        await _send_thinking(state.dev_id, False)
        log.warning("Clarification failed: %s, proceeding with original description", exc)
        await finalize_spec(state)
