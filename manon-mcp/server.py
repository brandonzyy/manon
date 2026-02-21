"""Manon MCP Server — expose code intelligence as Claude Code tools.

Supports both git-based repos (server-side clone) and local repos
(client-side AST extraction + cloud sync).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("manon", instructions="Manon 代码智能工具 — 语义搜索、图遍历、影响分析")

log = logging.getLogger("manon-mcp")

# ── Config ────────────────────────────────────────────
SYNC_BATCH_SIZE = 50
PROJECTS_DIR = Path.home() / ".manon"
PROJECTS_FILE = PROJECTS_DIR / "projects.json"

# ── Geo-routing ───────────────────────────────────────
API_URL_CN = os.environ.get("MANON_API_URL_CN", "http://117.131.45.179:3700")
API_URL_INTL = os.environ.get("MANON_API_URL_INTL", "")
API_KEY = os.environ.get("MANON_API_KEY", "")
_explicit_url = os.environ.get("MANON_API_URL", "")


def _detect_region() -> str:
    for endpoint in ["http://ip-api.com/json/?fields=countryCode", "https://ipinfo.io/json"]:
        try:
            r = httpx.get(endpoint, timeout=5)
            data = r.json()
            country = data.get("countryCode") or data.get("country", "")
            if country.upper() == "CN":
                return "CN"
            if country:
                return "INTL"
        except Exception:
            continue
    return "CN"


def _fetch_tunnel_url() -> str:
    try:
        r = httpx.get(f"{API_URL_CN}/tunnel-url", timeout=5)
        if r.status_code == 200:
            url = r.json().get("url", "")
            if url:
                return url
    except Exception:
        pass
    return ""


def _resolve_api_url() -> str:
    if _explicit_url:
        return _explicit_url
    region = _detect_region()
    if region == "CN":
        url = API_URL_CN
    else:
        url = API_URL_INTL or _fetch_tunnel_url() or API_URL_CN
    log.info("Geo-routing: region=%s, api_url=%s", region, url)
    return url


API_URL = _resolve_api_url()


def _headers():
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def _get(path: str, **params) -> dict:
    with httpx.Client(base_url=API_URL, headers=_headers(), timeout=120) as c:
        r = c.get(path, params=params)
        r.raise_for_status()
        return r.json()


def _get_no_auth(path: str) -> dict:
    with httpx.Client(base_url=API_URL, timeout=10) as c:
        r = c.get(path)
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict) -> dict:
    with httpx.Client(base_url=API_URL, headers=_headers(), timeout=120) as c:
        r = c.post(path, json=body)
        r.raise_for_status()
        return r.json()


def _delete(path: str) -> None:
    with httpx.Client(base_url=API_URL, headers=_headers(), timeout=120) as c:
        r = c.delete(path)
        r.raise_for_status()


# ── Local project registry ────────────────────────────

def _load_projects() -> dict:
    if PROJECTS_FILE.exists():
        return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    return {"projects": {}}


def _save_projects(data: dict) -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_project(local_path: str) -> dict | None:
    norm = str(Path(local_path).resolve()).replace("\\", "/")
    return _load_projects()["projects"].get(norm)


def _set_project(local_path: str, info: dict) -> None:
    norm = str(Path(local_path).resolve()).replace("\\", "/")
    data = _load_projects()
    data["projects"][norm] = info
    _save_projects(data)


def _find_project_by_repo_id(repo_id: str) -> tuple[str, dict] | None:
    for path, info in _load_projects()["projects"].items():
        if info.get("repo_id") == repo_id:
            return path, info
    return None


# ── File scanning + AST extraction ────────────────────

def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _scan_and_parse(local_path: str, old_hashes: dict[str, str]):
    """Scan directory, parse changed files, return sync payload.

    Returns (file_results, deleted_files, new_hashes) where:
    - file_results: list of dicts ready for SyncAstRequest.files
    - deleted_files: list of relative paths that were removed
    - new_hashes: updated hash map
    """
    from codeindex.scanner import scan_directory
    from codeindex.parser import parse_file
    from codeindex.config import Config

    root = Path(local_path).resolve()
    config = Config.load(root / ".codeindex.yaml")
    scan_result = scan_directory(root, config, root)

    new_hashes: dict[str, str] = {}
    file_results = []

    for f in scan_result.files:
        rel = str(f.relative_to(root)).replace("\\", "/")
        h = _file_hash(f)
        new_hashes[rel] = h
        if old_hashes.get(rel) == h:
            continue  # unchanged
        # Parse AST
        pr = parse_file(f)
        if pr.error:
            log.warning("Parse error %s: %s", rel, pr.error)
            continue
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            source = ""
        file_results.append({
            "rel_path": rel,
            "hash": h,
            "source": source,
            "parse_result": pr.to_dict(),
        })

    # Detect deleted files
    old_files = set(old_hashes.keys())
    new_files = set(new_hashes.keys())
    deleted_files = list(old_files - new_files)

    return file_results, deleted_files, new_hashes


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
        return result["context"]
    if not result.get("entities") and not result.get("chunks"):
        return f"未找到与 '{query}' 相关的结果。"
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def manon_graph(repo_id: str, symbol: str, depth: int = 1) -> str:
    """查询代码符号的调用关系和依赖图。

    Args:
        repo_id: 仓库 ID
        symbol: 代码符号名，如 "UserService"、"authenticate"
        depth: 遍历深度（默认 1，最大 3）
    """
    result = _get(f"/api/v1/repos/{repo_id}/graph", symbol=symbol, depth=depth)
    if result.get("context"):
        return result["context"]
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def manon_impact(repo_id: str, commit: str = "HEAD", max_depth: int = 2) -> str:
    """分析某次 commit 的影响范围。返回变更的符号、直接/间接调用者、受影响模块和风险评估。

    Args:
        repo_id: 仓库 ID
        commit: commit hash（默认 HEAD）
        max_depth: 影响传播深度（默认 2）
    """
    result = _get(f"/api/v1/repos/{repo_id}/impact", commit=commit, max_depth=max_depth)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def manon_index(repo_id: str, incremental: bool = True) -> str:
    """触发代码索引构建。索引完成后才能进行搜索和分析。

    Args:
        repo_id: 仓库 ID
        incremental: 增量索引（默认 True），设为 False 全量重建
    """
    # For local repos, do a full local scan + sync
    found = _find_project_by_repo_id(repo_id)
    if found:
        local_path, info = found
        old_hashes = {} if not incremental else info.get("file_hashes", {})
        file_results, deleted, new_hashes = _scan_and_parse(local_path, old_hashes)
        if file_results or deleted:
            _sync_to_server(repo_id, file_results, deleted, full_reindex=not incremental)
        info["file_hashes"] = new_hashes
        info["last_sync"] = __import__("datetime").datetime.now().isoformat()
        _set_project(local_path, info)
        return f"本地扫描完成: {len(file_results)} 文件变更, {len(deleted)} 文件删除。同步已触发。"

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
        msg += f"\n文件扫描: {stats.get('files_scanned', stats.get('files_synced', 0))}"
        msg += f", 索引: {stats.get('files_indexed', 0)}"
        msg += f"\n实体: {stats.get('entities_added', 0)}, 关系: {stats.get('relations_added', 0)}"
        msg += f", 块: {stats.get('chunks_added', 0)}"
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

        # Scan + parse + upload
        file_results, deleted, new_hashes = _scan_and_parse(resolved, {})
        if file_results:
            _sync_to_server(repo_id, file_results, deleted, full_reindex=True)

        # Save local project mapping
        _set_project(resolved, {
            "repo_id": repo_id,
            "name": name,
            "last_sync": __import__("datetime").datetime.now().isoformat(),
            "file_hashes": new_hashes,
        })
        return (
            f"仓库已创建: id={repo_id}, name={name}\n"
            f"本地路径: {resolved}\n"
            f"已扫描 {len(new_hashes)} 文件, {len(file_results)} 文件已上传 AST。\n"
            f"用 manon_index_status {repo_id} 查看索引进度。"
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
    found = _find_project_by_repo_id(repo_id)
    if found:
        local_path, _ = found
        data = _load_projects()
        data["projects"].pop(local_path, None)
        _save_projects(data)

    _delete(f"/api/v1/repos/{repo_id}")
    return f"仓库 {repo_id} 已删除。"


@mcp.tool()
def manon_push_update(repo_id: str) -> str:
    """拉取最新代码并增量重建索引。本地仓库会扫描变更文件并上传 AST。

    Args:
        repo_id: 仓库 ID
    """
    # Check if this is a local project
    found = _find_project_by_repo_id(repo_id)
    if found:
        local_path, info = found
        old_hashes = info.get("file_hashes", {})
        file_results, deleted, new_hashes = _scan_and_parse(local_path, old_hashes)
        if not file_results and not deleted:
            return "没有检测到文件变更。"
        _sync_to_server(repo_id, file_results, deleted)
        info["file_hashes"] = new_hashes
        info["last_sync"] = __import__("datetime").datetime.now().isoformat()
        _set_project(local_path, info)
        return (
            f"增量同步完成: {len(file_results)} 文件变更, {len(deleted)} 文件删除。\n"
            f"用 manon_index_status {repo_id} 查看索引进度。"
        )

    # Fallback: server-side git pull
    result = _post(f"/api/v1/repos/{repo_id}/push-update", {})
    return f"更新已触发: {result['status']}。用 manon_index_status 查看进度。"


# ── Init / Config / Deep Query ────────────────────────

@mcp.tool()
def manon_init(project_path: str, project_name: str = "") -> str:
    """初始化当前项目的 Manon 连接。检查 API 可达性、匹配或创建仓库、展示图谱状态。

    Args:
        project_path: 项目在本机的绝对路径（通常是当前工作目录）
        project_name: 项目名称（可选，默认从路径推断）
    """
    # 1. health check
    try:
        health = _get_no_auth("/health")
    except Exception as e:
        return f"Manon API 不可达 ({API_URL}): {e}\n请确认 saas 服务已启动。"

    lines = [f"Manon API 连接成功 — {health.get('status', 'ok')}"]
    lines.append(f"  服务器: {API_URL} ({'国内' if API_URL == API_URL_CN else '海外'})")
    lines.append(f"  模型: {health.get('llm_model', '?')}")
    lines.append(f"  Embedding: {health.get('embedding_url', '?')}")

    # 2. Check local project registry first
    proj = _get_project(project_path)
    if proj:
        rid = proj["repo_id"]
        lines.append(f"\n本地项目已注册: {proj['name']} (id={rid})")
        lines.append(f"  上次同步: {proj.get('last_sync', '未知')}")
        lines.append(f"  已跟踪文件: {len(proj.get('file_hashes', {}))}")
        try:
            repo = _get(f"/api/v1/repos/{rid}")
            lines.append(f"  索引状态: {repo['index_status']}")
            if repo.get("index_stats"):
                s = repo["index_stats"]
                lines.append(f"  实体: {s.get('entities_added', 0)}, 关系: {s.get('relations_added', 0)}, 块: {s.get('chunks_added', 0)}")
        except Exception:
            lines.append("  (无法获取服务端状态)")
        return "\n".join(lines)

    # 3. Check server repos by name match
    try:
        repos = _get("/api/v1/repos")
    except Exception as e:
        return "\n".join(lines) + f"\n\n获取仓库列表失败: {e}"

    norm = project_path.replace("\\", "/").rstrip("/")
    name = project_name or norm.split("/")[-1]
    matched = None
    for r in repos:
        if r.get("name") == name:
            matched = r
            break

    if matched:
        rid = matched["id"]
        lines.append(f"\n服务端仓库匹配: {matched['name']} (id={rid})")
        lines.append(f"  索引状态: {matched['index_status']}")
        # Register locally if it's a local source_type
        if matched.get("source_type") == "local":
            _set_project(project_path, {
                "repo_id": rid, "name": matched["name"],
                "last_sync": "", "file_hashes": {},
            })
            lines.append("  已注册到本地项目表。")
    else:
        # 4. Create new local repo
        try:
            result = _post("/api/v1/repos", {
                "name": name, "source_type": "local",
            })
            rid = result["id"]
            lines.append(f"\n仓库已创建: {name} (id={rid})")

            # Scan + sync
            file_results, deleted, new_hashes = _scan_and_parse(project_path, {})
            if file_results:
                _sync_to_server(rid, file_results, deleted, full_reindex=True)
                lines.append(f"已扫描 {len(new_hashes)} 文件, {len(file_results)} 文件已上传 AST。")
            _set_project(project_path, {
                "repo_id": rid, "name": name,
                "last_sync": __import__("datetime").datetime.now().isoformat(),
                "file_hashes": new_hashes,
            })
            lines.append("用 manon_index_status 查看索引进度。")
        except Exception as e:
            lines.append(f"\n创建仓库失败: {e}")

    return "\n".join(lines)


@mcp.tool()
def manon_config() -> str:
    """查看当前 Manon 配置：LLM 模型、Embedding 地址、租户信息、速率限制。"""
    try:
        cfg = _get("/api/v1/config")
    except Exception as e:
        return f"获取配置失败: {e}"
    lines = [
        f"LLM 模型: {cfg['llm_model']}",
        f"LLM API: {cfg['llm_api_url']}",
        f"Embedding: {cfg['embedding_url']}",
        f"租户: {cfg['tenant_id']} ({cfg['tier']})",
        f"速率限制: {cfg['rate_limit']} req/min",
    ]
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
        f"租户: {acc['tenant_id']} ({acc['tier']})",
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
    result = _post(f"/api/v1/repos/{repo_id}/deep-query", {
        "question": question, "max_rounds": max_rounds,
    })
    lines = [result["context"]]
    lines.append(f"\n---\n查询轮次: {len(result['rounds'])}")
    if result.get("sub_questions"):
        lines.append(f"子问题: {', '.join(result['sub_questions'])}")
    if result.get("covered"):
        lines.append(f"已覆盖: {', '.join(result['covered'])}")
    for r in result["rounds"]:
        if r.get("queries"):
            lines.append(f"  Round {r['round']}: 补充查询 {r['queries']}")
    return "\n".join(lines)


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
