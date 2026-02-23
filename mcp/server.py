"""Manon MCP Server — expose code intelligence as Claude Code tools.

Supports both git-based repos (server-side clone) and local repos
(client-side AST extraction + cloud sync).

Uses shared.ast_sync for project registry and AST scanning.
Keeps sync HTTP helpers for MCP tool compatibility.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from shared.ast_sync import (
    load_projects, save_projects, get_project, set_project,
    find_project_by_repo_id, scan_and_parse, count_scannable_files,
    ensure_parsers, detect_languages,
    SYNC_BATCH_SIZE,
)

mcp = FastMCP("manon", instructions="Manon 代码智能工具 — 语义搜索、图遍历、影响分析")

log = logging.getLogger("manon-mcp")

# ── Log to file (MCP stdio occupies stdout/stderr) ────
_log_dir = Path.home() / ".manon"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_handler = logging.FileHandler(_log_dir / "mcp.log", encoding="utf-8")
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
log.addHandler(_log_handler)
log.setLevel(logging.DEBUG)

# ── Config ────────────────────────────────────────────
MAX_RESPONSE_CHARS = 8000
HTTP_TIMEOUT = 45
INLINE_SCAN_LIMIT = 50  # must be ≤ SYNC_BATCH_SIZE to fit in one HTTP call

def _get_client_version() -> str:
    """Read version from VERSION file, or fall back to git commit count."""
    install_dir = Path(__file__).resolve().parent.parent
    # 1. VERSION file (written by CI sync workflow for public releases)
    version_file = install_dir / "VERSION"
    try:
        v = version_file.read_text(encoding="utf-8").strip()
        if v:
            return v
    except Exception:
        pass
    # 2. Git commit count (works in dev / private repo)
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(install_dir),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            count = result.stdout.strip()
            return f"0.1.{count}"
    except Exception:
        pass
    return "0.1.0"

CLIENT_VERSION = _get_client_version()

# ── Geo-routing ───────────────────────────────────────
API_URL_CN = os.environ.get("MANON_API_URL_CN", "http://117.131.45.179:3700")
# TODO: SD-WAN 新加坡公网 IP 就绪后填入，格式 "http://<sg-ip>:3700"
# 空值时 INTL 用户自动回落到 API_URL_CN
API_URL_INTL = os.environ.get("MANON_API_URL_INTL", "")
API_KEY = os.environ.get("MANON_API_KEY", "")
_explicit_url = os.environ.get("MANON_API_URL", "")
_REGION_CACHE = Path.home() / ".manon" / "region.json"
GIT_REMOTE_CN = "https://gitee.com/ymxy_1_0/manon.git"
GIT_REMOTE_INTL = "https://github.com/brandonzyy/manon.git"
GIT_BRANCH_CN = "master"
GIT_BRANCH_INTL = "main"


def _detect_region() -> str:
    """Detect user region. Returns 'CN' or 'INTL'.

    Priority: OS locale/timezone (fast, offline) → IP lookup (slow, online).
    """
    # 1) OS-level hints — instant, no network
    import locale
    try:
        loc = locale.getdefaultlocale()[0] or ""
        if loc.startswith("zh_CN") or loc.startswith("zh_Hans"):
            return "CN"
    except Exception:
        pass
    # Windows: check system timezone
    import platform
    if platform.system() == "Windows":
        try:
            import subprocess as _sp
            tz = _sp.run(
                ["powershell", "-c", "(Get-TimeZone).Id"],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip()
            if "China" in tz or "Beijing" in tz or "Shanghai" in tz:
                return "CN"
        except Exception:
            pass
    # 2) IP lookup — fallback
    for endpoint, country_key in [
        ("https://api.country.is/", "country"),
        ("https://ipinfo.io/json", "country"),
    ]:
        try:
            r = httpx.get(endpoint, timeout=3)
            data = r.json()
            country = data.get(country_key, "")
            if country.upper() == "CN":
                return "CN"
            if country:
                return "INTL"
        except Exception as e:
            log.debug("Region detect via %s failed: %s", endpoint, e)
            continue
    return "CN"


def _get_cached_region() -> str:
    """Read cached region, or default to CN and detect in background."""
    try:
        if _REGION_CACHE.exists():
            data = json.loads(_REGION_CACHE.read_text(encoding="utf-8"))
            return data.get("region", "CN")
    except Exception:
        pass
    # No cache — default CN, detect in background for next time
    import threading
    def _bg_detect():
        try:
            region = _detect_region()
            _REGION_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _REGION_CACHE.write_text(
                json.dumps({"region": region}), encoding="utf-8",
            )
            log.info("Region detected and cached: %s", region)
        except Exception:
            pass
    threading.Thread(target=_bg_detect, daemon=True).start()
    return "CN"


def _resolve_api_url() -> str:
    if _explicit_url:
        return _explicit_url
    region = _get_cached_region()
    if region == "CN":
        url = API_URL_CN
    else:
        url = API_URL_INTL or API_URL_CN  # INTL 未配置时回落到 CN
    log.info("Geo-routing: region=%s, api_url=%s", region, url)
    return url


REGION = _get_cached_region()
API_URL = _resolve_api_url()

# ── Version check ─────────────────────────────────────
_version_checked = False
_update_notice: str = ""


def _check_version() -> str:
    """Compare local version with public repo via hosting API (no git ops).

    CN → Gitee API, INTL → GitHub API. Non-blocking.
    """
    global _version_checked, _update_notice
    if _version_checked:
        return _update_notice
    _version_checked = True
    try:
        if REGION == "CN":
            url = "https://gitee.com/api/v5/repos/ymxy_1_0/manon"
        else:
            url = "https://api.github.com/repos/brandonzyy/manon"
        r = httpx.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            # Gitee: pushed_at, GitHub: pushed_at — compare with local version age
            pushed = data.get("pushed_at", "")
            if pushed:
                import datetime
                remote_time = datetime.datetime.fromisoformat(pushed.replace("Z", "+00:00"))
                # Get local HEAD commit time
                install_dir = Path(__file__).resolve().parent.parent
                result = subprocess.run(
                    ["git", "log", "-1", "--format=%cI"],
                    cwd=str(install_dir), capture_output=True, text=True, timeout=3,
                )
                if result.returncode == 0 and result.stdout.strip():
                    local_time = datetime.datetime.fromisoformat(result.stdout.strip())
                    if remote_time > local_time + datetime.timedelta(hours=1):
                        _update_notice = (
                            f"\n⚠ 有新版本可用（当前 {CLIENT_VERSION}），调用 manon_update 更新"
                        )
    except Exception:
        pass
    return _update_notice


def _headers():
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def _get(path: str, *, timeout: int = HTTP_TIMEOUT, **params) -> dict:
    with httpx.Client(base_url=API_URL, headers=_headers(), timeout=timeout) as c:
        r = c.get(path, params=params)
        r.raise_for_status()
        return r.json()


def _get_no_auth(path: str) -> dict:
    with httpx.Client(base_url=API_URL, timeout=10) as c:
        r = c.get(path)
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict, *, timeout: int = HTTP_TIMEOUT) -> dict:
    with httpx.Client(base_url=API_URL, headers=_headers(), timeout=timeout) as c:
        r = c.post(path, json=body)
        r.raise_for_status()
        return r.json()


def _delete(path: str) -> None:
    with httpx.Client(base_url=API_URL, headers=_headers(), timeout=HTTP_TIMEOUT) as c:
        r = c.delete(path)
        r.raise_for_status()



def _sync_to_server(repo_id: str, file_results: list, deleted_files: list, full_reindex: bool = False) -> dict:
    """Upload AST data to server in batches."""
    last_result = {}
    for i in range(0, max(len(file_results), 1), SYNC_BATCH_SIZE):
        batch = file_results[i:i + SYNC_BATCH_SIZE]
        # Only send deleted_files in the first batch
        payload = {
            "files": batch,
            "deleted_files": deleted_files if i == 0 else [],
            "full_reindex": full_reindex and i == 0,
        }
        last_result = _post(f"/api/v1/repos/{repo_id}/sync-ast", payload)
    return last_result


# ── Response formatting ───────────────────────────────

def _truncate(text: str, limit: int = MAX_RESPONSE_CHARS) -> str:
    """Hard-truncate text to protect LLM context window."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... (已截断，共 {len(text)} 字符。用 manon_deep_query 获取完整分析)"


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

    changed = result.get("changed_symbols", [])
    if changed:
        parts.append(f"\n变更符号 ({len(changed)}):")
        for s in changed[:15]:
            if isinstance(s, dict):
                parts.append(f"  {s.get('name', '?')} [{s.get('kind', '?')}] {s.get('file_path', '')}")
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

    risk = result.get("risk", {})
    if risk:
        parts.append(f"\n风险评估: {risk.get('level', '?')} — {risk.get('reason', '')}")

    return _truncate("\n".join(parts))


