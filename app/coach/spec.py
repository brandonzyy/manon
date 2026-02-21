"""Spec generation — produce structured feature specification from requirements.

Mirrors coach-feature.js finalizeSpec() (lines 84-115).
"""

from __future__ import annotations

import logging

from ..services.llm import call_glm5, parse_json_from_llm
from .pipeline import FeatureState, Status, _send_chat, _send_dev, _send_thinking

log = logging.getLogger("manon.coach.spec")

SYSTEM_PROMPT = """你是 Manon 的产品经理。根据需求描述和用户确认，生成结构化的任务规格。
输出严格 JSON 格式（不要 markdown 代码块包裹）：
{"title":"任务标题","scope":"任务范围描述","requirements":[{"id":"R1","title":"需求标题","priority":"MUST","scenarios":[{"title":"场景名","condition":"在什么条件或操作下","expected":"预期结果是什么"}]}]}

priority 取值：MUST | SHOULD | MAY
每个 requirement 至少包含一个 scenario，用自然语言描述验收条件。"""


async def finalize_spec(state: FeatureState) -> None:
    log.info("Generating spec for feature #%s", state.feature_id)
    state.status = Status.SPEC_READY

    history_text = ""
    if state.conversation_history:
        pairs = [f"Q: {h['question']}\nA: {h['answer']}" for h in state.conversation_history]
        history_text = "\n\n## 用户确认\n" + "\n\n".join(pairs)

    user_prompt = f"## 需求描述\n{state.description}{history_text}"

    try:
        await _send_thinking(state.dev_id, True, "正在生成任务规格...")
        raw = await call_glm5(SYSTEM_PROMPT, user_prompt, timeout=90.0)
        await _send_thinking(state.dev_id, False)
        spec = parse_json_from_llm(raw)
        state.spec = spec
        log.info("Spec generated: %s", spec.get("title", ""))
        await _present_plan(state)
    except Exception as exc:
        await _send_thinking(state.dev_id, False)
        log.error("Spec generation failed: %s", exc)
        await _send_chat(state.dev_id, f"任务规格生成失败：{exc}。请重新描述需求。", role="system")
        await _send_dev(state.dev_id, {"type": "feature-failed", "featureId": state.feature_id, "reason": str(exc)})
        state.status = Status.IDLE


async def _present_plan(state: FeatureState) -> None:
    """Present spec to user for confirmation."""
    spec = state.spec
    state.status = Status.USER_CONFIRMING

    priority_label = {"MUST": "必须", "SHOULD": "建议", "MAY": "可选"}

    lines = [
        f"## 📋 执行计划确认\n",
        f"**{spec.get('title', '')}**\n",
        f"> {spec.get('scope', '')}\n",
        "---\n",
    ]

    for r in spec.get("requirements", []):
        tag = priority_label.get(r.get("priority", "MUST"), r.get("priority", ""))
        lines.append(f"### {r['id']}. {r['title']}  `{tag}`\n")
        for s in r.get("scenarios", []):
            # Support both old (given/when/then) and new (condition/expected) formats
            if s.get("condition"):
                lines.append(f"- **{s.get('title', '场景')}**：{s['condition']} → {s.get('expected', '')}")
            elif s.get("given"):
                lines.append(f"- **{s.get('title', '场景')}**：{s['given']}，{s.get('when', '')} → {s.get('then', '')}")
            else:
                lines.append(f"- **{s.get('title', '场景')}**")
        lines.append("")

    lines.append("---\n")
    lines.append("确认无误请回复 **确认**，或直接提出修改意见。")

    await _send_chat(state.dev_id, "\n".join(lines))
    await _send_dev(state.dev_id, {"type": "feature-spec-ready", "featureId": state.feature_id, "spec": spec})
