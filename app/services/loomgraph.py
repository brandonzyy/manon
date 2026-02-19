"""LoomGraph service — backed by MatrixoneGraph (built-in graph + vector engine).

Replaces the old LightRAG HTTP adapter with direct MatrixoneGraph calls.
All queries are pure vector search + graph BFS — no LLM calls during query.
"""

from __future__ import annotations

import logging
from pathlib import Path

from matrixone_graph import MatrixoneGraph

log = logging.getLogger("manon.loomgraph")

# ── Module-level config ──

_EMBEDDING_URL: str = "http://117.131.45.179:3002"
_instances: dict[str, MatrixoneGraph] = {}


def configure(*, embedding_url: str = "", **_kwargs) -> None:
    global _EMBEDDING_URL
    if embedding_url:
        _EMBEDDING_URL = embedding_url


def _get_graph(repo_path: str) -> MatrixoneGraph:
    key = str(Path(repo_path).resolve())
    if key not in _instances:
        _instances[key] = MatrixoneGraph(key, embedding_url=_EMBEDDING_URL)
    return _instances[key]


async def shutdown() -> None:
    """Close all MatrixoneGraph instances (call from app lifespan shutdown)."""
    for mg in _instances.values():
        try:
            await mg.close()
        except Exception:
            pass
    _instances.clear()


# ── Queries ──

async def search(query: str, *, repo_path: str, mode: str = "hybrid",
                 top_k: int = 10, depth: int = 1) -> dict:
    mg = _get_graph(repo_path)
    result = await mg.query(query, top_k=top_k, depth=depth)
    return {
        "success": True,
        "data": {
            "query": query,
            "mode": mode,
            "response": result.context,
            "entities": result.entities,
            "relations": result.relations,
            "chunks": result.chunks,
        },
    }


async def graph(symbol: str, *, repo_path: str, depth: int = 2) -> dict:
    mg = _get_graph(repo_path)
    result = await mg.query(symbol, top_k=5, depth=depth)
    return {
        "success": True,
        "data": {
            "symbol": symbol,
            "depth": depth,
            "response": result.context,
            "entities": result.entities,
            "relations": result.relations,
        },
    }


async def impact(*, file: str | None = None, staged: bool = False,
                 repo_path: str) -> dict:
    query_text = f"impact analysis for {file}" if file else "impact analysis for recent changes"
    mg = _get_graph(repo_path)
    result = await mg.query(query_text, top_k=15, depth=2)
    return {
        "success": True,
        "data": {
            "file": file,
            "staged": staged,
            "response": result.context,
            "entities": result.entities,
            "relations": result.relations,
        },
    }


async def status(repo_path: str | None = None) -> dict:
    if not repo_path:
        return {"success": False, "data": {"status": "no_repo_path"}}
    mg = _get_graph(repo_path)
    s = mg.status()
    indexed = s.get("indexed", False)
    return {
        "success": indexed,
        "data": {"status": "indexed" if indexed else "not_indexed", **s},
    }


# ── Indexing ──

async def index_repo(
    repo_path: str,
    *,
    incremental: bool = True,
    on_progress: callable | None = None,
) -> dict:
    """Index a repository and compute health score."""
    mg = _get_graph(repo_path)
    result = await mg.index(incremental=incremental, on_progress=on_progress)

    # Health scan (independent of graph indexing)
    from codeindex.scanner import scan_directory
    from codeindex.config import Config

    repo_root = Path(repo_path)
    config = Config.load(repo_root / ".codeindex.yaml")
    scan_result = scan_directory(repo_root, config, repo_root)

    max_lines = any_count = mt_issues = legacy_marks = 0
    for f in scan_result.files:
        h = _scan_file_health(f)
        if h.get("lines", 0) > max_lines:
            max_lines = h["lines"]
        any_count += h.get("any_count", 0)
        mt_issues += h.get("todos", 0) + h.get("hardcoded_urls", 0)
        legacy_marks += h.get("legacy_marks", 0)

    health = compute_health_score({
        "max_lines": max_lines, "any_count": any_count,
        "mt_issues": mt_issues, "legacy_marks": legacy_marks,
    })

    return {
        "files": result.files_scanned,
        "entities": result.entities_added,
        "relations": result.relations_added,
        "chunks": result.chunks_added,
        "skipped": result.files_skipped,
        "errors": [],
        "entityTypes": {},
        "health": health,
    }


