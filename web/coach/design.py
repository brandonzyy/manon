"""Technical design generation — produce architecture decisions and file change plan.

Mirrors coach-feature.js generateDesign() (lines 141-186).
"""

from __future__ import annotations

import logging

from ..services.llm import call_glm5, parse_json_from_llm
from .pipeline import FeatureState, Status, _send_chat, _send_dev, _send_thinking

log = logging.getLogger("manon.coach.design")

SYSTEM_PROMPT = """你是 Manon 的技术架构师。根据任务规格设计技术方案。
输出严格 JSON 格式（不要 markdown 代码块包裹）：
{"approach":"技术方案概述","decisions":[{"title":"决策标题","rationale":"理由"}],"fileChanges":[{"file":"路径","action":"new|modify","description":"说明"}]}

你面对的是一个任意 Git 仓库，根据 spec 中的信息推断项目结构和技术栈。"""


async def generate_design(state: FeatureState) -> None:
    from .decompose import decompose_to_tasks

    log.info("Generating design for feature #%s", state.feature_id)
    state.status = Status.DESIGNING

    spec = state.spec or {}
    req_summary = "\n".join(
        f"{r['id']}. [{r.get('priority','MUST')}] {r['title']}\n" +
        "\n".join(f"  {s.get('title','')}: {s.get('condition', s.get('given',''))} → {s.get('expected', s.get('then',''))}" for s in r.get("scenarios", []))
        for r in spec.get("requirements", [])
    )
    user_prompt = f"## 任务规格\n标题：{spec.get('title','')}\n范围：{spec.get('scope','')}\n\n## 需求\n{req_summary}"

    try:
        await _send_thinking(state.dev_id, True, "正在生成技术设计...")
        raw = await call_glm5(SYSTEM_PROMPT, user_prompt, max_tokens=4096, timeout=90.0)
        await _send_thinking(state.dev_id, False)
        design = parse_json_from_llm(raw)
        state.design = design
        log.info("Design generated: %s", str(design.get("approach", ""))[:100])

        decision_lines = "\n".join(f"- **{d['title']}**: {d['rationale']}" for d in design.get("decisions", []))
        file_lines = "\n".join(f"- `{f['file']}` ({f['action']}): {f['description']}" for f in design.get("fileChanges", []))
        await _send_chat(
            state.dev_id,
            f"## 技术设计\n\n**方案：** {design.get('approach','')}\n\n"
            f"**关键决策：**\n{decision_lines}\n\n**文件变更：**\n{file_lines}\n\n正在拆解子任务...",
        )
        await decompose_to_tasks(state)
    except Exception as exc:
        await _send_thinking(state.dev_id, False)
        log.warning("Design generation failed: %s, proceeding without design", exc)
        state.design = None
        await decompose_to_tasks(state)