def _local_impact(repo_id: str, local_path: str, commit: str, max_depth: int) -> str:
    """Client-side impact analysis for local repos using git diff + server graph."""
    import re

    root = Path(local_path).resolve()
    parts: list[str] = []

    # 0. Detect git root and compute project prefix
    #    Git root may differ from project root (e.g. git root = 一码行云, project = 一码行云/donnie)
    try:
        git_root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(root), capture_output=True, text=True, encoding="utf-8", timeout=5,
        )
        if git_root_result.returncode == 0:
            git_root = Path(git_root_result.stdout.strip()).resolve()
        else:
            git_root = root
    except Exception:
        git_root = root

    # prefix to strip from git diff paths (e.g. "donnie/")
    try:
        rel_prefix = root.relative_to(git_root).as_posix()
    except ValueError:
        rel_prefix = ""
    prefix_with_slash = (rel_prefix + "/") if rel_prefix else ""

    # 1. Get changed files from git diff
    #    Monorepo fix: find last commit touching project files, not just HEAD~1
    #    Also detect uncommitted/staged changes
    base_commit = commit  # resolved base for unified diff later
    try:
        if commit == "HEAD":
            # In monorepo, HEAD~1 may not touch this project at all.
            # Find the last commit that changed files under the project prefix.
            if prefix_with_slash:
                log_cmd = ["git", "log", "-1", "--format=%H", "--", prefix_with_slash]
            else:
                log_cmd = ["git", "log", "-1", "--format=%H"]
            log_result = subprocess.run(log_cmd, cwd=str(git_root), capture_output=True, text=True, encoding="utf-8", timeout=10)
            last_project_commit = log_result.stdout.strip() if log_result.returncode == 0 else ""

            if last_project_commit:
                base_commit = last_project_commit
                diff_cmd = ["git", "diff", f"{last_project_commit}~1", last_project_commit, "--name-only"]
                commit_msg_cmd = ["git", "log", "-1", "--format=%h %s", last_project_commit]
            else:
                diff_cmd = ["git", "diff", "HEAD~1", "--name-only"]
                commit_msg_cmd = ["git", "log", "-1", "--format=%h %s"]
        else:
            base_commit = commit
            diff_cmd = ["git", "diff", f"{commit}~1", commit, "--name-only"]
            commit_msg_cmd = ["git", "log", "-1", "--format=%h %s", commit]

        msg_result = subprocess.run(commit_msg_cmd, cwd=str(git_root), capture_output=True, text=True, encoding="utf-8", timeout=10)
        commit_info = msg_result.stdout.strip() if msg_result.returncode == 0 else commit

        diff_result = subprocess.run(diff_cmd, cwd=str(git_root), capture_output=True, text=True, encoding="utf-8", timeout=10)
        if diff_result.returncode != 0:
            return f"git diff 失败: {diff_result.stderr.strip()}"

        raw_files = [f for f in diff_result.stdout.strip().split("\n") if f]

        # Also detect uncommitted + staged changes (working tree vs HEAD)
        wt_result = subprocess.run(
            ["git", "diff", "HEAD", "--name-only"],
            cwd=str(git_root), capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        if wt_result.returncode == 0:
            wt_files = [f for f in wt_result.stdout.strip().split("\n") if f]
            raw_files = list(dict.fromkeys(raw_files + wt_files))  # dedupe, preserve order

        # Filter to project files and strip prefix
        changed_files = []
        for f in raw_files:
            if prefix_with_slash and f.startswith(prefix_with_slash):
                changed_files.append(f[len(prefix_with_slash):])
            elif not prefix_with_slash:
                changed_files.append(f)
            # else: file outside project, skip
    except Exception as e:
        return f"git 操作失败: {e}"

    if not changed_files:
        diag_parts = [
            f"commit={commit}",
            f"git_root={git_root}",
            f"project={root}",
            f"prefix={prefix_with_slash!r}",
            f"raw_files={raw_files[:5]}",
        ]
        return f"没有检测到文件变更。\n诊断: {', '.join(diag_parts)}"

    parts.append(f"影响分析: {commit_info}")
    parts.append(f"变更文件 ({len(changed_files)}):")
    for f in changed_files:
        parts.append(f"  {f}")

    # 2. Get changed line ranges to identify affected symbols
    changed_symbols: list[str] = []
    for cf in changed_files[:15]:  # cap to avoid subprocess storm
        ext = Path(cf).suffix.lower()
        if ext not in (".ts", ".tsx", ".js", ".jsx", ".py", ".java", ".php"):
            continue
        try:
            # Use the resolved base_commit for unified diff
            # Prepend prefix so git can find the file from git_root
            git_file_path = (prefix_with_slash + cf) if prefix_with_slash else cf
            if base_commit == commit and commit == "HEAD":
                udiff_ref = f"{base_commit}~1"
            elif commit == "HEAD":
                udiff_ref = f"{base_commit}~1..{base_commit}"
            else:
                udiff_ref = f"{commit}~1..{commit}"
            udiff = subprocess.run(
                ["git", "diff", udiff_ref,
                 "--unified=0", "--", git_file_path],
                cwd=str(git_root), capture_output=True, text=True, encoding="utf-8", timeout=10,
            )
            changed_lines: set[int] = set()
            for line in udiff.stdout.split("\n"):
                m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if m:
                    # Added lines
                    add_start = int(m.group(3))
                    add_count = int(m.group(4)) if m.group(4) else 1
                    changed_lines.update(range(add_start, add_start + add_count))
                    # Deleted lines — use the deletion position in the new file
                    if add_count == 0 and add_start > 0:
                        changed_lines.add(add_start)

            # Also check uncommitted changes for this file
            wt_udiff = subprocess.run(
                ["git", "diff", "HEAD", "--unified=0", "--", git_file_path],
                cwd=str(git_root), capture_output=True, text=True, encoding="utf-8", timeout=10,
            )
            for line in wt_udiff.stdout.split("\n"):
                m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if m:
                    add_start = int(m.group(3))
                    add_count = int(m.group(4)) if m.group(4) else 1
                    changed_lines.update(range(add_start, add_start + add_count))
                    if add_count == 0 and add_start > 0:
                        changed_lines.add(add_start)

            if not changed_lines:
                continue

            # Parse file to get symbols with line ranges
            full_path = root / cf
            if not full_path.exists():
                continue
            from codeindex.parser import parse_file
            pr = parse_file(full_path)
            for sym in pr.symbols:
                if hasattr(sym, "line_start") and hasattr(sym, "line_end"):
                    sym_lines = set(range(sym.line_start, sym.line_end + 1))
                    if sym_lines & changed_lines:
                        changed_symbols.append(sym.name)
                elif hasattr(sym, "line_number"):
                    if sym.line_number in changed_lines:
                        changed_symbols.append(sym.name)
        except Exception as e:
            log.debug("Failed to analyze %s: %s", cf, e)

    if not changed_symbols:
        parts.append("\n未能精确定位变更符号，按文件级别分析。")
        # Fall back to querying all symbols in changed files
        for cf in changed_files:
            module = cf.rsplit(".", 1)[0].replace("/", ".").replace("\\", ".")
            try:
                result = _get(f"/api/v1/repos/{repo_id}/graph", symbol=module, depth=1)
                for r in result.get("relations", [])[:5]:
                    parts.append(f"  {r.get('src_id', '?')} --{r.get('kind', '?')}--> {r.get('tgt_id', '?')}")
            except Exception:
                pass
        return _truncate("\n".join(parts))

    # Deduplicate
    changed_symbols = list(dict.fromkeys(changed_symbols))
    parts.append(f"\n变更符号 ({len(changed_symbols)}):")
    for s in changed_symbols:
        parts.append(f"  {s}")

    # 3. Query graph for callers of each changed symbol (parallel, capped)
    import concurrent.futures
    all_callers: list[str] = []
    affected_modules: set[str] = set()
    syms_to_query = changed_symbols[:8]

    def _query_sym(sym):
        try:
            return sym, _get(f"/api/v1/repos/{repo_id}/graph", symbol=sym, depth=max_depth, timeout=8)
        except Exception:
            return sym, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_query_sym, s): s for s in syms_to_query}
        for fut in concurrent.futures.as_completed(futures, timeout=15):
            try:
                sym, result = fut.result()
            except Exception:
                continue
            if not result:
                continue
            for r in result.get("relations", []):
                src = r.get("src_id", "")
                tgt = r.get("tgt_id", "")
                kind = r.get("kind", "")
                if kind == "calls" and tgt and sym in tgt:
                    all_callers.append(f"  {src} --calls--> {tgt}")
                    mod = ".".join(src.split(".")[:-1]) if "." in src else src
                    affected_modules.add(mod)

    if all_callers:
        callers_dedup = list(dict.fromkeys(all_callers))
        parts.append(f"\n调用者 ({len(callers_dedup)}):")
        parts.extend(callers_dedup[:20])

    if affected_modules:
        parts.append(f"\n受影响模块 ({len(affected_modules)}):")
        for m in sorted(affected_modules):
            parts.append(f"  {m}")

    # 4. Risk assessment
    risk = "low"
    if len(affected_modules) > 5:
        risk = "high"
    elif len(affected_modules) > 2:
        risk = "medium"
    parts.append(f"\n风险评估: {risk} — 影响 {len(affected_modules)} 个模块, {len(changed_symbols)} 个符号变更")

    return _truncate("\n".join(parts))


