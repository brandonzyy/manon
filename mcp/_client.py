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


def _format_impact(result: dict) -> str:
    """Format impact analysis into concise summary."""
    parts: list[str] = []

    commit = result.get("commit", "?")
    parts.append(f"影响分析: commit {commit[:12]}")

    # Changed files
    changed_files = result.get("changed_files", [])
    if changed_files:
        parts.append(f"\n变更文件 ({len(changed_files)}):")
        for f in changed_files[:15]:
            if isinstance(f, dict):
                parts.append(f"  {f.get('path', '?')} [{f.get('change_type', '?')}]")
            else:
                parts.append(f"  {f}")

    changed = result.get("changed_symbols", [])
    if changed:
        parts.append(f"\n变更符号 ({len(changed)}):")
        for s in changed[:15]:
            if isinstance(s, dict):
                diff_stat = ""
                if s.get("lines_changed"):
                    diff_stat = f" (+{s['lines_changed']})"
                parts.append(f"  {s.get('name', '?')} [{s.get('change_type', s.get('kind', '?'))}]{diff_stat} {s.get('file', s.get('file_path', ''))}")
            else:
                parts.append(f"  {s}")
        if len(changed) > 15:
            parts.append(f"  ... 还有 {len(changed) - 15} 个")

    for label, key, limit in [
        ("直接调用者", "direct_callers", 10),
        ("间接调用者", "indirect_callers", 10),
        ("受影响模块", "affected_modules", 10),
        ("受影响测试", "affected_tests", 10),
    ]:
        items = result.get(key, [])
        if items:
            parts.append(f"\n{label} ({len(items)}):")
            for item in items[:limit]:
                if isinstance(item, dict):
                    parts.append(f"  {item.get('name', item.get('id', str(item)))}")
                else:
                    parts.append(f"  {item}")
            if len(items) > limit:
                parts.append(f"  ... 还有 {len(items) - limit} 个")

    # Propagation chains
    chains = result.get("propagation_chains", [])
    if chains:
        parts.append(f"\n传播链路 ({len(chains)}):")
        for c in chains[:15]:
            parts.append(f"  {c}")

    risk = result.get("risk", {})
    if risk:
        level = risk.get("level", "?")
        reason = risk.get("reason", "")
        parts.append(f"\n风险评估: {level} — {reason}")
        suggestions = risk.get("suggestions", [])
        if suggestions:
            parts.append(f"  建议: {'; '.join(suggestions)}")

    return _truncate("\n".join(parts))