async def update_index(
    repo_path: str,
    *,
    changed_files: list[str] | None = None,
) -> dict:
    """Incremental update — MatrixoneGraph handles file-hash diffing internally."""
    return await index_repo(repo_path, incremental=True)


# ── Code Health scoring (8 dimensions, aligned with Donnie loomgraph-score.sh) ──

def compute_health_score(metrics: dict) -> dict:
    """Compute 8-dimension code health score.

    Static analysis dimensions (computed at index time):
      CQ (15) — Code Quality: max production file lines
      TS (10) — Type Safety: `: any` count in TS/TSX files
      MT (10) — Maintainability: hardcoded URLs + TODO/HACK/FIXME
      DC (10) — Dead Code: DEPRECATED/REMOVED/LEGACY marks

    Graph-based dimensions (require commit context, default 10 at index time):
      IR (15) — Impact Risk
      MC (15) — Module Coupling
      TC (15) — Test Coverage of Change
      CS (10) — Change Scope

    Score = (CQ*15 + TS*10 + MT*10 + DC*10 + IR*15 + MC*15 + TC*15 + CS*10) / 10
    Returns dict with score (0-100) and per-dimension scores (0-10).
    """
    max_lines = metrics.get("max_lines", 0)
    any_count = metrics.get("any_count", 0)
    mt_issues = metrics.get("mt_issues", 0)
    legacy_marks = metrics.get("legacy_marks", 0)

    if max_lines <= 300: cq = 10
    elif max_lines <= 500: cq = 9
    elif max_lines <= 800: cq = 7
    else: cq = 5

    if any_count == 0: ts = 10
    elif any_count <= 5: ts = 9
    elif any_count <= 15: ts = 7
    else: ts = 5

    if mt_issues == 0: mt = 10
    elif mt_issues <= 5: mt = 9
    else: mt = 7

    if legacy_marks == 0: dc = 10
    elif legacy_marks <= 3: dc = 9
    elif legacy_marks <= 8: dc = 7
    else: dc = 5

    ir = metrics.get("ir", 10)
    mc = metrics.get("mc", 10)
    tc = metrics.get("tc", 10)
    cs = metrics.get("cs", 10)

    raw = cq * 15 + ts * 10 + mt * 10 + dc * 10 + ir * 15 + mc * 15 + tc * 15 + cs * 10
    score = round(raw / 10, 1)

    return {
        "score": score, "cq": cq, "ts": ts, "mt": mt, "dc": dc,
        "ir": ir, "mc": mc, "tc": tc, "cs": cs,
    }


def _scan_file_health(file_path: Path) -> dict:
    """Scan a single file for health metrics (static analysis)."""
    import re
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    lines = content.split("\n")
    line_count = len(lines)
    suffix = file_path.suffix.lower()
    is_ts = suffix in (".ts", ".tsx")
    is_test = ".test." in file_path.name or ".spec." in file_path.name or "/tests/" in str(file_path).replace("\\", "/")

    result = {"lines": line_count, "any_count": 0, "todos": 0, "hardcoded_urls": 0, "legacy_marks": 0}
    if is_test:
        return result

    if is_ts:
        result["any_count"] = len(re.findall(r":\s*any\b", content))
    result["todos"] = len(re.findall(r"\bTODO\b|\bHACK\b|\bFIXME\b", content))
    result["hardcoded_urls"] = len(re.findall(r"http://", content)) if suffix not in (".md", ".txt", ".json") else 0
    result["legacy_marks"] = len(re.findall(r"//\s*DEPRECATED|//\s*REMOVED|//\s*LEGACY", content))
    return result
