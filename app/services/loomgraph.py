"""LoomGraph + CodeIndex service — direct Python API (no subprocess)."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("manon.loomgraph")

# Configured by main.py lifespan
_LIGHTRAG_URL: str = "http://117.131.45.179:3010"
_LIGHTRAG_TIMEOUT: float = 30.0
_WORKSPACE: str = "manon_default"


def configure(
    lightrag_url: str,
    workspace: str,
    lightrag_timeout: float = 30.0,
    **_kwargs,
) -> None:
    global _LIGHTRAG_URL, _WORKSPACE, _LIGHTRAG_TIMEOUT
    _LIGHTRAG_URL = lightrag_url
    _WORKSPACE = workspace
    _LIGHTRAG_TIMEOUT = lightrag_timeout


def _client(workspace: str | None = None):
    """Create a LightRAGClient instance."""
    from loomgraph.core.lightrag_client import LightRAGClient
    return LightRAGClient(
        base_url=_LIGHTRAG_URL,
        timeout=_LIGHTRAG_TIMEOUT,
        workspace=workspace or _WORKSPACE,
    )


# ── LoomGraph queries ──

async def search(query: str, *, mode: str = "local", workspace: str | None = None) -> dict:
    client = _client(workspace)
    result = await client.query(query, mode=mode)
    return {
        "success": True,
        "data": {
            "query": query,
            "mode": mode,
            "response": result.get("response", ""),
            "references": result.get("references", []),
        },
    }
async def graph(symbol: str, *, direction: str = "both", depth: int = 2, workspace: str | None = None) -> dict:
    client = _client(workspace)
    query = f"{symbol} {direction} depth:{depth}"
    result = await client.query(query, mode="local")
    return {
        "success": True,
        "data": {
            "symbol": symbol,
            "direction": direction,
            "depth": depth,
            "response": result.get("response", ""),
        },
    }


async def impact(*, file: str | None = None, staged: bool = False, workspace: str | None = None) -> dict:
    client = _client(workspace)
    if file:
        query = f"impact analysis for changes in {file}"
    elif staged:
        query = "impact analysis for staged changes"
    else:
        query = "impact analysis"
    result = await client.query(query, mode="local")
    return {
        "success": True,
        "data": {
            "file": file,
            "staged": staged,
            "response": result.get("response", ""),
        },
    }


async def status() -> dict:
    """Check LightRAG connectivity and return system status."""
    from loomgraph.core.lightrag_client import LightRAGClient, LightRAGAPIError
    client = _client()
    try:
        health = await client.health_check()
        return {
            "success": True,
            "data": {
                "lightrag_url": _LIGHTRAG_URL,
                "workspace": _WORKSPACE,
                "lightrag_status": "healthy",
                "lightrag_version": health.get("version", "unknown"),
            },
        }
    except LightRAGAPIError as exc:
        return {
            "success": False,
            "data": {
                "lightrag_url": _LIGHTRAG_URL,
                "workspace": _WORKSPACE,
                "lightrag_status": "unreachable",
                "error": str(exc),
            },
        }


# ── Symbol filtering & namespace ──

# Kinds to always drop
_DROP_KINDS = {"field", "type_alias"}
# variable kept only when line span >= this threshold
_VAR_MIN_LINES = 3


def _file_to_module(file_path: Path, repo_root: Path) -> str:
    """Convert file path to dot-separated module name relative to repo root."""
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:
        rel = file_path
    # agent/coach-agent.js → agent.coach-agent
    return ".".join(rel.with_suffix("").parts)


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
    mt_issues = metrics.get("mt_issues", 0)  # TODO + hardcoded URLs
    legacy_marks = metrics.get("legacy_marks", 0)

    # CQ: max production file lines
    if max_lines <= 300: cq = 10
    elif max_lines <= 500: cq = 9
    elif max_lines <= 800: cq = 7
    else: cq = 5

    # TS: type safety (`: any` count)
    if any_count == 0: ts = 10
    elif any_count <= 5: ts = 9
    elif any_count <= 15: ts = 7
    else: ts = 5

    # MT: maintainability (hardcoded URLs + TODO/HACK/FIXME)
    if mt_issues == 0: mt = 10
    elif mt_issues <= 5: mt = 9
    else: mt = 7

    # DC: dead code (DEPRECATED/REMOVED/LEGACY marks)
    if legacy_marks == 0: dc = 10
    elif legacy_marks <= 3: dc = 9
    elif legacy_marks <= 8: dc = 7
    else: dc = 5

    # Graph-based dimensions — default 10 at index time (no commit context)
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
        return result  # skip test files for scoring

    if is_ts:
        result["any_count"] = len(re.findall(r":\s*any\b", content))
    result["todos"] = len(re.findall(r"\bTODO\b|\bHACK\b|\bFIXME\b", content))
    result["hardcoded_urls"] = len(re.findall(r"http://", content)) if suffix not in (".md", ".txt", ".json") else 0
    result["legacy_marks"] = len(re.findall(r"//\s*DEPRECATED|//\s*REMOVED|//\s*LEGACY", content))
    return result


# ── Bypass injection (no LLM entity extraction) ──

async def inject_chunks_bypass(
    chunks: list[dict],
    *,
    workspace: str | None = None,
    batch_size: int = 10,
    on_progress: callable | None = None,
) -> int:
    """Inject code chunks via insert_custom_kg bypass route.

    Bypasses LightRAG's LLM entity extraction pipeline — chunks are stored
    and embedded directly without LLM processing.

    Returns number of chunks successfully injected.
    """
    client = _client(workspace)
    total = len(chunks)
    if on_progress:
        on_progress("Injecting chunks", 0, total)
    injected = 0
    for i in range(0, total, batch_size):
        batch = chunks[i : i + batch_size]
        try:
            await client.insert_custom_kg(
                entities=[], relationships=[], chunks=batch,
            )
            injected += len(batch)
        except Exception as exc:
            log.warning("Chunk batch %d failed: %s", i // batch_size, exc)
    log.info("Bypass injection: %d/%d chunks", injected, total)
    return injected


# ── Indexing ──

async def index_repo(
    repo_path: str,
    *,
    workspace: str | None = None,
    clear: bool = False,
    on_progress: callable | None = None,
) -> dict:
    """Full index: scan → filter → namespace → inject entities + chunks."""
    from codeindex.parser import parse_file as _raw_parse
    from loomgraph import index_repository as _index_repo
    from loomgraph.core.injector import chunk_file_for_kg
    from loomgraph.core.indexer import scan_code_files

    repo_root = Path(repo_path)

    def _filtered_parse(path: Path):
        result = _raw_parse(path)
        module = _file_to_module(path, repo_root)
        kept = []
        for s in result.symbols:
            # Drop field, type_alias
            if s.kind in _DROP_KINDS:
                continue
            # Drop short variables (< 3 lines)
            if s.kind == "variable":
                span = (s.line_end - s.line_start + 1) if s.line_end and s.line_start else 1
                if span < _VAR_MIN_LINES:
                    continue
            # Namespace: module.symbol_name
            s.name = f"{module}.{s.name}"
            kept.append(s)
            # Track entity types
            entity_types[s.kind] = entity_types.get(s.kind, 0) + 1
        result.symbols = kept
        # Adapt codeindex 0.19 Call objects for loomgraph mapper (line_number → line)
        for c in result.calls:
            if not hasattr(c, "line") and hasattr(c, "line_number"):
                c.line = c.line_number
            if not hasattr(c, "is_method"):
                c.is_method = False
        return result

    entity_types: dict[str, int] = {}
    client = _client(workspace)

    # Phase 1: entity + relation injection
    result = await _index_repo(
        repo_root,
        client,
        _filtered_parse,
        clear_existing=clear,
        on_progress=on_progress,
    )
    already_existed = sum(1 for e in result.errors if "already exists" in e.lower())
    real_errors = [e for e in result.errors if "already exists" not in e.lower()]

    # Phase 2: bypass chunk injection + health scan
    chunk_count = 0
    all_chunks: list[dict] = []
    files = scan_code_files(repo_root)
    max_lines = 0
    any_count = 0
    mt_issues = 0
    legacy_marks = 0
    for f in files:
        # Health scan
        h = _scan_file_health(f)
        if h.get("lines", 0) > max_lines:
            max_lines = h["lines"]
        any_count += h.get("any_count", 0)
        mt_issues += h.get("todos", 0) + h.get("hardcoded_urls", 0)
        legacy_marks += h.get("legacy_marks", 0)
        # Chunk extraction
        try:
            chunks = chunk_file_for_kg(str(f))
            if chunks:
                all_chunks.extend(c for c in chunks if (c.get("content") or "").strip())
        except Exception as exc:
            log.debug("chunk_file_for_kg failed for %s: %s", f, exc)

    if all_chunks:
        chunk_count = await inject_chunks_bypass(
            all_chunks, workspace=workspace, on_progress=on_progress,
        )

    # Phase 3: compute health score
    health = compute_health_score({
        "max_lines": max_lines,
        "any_count": any_count,
        "mt_issues": mt_issues,
        "legacy_marks": legacy_marks,
    })

    return {
        "files": result.files,
        "entities": result.entities + already_existed,
        "relations": result.relations,
        "chunks": chunk_count,
        "errors": real_errors[:20],
        "skipped": len(result.skipped_files),
        "entityTypes": entity_types,
        "health": health,
    }


async def update_index(
    repo_path: str,
    *,
    changed_files: list[str] | None = None,
    workspace: str | None = None,
) -> dict:
    """Incremental update after push — file-level chunk refresh + entity update.

    Strategy (aligned with Donnie post-merge-hook.sh):
      - change ratio < 20%: hot-update (clear old chunks → re-inject changed files)
      - change ratio >= 20%: full rebuild
    """
    from loomgraph.core.injector import chunk_file_for_kg
    from loomgraph.core.indexer import scan_code_files

    repo_root = Path(repo_path)
    ws = workspace or _WORKSPACE

    if not changed_files:
        # No file list provided — fall back to full index
        return await index_repo(repo_path, workspace=ws, clear=False)

    # Calculate change ratio
    all_files = scan_code_files(repo_root)
    total = len(all_files) or 1
    ratio = len(changed_files) * 100 // total

    if ratio >= 20:
        log.info("Change ratio %d%% >= 20%%, full rebuild", ratio)
        return await index_repo(repo_path, workspace=ws, clear=False)

    log.info("Hot-update: %d files changed (ratio %d%%)", len(changed_files), ratio)

    # Phase 1: clear old chunks for changed files
    cleared = await _clear_file_chunks(changed_files, workspace=ws)
    log.info("Cleared %d old chunks", cleared)

    # Phase 2: re-chunk changed files and inject
    all_chunks: list[dict] = []
    files_processed = 0
    for rel_path in changed_files:
        abs_path = repo_root / rel_path
        if not abs_path.exists():
            continue
        try:
            chunks = chunk_file_for_kg(str(abs_path))
            if chunks:
                all_chunks.extend(c for c in chunks if (c.get("content") or "").strip())
            files_processed += 1
        except Exception as exc:
            log.debug("chunk_file_for_kg failed for %s: %s", rel_path, exc)

    chunk_count = 0
    if all_chunks:
        chunk_count = await inject_chunks_bypass(all_chunks, workspace=ws)

    # Phase 3: re-index affected subdirectories for entities
    affected_dirs = {str(Path(f).parent) for f in changed_files if Path(f).parent != Path(".")}
    entity_count = 0
    relation_count = 0
    for d in affected_dirs:
        try:
            sub_result = await index_repo(
                str(repo_root / d), workspace=ws, clear=False,
            )
            entity_count += sub_result.get("entities", 0)
            relation_count += sub_result.get("relations", 0)
        except Exception as exc:
            log.warning("Entity update for %s failed: %s", d, exc)

    return {
        "strategy": "hot-update",
        "ratio": ratio,
        "files": files_processed,
        "chunks_cleared": cleared,
        "chunks": chunk_count,
        "entities": entity_count,
        "relations": relation_count,
    }


async def _clear_file_chunks(
    file_paths: list[str],
    *,
    workspace: str | None = None,
) -> int:
    """Delete old chunks for specific files from LightRAG.

    Queries GET /documents, matches by file_path, deletes via batch API.
    """
    import httpx

    ws = workspace or _WORKSPACE
    headers = {"LIGHTRAG-WORKSPACE": ws}
    cleared = 0

    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            # Fetch all documents
            resp = await client.get(f"{_LIGHTRAG_URL}/documents", headers=headers)
            if resp.status_code != 200:
                log.warning("GET /documents failed: %d", resp.status_code)
                return 0
            raw = resp.json()

        # Parse document list
        if isinstance(raw, list):
            docs = raw
        elif isinstance(raw, dict):
            docs = []
            for status_list in raw.get("statuses", {}).values():
                if isinstance(status_list, list):
                    docs.extend(status_list)
        else:
            return 0

        # Normalize paths for matching
        norm_targets = set()
        for fp in file_paths:
            norm_targets.add(fp.replace("\\", "/"))
            # Also add without common prefixes
            parts = fp.replace("\\", "/").split("/")
            if len(parts) > 1:
                norm_targets.add("/".join(parts))

        # Find chunks belonging to changed files
        delete_ids = []
        for doc in docs:
            doc_fp = (doc.get("file_path") or "").replace("\\", "/")
            source_id = (doc.get("source_id") or "").replace("\\", "/")
            matched = False
            for target in norm_targets:
                if doc_fp.endswith(target) or source_id.startswith(target + ":"):
                    matched = True
                    break
            if matched:
                doc_id = doc.get("id") or doc.get("doc_id") or ""
                if doc_id:
                    delete_ids.append(doc_id)

        if not delete_ids:
            return 0

        # Batch delete
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            resp = await client.request(
                "DELETE",
                f"{_LIGHTRAG_URL}/documents/delete_document",
                headers={**headers, "Content-Type": "application/json"},
                json={"doc_ids": delete_ids},
            )
            if resp.status_code == 200:
                cleared = len(delete_ids)
            else:
                log.warning("DELETE /documents failed: %d", resp.status_code)

    except Exception as exc:
        log.warning("clear_file_chunks error: %s", exc)

    return cleared


async def index_dir(
    directory: str,
    *,
    workspace: str | None = None,
    cwd: str | None = None,
    clear: bool = False,
) -> dict:
    """Index a subdirectory within a repo."""
    base = Path(cwd) if cwd else Path(".")
    target = base / directory
    if not target.exists():
        return {"files": 0, "entities": 0, "relations": 0, "errors": [f"{target} not found"], "skipped": 0}
    return await index_repo(str(target), workspace=workspace, clear=clear)
