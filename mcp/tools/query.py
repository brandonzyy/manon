"""Deep query tools."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("manon-mcp")

# Will be injected by parent
_client = None


def init(client):
    """Inject dependencies."""
    global _client
    _client = client


def register_query_tools(mcp):
    """Deep query tools."""

    @mcp.tool()
    def manon_deep_query(repo_id: str, question: str, max_rounds: int = 3) -> str:
        """深度查询代码知识图谱。自动多轮迭代，确保覆盖问题的所有子方面。

        Args:
            repo_id: 仓库 ID
            question: 要查询的问题（自然语言）
            max_rounds: 最大迭代轮数（默认 3，最大 5）
        """
        try:
            result = _client._post(f"/api/v1/repos/{repo_id}/deep-query", {
                "question": question, "max_rounds": max_rounds,
            }, timeout=30 + max_rounds * 30)
        except httpx.TimeoutException:
            try:
                fallback = _client._get(f"/api/v1/repos/{repo_id}/search", q=question, top_k=10, depth=1)
                ctx = fallback.get("context", "")
                if ctx:
                    return _client._truncate(f"(deep-query 超时，回退到单轮搜索)\n\n{ctx}")
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
        return _client._truncate("\n".join(lines))
