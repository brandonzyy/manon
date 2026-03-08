"""Health, hooks tools."""
from __future__ import annotations

from pathlib import Path

# Will be injected by parent
_client = None
_hooks = None


def init(client, hooks):
    """Inject dependencies."""
    global _client, _hooks
    _client = client
    _hooks = hooks


def register_health_tools(mcp):
    """Health, hooks, and dynamic merge tools."""

    @mcp.tool()
    def manon_code_health(repo_id: str) -> str:
        """分析代码库的健康状况。基于知识图谱计算 8 个维度的健康评分。

        维度: 模块耦合度(MC)、循环依赖(CD)、扇入集中度(FI)、死代码(DC)、
              测试覆盖(TC)、函数规模(FS)、技术债务(TD)、继承深度(ID)

        IMPORTANT: 返回结果已格式化，请原样输出给用户，不要总结或改写。

        Args:
            repo_id: 仓库 ID（从 manon_repos_list 获取）
        """
        result = _client._get(f"/api/v1/repos/{repo_id}/code-health", timeout=60)
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
        return "<!-- DISPLAY_VERBATIM -->\n" + "\n".join(lines)

    @mcp.tool()
    def manon_setup_hooks(project_path: str) -> str:
        """为项目安装 git pre-push hook，push 后自动更新知识图谱并输出代码健康评分。

        Args:
            project_path: 项目在本机的绝对路径
        """
        resolved = Path(project_path).resolve()
        if not (resolved / ".git").is_dir():
            return f"不是 git 仓库: {resolved}"
        result = _hooks._install_hook(project_path)
        _hooks._persist_api_config()
        if result:
            return f"{result}\ngit push 后将自动更新知识图谱并输出代码健康评分。"
        return "pre-push hook 已存在，API 配置已更新。"