# ── Tools ──────────────────────────────────────────────

@mcp.tool()
def manon_repos_list() -> str:
    """列出当前租户的所有代码仓库及其索引状态。"""
    repos = _get("/api/v1/repos")
    if not repos:
        return "没有仓库。用 manon_repos_create 添加。"
    lines = []
    for r in repos:
        icon = {"done": "+", "indexing": "~", "error": "x"}.get(r["index_status"], "-")
        src = " [local]" if r.get("source_type") == "local" else ""
        lines.append(f"  {icon} {r['id']}  {r['name']:<20s}  {r['index_status']}{src}")
    return "\n".join(lines)


@mcp.tool()
def manon_search(repo_id: str, query: str, top_k: int = 10, depth: int = 1) -> str:
    """语义搜索代码库。用自然语言描述你要找的内容，返回相关的代码实体、关系和上下文。

    Args:
        repo_id: 仓库 ID（从 manon_repos_list 获取）
        query: 搜索内容，如 "用户认证流程"、"数据库连接池"
        top_k: 返回结果数量（默认 10）
        depth: 图遍历深度（默认 1）
    """
    result = _get(f"/api/v1/repos/{repo_id}/search", q=query, top_k=top_k, depth=depth)
    if result.get("context"):
        return _truncate(result["context"])
    if not result.get("entities") and not result.get("chunks"):
        return f"未找到与 '{query}' 相关的结果。"
    return _format_search(result)


