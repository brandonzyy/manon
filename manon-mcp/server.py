"""Manon MCP Server — expose code intelligence as Claude Code tools."""
from __future__ import annotations

import json
import os
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("manon", instructions="Manon 代码智能工具 — 语义搜索、图遍历、影响分析")

API_URL = os.environ.get("MANON_API_URL", "http://localhost:3700")
API_KEY = os.environ.get("MANON_API_KEY", "")


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
        lines.append(f"  {icon} {r['id']}  {r['name']:<20s}  {r['index_status']}")
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
    import json
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def manon_graph(repo_id: str, symbol: str, depth: int = 1) -> str:
    """查询代码符号的调用关系和依赖图。输入函数名、类名等，返回它的调用者、被调用者、继承关系等。

    Args:
        repo_id: 仓库 ID
        symbol: 代码符号名，如 "UserService"、"authenticate"
        depth: 遍历深度（默认 1，最大 3）
    """
    result = _get(f"/api/v1/repos/{repo_id}/graph", symbol=symbol, depth=depth)
    if result.get("context"):
        return result["context"]
    import json
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
    import json
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def manon_index(repo_id: str, incremental: bool = True) -> str:
    """触发代码索引构建。索引完成后才能进行搜索和分析。

    Args:
        repo_id: 仓库 ID
        incremental: 增量索引（默认 True），设为 False 全量重建
    """
    result = _post(f"/api/v1/repos/{repo_id}/index", {"incremental": incremental})
    return f"索引已触发: {result['status']}。用 manon_index_status 查看进度。"


@mcp.tool()
def manon_index_status(repo_id: str) -> str:
    """查看仓库的索引状态和统计信息。

    Args:
        repo_id: 仓库 ID
    """
    result = _get(f"/api/v1/repos/{repo_id}/index-status")
    status = result["status"]
    stats = result.get("stats")
    msg = f"状态: {status}"
    if stats:
        msg += f"\n文件扫描: {stats.get('files_scanned', 0)}, 索引: {stats.get('files_indexed', 0)}"
        msg += f"\n实体: {stats.get('entities_added', 0)}, 关系: {stats.get('relations_added', 0)}, 块: {stats.get('chunks_added', 0)}"
    return msg


@mcp.tool()
def manon_repos_create(name: str, git_url: str = "", branch: str = "main", local_path: str = "") -> str:
    """创建新的代码仓库。可以通过 git URL 克隆，或指定服务器上的本地路径。

    Args:
        name: 仓库名称
        git_url: Git 仓库地址（可选，会自动 clone）
        branch: 分支名（默认 main）
        local_path: 服务器上的本地路径（与 git_url 二选一）
    """
    body: dict = {"name": name, "branch": branch}
    if git_url:
        body["git_url"] = git_url
    if local_path:
        body["local_path"] = local_path
    result = _post("/api/v1/repos", body)
    return f"仓库已创建: id={result['id']}, name={result['name']}, status={result['index_status']}"


@mcp.tool()
def manon_repos_get(repo_id: str) -> str:
    """查看仓库详情，包括索引状态、统计信息、创建时间等。

    Args:
        repo_id: 仓库 ID
    """
    import json
    result = _get(f"/api/v1/repos/{repo_id}")
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def manon_repos_delete(repo_id: str) -> str:
    """删除仓库及其所有索引数据。

    Args:
        repo_id: 仓库 ID
    """
    _delete(f"/api/v1/repos/{repo_id}")
    return f"仓库 {repo_id} 已删除。"


