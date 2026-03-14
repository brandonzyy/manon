"""Helper functions for manon_init tool."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from shared.ast_sync import set_project

log = logging.getLogger("manon-mcp")

# Will be injected by parent module
_client = None
_hooks = None


def init(client, hooks):
    """Inject dependencies."""
    global _client, _hooks
    _client = client
    _hooks = hooks


# ── Formatting helpers ────────────────────────────────

def _fmt_stats(s: dict) -> str:
    """Format index stats into a single line."""
    fil = s.get("total_files", s.get("files_indexed", 0))
    ent = s.get("total_entities", s.get("entities_added", 0))
    rel = s.get("total_relations", s.get("relations_added", 0))
    chk = s.get("total_chunks", s.get("chunks_added", 0))
    return f"  📊 文件 {fil}  ·  实体 {ent}  ·  关系 {rel}  ·  块 {chk}"


# ── Init workflows ────────────────────────────────────

def _init_existing_project(project_path: str, proj: dict,
                           progress_cb=None) -> tuple[str, list[str], list[str]]:
    """Handle init for an already-registered local project. Returns (rid, lines, graph_lines).

    Only fetches repo status from server. Language detection, test detection,
    smart analysis, coverage, and health are handled by the Skill layer.

    Args:
        progress_cb: optional callable(pct, msg) for progress reporting (thread-safe)
    """
    rid = proj["repo_id"]
    log.info("Local project found: %s (repo_id=%s)", proj['name'], rid)
    lines = [f"  {proj['name']}  ({rid[:8]})"]
    graph_lines: list[str] = []
    sync = proj.get('last_sync', '') or '—'
    tracked = len(proj.get('file_hashes', {}))

    # Fetch repo status from server
    if progress_cb:
        progress_cb(20, "📡 获取服务端状态...")
    try:
        t0 = time.time()
        repo = _client._get(f"/api/v1/repos/{rid}")
        log.info("Fetch repo status took %.1fs", time.time() - t0)
        status = repo['index_status']
        status_icon = "🟢" if status == "done" else "🟡" if status == "indexing" else "⚪"
        graph_lines.append(f"  {status_icon} 索引 {status}  ·  🕐 同步 {sync}")
        if repo.get("index_stats"):
            graph_lines.append(_fmt_stats(repo["index_stats"]))
    except Exception as e:
        log.warning("Failed to fetch repo %s status: %s", rid, e)
        graph_lines.append(f"  🕐 同步 {sync}  ·  📁 跟踪 {tracked} 文件")
        graph_lines.append(f"  ⚠️ 获取服务端状态失败: {e}")

    return rid, lines, graph_lines


def _init_match_or_create(
    project_path: str, project_name: str, header_lines: list[str],
    progress_cb=None,
) -> tuple[str | None, list[str], list[str]] | str:
    """Match existing repo or create new one. Returns (rid, lines, graph_lines) or error string.

    Args:
        progress_cb: optional callable(pct, msg) for progress reporting (thread-safe)
    """
    try:
        repos = _client._get("/api/v1/repos")
    except Exception as e:
        return "\n".join(header_lines) + f"\n\n  ❌ 获取仓库列表失败: {e}"

    # Infer project name from path if not provided
    name = project_name or Path(project_path).resolve().name
    lines = []
    graph_lines: list[str] = []

    # Try to match existing local repo by name
    matched = None
    for r in repos:
        if r.get("source_type") == "local" and r["name"] == name:
            matched = r
            break

    if matched:
        rid = matched["id"]
        lines.append(f"  {name}  ({rid[:8]})")
        status = matched['index_status']
        status_icon = "🟢" if status == "done" else "🟡" if status == "indexing" else "⚪"
        graph_lines.append(f"  {status_icon} 索引 {status}")
        try:
            repo = _client._get(f"/api/v1/repos/{rid}")
            if repo.get("index_stats"):
                graph_lines.append(_fmt_stats(repo["index_stats"]))
        except Exception:
            pass

        if matched.get("source_type") == "local":
            info = {"repo_id": rid, "name": matched["name"], "last_sync": "", "file_hashes": {}}
            set_project(project_path, info)
            lines.append("  ✅ 已注册到本地项目表")

        return rid, lines, graph_lines

    # No match — create new repo
    try:
        result = _client._post("/api/v1/repos", {"name": name, "source_type": "local"})
        rid = result["id"]
        info = {"repo_id": rid, "name": name, "last_sync": "", "file_hashes": {}}
        set_project(project_path, info)
        lines.append(f"  🆕 {name}  ({rid[:8]})")
    except Exception as e:
        lines.append(f"\n  ❌ 创建仓库失败: {e}")
        rid = None

    return rid, lines, graph_lines


# ── Health and hooks ──────────────────────────────────

def _build_health_lines(rid: str) -> list[str]:
    """Fetch and format code health score.

    Uses a short timeout (5s) since this is non-critical and the API
    returns 400 for newly created repos that haven't been indexed yet.
    """
    lines = []
    try:
        health = _client._get(f"/api/v1/repos/{rid}/code-health", timeout=5)
        score = health.get("score", 0)
        grade = health.get("grade", "?")
        dims = health.get("dimensions", {})
        dim_str = "  ".join(f"{k}{v}" for k, v in dims.items())
        lines.append(f"\n💊 代码健康")
        lines.append(f"  {grade} {score:.1f}/100  {dim_str}")
    except Exception as e:
        log.debug("Code health not available (expected for new repos): %s", e)
    return lines


def _build_hooks_lines(project_path: str) -> list[str]:
    """Install git hooks and Claude Code hooks.

    Runs synchronously — hook installation is fast (<1s) and using
    ThreadPoolExecutor caused 10-20s GIL contention delays on Windows
    when background sync threads were active.
    """
    log.info("_build_hooks_lines starting for %s", project_path)
    lines = ["\n🔗 钩子"]

    try:
        t0 = time.time()
        hook_msg = _hooks._install_hook(project_path)
        log.info("Install git hook took %.1fs", time.time() - t0)
        t1 = time.time()
        claude_hook_msg = _hooks._install_claude_hooks()
        log.info("Install claude hooks took %.1fs", time.time() - t1)
        t2 = time.time()
        codex_msg = _hooks._install_codex_config()
        log.info("Install codex config took %.1fs", time.time() - t2)
        lines.append(f"  {hook_msg}" if hook_msg else "  ✅ Push hook 已就绪")
        lines.append(f"  {claude_hook_msg}" if claude_hook_msg else "  ✅ Claude Code hooks 已就绪")
        lines.append(f"  {codex_msg}" if codex_msg else "  ✅ Codex CLI 已就绪")
    except Exception as e:
        log.warning("Hooks installation failed: %s", e)
        lines.append(f"  ⚠️ 钩子安装失败: {e}")

    return lines