@mcp.tool()
def manon_graph(repo_id: str, symbol: str, depth: int = 1, direction: str = "both") -> str:
    """查询代码符号的调用关系和依赖图。

    Args:
        repo_id: 仓库 ID
        symbol: 代码符号名，如 "UserService"、"authenticate"
        depth: 遍历深度（默认 1，最大 3）
        direction: 遍历方向 - "both"(双向), "callers"(只查上游调用者), "callees"(只查下游被调用者)
    """
    result = _get(f"/api/v1/repos/{repo_id}/graph", symbol=symbol, depth=depth, direction=direction)
    if result.get("context"):
        return _truncate(result["context"])
    return _format_graph(result)


@mcp.tool()
def manon_impact(repo_id: str, commit: str = "HEAD", max_depth: int = 2) -> str:
    """分析某次 commit 的影响范围。返回变更的符号、直接/间接调用者、受影响模块和风险评估。

    Args:
        repo_id: 仓库 ID
        commit: commit hash（默认 HEAD）
        max_depth: 影响传播深度（默认 2）
    """
    # For local repos, do client-side impact analysis using local git + server graph
    found = find_project_by_repo_id(repo_id)
    if found:
        return _local_impact(repo_id, found[0], commit, max_depth)

    result = _get(f"/api/v1/repos/{repo_id}/impact", commit=commit, max_depth=max_depth)
    return _format_impact(result)


@mcp.tool()
def manon_index(repo_id: str, incremental: bool = True) -> str:
    """触发代码索引构建。索引完成后才能进行搜索和分析。

    Args:
        repo_id: 仓库 ID
        incremental: 增量索引（默认 True），设为 False 全量重建
    """
    # For local repos, do a local scan + sync (with limit to avoid timeout)
    found = find_project_by_repo_id(repo_id)
    if found:
        local_path, info = found
        old_hashes = {} if not incremental else info.get("file_hashes", {})
        # Full reindex: no file limit (must upload all files before server clears old data)
        limit = None if not incremental else INLINE_SCAN_LIMIT
        file_results, deleted, new_hashes = scan_and_parse(
            local_path, old_hashes, max_files=limit,
        )
        if file_results or deleted:
            _sync_to_server(repo_id, file_results, deleted, full_reindex=not incremental)
        # Only record hashes for actually synced files; keep old hashes for
        # unsynced files so the next call picks them up as changed.
        # Exception: full reindex (non-incremental) writes all hashes since
        # there's no file limit in that mode.
        if not incremental:
            info["file_hashes"] = new_hashes
        else:
            synced_set = {f["rel_path"] for f in file_results}
            partial_hashes = dict(old_hashes)
            for f in file_results:
                rp = f["rel_path"]
                if rp in new_hashes:
                    partial_hashes[rp] = new_hashes[rp]
            for d in deleted:
                partial_hashes.pop(d, None)
            info["file_hashes"] = partial_hashes
        info["last_sync"] = __import__("datetime").datetime.now().isoformat()
        set_project(local_path, info)

        # Check if there are remaining unsynced files
        synced_set = {f["rel_path"] for f in file_results}
        unsynced = [k for k, v in new_hashes.items()
                    if info["file_hashes"].get(k) != v and k not in synced_set]
        msg = f"本地扫描完成: {len(file_results)} 文件已同步, {len(deleted)} 文件删除。"
        if unsynced:
            msg += f"\n还有 {len(unsynced)} 文件未同步（超出单次限制），请再次调用 manon_index {repo_id} 继续。"
        else:
            msg += "\n所有文件已同步。用 manon_index_status 查看索引进度。"
        return msg

    result = _post(f"/api/v1/repos/{repo_id}/index", {"incremental": incremental})
    return f"索引已触发: {result['status']}。用 manon_index_status 查看进度。"


@mcp.tool()
def manon_index_status(repo_id: str) -> str:
    """查看仓库的索引状态和统计信息。

    Args:
        repo_id: 仓库 ID
    """
    result = _get(f"/api/v1/repos/{repo_id}/index-status")
    s = result["status"]
    stats = result.get("stats")
    msg = f"状态: {s}"
    if stats:
        msg += f"\n文件扫描: {stats.get('total_files', stats.get('files_scanned', stats.get('files_synced', 0)))}"
        msg += f", 索引: {stats.get('files_indexed', 0)}"
        msg += f"\n实体: {stats.get('total_entities', stats.get('entities_added', 0))}"
        msg += f", 关系: {stats.get('total_relations', stats.get('relations_added', 0))}"
        msg += f", 块: {stats.get('total_chunks', stats.get('chunks_added', 0))}"
    return msg


