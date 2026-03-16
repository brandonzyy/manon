"""Preferred AST/core API surface."""
from .analysis import (
    analyze_index_coverage,
    collect_directory_signals,
    detect_test_patterns,
    needs_smart_analysis_refresh,
    preview_project_structure,
    smart_analysis_signature,
)
from .config import (
    _load_scan_config,
    get_always_exclude,
    get_auto_exclude_patterns,
    set_custom_excludes,
)
from .parser_utils import ensure_parsers
from .project import (
    PROJECTS_DIR,
    PROJECTS_FILE,
    find_project_by_repo_id,
    get_project,
    load_projects,
    save_projects,
    set_project,
)
from .scanner import SYNC_BATCH_SIZE, count_scannable_files, scan_and_parse, sync_to_server

__all__ = [
    "PROJECTS_DIR",
    "PROJECTS_FILE",
    "load_projects",
    "save_projects",
    "get_project",
    "set_project",
    "find_project_by_repo_id",
    "_load_scan_config",
    "set_custom_excludes",
    "get_always_exclude",
    "get_auto_exclude_patterns",
    "detect_test_patterns",
    "preview_project_structure",
    "analyze_index_coverage",
    "collect_directory_signals",
    "smart_analysis_signature",
    "needs_smart_analysis_refresh",
    "ensure_parsers",
    "scan_and_parse",
    "count_scannable_files",
    "sync_to_server",
    "SYNC_BATCH_SIZE",
]
