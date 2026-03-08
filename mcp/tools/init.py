"""Init and configure tools."""
from __future__ import annotations

import logging

from shared.ast_sync import get_project, set_custom_excludes, preview_project_structure

log = logging.getLogger("manon-mcp")

# Will be injected by parent
_client = None
_config = None
_read_update_status = None
_init_existing_project = None
_init_match_or_create = None
_build_health_lines = None
_build_hooks_lines = None


def init(client, config, read_update_status_func, init_existing_func,
         init_match_func, build_health_func, build_hooks_func):
    """Inject dependencies."""
    global _client, _config, _read_update_status, _init_existing_project
    global _init_match_or_create, _build_health_lines, _build_hooks_lines
    _client = client
    _config = config
    _read_update_status = read_update_status_func
    _init_existing_project = init_existing_func
    _init_match_or_create = init_match_func
    _build_health_lines = build_health_func
    _build_hooks_lines = build_hooks_func


def register_init_tools(mcp):
    """Init and configure tools."""

    @mcp.tool()
    def manon_init(project_path: str, project_name: str = "") -> str:
        """初始化当前项目的 Manon 连接。检查 API 可达性、匹配或创建仓库、展示图谱状态。

        IMPORTANT: 返回结果已格式化，请原样输出给用户，不要总结或改写。

        Args:
            project_path: 项目在本机的绝对路径（通常是当前工作目录）
            project_name: 项目名称（可选，默认从路径推断）
        """
        log.info("manon_init called: path=%s, name=%s", project_path, project_name)
        try:
            health = _client._get_no_auth("/health")
            log.info("Health check OK")
        except Exception as e:
            log.error("Health check failed: %s", e)
            return f"❌ Manon API 不可达 ({_config.API_URL}): {e}\n   请确认 saas 服务已启动。"

        lines = [f"─── 🧠 Manon v{_config.CLIENT_VERSION} {'─' * 28}"]
        lines.append("\n📦 项目状态")
        lines.append("  ✅ API 连接成功")
        prev = _read_update_status()
        if prev:
            lines.append(prev)

        rid = None
        graph_lines: list[str] = []

        proj = get_project(project_path)
        if proj:
            rid, proj_lines, graph_lines = _init_existing_project(project_path, proj)
            lines.extend(proj_lines)
        else:
            result = _init_match_or_create(project_path, project_name, lines)
            if isinstance(result, str):
                return result  # error message
            rid, proj_lines, graph_lines = result
            lines.extend(proj_lines)

        if graph_lines:
            lines.append("\n🕸️ 知识图谱")
            lines.extend(graph_lines)

        if rid:
            lines.extend(_build_health_lines(rid))

        lines.extend(_build_hooks_lines(project_path))

        return "<!-- DISPLAY_VERBATIM -->\n" + "\n".join(lines)

    @mcp.tool()
    def manon_configure_excludes(project_path: str, exclude_patterns: list[str]) -> str:
        """为项目设置自定义排除模式。在 manon_init 返回目录预览后，如果发现有非源码目录未被排除，调用此工具添加排除规则。

        排除模式使用 glob 语法，例如:
        - "**/data/**" 排除所有 data 目录
        - "**/logs/**" 排除所有 logs 目录
        - "scripts/_*" 排除 scripts 下以 _ 开头的文件

        Args:
            project_path: 项目在本机的绝对路径
            exclude_patterns: glob 排除模式列表
        """
        proj = get_project(project_path)
        if not proj:
            return "❌ 项目未注册，请先调用 manon_init"
        set_custom_excludes(project_path, exclude_patterns)
        preview = preview_project_structure(project_path)
        return f"✅ 已设置 {len(exclude_patterns)} 条自定义排除规则\n\n📂 更新后的目录结构:\n{preview}"