@mcp.tool()
def manon_repos_create(name: str, git_url: str = "", branch: str = "main", local_path: str = "") -> str:
    """创建新的代码仓库。支持 git URL（服务端 clone）或本地路径（客户端 AST 同步）。

    Args:
        name: 仓库名称
        git_url: Git 仓库地址（可选，服务端会自动 clone）
        branch: 分支名（默认 main）
        local_path: 本地项目路径（与 git_url 二选一，会在本地提取 AST 上传到云端）
    """
    if local_path and not git_url:
        # Local AST sync mode
        resolved = str(Path(local_path).resolve())
        if not Path(resolved).is_dir():
            return f"路径不存在: {resolved}"

        # Create repo on server with source_type="local"
        result = _post("/api/v1/repos", {
            "name": name, "branch": branch,
            "source_type": "local",
        })
        repo_id = result["id"]

        # Register locally IMMEDIATELY (before any slow ops)
        set_project(resolved, {
            "repo_id": repo_id, "name": name,
            "last_sync": "", "file_hashes": {},
        })

        # Always defer scanning to manon_index
        file_count = count_scannable_files(resolved)
        return (
            f"仓库已创建: id={repo_id}, name={name}\n"
            f"本地路径: {resolved}\n"
            f"检测到 {file_count} 个文件，请调用 manon_index {repo_id} 开始索引。"
        )

    # Git URL mode — original logic
    body: dict = {"name": name, "branch": branch}
    if git_url:
        body["git_url"] = git_url
    if local_path:
        body["local_path"] = local_path
    result = _post("/api/v1/repos", body)
    return f"仓库已创建: id={result['id']}, name={result['name']}, status={result['index_status']}"


@mcp.tool()
def manon_repos_get(repo_id: str) -> str:
    """查看仓库详情。

    Args:
        repo_id: 仓库 ID
    """
    result = _get(f"/api/v1/repos/{repo_id}")
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def manon_repos_delete(repo_id: str) -> str:
    """删除仓库及其所有索引数据。

    Args:
        repo_id: 仓库 ID
    """
    # Also clean up local project mapping
    found = find_project_by_repo_id(repo_id)
    if found:
        local_path, _ = found
        data = load_projects()
        data["projects"].pop(local_path, None)
        save_projects(data)

    _delete(f"/api/v1/repos/{repo_id}")
    return f"仓库 {repo_id} 已删除。"


@mcp.tool()
def manon_push_update(repo_id: str) -> str:
    """拉取最新代码并增量重建索引。本地仓库会扫描变更文件并上传 AST。

    Args:
        repo_id: 仓库 ID
    """
    # Check if this is a local project
    found = find_project_by_repo_id(repo_id)
    if found:
        local_path, info = found
        old_hashes = info.get("file_hashes", {})
        file_results, deleted, new_hashes = scan_and_parse(
            local_path, old_hashes, max_files=INLINE_SCAN_LIMIT,
        )
        if not file_results and not deleted:
            return "没有检测到文件变更。"
        _sync_to_server(repo_id, file_results, deleted)
        # Only record hashes for actually synced files; keep old hashes for
        # unsynced files so the next call picks them up as changed.
        synced_set = {f["rel_path"] for f in file_results}
        partial_hashes = dict(old_hashes)
        for f in file_results:
            rp = f["rel_path"]
            if rp in new_hashes:
                partial_hashes[rp] = new_hashes[rp]
        for d in deleted:
            partial_hashes.pop(d, None)
        info["file_hashes"] = partial_hashes
        info["last_sync"] = __import__("datetime").datetime.now().isoformat()
        set_project(local_path, info)

        unsynced = [k for k, v in new_hashes.items()
                    if partial_hashes.get(k) != v and k not in synced_set]
        msg = f"增量同步: {len(file_results)} 文件已同步, {len(deleted)} 文件删除。"
        if unsynced:
            msg += f"\n还有 {len(unsynced)} 文件未同步，请再次调用 manon_push_update {repo_id} 继续。"
        else:
            msg += "\n用 manon_index_status 查看索引进度。"
        return msg

    # Not a registered local project — check if it's a local-type repo on server
    try:
        repo = _get(f"/api/v1/repos/{repo_id}")
        if repo.get("source_type") == "local":
            return (
                f"本地项目未注册（可能 manon_init 超时未完成）。\n"
                f"请先在项目目录执行 manon_init 注册本地项目，再调用 push_update。"
            )
    except Exception:
        pass

    # Fallback: server-side git pull (only for git-url repos)
    result = _post(f"/api/v1/repos/{repo_id}/push-update", {})
    return f"更新已触发: {result['status']}。用 manon_index_status 查看进度。"


# ── Graph completeness check ─────────────────────────

def _ensure_graph_complete(project_path: str, repo_id: str, proj_info: dict) -> list[str]:
    """Check if knowledge graph is complete; sync missing files if needed.

    Compares local scannable file count against cached file_hashes.
    If files are missing, loops scan_and_parse in batches until caught up.

    Returns list of status lines to append to manon_init output.
    """
    import datetime

    try:
        local_count = count_scannable_files(project_path)
    except Exception as e:
        log.warning("count_scannable_files failed: %s", e)
        return [f"  ⚠️ 文件扫描失败: {e}"]

    synced_count = len(proj_info.get("file_hashes", {}))

    if local_count <= synced_count:
        return ["  ✅ 图谱完整"]

    # Files are missing — loop sync
    lines: list[str] = []
    lines.append(f"  🔄 图谱不完整（本地 {local_count} / 已同步 {synced_count}），自动补齐中...")
    total_synced = 0
    total_deleted = 0

    while True:
        old_hashes = proj_info.get("file_hashes", {})
        try:
            file_results, deleted, new_hashes = scan_and_parse(
                project_path, old_hashes, max_files=INLINE_SCAN_LIMIT,
            )
        except Exception as e:
            log.warning("scan_and_parse failed during completeness check: %s", e)
            lines.append(f"  ⚠️ 扫描失败: {e}")
            break

        if not file_results and not deleted:
            break

        try:
            _sync_to_server(repo_id, file_results, deleted)
        except Exception as e:
            log.warning("sync failed during completeness check: %s", e)
            lines.append(f"  ⚠️ 同步失败: {e}")
            break

        # Incremental hash update (same partial logic as manon_index)
        partial_hashes = dict(old_hashes)
        for f in file_results:
            rp = f["rel_path"]
            if rp in new_hashes:
                partial_hashes[rp] = new_hashes[rp]
        for d in deleted:
            partial_hashes.pop(d, None)
        proj_info["file_hashes"] = partial_hashes
        proj_info["last_sync"] = datetime.datetime.now().isoformat()
        set_project(project_path, proj_info)

        total_synced += len(file_results)
        total_deleted += len(deleted)
        synced_count = len(partial_hashes)
        log.info("Completeness sync batch: +%d synced, +%d deleted, total tracked=%d",
                 len(file_results), len(deleted), synced_count)

    if total_synced or total_deleted:
        lines.append(f"  ✅ 补齐完成: +{total_synced} 文件同步, {total_deleted} 文件删除 (共 {synced_count} 文件)")
    else:
        # No new files but counts didn't match — hashes were stale
        lines[-1] = "  ✅ 图谱完整"

    return lines


