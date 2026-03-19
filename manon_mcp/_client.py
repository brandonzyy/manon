"""Manon MCP — HTTP client helpers and response formatters."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("manon-mcp")

# ── Injected dependencies ────────────────────────────
_config = None  # _config module
MAX_RESPONSE_CHARS = 8000
HTTP_TIMEOUT = 45


def init(config, constants):
    """Inject dependencies from server.py."""
    global _config, MAX_RESPONSE_CHARS, HTTP_TIMEOUT
    _config = config
    MAX_RESPONSE_CHARS = constants["MAX_RESPONSE_CHARS"]
    HTTP_TIMEOUT = constants["HTTP_TIMEOUT"]


# ── HTTP helpers ─────────────────────────────────────

def _headers():
    return {"Authorization": f"Bearer {_config.API_KEY}", "Content-Type": "application/json"}


def _get(path: str, *, timeout: int = 0, **params) -> dict:
    t = timeout or HTTP_TIMEOUT
    with httpx.Client(base_url=_config.API_URL, headers=_headers(), timeout=t) as c:
        r = c.get(path, params=params)
        r.raise_for_status()
        return r.json()


def _get_no_auth(path: str) -> dict:
    with httpx.Client(base_url=_config.API_URL, timeout=10) as c:
        r = c.get(path)
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict, *, timeout: int = 0) -> dict:
    t = timeout or HTTP_TIMEOUT
    with httpx.Client(base_url=_config.API_URL, headers=_headers(), timeout=t) as c:
        r = c.post(path, json=body)
        r.raise_for_status()
        return r.json()


def _delete(path: str) -> None:
    with httpx.Client(base_url=_config.API_URL, headers=_headers(), timeout=HTTP_TIMEOUT) as c:
        r = c.delete(path)
        r.raise_for_status()


# ── Response formatting ──────────────────────────────

def _truncate(text: str, limit: int = 0) -> str:
    """Hard-truncate text to protect LLM context window."""
    lim = limit or MAX_RESPONSE_CHARS
    if len(text) <= lim:
        return text
    return text[:lim] + f"\n\n... (已截断，共 {len(text)} 字符。用 manon_deep_query 获取完整分析)"


def _format_search(result: dict) -> str:
    """Format search results into concise structured text."""
    entities = result.get("entities", [])
    relations = result.get("relations", [])
    chunks = result.get("chunks", [])
    parts: list[str] = []

    parts.append(f"找到 {len(entities)} 个实体, {len(relations)} 条关系, {len(chunks)} 个代码片段")

    if entities:
        parts.append("\n=== 实体 ===")
        for e in entities[:15]:
            score = f" score={e['score']}" if e.get("score") else ""
            loc = f"{e.get('file_path', '?')}:{e.get('line_start', 0)}"
            parts.append(f"  [{e.get('kind', '?')}] {e.get('name', '?')} ({loc}){score}")
            desc = e.get("description", "")
            if desc:
                parts.append(f"    {desc[:120]}")
        if len(entities) > 15:
            parts.append(f"  ... 还有 {len(entities) - 15} 个实体")

    if relations:
        parts.append("\n=== 关系 ===")
        for r in relations[:15]:
            parts.append(f"  {r.get('src_id', '?')} --{r.get('kind', '?')}--> {r.get('tgt_id', '?')}")
        if len(relations) > 15:
            parts.append(f"  ... 还有 {len(relations) - 15} 条关系")

    if chunks:
        parts.append("\n=== 代码片段 ===")
        for c in chunks[:5]:
            score = f" score={c['score']}" if c.get("score") else ""
            sym = f" ({c['symbol_name']})" if c.get("symbol_name") else ""
            parts.append(f"--- {c.get('file_path', '?')}:{c.get('line_start', 0)}-{c.get('line_end', 0)}{sym}{score} ---")
            content = c.get("content", "")
            parts.append(content[:400] + ("..." if len(content) > 400 else ""))
        if len(chunks) > 5:
            parts.append(f"  ... 还有 {len(chunks) - 5} 个片段")

    return _truncate("\n".join(parts))


def _format_graph(result: dict) -> str:
    """Format graph traversal results into concise text."""
    entities = result.get("entities", [])
    relations = result.get("relations", [])
    chunks = result.get("chunks", [])
    parts: list[str] = []

    parts.append(f"图谱结果: {len(entities)} 个实体, {len(relations)} 条关系")

    if entities:
        parts.append("\n=== 实体 ===")
        for e in entities[:20]:
            loc = f"{e.get('file_path', '?')}:{e.get('line_start', 0)}"
            parts.append(f"  [{e.get('kind', '?')}] {e.get('name', '?')} ({loc})")
        if len(entities) > 20:
            parts.append(f"  ... 还有 {len(entities) - 20} 个实体")

    if relations:
        parts.append("\n=== 关系 ===")
        for r in relations[:20]:
            parts.append(f"  {r.get('src_id', '?')} --{r.get('kind', '?')}--> {r.get('tgt_id', '?')}")
        if len(relations) > 20:
            parts.append(f"  ... 还有 {len(relations) - 20} 条关系")

    if chunks:
        parts.append("\n=== 代码片段 ===")
        for c in chunks[:3]:
            sym = f" ({c['symbol_name']})" if c.get("symbol_name") else ""
            parts.append(f"--- {c.get('file_path', '?')}:{c.get('line_start', 0)}-{c.get('line_end', 0)}{sym} ---")
            content = c.get("content", "")
            parts.append(content[:300] + ("..." if len(content) > 300 else ""))

    return _truncate("\n".join(parts))


def _fmt_changed_symbols(changed: list, summary_mode: bool) -> list[str]:
    """Format changed symbols section."""
    parts: list[str] = []
    if summary_mode:
        parts.append("\n[摘要模式 — 符号过多，按文件聚合]")
        file_agg: dict[str, dict] = {}
        for s in changed:
            if isinstance(s, dict):
                f = s.get("file", s.get("file_path", "?"))
                if f not in file_agg:
                    file_agg[f] = {"count": 0, "lines": 0}
                file_agg[f]["count"] += 1
                file_agg[f]["lines"] += s.get("lines_changed", 0)
        parts.append(f"\n变更符号 ({len(changed)}, 按文件聚合):")
        for f, agg in sorted(file_agg.items()):
            parts.append(f"  {f}: {agg['count']} 个符号 (+{agg['lines']})")
    else:
        parts.append(f"\n变更符号 ({len(changed)}):")
        for s in changed[:15]:
            if isinstance(s, dict):
                diff_stat = f" (+{s['lines_changed']})" if s.get("lines_changed") else ""
                parts.append(f"  {s.get('name', '?')} [{s.get('change_type', s.get('kind', '?'))}]{diff_stat} {s.get('file', s.get('file_path', ''))}")
            else:
                parts.append(f"  {s}")
        if len(changed) > 15:
            parts.append(f"  ... 还有 {len(changed) - 15} 个")
    return parts


def _fmt_callers_and_modules(result: dict, summary_mode: bool) -> list[str]:
    """Format callers, modules, and propagation chains sections."""
    parts: list[str] = []
    for label, key in [("直接调用者", "direct_callers"), ("间接调用者", "indirect_callers"), ("受影响测试", "affected_tests")]:
        items = result.get(key, [])
        if items:
            parts.append(f"\n{label} ({len(items)}):")
            for item in items[:10]:
                parts.append(f"  {item.get('name', item.get('id', str(item))) if isinstance(item, dict) else item}")
            if len(items) > 10:
                parts.append(f"  ... 还有 {len(items) - 10} 个")

    all_modules = result.get("affected_modules", [])
    direct_modules_set = set(result.get("directly_changed_modules", []))
    if all_modules:
        direct_mods = [m for m in all_modules if m in direct_modules_set]
        indirect_mods = [m for m in all_modules if m not in direct_modules_set]
        if direct_mods:
            parts.append(f"\n直接变更模块 ({len(direct_mods)}):")
            parts.extend(f"  {m}" for m in direct_mods[:10])
        if indirect_mods:
            parts.append(f"\n间接波及模块 ({len(indirect_mods)}):")
            parts.extend(f"  {m}" for m in indirect_mods[:10])

    chains = result.get("propagation_chains", [])
    if chains:
        parts.append(f"\n传播链路 ({len(chains)}):")
        parts.extend(f"  {c}" for c in chains[:5 if summary_mode else 15])
    return parts


def _format_impact(result: dict) -> str:
    """Format impact analysis into concise summary."""
    parts: list[str] = []
    commit = result.get("commit", "?")
    parts.append(f"影响分析: commit {commit[:12]}")

    changed_files = result.get("changed_files", [])
    if changed_files:
        parts.append(f"\n变更文件 ({len(changed_files)}):")
        for f in changed_files[:15]:
            parts.append(f"  {f.get('path', '?')} [{f.get('change_type', '?')}]" if isinstance(f, dict) else f"  {f}")

    changed = result.get("changed_symbols", [])
    summary_mode = len(changed) > 30
    if changed:
        parts.extend(_fmt_changed_symbols(changed, summary_mode))

    parts.extend(_fmt_callers_and_modules(result, summary_mode))

    boundary_count = result.get("boundary_callers_count", 0)
    if boundary_count > 0:
        parts.append(f"\n⚠ 当前追踪深度 2 跳，边界有 {boundary_count} 个调用者。可用 max_depth=3 扩大范围。")

    risk = result.get("risk", {})
    if risk:
        parts.append(f"\n风险评估: {risk.get('level', '?')} — {risk.get('reason', '')}")
        if risk.get("suggestions"):
            parts.append(f"  建议: {'; '.join(risk['suggestions'])}")

    return _truncate("\n".join(parts))
