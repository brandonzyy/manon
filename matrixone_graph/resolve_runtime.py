"""Resolve raw runtime trace edges (file paths) into graph entity IDs.

Handles JS/TS Module._load output: [{from: abs_path, to: abs_path}]
and converts to {"module->module": count} format for merge_dynamic_edges().

Entity ID convention (matches pipeline._module_prefix):
  electron/orchestrator/intent-detector.ts → electron.orchestrator.intent-detector
  renderer/components/Button.tsx           → renderer.components.Button
  src/utils/index.ts                       → src.utils
"""

from __future__ import annotations

import os
import posixpath
from pathlib import PurePosixPath
from typing import Any

# Extensions to strip when converting file paths to module IDs
_JS_EXTENSIONS = frozenset((
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".mts", ".cts", ".d.ts",
))

# Directories that indicate non-project code
_SKIP_DIRS = frozenset((
    "node_modules", ".git", "dist", "build", ".next",
    "__pycache__", ".tox", ".venv", "venv",
))


def _normalize_path(abs_path: str) -> str:
    """Normalize to forward-slash posix style."""
    return abs_path.replace("\\", "/")


def _is_project_file(path: str, project_root: str) -> bool:
    """Check if a resolved path belongs to the project (not node_modules etc)."""
    if not path.startswith(project_root):
        return False
    rel = path[len(project_root):]
    parts = rel.strip("/").split("/")
    return not any(p in _SKIP_DIRS for p in parts)


def _path_to_module_id(abs_path: str, project_root: str) -> str | None:
    """Convert an absolute file path to a dotted module ID.

    Examples:
        /proj/electron/main.ts        → electron.main
        /proj/renderer/components/A.tsx → renderer.components.A
        /proj/src/utils/index.ts       → src.utils
    """
    if not abs_path.startswith(project_root):
        return None
    rel = abs_path[len(project_root):].strip("/")
    if not rel:
        return None

    # Strip extension — handle .d.ts first (compound extension)
    if rel.endswith(".d.ts"):
        rel = rel[:-5]
    else:
        for ext in _JS_EXTENSIONS:
            if rel.endswith(ext):
                rel = rel[:-len(ext)]
                break
        else:
            # Also handle Python extensions for mixed projects
            for ext in (".py", ".pyx"):
                if rel.endswith(ext):
                    rel = rel[:-len(ext)]
                    break

    # Convert path separators to dots
    parts = rel.split("/")

    # Handle index files (like __init__.py in Python)
    if parts and parts[-1] in ("index", "__init__"):
        parts = parts[:-1]

    if not parts:
        return None

    return ".".join(parts)


def resolve_js_edges(
    raw_edges: list[dict[str, str]],
    project_root: str,
    *,
    graph: Any | None = None,
) -> dict[str, int]:
    """Convert raw Module._load edges to {"module->module": count} format.

    Args:
        raw_edges: List of {"from": abs_path, "to": abs_path_or_specifier}.
        project_root: Absolute path to the project root.
        graph: Optional CodeGraph for entity-aware matching. When provided,
               tries to match module IDs against existing entities and
               expands module-level edges to symbol-level where possible.

    Returns:
        {"src_module->tgt_module": count} ready for merge_dynamic_edges().
    """
    root = _normalize_path(project_root).rstrip("/") + "/"
    counts: dict[str, int] = {}

    # If graph provided, build a module→entities index for symbol matching
    entity_modules: dict[str, list[str]] | None = None
    if graph is not None:
        entity_modules = _build_module_index(graph)

    for edge in raw_edges:
        from_path = _normalize_path(edge.get("from", ""))
        to_path = _normalize_path(edge.get("to", ""))

        if not from_path or not to_path:
            continue

        # Filter: both must be project files
        if not _is_project_file(from_path, root):
            continue
        if not _is_project_file(to_path, root):
            continue

        src_mod = _path_to_module_id(from_path, root)
        tgt_mod = _path_to_module_id(to_path, root)

        if not src_mod or not tgt_mod:
            continue
        if src_mod == tgt_mod:
            continue  # self-import, skip

        key = f"{src_mod}->{tgt_mod}"
        counts[key] = counts.get(key, 0) + 1

    # If graph available, try to expand module→module edges to
    # module→symbol edges for better dead-code resolution
    if entity_modules and counts:
        counts = _expand_to_symbols(counts, entity_modules)

    return counts


def _build_module_index(graph: Any) -> dict[str, list[str]]:
    """Build module_id → [entity_ids] mapping from graph nodes."""
    index: dict[str, list[str]] = {}
    for nid, data in graph._g.nodes(data=True):
        kind = data.get("kind", "")
        if kind == "module":
            continue
        # Module = everything before the last dot
        if "." in nid:
            mod = nid.rsplit(".", 1)[0]
            index.setdefault(mod, []).append(nid)
    return index


def _expand_to_symbols(
    module_edges: dict[str, int],
    entity_modules: dict[str, list[str]],
) -> dict[str, int]:
    """Expand module→module edges to module→symbol edges.

    If the target module has known symbols in the graph, create edges
    from the source module to each exported symbol. This gives those
    symbols in-degree, reducing false dead-code positives.
    """
    expanded: dict[str, int] = {}
    for edge_key, count in module_edges.items():
        parts = edge_key.split("->", 1)
        if len(parts) != 2:
            continue
        src_mod, tgt_mod = parts

        # If target module has known symbols, expand
        tgt_symbols = entity_modules.get(tgt_mod, [])
        if tgt_symbols:
            for sym_id in tgt_symbols:
                key = f"{src_mod}->{sym_id}"
                expanded[key] = expanded.get(key, 0) + count
        else:
            # Keep module-level edge as fallback
            expanded[edge_key] = expanded.get(edge_key, 0) + count

    return expanded