# ── Init / Config / Deep Query ────────────────────────

@mcp.tool()
def manon_init(project_path: str, project_name: str = "") -> str:
    """初始化当前项目的 Manon 连接。检查 API 可达性、匹配或创建仓库、展示图谱状态。

    Args:
        project_path: 项目在本机的绝对路径（通常是当前工作目录）
        project_name: 项目名称（可选，默认从路径推断）
    """
    log.info("manon_init called: path=%s, name=%s", project_path, project_name)
    # 1. health check
    try:
        health = _get_no_auth("/health")
        log.info("Health check OK")
    except Exception as e:
        log.error("Health check failed: %s", e)
        return f"❌ Manon API 不可达 ({API_URL}): {e}\n   请确认 saas 服务已启动。"

    lines = [f"─── 🧠 Manon v{CLIENT_VERSION} {'─' * 28}"]
    lines.append("  ✅ API 连接成功")
    # Report previous background update result (if any)
    prev = _read_update_status()
    if prev:
        lines.append(prev)

    def _fmt_stats(s: dict) -> str:
        fil = s.get("total_files", s.get("files_indexed", 0))
        ent = s.get("total_entities", s.get("entities_added", 0))
        rel = s.get("total_relations", s.get("relations_added", 0))
        chk = s.get("total_chunks", s.get("chunks_added", 0))
        return f"  📊 文件 {fil}  ·  实体 {ent}  ·  关系 {rel}  ·  块 {chk}"

    # 2. Check local project registry first
    proj = get_project(project_path)
    if proj:
        rid = proj["repo_id"]
        log.info("Local project found: %s (repo_id=%s)", proj['name'], rid)
        lines.append(f"\n  📦 {proj['name']}  ({rid[:8]})")
        sync = proj.get('last_sync', '') or '—'
        tracked = len(proj.get('file_hashes', {}))
        try:
            repo = _get(f"/api/v1/repos/{rid}")
            status = repo['index_status']
            status_icon = "🟢" if status == "done" else "🟡" if status == "indexing" else "⚪"
            lines.append(f"  {status_icon} 状态 {status}  ·  🕐 同步 {sync}")
            if repo.get("index_stats"):
                lines.append(_fmt_stats(repo["index_stats"]))
        except Exception as e:
            lines.append(f"  🕐 同步 {sync}  ·  📁 跟踪 {tracked} 文件")
            lines.append(f"  ⚠️ 获取服务端状态失败: {e}")
            log.warning("Failed to fetch repo %s status: %s", rid, e)
        # Check graph completeness and auto-sync missing files
        lines.extend(_ensure_graph_complete(project_path, rid, proj))
        return "\n".join(lines)

    # 3. Check server repos by name match
    try:
        repos = _get("/api/v1/repos")
    except Exception as e:
        return "\n".join(lines) + f"\n\n  ❌ 获取仓库列表失败: {e}"

    norm = project_path.replace("\\", "/").rstrip("/")
    name = project_name or norm.split("/")[-1]
    matched = None
    for r in repos:
        if r.get("name") == name:
            matched = r
            break

    if matched:
        rid = matched["id"]
        lines.append(f"\n  📦 {matched['name']}  ({rid[:8]})")
        status = matched['index_status']
        status_icon = "🟢" if status == "done" else "🟡" if status == "indexing" else "⚪"
        lines.append(f"  {status_icon} 状态 {status}")
        # Fetch detailed stats
        try:
            repo = _get(f"/api/v1/repos/{rid}")
            if repo.get("index_stats"):
                lines.append(_fmt_stats(repo["index_stats"]))
        except Exception:
            pass
        # Register locally if it's a local source_type
        if matched.get("source_type") == "local":
            info = {
                "repo_id": rid, "name": matched["name"],
                "last_sync": "", "file_hashes": {},
            }
            set_project(project_path, info)
            lines.append("  ✅ 已注册到本地项目表")
            lines.extend(_ensure_graph_complete(project_path, rid, info))
    else:
        # 4. Create new local repo — fast path, no inline scanning
        try:
            result = _post("/api/v1/repos", {
                "name": name, "source_type": "local",
            })
            rid = result["id"]

            # Register locally IMMEDIATELY (before any slow ops)
            info = {
                "repo_id": rid, "name": name,
                "last_sync": "", "file_hashes": {},
            }
            set_project(project_path, info)
            lines.append(f"\n  🆕 {name}  ({rid[:8]})")

            # Auto-detect languages and install parsers (best-effort)
            try:
                parser_status = ensure_parsers(project_path)
                if parser_status:
                    all_langs = sorted(parser_status.keys())
                    lines.append(f"  🗂️ 语言: {', '.join(all_langs)}")
            except Exception:
                pass

            lines.extend(_ensure_graph_complete(project_path, rid, info))
        except Exception as e:
            lines.append(f"\n  ❌ 创建仓库失败: {e}")

    # Auto-install pre-push hook
    hook_msg = _install_hook(project_path)
    if hook_msg:
        lines.append(hook_msg)

    return "\n".join(lines)


