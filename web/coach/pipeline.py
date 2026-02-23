"""Coach pipeline state machine — orchestrates clarify → spec → design → decompose → execute.

Mirrors donnie/agent/lib/coach-feature.js but in async Python.

Sub-modules:
  chat     — _handle_manon_chat (conversational AI)
  compact  — _microcompact / _auto_compact (context compaction)
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
    _failed_phase: str | None = None
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


# ── Chat history persistence ─────────────────────────
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

# ── Report generation ─────────────────────────────────

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

    parts: list[str] = []
    parts.append(f"<h1>{_esc(title)}</h1>")
    parts.append(f'<div class="meta">Task #{_esc(state.feature_id)} · {now.strftime("%Y-%m-%d %H:%M")}</div>')

    parts.append("<h2>需求描述</h2>")
    parts.append(f"<p>{_esc(state.description)}</p>")

    if state.conversation_history:
        parts.append("<h2>需求对齐</h2>")
        parts.append("<table><tr><th>问题</th><th>回答</th></tr>")
        for qa in state.conversation_history:
            parts.append(f"<tr><td>{_esc(qa.get('question',''))}</td><td>{_esc(qa.get('answer',''))}</td></tr>")
        parts.append("</table>")

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

        for t in state.tasks:
            task_diffs = t.get("diffs") or {}
            task_st = t.get("selfTest") or {}
            if task_diffs or task_st:
                parts.append(f'<h2>任务 #{t.get("id","")} — {_esc(t.get("title",""))}</h2>')
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
                if task_diffs:
                    for fp, diff_content in task_diffs.items():
                        parts.append(f"<p><strong>{_esc(fp)}</strong></p>")
                        parts.append(f"<pre>{_render_diff_html(diff_content)}</pre>")

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

    total_all = total_prompt + total_completion
    parts.append("<h2>Token 统计</h2>")
    parts.append('<table class="token-table"><tr><th>类型</th><th>数量</th></tr>')
    parts.append(f"<tr><td>Prompt Tokens</td><td>{total_prompt:,}</td></tr>")
    parts.append(f"<tr><td>Completion Tokens</td><td>{total_completion:,}</td></tr>")
    parts.append(f"<tr><td><strong>Total</strong></td><td><strong>{total_all:,}</strong></td></tr>")
    parts.append("</table>")

    parts.append("<h2>执行结果</h2>")
    parts.append(f"<p><strong>状态:</strong> {state.status.value}</p>")

    body = "\n".join(parts)
    html = f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><title>{_esc(title)}</title><style>{_REPORT_CSS}</style></head><body>{body}</body></html>"

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

    await _send_dev(state.dev_id, {
        "type": "feature-report",
        "featureId": state.feature_id,
        "title": title,
        "url": report_url,
        "status": state.status.value,
        "ts": now.isoformat(),
    })

# ── Entry points called from main.py ─────────────────

async def handle_dev_message(dev_id: str, msg: dict) -> None:
    """Route incoming developer WebSocket messages."""
    msg_type = msg.get("type", "")

    if msg_type == "feature-request":
        await _start_feature(dev_id, msg)
    elif msg_type == "manon-chat":
        from .chat import _handle_manon_chat
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


# ── Feature review handlers ──────────────────────────

async def handle_feature_approved(dev_id: str) -> None:
    """Handle report approval — git add + commit + push."""
    state = get_session(dev_id)
    if not state or state.status != Status.REVIEWING:
        return

    title = (state.spec or {}).get("title", state.description[:40]) or "feature"
    repo_path = ""
    if state.project_id:
        from shared.ast_sync import find_project_by_repo_id
        found = find_project_by_repo_id(state.project_id)
        if found:
            repo_path = found[0]

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

    state.description += f"\n\n## 用户驳回意见\n{reason}"
    for t in state.tasks:
        if t.get("status") in ("completed", "failed"):
            t["status"] = "pending"

    from .decompose import execute_task_loop
    await execute_task_loop(state)


# ── Internal handlers ────────────────────────────────

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