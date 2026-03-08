"""Local AST extraction + incremental sync to saas/ backend.

DEPRECATED: This module has been split into shared/ast/ submodules.
This file now serves as a compatibility shim, re-exporting all functions.

New code should import from shared.ast directly:
    from shared.ast import scan_and_parse, get_project, etc.
"""
from __future__ import annotations

# Re-export everything from the new modular structure
from .ast import *  # noqa: F401, F403

__all__ = [
    # Project registry
    "load_projects",
    "save_projects",
    "get_project",
    "set_project",
    "find_project_by_repo_id",
    "PROJECTS_DIR",
    "PROJECTS_FILE",
    # Config
    "_load_scan_config",
    "set_custom_excludes",
    "get_always_exclude",
    # Analysis
    "detect_test_patterns",
    "preview_project_structure",
    "analyze_index_coverage",
    "collect_directory_signals",
    # Parser utils
    "ensure_parsers",
    # Scanner
    "scan_and_parse",
    "count_scannable_files",
    "sync_to_server",
    "SYNC_BATCH_SIZE",
]
