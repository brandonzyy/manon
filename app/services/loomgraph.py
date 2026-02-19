"""LoomGraph + CodeIndex service — direct Python API (no subprocess)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("manon.loomgraph")

# Configured by main.py lifespan
_LIGHTRAG_URL: str = "http://117.131.45.179:3010"
_LIGHTRAG_TIMEOUT: float = 30.0
_WORKSPACE: str = "donnie_default"


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
        result.symbols = kept
        return result

    client = _client(workspace)

    # Phase 1: entity + relation injection
    result = await _index_repo(
        repo_root,
        client,
        _filtered_parse,
        clear_existing=False,
        on_progress=on_progress,
    )
    already_existed = sum(1 for e in result.errors if "already exists" in e.lower())
    real_errors = [e for e in result.errors if "already exists" not in e.lower()]

    # Phase 2: chunk injection via /documents/texts (proper embedding)
    # Concatenate chunks per file → one document per file
    chunk_count = 0
    files = scan_code_files(repo_root)
    file_texts: list[str] = []
    file_sources: list[str] = []
    for f in files:
        try:
            chunks = chunk_file_for_kg(str(f))
            if chunks:
                combined = "\n\n".join(c["content"] for c in chunks)
                file_texts.append(combined)
                file_sources.append(str(f))
                chunk_count += len(chunks)
        except Exception as exc:
            log.debug("chunk_file_for_kg failed for %s: %s", f, exc)

    if file_texts:
        if on_progress:
            on_progress("Injecting documents", 0, len(file_texts))
        # Batch insert via /documents/texts (background processing)
        BATCH = 20
        import httpx
        ws = workspace or _WORKSPACE
        for i in range(0, len(file_texts), BATCH):
            batch_texts = file_texts[i : i + BATCH]
            batch_sources = file_sources[i : i + BATCH]
            try:
                async with httpx.AsyncClient(timeout=60.0, trust_env=False) as http:
                    resp = await http.post(
                        f"{_LIGHTRAG_URL}/documents/texts",
                        headers={"LIGHTRAG-WORKSPACE": ws},
                        json={"texts": batch_texts, "file_sources": batch_sources},
                    )
                    if resp.status_code >= 400:
                        log.warning("Texts batch %d failed: %s", i // BATCH, resp.text[:200])
            except Exception as exc:
                log.warning("Texts batch %d failed: %s", i // BATCH, exc)
        log.info("Document injection: %d files, %d chunks submitted", len(file_texts), chunk_count)

    return {
        "files": result.files,
        "entities": result.entities + already_existed,
        "relations": result.relations,
        "chunks": chunk_count,
        "errors": real_errors[:20],
        "skipped": len(result.skipped_files),
    }


async def update_index(
    repo_path: str,
    *,
    workspace: str | None = None,
) -> dict:
    """Incremental update — for now delegates to full index with clear=False."""
    return await index_repo(repo_path, workspace=workspace, clear=False)


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


# ── Stats parsing ──

def parse_response_stats(result: dict) -> dict:
    """Parse raw LoomGraph response into structured stats for the UI."""
    data = result.get("data", result)
    response_text = data.get("response", "")

    entities = []
    entity_types: dict[str, int] = {}
    entity_section = re.search(
        r"Knowledge Graph Data \(Entity\):\s*```json\s*(.*?)```",
        response_text, re.DOTALL,
    )
    if entity_section:
        for line in entity_section.group(1).strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ent = json.loads(line)
                entities.append(ent)
                etype = ent.get("type", "unknown")
                entity_types[etype] = entity_types.get(etype, 0) + 1
            except json.JSONDecodeError:
                pass

    relations = []
    rel_section = re.search(
        r"Knowledge Graph Data \(Relationship\):\s*```json\s*(.*?)```",
        response_text, re.DOTALL,
    )
    if rel_section:
        for line in rel_section.group(1).strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                relations.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    chunk_section = re.search(
        r"Document Chunks.*?```json\s*(.*?)```",
        response_text, re.DOTALL,
    )
    chunk_count = 0
    if chunk_section:
        for line in chunk_section.group(1).strip().splitlines():
            if line.strip():
                chunk_count += 1

    # Extract file paths from entity descriptions
    files = set()
    for ent in entities:
        desc = ent.get("description", "")
        # Pattern: "... | /path/to/file.ts" or "... | Python | /path/to/file.py:1-10"
        parts = desc.split("|")
        for part in parts:
            part = part.strip()
            if "/" in part and ("." in part.split("/")[-1]):
                # Looks like a file path
                path = part.split(":")[0].strip()
                files.add(path)

    return {
        "entities": len(entities),
        "relations": len(relations),
        "files": len(files),
        "chunks": chunk_count or len(entities),
        "lastUpdate": datetime.now(timezone.utc).isoformat(),
        "entityTypes": entity_types,
    }


async def get_stats(*, workspace: str | None = None) -> dict:
    """Get LoomGraph stats — returns structured counts for the UI."""
    try:
        result = await search("project overview", mode="local", workspace=workspace)
        return parse_response_stats(result)
    except Exception as exc:
        log.warning("Failed to get stats: %s", exc)
        return {"entities": 0, "relations": 0, "files": 0, "chunks": 0}
