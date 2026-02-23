"""Code health scoring — 8-dimension graph-based analysis.

Dimensions (weight):
  MC (15) — Module Coupling: cross-module edge ratio
  CD (10) — Circular Dependencies: SCC detection
  FI (15) — Fan-in Concentration: high in-degree entity ratio
  DC (10) — Dead Code: zero in-degree non-module entities
  TC (15) — Test Coverage: entities referenced by test files
  FS (10) — Function Size: oversized function ratio (>50 lines)
  TD (15) — Tech Debt: TODO/HACK/FIXME/`:any` density
  ID (10) — Inheritance Depth: max inheritance chain depth

Total weight: 100, each dimension 0-10, total = sum(dim * weight) / 10 -> 0-100
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import networkx as nx

if TYPE_CHECKING:
    from .store import CodeGraph


def _entity_module(entity_id: str) -> str:
    """Extract top-level module from entity ID (e.g. 'foo.bar.Baz' -> 'foo')."""
    parts = entity_id.split(".")
    return parts[0] if parts else ""


def compute_graph_metrics(graph: "CodeGraph") -> dict[str, Any]:
    """Compute all 8 health dimensions from the knowledge graph.

    Returns dict with keys: mc, cd, fi, dc, tc, fs, id, and their details.
    """
    g = graph._g
    nodes = dict(g.nodes(data=True))
    edges = list(g.edges(data=True))

    # ── MC: Module Coupling ──────────────────────────────
    # Only count `calls` edges — imports are inherently cross-module in Python
    # and don't reflect runtime coupling.
    call_edges = [e for e in edges if e[2].get("kind") == "calls"]
    cross_module = sum(
        1 for src, tgt, _ in call_edges
        if _entity_module(src) != _entity_module(tgt)
    )
    mc_ratio = cross_module / max(len(call_edges), 1)

    # ── CD: Circular Dependencies ────────────────────────
    # Build module-level import subgraph
    module_g = nx.DiGraph()
    for src, tgt, data in edges:
        if data.get("kind") != "imports":
            continue
        src_mod = _entity_module(src)
        tgt_mod = _entity_module(tgt)
        if src_mod and tgt_mod and src_mod != tgt_mod:
            module_g.add_edge(src_mod, tgt_mod)
    sccs = [c for c in nx.strongly_connected_components(module_g) if len(c) > 1]
    cycle_count = len(sccs)
    cycle_modules = sorted({m for scc in sccs for m in scc})

    # ── FI: Fan-in Concentration ─────────────────────────
    in_degrees: dict[str, int] = defaultdict(int)
    for src, tgt, data in edges:
        if data.get("kind") == "calls":
            in_degrees[tgt] += 1
    high_fanin = [eid for eid, deg in in_degrees.items() if deg > 5]
    total_called = len(in_degrees)
    fi_ratio = len(high_fanin) / max(total_called, 1)

    # ── DC: Dead Code ────────────────────────────────────
    # Exclude known entry points: dunder methods (called by runtime),
    # test entities (called by test runner), classes (structural containers).
    all_in_degrees = dict(g.in_degree())
    non_module_entities = [
        nid for nid, data in nodes.items()
        if data.get("kind") and data.get("kind") != "module"
    ]
    entry_point_ids: set[str] = set()
    for nid, data in nodes.items():
        kind = data.get("kind", "")
        name = nid.rsplit(".", 1)[-1] if "." in nid else nid
        fp = data.get("file_path", "")
        if name.startswith("__") and name.endswith("__"):
            entry_point_ids.add(nid)
        elif _is_test_file(fp):
            entry_point_ids.add(nid)
        elif kind == "class":
            entry_point_ids.add(nid)
    checkable = [nid for nid in non_module_entities if nid not in entry_point_ids]
    dead = [nid for nid in checkable if all_in_degrees.get(nid, 0) == 0]
    dc_ratio = len(dead) / max(len(checkable), 1)

    # ── TC: Test Coverage ────────────────────────────────
    test_entity_ids = set()
    for nid, data in nodes.items():
        fp = data.get("file_path", "")
        if _is_test_file(fp):
            test_entity_ids.add(nid)
    # Entities referenced by test files (via imports or calls)
    tested_entities: set[str] = set()
    for src, tgt, data in edges:
        if src in test_entity_ids and tgt not in test_entity_ids:
            tested_entities.add(tgt)
        if tgt in test_entity_ids and src not in test_entity_ids:
            tested_entities.add(src)
    testable = [nid for nid in non_module_entities if nid not in test_entity_ids]
    tc_ratio = len(tested_entities & set(testable)) / max(len(testable), 1)

    # ── FS: Function Size ────────────────────────────────
    functions = [
        data for _, data in nodes.items()
        if data.get("kind") in ("function", "method")
    ]
    oversized = [
        f for f in functions
        if (f.get("line_end", 0) - f.get("line_start", 0)) > 50
    ]
    fs_ratio = len(oversized) / max(len(functions), 1)

    # ── ID: Inheritance Depth ────────────────────────────
    inherit_g = nx.DiGraph()
    for src, tgt, data in edges:
        if data.get("kind") == "inherits":
            inherit_g.add_edge(src, tgt)
    max_depth = 0
    for node in inherit_g.nodes():
        if inherit_g.in_degree(node) == 0:  # root of chain
            for _, lengths in nx.single_source_shortest_path_length(inherit_g, node).items():
                if lengths > max_depth:
                    max_depth = lengths

    return {
        "mc": {"ratio": round(mc_ratio, 3), "cross_module": cross_module, "total": len(call_edges)},
        "cd": {"cycles": cycle_count, "modules": cycle_modules},
        "fi": {"ratio": round(fi_ratio, 3), "high_fanin_count": len(high_fanin), "total_called": total_called},
        "dc": {"ratio": round(dc_ratio, 3), "dead_count": len(dead), "total": len(checkable), "excluded_entry_points": len(entry_point_ids)},
        "tc": {"ratio": round(tc_ratio, 3), "tested": len(tested_entities & set(testable)), "testable": len(testable)},
        "fs": {"ratio": round(fs_ratio, 3), "oversized": len(oversized), "total": len(functions)},
        "id": {"max_depth": max_depth},
        "entity_count": len(nodes),
        "relation_count": len(edges),
    }


def _is_test_file(file_path: str) -> bool:
    """Check if a file path looks like a test file."""
    fp = file_path.replace("\\", "/")
    basename = fp.rsplit("/", 1)[-1]
    if basename.startswith("test_") or ".test." in basename or ".spec." in basename or "_test." in basename:
        return True
    parts = fp.split("/")
    return any(p in ("tests", "test", "__tests__") for p in parts[:-1])


# ── TD: Static scan for tech debt markers ────────────────

def scan_file(file_path: Path) -> dict[str, int]:
    """Scan a single file for tech debt markers (TODO/HACK/FIXME/:any)."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    suffix = file_path.suffix.lower()
    is_test = _is_test_file(str(file_path))
    result = {"todos": 0, "any_count": 0, "lines": len(content.split("\n"))}
    if is_test:
        return result
    result["todos"] = len(re.findall(r"\bTODO\b|\bHACK\b|\bFIXME\b", content))
    if suffix in (".ts", ".tsx"):
        result["any_count"] = len(re.findall(r":\s*any\b", content))
    return result


