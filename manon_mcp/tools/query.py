"""Deep query tools."""
from __future__ import annotations

import logging

import httpx

from .deps import ToolDependencies

log = logging.getLogger("manon-mcp")


def register_query_tools(mcp, deps: ToolDependencies):
    """Register deep query tools."""
    client = deps.client

    @mcp.tool()
    def manon_deep_query(repo_id: str, question: str, max_rounds: int = 3) -> str:
        """深度查询代码知识图谱。"""
        try:
            result = client._post(
                f"/api/v1/repos/{repo_id}/deep-query",
                {"question": question, "max_rounds": max_rounds},
                timeout=30 + max_rounds * 30,
            )
        except httpx.TimeoutException:
            try:
                fallback = client._get(f"/api/v1/repos/{repo_id}/search", q=question, top_k=10, depth=1)
                context = fallback.get("context", "")
                if context:
                    return client._truncate(f"(deep-query timed out, fell back to search)\n\n{context}")
                return "deep-query timed out and fallback search returned no result"
            except Exception as exc:
                log.warning("deep-query timeout, fallback search also failed: %s", exc)
                return "deep-query timed out"
        lines = [result["context"]]
        lines.append(f"\n---\nrounds: {len(result['rounds'])}")
        if result.get("sub_questions"):
            lines.append(f"sub_questions: {', '.join(result['sub_questions'])}")
        if result.get("covered"):
            lines.append(f"covered: {', '.join(result['covered'])}")
        for round_info in result["rounds"]:
            if round_info.get("queries"):
                lines.append(f"  Round {round_info['round']}: follow-up {round_info['queries']}")
        return client._truncate("\n".join(lines))