@mcp.tool()
def manon_config() -> str:
    """查看当前 Manon 配置和连接状态。"""
    log.info("manon_config called")
    lines = [f"─── ⚙️ Manon 配置 {'─' * 28}"]
    lines.append(f"  🏷️ 版本  {CLIENT_VERSION}")
    lines.append(f"  🌐 区域  {REGION}")
    lines.append(f"  🔗 API   {API_URL}")
    # Try fetching server config with short timeout
    import time as _time
    t0 = _time.monotonic()
    try:
        cfg = _get("/api/v1/config", timeout=3)
        elapsed = _time.monotonic() - t0
        log.info("manon_config /api/v1/config OK in %.1fs", elapsed)
        lines.append(f"  💎 套餐  {cfg['tier']}")
        lines.append(f"  ⚡ 限速  {cfg['rate_limit']} req/min")
    except Exception as e:
        elapsed = _time.monotonic() - t0
        log.warning("manon_config /api/v1/config failed in %.1fs: %s", elapsed, e)
        lines.append("  ⚠️ 服务  连接超时")
    # Show cached update notice; trigger background check if not yet done
    if _update_notice:
        lines.append(_update_notice)
    elif not _version_checked:
        import threading
        threading.Thread(target=_check_version, daemon=True).start()
    return "\n".join(lines)


@mcp.tool()
def manon_account() -> str:
    """查看账户信息：套餐、配额使用情况、近 30 天用量。"""
    try:
        acc = _get("/api/v1/account")
    except Exception as e:
        return f"获取账户信息失败: {e}"
    q = acc["quotas"]
    lines = [
        f"套餐: {acc['tier']}",
        f"速率限制: {acc['rate_limit']} req/min",
        f"仓库: {q['repos']['used']}/{q['repos']['limit']}",
        f"深度查询 (今日): {q['deep_query_daily']['used']}/{q['deep_query_daily']['limit']}",
        f"30 天总调用: {acc['usage_30d']}",
    ]
    return "\n".join(lines)


@mcp.tool()
def manon_deep_query(repo_id: str, question: str, max_rounds: int = 3) -> str:
    """深度查询代码知识图谱。自动多轮迭代，确保覆盖问题的所有子方面。

    Args:
        repo_id: 仓库 ID
        question: 要查询的问题（自然语言）
        max_rounds: 最大迭代轮数（默认 3，最大 5）
    """
    try:
        result = _post(f"/api/v1/repos/{repo_id}/deep-query", {
            "question": question, "max_rounds": max_rounds,
        }, timeout=30 + max_rounds * 30)  # ~30s per round + 30s base
    except httpx.TimeoutException:
        # Graceful degradation: fall back to single-round search
        try:
            fallback = _get(f"/api/v1/repos/{repo_id}/search", q=question, top_k=10, depth=1)
            ctx = fallback.get("context", "")
            if ctx:
                return _truncate(f"(deep-query 超时，回退到单轮搜索)\n\n{ctx}")
            return f"deep-query 超时且回退搜索无结果。建议拆分为更小的问题后用 manon_search 逐个查询。"
        except Exception as e2:
            log.warning("deep-query timeout, fallback search also failed: %s", e2)
            return "deep-query 超时。建议减少 max_rounds 或拆分为更小的问题用 manon_search 查询。"
    lines = [result["context"]]
    lines.append(f"\n---\n查询轮次: {len(result['rounds'])}")
    if result.get("sub_questions"):
        lines.append(f"子问题: {', '.join(result['sub_questions'])}")
    if result.get("covered"):
        lines.append(f"已覆盖: {', '.join(result['covered'])}")
    for r in result["rounds"]:
        if r.get("queries"):
            lines.append(f"  Round {r['round']}: 补充查询 {r['queries']}")
    return _truncate("\n".join(lines))


@mcp.tool()
def manon_usage(days: int = 30) -> str:
    """查看 API 用量统计。

    Args:
        days: 统计天数（默认 30）
    """
    result = _get("/api/v1/usage", days=days)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def manon_embedding(texts: list[str]) -> str:
    """将文本转换为向量嵌入。

    Args:
        texts: 要嵌入的文本列表（最多 128 条）
    """
    result = _post("/api/v1/embedding", {"inputs": texts})
    return f"生成了 {result['count']} 个向量（维度: {len(result['embeddings'][0])}）"



_UPDATE_STATUS_FILE = Path.home() / ".manon" / "update_status.json"


def _git_branch() -> str:
    """Return git branch name based on cached region."""
    return GIT_BRANCH_CN if REGION == "CN" else GIT_BRANCH_INTL


def _do_update() -> list[str]:
    """Execute git pull + pip install. Writes result to status file."""
    install_dir = Path(__file__).resolve().parent.parent
    lines: list[str] = []
    ok = False

    branch = _git_branch()

    # Git pull from origin (install scripts already set origin to gitee/github)
    try:
        result = subprocess.run(
            ["git", "pull", "--quiet", "origin", branch],
            cwd=str(install_dir),
            capture_output=True, text=True, timeout=15,
        )
        git_out = result.stdout.strip()
        if "Already up to date" in git_out or "Already up-to-date" in git_out or not git_out:
            lines.append("代码已是最新，无需更新。")
            ok = True
            _write_update_status(ok, lines)
            return lines
        lines.append(f"代码已更新:\n{git_out}")
    except subprocess.TimeoutExpired:
        lines.append("git pull 超时（15s），请手动执行: cd manon && git pull")
        _write_update_status(False, lines)
        return lines
    except Exception as e:
        lines.append(f"git pull 失败: {e}")
        _write_update_status(False, lines)
        return lines

    # Reinstall dependencies (timeout 30s)
    req_file = install_dir / "mcp" / "requirements.txt"
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(req_file)],
            capture_output=True, timeout=30,
        )
        lines.append("依赖已更新。")
        ok = True
    except subprocess.TimeoutExpired:
        lines.append("pip install 超时，请手动执行: pip install -r mcp/requirements.txt")
    except Exception as e:
        lines.append(f"依赖安装失败: {e}")

    lines.append("请重启 Claude Code 使新版本生效。")
    _write_update_status(ok, lines)
    return lines


