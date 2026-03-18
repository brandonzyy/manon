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

# Builtin/stdlib names excluded from fan-in counting — these inflate FI
# because every module calls them, but they aren't meaningful coupling.
_BUILTINS = frozenset({
    "len", "str", "int", "float", "bool", "bytes", "list", "dict", "set",
    "tuple", "range", "enumerate", "sorted", "reversed", "zip", "map",
    "filter", "max", "min", "sum", "any", "all", "abs", "round", "pow",
    "print", "repr", "format", "type", "isinstance", "issubclass",
    "hasattr", "getattr", "setattr", "delattr", "callable", "super",
    "open", "iter", "next", "id", "hash", "chr", "ord", "hex", "oct",
    "bin", "vars", "dir", "staticmethod", "classmethod", "property",
})

_DEBT_EXCLUDE_PATTERNS = [
    "**/.git/**",
    "**/.svn/**",
    "**/.hg/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/*.egg-info/**",
    "**/.tox/**",
    "**/.nox/**",
    "**/.mypy_cache/**",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/node_modules/**",
    "**/.yarn/**",
    "**/.pnpm-store/**",
    "**/bower_components/**",
    "**/dist/**",
    "**/build/**",
    "**/out/**",
    "**/target/**",
    "**/_build/**",
    "**/.next/**",
    "**/.nuxt/**",
    "**/.output/**",
    "**/.svelte-kit/**",
    "**/.turbo/**",
    "**/.gradle/**",
    "**/.m2/**",
    "**/.idea/**",
    "**/.vscode/**",
    "**/.vs/**",
    "**/.eclipse/**",
    "**/htmlcov/**",
    "**/.nyc_output/**",
    "**/coverage/**",
    "**/.cache/**",
    "**/.tmp/**",
    "**/.temp/**",
    "**/tests/**",
    "**/test/**",
    "**/__tests__/**",
    "**/spec/**",
    "**/e2e/**",
    "**/cypress/**",
    "**/indexes/**",
    "**/saas_indexes/**",
    "**/saas_repos/**",
]

_AUTO_EXCLUDE_PATTERNS = (
    re.compile(r"^\.(?:venv|virtualenv)(?:[._-].+)+$"),
    re.compile(r"^(?:venv|virtualenv)(?:[._-].+)+$"),
)


def _entity_module(entity_id: str) -> str:
    """Extract top-level module from entity ID (e.g. 'foo.bar.Baz' -> 'foo')."""
    parts = entity_id.split(".")
    return parts[0] if parts else ""


def _compute_mc(edges: list) -> dict:
    """MC: Module Coupling — cross-module call ratio."""
    call_edges = [e for e in edges if e[2].get("kind") == "calls"]
    cross_module = sum(
        1 for src, tgt, _ in call_edges
        if _entity_module(src) != _entity_module(tgt)
    )
    ratio = cross_module / max(len(call_edges), 1)
    return {"ratio": round(ratio, 3), "cross_module": cross_module, "total": len(call_edges)}


def _compute_cd(edges: list) -> dict:
    """CD: Circular Dependencies — SCC detection on module import graph."""
    module_g = nx.DiGraph()
    for src, tgt, data in edges:
        if data.get("kind") != "imports":
            continue
        src_mod, tgt_mod = _entity_module(src), _entity_module(tgt)
        if src_mod and tgt_mod and src_mod != tgt_mod:
            module_g.add_edge(src_mod, tgt_mod)
    sccs = [c for c in nx.strongly_connected_components(module_g) if len(c) > 1]
    return {"cycles": len(sccs), "modules": sorted({m for scc in sccs for m in scc})}


def _compute_fi(edges: list) -> dict:
    """FI: Fan-in Concentration — high in-degree entity ratio (excluding builtins)."""
    in_degrees: dict[str, int] = defaultdict(int)
    for src, tgt, data in edges:
        if data.get("kind") == "calls":
            short_name = tgt.rsplit(".", 1)[-1] if "." in tgt else tgt
            if short_name in _BUILTINS:
                continue
            in_degrees[tgt] += 1
    high_fanin = [eid for eid, deg in in_degrees.items() if deg > 5]
    total_called = len(in_degrees)
    ratio = len(high_fanin) / max(total_called, 1)
    return {"ratio": round(ratio, 3), "high_fanin_count": len(high_fanin), "total_called": total_called}


_STRUCTURAL_KINDS = frozenset({
    "type_alias", "interface", "property", "enum", "enum_member",
    "field", "namespace", "constructor",
})


def _is_dc_entry_point(nid: str, data: dict) -> bool:
    """Return True if this entity should be excluded from dead-code analysis."""
    kind = data.get("kind", "")
    name = nid.rsplit(".", 1)[-1] if "." in nid else nid
    fp = data.get("file_path", "").replace("\\", "/")
    decorators = data.get("decorators", [])
    is_script = fp.startswith("scripts/") or "/scripts/" in fp
    if name.startswith("__") and name.endswith("__"): return True
    if _is_test_file(fp): return True
    if kind == "class": return True
    if kind in _STRUCTURAL_KINDS: return True
    if decorators: return True
    if kind == "method" and not name.startswith("_"): return True
    if is_script or fp.endswith("__main__.py"): return True
    if kind == "function" and not name.startswith("_"): return True
    if kind == "function" and any(t in name for t in ("ForTesting", "forTesting", "ForTest", "forTest")):
        return True
    if kind == "variable": return True
    return False


def _compute_dc(nodes: dict, g: nx.DiGraph) -> tuple[dict, list]:
    """DC: Dead Code — zero in-degree non-entry-point entities."""
    all_in_degrees = dict(g.in_degree())
    non_module_entities = [nid for nid, d in nodes.items() if d.get("kind") and d.get("kind") != "module"]
    entry_point_ids = {nid for nid, data in nodes.items() if _is_dc_entry_point(nid, data)}
    checkable = [nid for nid in non_module_entities if nid not in entry_point_ids]
    dead = [nid for nid in checkable if all_in_degrees.get(nid, 0) == 0]
    ratio = len(dead) / max(len(checkable), 1)
    metrics = {"ratio": round(ratio, 3), "dead_count": len(dead),
               "total": len(checkable), "excluded_entry_points": len(entry_point_ids)}
    return metrics, non_module_entities


def _compute_tc(nodes: dict, edges: list, non_module_entities: list) -> dict:
    """TC: Test Coverage — entities reachable from test files (transitive)."""
    test_entity_ids = set()
    for nid, data in nodes.items():
        if _is_test_file(data.get("file_path", "")):
            test_entity_ids.add(nid)
    # Direct references from/to test entities
    tested_entities: set[str] = set()
    for src, tgt, _ in edges:
        if src in test_entity_ids and tgt not in test_entity_ids:
            tested_entities.add(tgt)
        if tgt in test_entity_ids and src not in test_entity_ids:
            tested_entities.add(src)
    # Transitive: follow outgoing calls edges
    calls_out: dict[str, list[str]] = defaultdict(list)
    for src, tgt, data in edges:
        if data.get("kind") == "calls":
            calls_out[src].append(tgt)
    queue = list(tested_entities)
    visited = set(tested_entities)
    while queue:
        node = queue.pop()
        for callee in calls_out.get(node, []):
            if callee not in visited and callee not in test_entity_ids:
                visited.add(callee)
                tested_entities.add(callee)
                queue.append(callee)
    testable = [nid for nid in non_module_entities if nid not in test_entity_ids]
    tested_count = len(tested_entities & set(testable))
    ratio = tested_count / max(len(testable), 1)
    return {"ratio": round(ratio, 3), "tested": tested_count, "testable": len(testable)}


def _compute_fs(nodes: dict) -> dict:
    """FS: Function Size — oversized function ratio (>50 lines)."""
    functions = [d for _, d in nodes.items() if d.get("kind") in ("function", "method")]
    oversized = [f for f in functions if (f.get("line_end", 0) - f.get("line_start", 0)) > 50]
    ratio = len(oversized) / max(len(functions), 1)
    return {"ratio": round(ratio, 3), "oversized": len(oversized), "total": len(functions)}


def _compute_id(edges: list) -> dict:
    """ID: Inheritance Depth — max inheritance chain length."""
    inherit_g = nx.DiGraph()
    for src, tgt, data in edges:
        if data.get("kind") == "inherits":
            inherit_g.add_edge(src, tgt)
    max_depth = 0
    for node in inherit_g.nodes():
        if inherit_g.in_degree(node) == 0:
            for _, length in nx.single_source_shortest_path_length(inherit_g, node).items():
                if length > max_depth:
                    max_depth = length
    return {"max_depth": max_depth}


def compute_graph_metrics(graph: "CodeGraph") -> dict[str, Any]:
    """Compute all 8 health dimensions from the knowledge graph."""
    g = graph._g
    nodes = dict(g.nodes(data=True))
    edges = list(g.edges(data=True))
    dc, non_module_entities = _compute_dc(nodes, g)
    return {
        "mc": _compute_mc(edges),
        "cd": _compute_cd(edges),
        "fi": _compute_fi(edges),
        "dc": dc,
        "tc": _compute_tc(nodes, edges, non_module_entities),
        "fs": _compute_fs(nodes),
        "id": _compute_id(edges),
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


def _load_debt_scan_config(repo_path: Path):
    """Load a scan config for debt analysis without importing core.ast."""
    from codeindex.config import Config

    root = repo_path.resolve()
    config = Config.load_with_auto_setup(root)
    excludes = set(config.exclude)
    excludes.update(_DEBT_EXCLUDE_PATTERNS)
    for item in root.iterdir():
        if item.is_dir() and any(pattern.match(item.name.lower()) for pattern in _AUTO_EXCLUDE_PATTERNS):
            excludes.add(f"**/{item.name}/**")

    gitignore = root / ".gitignore"
    if gitignore.exists():
        for line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            pattern = line.rstrip("/")
            excludes.add(f"**/{pattern}/**")
            excludes.add(f"**/{pattern}")

    config.exclude = sorted(excludes)
    return config, root


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

        config, root = _load_debt_scan_config(repo_path)
        files = scan_directory(root, config, root).files
    except Exception:
        try:
            from codeindex.config import Config
            from codeindex.scanner import scan_directory

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


_DIM_NAMES = {
    "mc": "模块耦合度", "cd": "循环依赖", "fi": "扇入集中度", "dc": "死代码",
    "tc": "测试覆盖", "fs": "函数规模", "td": "技术债务", "id": "继承深度",
}
_DIM_ORDER = ("mc", "cd", "fi", "dc", "tc", "fs", "td", "id")


def _build_dim_scores(graph_metrics: dict, td_density: float) -> dict[str, float]:
    m = graph_metrics
    return {
        "mc": _score_mc(m["mc"]["ratio"]),
        "cd": _score_cd(m["cd"]["cycles"]),
        "fi": _score_fi(m["fi"]["ratio"]),
        "dc": _score_dc(m["dc"]["ratio"]),
        "tc": _score_tc(m["tc"]["ratio"]),
        "fs": _score_fs(m["fs"]["ratio"]),
        "td": _score_td(td_density),
        "id": _score_id(m["id"]["max_depth"]),
    }


def compute_score(graph_metrics: dict, debt_metrics: dict | None = None) -> dict[str, Any]:
    """Compute 8-dimension health score. Returns {score, dimensions, entity_count, ...}."""
    debt = debt_metrics or {}
    total_lines = max(debt.get("total_lines", 1), 1)
    td_density = (debt.get("todos", 0) + debt.get("any_count", 0)) / (total_lines / 1000)
    scores = _build_dim_scores(graph_metrics, td_density)
    total = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
    dimensions = [
        {"abbr": k.upper(), "name": _DIM_NAMES[k], "weight": WEIGHTS[k], "value": scores[k],
         "detail": graph_metrics.get(k, {}) if k != "td" else {"density": round(td_density, 2), **debt}}
        for k in _DIM_ORDER
    ]
    return {
        "score": round(total / 10, 1),
        "grade": "A" if total >= 850 else "B" if total >= 700 else "C" if total >= 500 else "D",
        "dimensions": dimensions,
        "entity_count": graph_metrics.get("entity_count", 0),
        "relation_count": graph_metrics.get("relation_count", 0),
        "reliable": graph_metrics.get("entity_count", 0) > 0,
    }
