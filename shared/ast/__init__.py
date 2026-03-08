"""AST extraction and sync utilities - modular structure."""
from __future__ import annotations

# Re-export all public functions for backward compatibility
from .project import (
    load_projects,
    save_projects,
    get_project,
    set_project,
    find_project_by_repo_id,
    PROJECTS_DIR,
    PROJECTS_FILE,
)

from .config import (
    _load_scan_config,
    set_custom_excludes,
    get_always_exclude,
)

from .analysis import (
    detect_test_patterns,
    preview_project_structure,
    analyze_index_coverage,
    collect_directory_signals,
)

from .parser_utils import (
    ensure_parsers,
)

from .scanner import (
    scan_and_parse,
    count_scannable_files,
    sync_to_server,
    SYNC_BATCH_SIZE,
)

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
