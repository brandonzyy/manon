"""Spec generation — produce structured feature specification from requirements.

Mirrors coach-feature.js finalizeSpec() (lines 84-115).
"""

from __future__ import annotations

import logging

from ..services.llm import call_glm5, parse_json_from_llm
from .pipeline import FeatureState, Status, _send_chat, _send_dev, _send_thinking

log = logging.getLogger("manon.coach.spec")

SYSTEM_PROMPT = """你是 Manon 的产品经理。根据需求描述和用户确认，生成结构化的功能规格。
输出严格 JSON 格式（不要 markdown 代码块包裹）：
{"title":"功能标题","scope":"功能范围描述","requirements":[{"id":"R1","title":"需求标题","priority":"MUST","scenarios":[{"title":"场景名","given":"前置条件","when":"触发动作","then":"预期结果"}]}]}

priority 取值：MUST | SHOULD | MAY
每个 requirement 至少包含一个 scenario，scenario 用 Given/When/Then 格式描述验收条件。"""


async def finalize_spec(state: FeatureState) -> None:
    log.info("Generating spec for feature #%s", state.feature_id)
    state.status = Status.SPEC_READY

    history_text = ""
    if state.conversation_history:
        pairs = [f"Q: {h['question']}\nA: {h['answer']}" for h in state.conversation_history]
        history_text = "\n\n## 用户确认\n" + "\n\n".join(pairs)

    user_prompt = f"## 功能需求\n{state.description}{history_text}"

    try:
        await _send_thinking(state.dev_id, True, "正在生成功能规格...")
        raw = await call_glm5(SYSTEM_PROMPT, user_prompt, timeout=90.0)
        await _send_thinking(state.dev_id, False)
        spec = parse_json_from_llm(raw)
        state.spec = spec
        log.info("Spec generated: %s", spec.get("title", ""))
        await _present_plan(state)
    except Exception as exc:
        await _send_thinking(state.dev_id, False)
        log.error("Spec generation failed: %s", exc)
        await _send_chat(state.dev_id, f"功能规格生成失败：{exc}。请重新描述需求。", role="system")
        await _send_dev(state.dev_id, {"type": "feature-failed", "featureId": state.feature_id, "reason": str(exc)})
        state.status = Status.IDLE


async def _present_plan(state: FeatureState) -> None:
    """Present spec to user for confirmation."""
    spec = state.spec
    state.status = Status.USER_CONFIRMING

    req_lines = []
    for r in spec.get("requirements", []):
        scenarios = "\n".join(
            f"   - {s['title']}: Given {s['given']}, When {s['when']}, Then {s['then']}"
            for s in r.get("scenarios", [])
        )
        req_lines.append(f"**{r['id']}. {r['title']}** [{r.get('priority', 'MUST')}]\n{scenarios}")

    content = (
        f"## 开发计划确认\n\n"
        f"**功能：** {spec.get('title', '')}\n"
        f"**范围：** {spec.get('scope', '')}\n\n"
        f"**需求与场景：**\n" + "\n\n".join(req_lines) +
        "\n\n请确认是否开始开发？回复\"确认\"开始，或提出修改意见。"
    )
    await _send_chat(state.dev_id, content)
    await _send_dev(state.dev_id, {"type": "feature-spec-ready", "featureId": state.feature_id, "spec": spec})
