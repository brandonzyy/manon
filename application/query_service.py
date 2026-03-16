"""Compatibility facade for query-oriented application services."""
from .deep_query_service import deep_query_repo
from .repo_query_service import (
    code_health_repo,
    graph_repo,
    impact_repo,
    require_indexed_repo,
    search_repo,
)

__all__ = [
    "require_indexed_repo",
    "search_repo",
    "graph_repo",
    "impact_repo",
    "code_health_repo",
    "deep_query_repo",
]