def scan_directory_debt(repo_path: Path) -> dict[str, int]:
    """Scan all source files for tech debt markers. Returns totals."""
    total_todos = 0
    total_any = 0
    total_lines = 0
    try:
        from codeindex.scanner import scan_directory
        from codeindex.config import Config
        config = Config.load(repo_path / ".codeindex.yaml")
        files = scan_directory(repo_path, config, repo_path).files
    except Exception:
        # Fallback: glob common source files
        files = []
        for ext in ("*.py", "*.ts", "*.tsx", "*.js", "*.jsx", "*.java", "*.go"):
            files.extend(repo_path.rglob(ext))
    for f in files:
        h = scan_file(Path(f))
        total_todos += h.get("todos", 0)
        total_any += h.get("any_count", 0)
        total_lines += h.get("lines", 0)
    return {"todos": total_todos, "any_count": total_any, "total_lines": total_lines}


# ── Scoring ──────────────────────────────────────────────

WEIGHTS = {
    "mc": 15, "cd": 10, "fi": 15, "dc": 10,
    "tc": 15, "fs": 10, "td": 15, "id": 10,
}


def _score_mc(ratio: float) -> int:
    if ratio <= 0.2: return 10
    if ratio <= 0.35: return 8
    if ratio <= 0.5: return 6
    return 4


