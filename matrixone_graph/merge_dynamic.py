"""Merge dynamic call edges into the knowledge graph.

Dynamic edges use file_path="__dynamic__" as a sentinel so they can be
distinguished from static AST edges and cleaned up independently.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .store import CodeGraph, Relation

DYNAMIC_FILE_PATH = "__dynamic__"


def load_dynamic_deps(path: str | Path) -> dict[str, int]:
    """Load dynamic-deps.json → {"caller->callee": count}."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _remove_dynamic_edges(graph: CodeGraph) -> int:
    """Remove all edges with file_path == __dynamic__. Returns count removed."""
    g = graph._g
    to_remove = [
        (u, v) for u, v, d in g.edges(data=True)
        if d.get("file_path") == DYNAMIC_FILE_PATH
    ]
    g.remove_edges_from(to_remove)
    return len(to_remove)


def _compute_weight(count: int) -> float:
    """Weight = min(1.0 + log2(count), 5.0)."""
    return min(1.0 + math.log2(max(count, 1)), 5.0)


def merge_dynamic_edges(
    graph: CodeGraph,
    edges: dict[str, int],
    *,
    replace: bool = True,
) -> dict[str, Any]:
    """Merge dynamic call edges into the graph.

    Args:
        graph: The CodeGraph to merge into.
        edges: {"caller->callee": count} from tracer output.
        replace: If True, remove all existing __dynamic__ edges first.

    Returns:
        Stats dict with keys: removed, added, skipped.
    """
    removed = 0
    if replace:
        removed = _remove_dynamic_edges(graph)

    added = 0
    skipped = 0
    for edge_key, count in edges.items():
        parts = edge_key.split("->", 1)
        if len(parts) != 2:
            skipped += 1
            continue
        src_id, tgt_id = parts
        # Only add if at least one endpoint exists in the graph
        if not graph.has_entity(src_id) and not graph.has_entity(tgt_id):
            skipped += 1
            continue
        weight = _compute_weight(count)
        rel = Relation(
            src_id=src_id,
            tgt_id=tgt_id,
            kind="calls",
            description=f"[dynamic] {src_id} -> {tgt_id} (count={count})",
            file_path=DYNAMIC_FILE_PATH,
            weight=round(weight, 2),
        )
        graph.add_relation(rel)
        added += 1

    return {"removed": removed, "added": added, "skipped": skipped}
