"""Tool registration modules for Manon MCP."""
from __future__ import annotations

from .deps import ToolDependencies
from .search import register_search_tools
from .index import register_index_tools
from .repo_crud import register_repo_crud_tools
from .init import register_init_tools
from .config import register_config_tools
from .query import register_query_tools
from .utility import register_utility_tools
from .health import register_health_tools
from .dynamic import register_dynamic_tools


def register_all_tools(mcp, deps: ToolDependencies):
    """Register all MCP tools on the given FastMCP instance."""
    register_search_tools(mcp, deps)
    register_index_tools(mcp, deps)
    register_repo_crud_tools(mcp, deps)
    register_init_tools(mcp, deps)
    register_config_tools(mcp, deps)
    register_query_tools(mcp, deps)
    register_utility_tools(mcp, deps)
    register_health_tools(mcp, deps)
    register_dynamic_tools(mcp, deps)