def _score_cd(cycles: int) -> int:
    if cycles == 0: return 10
    if cycles <= 2: return 6
    return 3


def _score_fi(ratio: float) -> int:
    if ratio <= 0.05: return 10
    if ratio <= 0.1: return 8
    if ratio <= 0.2: return 6
    return 4


def _score_dc(ratio: float) -> int:
    # Thresholds account for framework entry points (decorators, callbacks)
    # that static analysis cannot detect as "called".
    if ratio <= 0.1: return 10
    if ratio <= 0.25: return 8
    if ratio <= 0.45: return 6
    if ratio <= 0.65: return 4
    return 2


def _score_tc(ratio: float) -> int:
    if ratio >= 0.8: return 10
    if ratio >= 0.5: return 8
    if ratio >= 0.3: return 6
    return 4


def _score_fs(ratio: float) -> int:
    if ratio <= 0.05: return 10
    if ratio <= 0.1: return 8
    if ratio <= 0.2: return 6
    return 4


def _score_td(density: float) -> int:
    """density = (todos + any_count) / (total_lines / 1000)"""
    if density <= 1: return 10
    if density <= 3: return 8
    if density <= 6: return 6
    return 4


def _score_id(max_depth: int) -> int:
    if max_depth <= 2: return 10
    if max_depth <= 4: return 7
    return 4


def compute_score(graph_metrics: dict, debt_metrics: dict | None = None) -> dict[str, Any]:
    """Compute 8-dimension health score from graph + static metrics.

    Returns {score, dimensions: [{abbr, name, weight, value, detail}]}.
    """
    mc = graph_metrics["mc"]
    cd = graph_metrics["cd"]
    fi = graph_metrics["fi"]
    dc = graph_metrics["dc"]
    tc = graph_metrics["tc"]
    fs = graph_metrics["fs"]
    id_ = graph_metrics["id"]

    # TD from debt_metrics or default
    debt = debt_metrics or {}
    total_lines = max(debt.get("total_lines", 1), 1)
    td_density = (debt.get("todos", 0) + debt.get("any_count", 0)) / (total_lines / 1000)

    scores = {
        "mc": _score_mc(mc["ratio"]),
        "cd": _score_cd(cd["cycles"]),
        "fi": _score_fi(fi["ratio"]),
        "dc": _score_dc(dc["ratio"]),
        "tc": _score_tc(tc["ratio"]),
        "fs": _score_fs(fs["ratio"]),
        "td": _score_td(td_density),
        "id": _score_id(id_["max_depth"]),
    }

    names = {
        "mc": "模块耦合度", "cd": "循环依赖", "fi": "扇入集中度", "dc": "死代码",
        "tc": "测试覆盖", "fs": "函数规模", "td": "技术债务", "id": "继承深度",
    }

    total = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
    dimensions = []
    for k in ("mc", "cd", "fi", "dc", "tc", "fs", "td", "id"):
        dimensions.append({
            "abbr": k.upper(),
            "name": names[k],
            "weight": WEIGHTS[k],
            "value": scores[k],
            "detail": graph_metrics.get(k, {}) if k != "td" else {"density": round(td_density, 2), **debt},
        })

    return {
        "score": round(total / 10, 1),
        "grade": "A" if total >= 850 else "B" if total >= 700 else "C" if total >= 500 else "D",
        "dimensions": dimensions,
        "entity_count": graph_metrics.get("entity_count", 0),
        "relation_count": graph_metrics.get("relation_count", 0),
        "reliable": graph_metrics.get("entity_count", 0) > 0,
    }
