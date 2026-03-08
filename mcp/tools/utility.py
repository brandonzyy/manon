"""Usage, embedding, and update tools."""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

# Will be injected by parent
_client = None
_config = None
_read_update_status = None
_do_update = None


def init(client, config, read_update_status_func, do_update_func):
    """Inject dependencies."""
    global _client, _config, _read_update_status, _do_update
    _client = client
    _config = config
    _read_update_status = read_update_status_func
    _do_update = do_update_func


def register_utility_tools(mcp):
    """Usage, embedding, and update tools."""

    @mcp.tool()
    def manon_usage(days: int = 30) -> str:
        """查看 API 用量统计。

        Args:
            days: 统计天数（默认 30）
        """
        result = _client._get("/api/v1/usage", days=days)
        return json.dumps(result, indent=2, ensure_ascii=False)

    @mcp.tool()
    def manon_embedding(texts: list[str]) -> str:
        """将文本转换为向量嵌入。

        Args:
            texts: 要嵌入的文本列表（最多 128 条）
        """
        result = _client._post("/api/v1/embedding", {"inputs": texts})
        return f"生成了 {result['count']} 个向量（维度: {len(result['embeddings'][0])}）"

    @mcp.tool()
    def manon_update() -> str:
        """检查并更新 Manon 到最新版本。后台执行 git pull + 依赖安装。

        更新完成后需要重启 Claude Code 使新版本生效。
        """
        install_dir = Path(__file__).resolve().parent.parent.parent
        parts: list[str] = []
        prev = _read_update_status()
        if prev:
            parts.append(prev)
        try:
            branch = _config._git_branch()
            subprocess.run(
                ["git", "fetch", "--quiet", "origin", branch],
                cwd=str(install_dir), capture_output=True, stdin=subprocess.DEVNULL, timeout=5,
            )
            behind = subprocess.run(
                ["git", "rev-list", "--count", f"HEAD..origin/{branch}"],
                cwd=str(install_dir), capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=3,
            ).stdout.strip()
            if behind and int(behind) > 0:
                threading.Thread(target=_do_update, daemon=True).start()
                parts.append(
                    f"发现 {behind} 个新提交（当前 {_config.CLIENT_VERSION}），"
                    f"更新已在后台启动。\n完成后请重启 Claude Code 使新版本生效。"
                )
            else:
                parts.append(f"当前版本 {_config.CLIENT_VERSION} 已是最新。")
        except Exception:
            threading.Thread(target=_do_update, daemon=True).start()
            parts.append(
                f"当前版本: {_config.CLIENT_VERSION}，更新已在后台启动。\n"
                f"完成后请重启 Claude Code 使新版本生效。"
            )
        return "\n".join(parts)
