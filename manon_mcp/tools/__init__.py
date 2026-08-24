"""Tool registration modules for Manon MCP."""
from __future__ import annotations

from .deps import ToolDependencies
from .search import register_search_tools
from .repo_crud import register_repo_crud_tools
from .init import register_init_tools
from .utility import register_utility_tools
from .health import register_health_tools
from .contract import register_contract_tools


def register_all_tools(mcp, deps: ToolDependencies):
    """Register all MCP tools on the given FastMCP instance."""
    register_search_tools(mcp, deps)
    register_repo_crud_tools(mcp, deps)
    register_init_tools(mcp, deps)
    register_utility_tools(mcp, deps)
    register_health_tools(mcp, deps)
    register_contract_tools(mcp, deps)
