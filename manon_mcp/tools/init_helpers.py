"""Helper functions for the `manon_init` tool."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from core.ast import set_project, load_projects, save_projects

log = logging.getLogger("manon-mcp")


class StaleRepoError(Exception):
    """Raised when local repo_id doesn't exist on the server and can't be recovered."""
    def __init__(self, name: str, repo_id: str):
        self.name = name
        self.repo_id = repo_id
        super().__init__(f"stale repo_id {repo_id} for '{name}'")


def _fmt_stats(stats: dict) -> str:
    """Format index stats into a single line."""
    files = stats.get("total_files", stats.get("files_indexed", 0))
    entities = stats.get("total_entities", stats.get("entities_added", 0))
    relations = stats.get("total_relations", stats.get("relations_added", 0))
    chunks = stats.get("total_chunks", stats.get("chunks_added", 0))
    return f"  files {files} | entities {entities} | relations {relations} | chunks {chunks}"


def _init_existing_project(
    project_path: str,
    proj: dict,
    *,
    client,
    progress_cb=None,
) -> tuple[str, list[str], list[str]]:
    """Handle `manon_init` for an already-registered local project."""
    repo_id = proj["repo_id"]
    lines = [f"  {proj['name']} ({repo_id[:8]})"]
    graph_lines: list[str] = []
    last_sync = proj.get("last_sync", "") or "-"
    tracked = len(proj.get("file_hashes", {}))

    if progress_cb:
        progress_cb(20, "Fetching repo status...")

    try:
        start = time.time()
        repo = client._get(f"/api/v1/repos/{repo_id}")
        log.info("Fetched repo status in %.1fs", time.time() - start)
        status = repo["index_status"]
        graph_lines.append(f"  index {status} | last_sync {last_sync}")
        if repo.get("index_stats"):
            graph_lines.append(_fmt_stats(repo["index_stats"]))
    except Exception as exc:
        if "404" in str(exc) or "not found" in str(exc).lower():
            # repo_id not found on server — try to recover by name match
            log.warning("repo_id %s not found on server, attempting recovery by name '%s'", repo_id, proj["name"])
            recovered = _recover_repo_by_name(project_path, proj, client=client)
            if recovered:
                new_id, new_lines, new_graph = recovered
                return new_id, new_lines, new_graph
            # Cannot recover — clear local registration; raise so caller can re-create
            norm = str(Path(project_path).resolve()).replace("\\", "/")
            data = load_projects()
            data["projects"].pop(norm, None)
            save_projects(data)
            log.warning("Cleared stale local registration for %s (repo_id %s)", proj["name"], repo_id)
            raise StaleRepoError(proj["name"], repo_id)
        else:
            log.warning("Failed to fetch repo %s status: %s", repo_id, exc)
            graph_lines.append(f"  last_sync {last_sync} | tracked_files {tracked}")
            graph_lines.append(f"  warning: failed to fetch server status: {exc}")

    return repo_id, lines, graph_lines


def _recover_repo_by_name(
    project_path: str,
    proj: dict,
    *,
    client,
) -> tuple[str, list[str], list[str]] | None:
    """Try to find the correct repo on the server by name and fix local state."""
    try:
        repos = client._get("/api/v1/repos")
    except Exception:
        return None
    matched = next(
        (r for r in repos if r["name"] == proj["name"] and r.get("source_type") == "local"),
        None,
    )
    if not matched:
        return None
    new_id = matched["id"]
    log.info("Recovered repo '%s': local %s -> server %s", proj["name"], proj["repo_id"][:8], new_id[:8])
    proj["repo_id"] = new_id
    proj["file_hashes"] = {}  # force full re-sync
    proj["last_sync"] = ""
    set_project(project_path, proj)
    lines = [f"  {proj['name']} ({new_id[:8]}) (recovered)"]
    graph_lines = [f"  index {matched['index_status']}"]
    try:
        repo = client._get(f"/api/v1/repos/{new_id}")
        if repo.get("index_stats"):
            graph_lines.append(_fmt_stats(repo["index_stats"]))
    except Exception:
        pass
    return new_id, lines, graph_lines


def _init_match_or_create(
    project_path: str,
    project_name: str,
    header_lines: list[str],
    *,
    client,
    progress_cb=None,
) -> tuple[str | None, list[str], list[str]] | str:
    """Match an existing local repo by name or create a new one."""
    try:
        repos = client._get("/api/v1/repos")
    except Exception as exc:
        return "\n".join(header_lines) + f"\n\n  failed to list repos: {exc}"

    name = project_name or Path(project_path).resolve().name
    lines: list[str] = []
    graph_lines: list[str] = []

    matched = None
    for repo in repos:
        if repo.get("source_type") == "local" and repo["name"] == name:
            matched = repo
            break

    if matched:
        repo_id = matched["id"]
        lines.append(f"  {name} ({repo_id[:8]})")
        graph_lines.append(f"  index {matched['index_status']}")
        try:
            repo = client._get(f"/api/v1/repos/{repo_id}")
            if repo.get("index_stats"):
                graph_lines.append(_fmt_stats(repo["index_stats"]))
        except Exception:
            pass

        info = {"repo_id": repo_id, "name": matched["name"], "last_sync": "", "file_hashes": {}}
        set_project(project_path, info)
        lines.append("  linked existing local repo")
        return repo_id, lines, graph_lines

    try:
        if progress_cb:
            progress_cb(25, "Creating repository...")
        result = client._post("/api/v1/repos", {"name": name, "source_type": "local"})
        repo_id = result["id"]
        info = {"repo_id": repo_id, "name": name, "last_sync": "", "file_hashes": {}}
        set_project(project_path, info)
        lines.append(f"  created {name} ({repo_id[:8]})")
        return repo_id, lines, graph_lines
    except Exception as exc:
        return "\n".join(header_lines + ["", f"  failed to create repo: {exc}"])


def _detect_client() -> str:
    """Detect the host client environment."""
    if os.environ.get("CLAUDE_CODE") or os.environ.get("CLAUDE_CODE_ENTRY"):
        return "claude"
    if os.environ.get("CODEX_CLI") or os.environ.get("CODEX_SESSION_ID"):
        return "codex"
    if os.environ.get("CODEX_SANDBOX_TYPE"):
        return "codex"
    caller = os.environ.get("MCP_CALLER", "").lower()
    if "claude" in caller:
        return "claude"
    if "codex" in caller:
        return "codex"
    return "unknown"


def _build_hooks_lines(project_path: str, *, hooks) -> list[str]:
    """Install git hooks and client-specific helpers."""
    log.info("_build_hooks_lines starting for %s", project_path)
    client_name = _detect_client()
    lines = ["\nHooks"]

    try:
        start = time.time()
        hook_msg = hooks._install_hook(project_path)
        log.info("Install git hook took %.1fs", time.time() - start)
        lines.append(f"  {hook_msg}" if hook_msg else "  git push hook installed")

        if client_name in ("claude", "unknown"):
            start = time.time()
            claude_msg = hooks._install_claude_hooks()
            log.info("Install claude hooks took %.1fs", time.time() - start)
            lines.append(f"  {claude_msg}" if claude_msg else "  Claude Code hooks installed")

        if client_name in ("codex", "unknown"):
            start = time.time()
            codex_msg = hooks._install_codex_config()
            log.info("Install codex config took %.1fs", time.time() - start)
            lines.append(f"  {codex_msg}" if codex_msg else "  Codex CLI config installed")
    except Exception as exc:
        log.warning("Hook installation failed: %s", exc)
        lines.append(f"  warning: hook install failed: {exc}")

    return lines