def _write_update_status(ok: bool, lines: list[str]) -> None:
    """Persist update result so next manon_update/init can report it."""
    try:
        import datetime
        _UPDATE_STATUS_FILE.write_text(json.dumps({
            "ok": ok,
            "message": "\n".join(lines),
            "timestamp": datetime.datetime.now().isoformat(),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _read_update_status() -> str | None:
    """Read and clear previous background update result."""
    try:
        if not _UPDATE_STATUS_FILE.exists():
            return None
        data = json.loads(_UPDATE_STATUS_FILE.read_text(encoding="utf-8"))
        _UPDATE_STATUS_FILE.unlink(missing_ok=True)
        tag = "✓" if data.get("ok") else "✗"
        return f"[上次后台更新 {tag}] {data.get('message', '')}"
    except Exception:
        return None


@mcp.tool()
def manon_update() -> str:
    """检查并更新 Manon 到最新版本。后台执行 git pull + 依赖安装。

    更新完成后需要重启 Claude Code 使新版本生效。
    """
    install_dir = Path(__file__).resolve().parent.parent
    parts: list[str] = []

    # Report previous background update result (if any)
    prev = _read_update_status()
    if prev:
        parts.append(prev)

    # Quick check how far behind
    try:
        branch = _git_branch()
        # Fetch latest refs from origin (public repo, no credentials needed)
        subprocess.run(
            ["git", "fetch", "--quiet", "origin", branch],
            cwd=str(install_dir), capture_output=True, timeout=5,
        )
        behind = subprocess.run(
            ["git", "rev-list", "--count", f"HEAD..origin/{branch}"],
            cwd=str(install_dir), capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        if behind and int(behind) > 0:
            import threading
            threading.Thread(target=_do_update, daemon=True).start()
            parts.append(
                f"发现 {behind} 个新提交（当前 {CLIENT_VERSION}），"
                f"更新已在后台启动。\n完成后请重启 Claude Code 使新版本生效。"
            )
        else:
            parts.append(f"当前版本 {CLIENT_VERSION} 已是最新。")
    except Exception:
        import threading
        threading.Thread(target=_do_update, daemon=True).start()
        parts.append(
            f"当前版本: {CLIENT_VERSION}，更新已在后台启动。\n"
            f"完成后请重启 Claude Code 使新版本生效。"
        )
    return "\n".join(parts)


@mcp.tool()
def manon_code_health(repo_id: str) -> str:
    """分析代码库的健康状况。基于知识图谱计算 8 个维度的健康评分。

    维度: 模块耦合度(MC)、循环依赖(CD)、扇入集中度(FI)、死代码(DC)、
          测试覆盖(TC)、函数规模(FS)、技术债务(TD)、继承深度(ID)

    Args:
        repo_id: 仓库 ID（从 manon_repos_list 获取）
    """
    result = _get(f"/api/v1/repos/{repo_id}/code-health", timeout=60)
    score = result.get("score", 0)
    dims = result.get("dimensions", [])

    grade = result.get("grade", "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D")
    lines = [f"代码健康评分: {score}/100 ({grade})"]
    lines.append(f"实体: {result.get('entity_count', 0)}, 关系: {result.get('relation_count', 0)}")
    if not result.get("reliable", True):
        lines.append("⚠ 图谱数据为空，评分不可靠。请先运行 manon_index 重建索引。")
    lines.append("")
    for d in dims:
        bar = "█" * d["value"] + "░" * (10 - d["value"])
        lines.append(f"  {d['abbr']:>2s} {d['name']:<6s} {bar} {d['value']}/10 (权重{d['weight']})")
        detail = d.get("detail", {})
        if detail:
            info = ", ".join(f"{k}={v}" for k, v in detail.items() if not isinstance(v, list))
            if info:
                lines.append(f"     {info}")
    return "\n".join(lines)


def _persist_api_config() -> None:
    """Save current API_URL and API_KEY to ~/.manon/config.json.

    Called during hook install so that git hooks (which run outside MCP)
    can read the credentials without relying on environment variables.
    """
    cfg_file = Path.home() / ".manon" / "config.json"
    try:
        existing = {}
        if cfg_file.exists():
            existing = json.loads(cfg_file.read_text(encoding="utf-8"))
        existing["api_url"] = API_URL
        if API_KEY:
            existing["api_key"] = API_KEY
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning("Failed to persist API config: %s", e)


def _install_hook(project_path: str) -> str | None:
    """Install pre-push hook if .git exists. Returns status message or None."""
    resolved = Path(project_path).resolve()
    # Find git root (may differ from project_path, e.g. monorepo)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(resolved), capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            git_root = Path(result.stdout.strip()).resolve()
        else:
            return None
    except Exception:
        return None
    git_dir = git_root / ".git"
    if not git_dir.is_dir():
        return None
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_file = hooks_dir / "pre-push"
    # Skip if already installed by manon
    if hook_file.exists() and "manon" in hook_file.read_text(encoding="utf-8", errors="replace"):
        return None
    script_path = Path(__file__).resolve().parent / "hooks" / "post_push.py"
    python_exe = sys.executable or "python3"
    hook_content = f"""#!/bin/sh
# Manon push hook — async knowledge graph update + health score
# Runs in background so push is not blocked; output still prints to terminal
"{python_exe}" "{script_path}" "{resolved}" &
exit 0
"""
    hook_file.write_text(hook_content, encoding="utf-8")
    try:
        hook_file.chmod(0o755)
    except Exception:
        pass
    # Persist API credentials so the hook can read them
    _persist_api_config()
    return "🔗 Push hook 已安装"


@mcp.tool()
def manon_setup_hooks(project_path: str) -> str:
    """为项目安装 git pre-push hook，push 后自动更新知识图谱并输出代码健康评分。

    Args:
        project_path: 项目在本机的绝对路径
    """
    resolved = Path(project_path).resolve()
    if not (resolved / ".git").is_dir():
        return f"不是 git 仓库: {resolved}"
    result = _install_hook(project_path)
    # Always persist config so existing hooks pick up current credentials
    _persist_api_config()
    if result:
        return f"{result}\ngit push 后将自动更新知识图谱并输出代码健康评分。"
    return "pre-push hook 已存在，API 配置已更新。"
