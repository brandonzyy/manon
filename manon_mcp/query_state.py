"""按仓记录最近一次查询的时间戳，给会话钩子判定「先查过 manon 没有」。

写入方只有本模块（MCP 查询工具在处理时调 record_query）；读取方是
~/.claude/hooks/ 的生成物（pre_agent_plan.py 经 manon_scope.manon_queried）。
两边只共享 last_query.json 这一个文件格式，tests/test_mcp_helpers.py 里有
round-trip 测试钉住它——两份格式迟早不一致，不一致的表现是钩子在刚查过的
仓里继续拦。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from core.ast import find_project_by_repo_id

log = logging.getLogger("manon-mcp")

STATE_FILE = Path.home() / ".manon" / "last_query.json"


def record_query(repo_id: str) -> None:
    """记下 repo_id 这个仓刚被查询过（键是注册表里的仓路径）。

    尽力而为：repo_id 没在本机注册、或状态写不下来，都静默跳过——
    记录状态是给钩子放行用的，它失败不该连查询一起拖失败。"""
    found = find_project_by_repo_id(repo_id)
    if not found:
        return
    key = str(Path(found[0]).resolve())
    try:
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        state[key] = time.time()
        # 原子换名：钩子随时在读，写一半的文件会被当 fail-open 放行，
        # 虽然方向安全，但没必要让「查过」的判定靠运气。
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError as exc:
        log.warning("Failed to record query state for %s: %s", key, exc)