@mcp.tool()
def manon_push_update(repo_id: str) -> str:
    """拉取仓库最新代码并增量重建索引。适用于代码有更新后刷新图谱。

    Args:
        repo_id: 仓库 ID
    """
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
    lines.append(f"  模型: {health.get('llm_model', '?')}")
    lines.append(f"  Embedding: {health.get('embedding_url', '?')}")

    # 2. list repos, match by local_path
    try:
        repos = _get("/api/v1/repos")
    except Exception as e:
        return "\n".join(lines) + f"\n\n获取仓库列表失败: {e}"

    # normalize path for comparison
    norm = project_path.replace("\\", "/").rstrip("/")
    matched = None
    for r in repos:
        rp = (r.get("local_path") or "").replace("\\", "/").rstrip("/")
        if rp == norm:
            matched = r
            break

    if matched:
        rid = matched["id"]
        lines.append(f"\n仓库已存在: {matched['name']} (id={rid})")
        lines.append(f"  索引状态: {matched['index_status']}")
        if matched.get("index_stats"):
            s = matched["index_stats"]
            lines.append(f"  实体: {s.get('entities_added', 0)}, 关系: {s.get('relations_added', 0)}, 块: {s.get('chunks_added', 0)}")

        # if indexed, show a quick overview
        if matched["index_status"] == "done":
            try:
                overview = _get(f"/api/v1/repos/{rid}/search", q="项目架构 主要模块", top_k=5, depth=0)
                if overview.get("context"):
                    lines.append(f"\n图谱概览:\n{overview['context'][:800]}")
            except Exception:
                pass
    else:
        # 3. create repo
        name = project_name or norm.split("/")[-1]
        try:
            created = _post("/api/v1/repos", {
                "name": name, "local_path": project_path, "branch": "main",
            })
            rid = created["id"]
            lines.append(f"\n仓库已创建: {name} (id={rid})")
            # trigger index
            _post(f"/api/v1/repos/{rid}/index", {"incremental": False})
            lines.append("索引已触发，用 manon_index_status 查看进度。")
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
def manon_deep_query(repo_id: str, question: str, max_rounds: int = 3) -> str:
    """深度查询代码知识图谱。自动多轮迭代，确保覆盖问题的所有子方面。

    比 manon_search 更彻底：LLM 会分析哪些子问题未被覆盖，自动补充查询，
    直到所有相关代码信息都被收集到。适合复杂问题如"这个功能的完整实现链路"。

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
    """查看 API 用量统计，包括总调用次数、token 消耗、各端点调用分布。

    Args:
        days: 统计天数（默认 30）
    """
    import json
    result = _get("/api/v1/usage", days=days)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def manon_embedding(texts: list[str]) -> str:
    """将文本转换为向量嵌入。用于语义相似度计算、自定义检索等场景。

    Args:
        texts: 要嵌入的文本列表（最多 128 条）
    """
    result = _post("/api/v1/embedding", {"inputs": texts})
    return f"生成了 {result['count']} 个向量（维度: {len(result['embeddings'][0])}）"


# ── Pipeline Tools ────────────────────────────────────

@mcp.tool()
def manon_pipeline_start(repo_id: str, description: str, auto_execute: bool = False) -> str:
    """启动任务规划 Pipeline。Manon 会自动进行需求澄清、生成规格、技术设计、任务拆解。

    Pipeline 流程: 需求澄清 → 任务规格 → 用户确认 → 技术设计 → 任务拆解 → 评审 → 报告

    Args:
        repo_id: 仓库 ID
        description: 需求描述（越详细越好）
        auto_execute: 是否自动跳过用户确认步骤（默认 False）
    """
    result = _post(f"/api/v1/repos/{repo_id}/pipeline", {
        "description": description,
        "auto_execute": auto_execute,
    })
    pid = result["pipeline_id"]
    return f"Pipeline 已启动: {pid}\n阶段: {result['stage']}\n用 manon_pipeline_status 查看进度。"


@mcp.tool()
def manon_pipeline_status(repo_id: str, pipeline_id: str, since: int = 0) -> str:
    """查看 Pipeline 状态和消息。用 since 参数获取增量消息。

    Args:
        repo_id: 仓库 ID
        pipeline_id: Pipeline ID（从 manon_pipeline_start 获取）
        since: 消息偏移量（默认 0 获取全部，传上次消息数量获取增量）
    """
    result = _get(f"/api/v1/repos/{repo_id}/pipeline/{pipeline_id}/status", since=since)
    lines = [f"阶段: {result['stage']}"]
    if result.get("pending_action"):
        lines.append(f"等待用户操作: {result['pending_action']}")
    for msg in result.get("messages", []):
        role = msg.get("role", "system")
        content = msg.get("content", "")[:500]
        lines.append(f"[{role}] {content}")
    if result.get("spec"):
        lines.append(f"\n规格: {result['spec'].get('title', '')}")
    if result.get("design"):
        lines.append(f"设计: {result['design'].get('approach', '')[:200]}")
    if result.get("tasks"):
        lines.append(f"子任务: {len(result['tasks'])} 个")
        for t in result["tasks"]:
            lines.append(f"  {t.get('id')}. {t.get('title','')}")
    if result.get("report_url"):
        lines.append(f"报告: {result['report_url']}")
    return "\n".join(lines)


@mcp.tool()
def manon_pipeline_respond(repo_id: str, pipeline_id: str, content: str) -> str:
    """回复 Pipeline 的等待操作。Pipeline 在需求澄清和计划确认时会暂停等待用户输入。

    常见回复:
    - 需求澄清阶段: 回答 Manon 的问题
    - 计划确认阶段: "confirm" 确认 / 提出修改意见

    Args:
        repo_id: 仓库 ID
        pipeline_id: Pipeline ID
        content: 回复内容
    """
    result = _post(f"/api/v1/repos/{repo_id}/pipeline/{pipeline_id}/respond", {
        "content": content,
    })
    return f"已回复。当前阶段: {result['stage']}"


@mcp.tool()
def manon_pipeline_report(repo_id: str, pipeline_id: str) -> str:
    """获取 Pipeline 生成的报告 URL。报告包含完整的需求规格、技术设计和任务拆解。

    Args:
        repo_id: 仓库 ID
        pipeline_id: Pipeline ID
    """
    result = _get(f"/api/v1/repos/{repo_id}/pipeline/{pipeline_id}/report")
    return f"报告地址: {API_URL}{result['report_url']}"


if __name__ == "__main__":
    mcp.run()
