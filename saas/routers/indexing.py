"""Indexing endpoints — trigger, poll status, push-update, sync-ast."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import TenantContext, require_tenant
from ..db import get_db
from ..metering import record_usage
from ..models import IndexTrigger, IndexStatus, SyncAstRequest
from ..services.graph import get_graph
from ..services.git import clone_or_pull
from ..config import settings

router = APIRouter(prefix="/api/v1/repos/{repo_id}", tags=["indexing"])
logger = logging.getLogger(__name__)


async def _get_repo_row(repo_id: str, tenant_id: str):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM repos WHERE id = ? AND tenant_id = ?", (repo_id, tenant_id),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repo not found")
    return row


async def _run_index(repo_id: str, tenant_id: str, local_path: str, incremental: bool):
    """Background task: run indexing and update DB status."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE repos SET index_status = 'indexing', updated_at = datetime('now') WHERE id = ?",
            (repo_id,),
        )
        await db.commit()

        mg = get_graph(tenant_id, local_path)
        result = await mg.index(incremental=incremental)
        st = mg.status()
        stats = {
            "files_scanned": result.files_scanned,
            "files_indexed": result.files_indexed,
            "entities_added": result.entities_added,
            "relations_added": result.relations_added,
            "chunks_added": result.chunks_added,
            "total_files": result.files_indexed,
            "total_entities": st.get("entity_count", 0),
            "total_relations": st.get("relation_count", 0),
            "total_chunks": st.get("chunk_count", 0),
        }
        await db.execute(
            "UPDATE repos SET index_status = 'done', index_stats = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(stats), repo_id),
        )
        await db.commit()
    except Exception as exc:
        await db.execute(
            "UPDATE repos SET index_status = 'error', index_stats = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps({"error": str(exc)[:500]}), repo_id),
        )
        await db.commit()


@router.post("/index", status_code=202)
async def trigger_index(
    repo_id: str,
    body: IndexTrigger = IndexTrigger(),
    ctx: TenantContext = Depends(require_tenant),
):
    row = await _get_repo_row(repo_id, ctx.tenant_id)
    if not row["local_path"]:
        raise HTTPException(400, "repo has no local path — create with git_url or local_path first")

    asyncio.create_task(_run_index(repo_id, ctx.tenant_id, row["local_path"], body.incremental))
    await record_usage(ctx.tenant_id, "indexing.trigger", repo_id)
    return {"repo_id": repo_id, "status": "indexing"}


@router.get("/index-status")
async def index_status(repo_id: str, ctx: TenantContext = Depends(require_tenant)):
    row = await _get_repo_row(repo_id, ctx.tenant_id)
    stats = json.loads(row["index_stats"]) if row["index_stats"] else None
    return IndexStatus(repo_id=repo_id, status=row["index_status"], stats=stats)


@router.post("/push-update", status_code=202)
async def push_update(repo_id: str, ctx: TenantContext = Depends(require_tenant)):
    """Pull latest changes then re-index incrementally."""
    row = await _get_repo_row(repo_id, ctx.tenant_id)
    if row["git_url"]:
        local_path = await clone_or_pull(repo_id, row["git_url"], row["branch"])
        db = await get_db()
        await db.execute("UPDATE repos SET local_path = ? WHERE id = ?", (local_path, repo_id))
        await db.commit()
    else:
        local_path = row["local_path"]

    if not local_path:
        raise HTTPException(400, "repo has no local path or git_url — use sync-ast for local repos")

    asyncio.create_task(_run_index(repo_id, ctx.tenant_id, local_path, incremental=True))
    await record_usage(ctx.tenant_id, "indexing.push_update", repo_id)
    return {"repo_id": repo_id, "status": "indexing"}


# ---------------------------------------------------------------------------
# sync-ast — receive pre-parsed AST from MCP client
# ---------------------------------------------------------------------------

@dataclass
class _Symbol:
    kind: str = ""
    name: str = ""
    signature: str = ""
    docstring: str = ""
    line_start: int = 0
    line_end: int = 0
    annotations: list = field(default_factory=list)

@dataclass
class _Call:
    caller: str = ""
    callee: str = ""

@dataclass
class _Inheritance:
    child: str = ""
    parent: str = ""

@dataclass
class _Import:
    module: str = ""
    names: list = None
    def __post_init__(self):
        if self.names is None:
            self.names = []

@dataclass
class _FakeParseResult:
    """Lightweight stand-in for codeindex.parser.ParseResult."""
    path: str = ""
    symbols: list = None
    calls: list = None
    inheritances: list = None
    imports: list = None
    error: str = ""
    def __post_init__(self):
        self.symbols = self.symbols or []
        self.calls = self.calls or []
        self.inheritances = self.inheritances or []
        self.imports = self.imports or []


def _reconstruct_parse_result(d: dict, file_path: str) -> _FakeParseResult:
    """Rebuild a ParseResult-like object from the dict sent by MCP client."""
    symbols = [_Symbol(**{k: s.get(k, f.default) for k, f in _Symbol.__dataclass_fields__.items()})
               for s in d.get("symbols", [])]
    calls = [_Call(**{k: c.get(k, "") for k in ("caller", "callee")})
             for c in d.get("calls", [])]
    inheritances = [_Inheritance(**{k: i.get(k, "") for k in ("child", "parent")})
                    for i in d.get("inheritances", [])]
    imports = [_Import(module=im.get("module", ""), names=im.get("names", []))
               for im in d.get("imports", [])]
    return _FakeParseResult(
        path=file_path, symbols=symbols, calls=calls,
        inheritances=inheritances, imports=imports,
        error=d.get("error", ""),
    )


async def _run_ast_sync(repo_id: str, tenant_id: str, repo_name: str, body: SyncAstRequest):
    """Background task: process pre-parsed AST data from MCP client."""
    import sys
    _project_root = str(Path(__file__).resolve().parents[2])
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

    from matrixone_graph.pipeline import (
        _map_parse_result, _chunk_file, _module_prefix,
        GRAPH_FILE, VECTORS_FILE, CHUNKS_FILE, META_FILE,
        _load_meta, _save_meta, _load_chunks, _save_chunks,
        invalidate_kg_cache,
    )
    from matrixone_graph.store import CodeGraph, VectorIndex, Entity, Chunk
    from matrixone_graph.embed import EmbeddingClient

    db = await get_db()
    try:
        await db.execute(
            "UPDATE repos SET index_status = 'indexing', updated_at = datetime('now') WHERE id = ?",
            (repo_id,),
        )
        await db.commit()

        # Resolve kg_path (tenant-isolated)
        kg_path = Path(settings.index_dir) / tenant_id / repo_name / "kg"
        kg_path.mkdir(parents=True, exist_ok=True)

        graph = CodeGraph()
        vec_index = VectorIndex()
        graph.load(kg_path / GRAPH_FILE)
        vec_index.load(kg_path / VECTORS_FILE)
        all_chunks = _load_chunks(kg_path)
        meta = _load_meta(kg_path)

        # Handle full reindex — clear everything
        if body.full_reindex:
            graph = CodeGraph()
            vec_index = VectorIndex()
            all_chunks = {}
            meta = {"version": 1, "hashes": {}}

        # Remove deleted files
        for rel_path in body.deleted_files:
            graph.remove_by_file(rel_path)
            old_cids = {cid for cid, c in all_chunks.items() if c.file_path == rel_path}
            vec_index.remove_by_ids(old_cids)
            for cid in old_cids:
                del all_chunks[cid]
            meta.get("hashes", {}).pop(rel_path, None)

        # Process each file's AST
        all_entities: list[Entity] = []
        all_relations = []
        new_chunks: list[Chunk] = []
        new_hashes = dict(meta.get("hashes", {}))

        for f in body.files:
            # Remove old data for this file
            graph.remove_by_file(f.rel_path)
            old_cids = {cid for cid, c in all_chunks.items() if c.file_path == f.rel_path}
            vec_index.remove_by_ids(old_cids)
            for cid in old_cids:
                del all_chunks[cid]

            # Reconstruct ParseResult
            pr = _reconstruct_parse_result(f.parse_result, f.rel_path)
            if pr.error:
                logger.warning("Skipping %s: parse error %s", f.rel_path, pr.error)
                continue

            # Derive module prefix from relative path
            rel = Path(f.rel_path)
            parts = list(rel.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            module = ".".join(parts)

            entities, relations = _map_parse_result(pr, module)
            all_entities.extend(entities)
            all_relations.extend(relations)

            chunks = _chunk_file(f.source, pr, module)
            new_chunks.extend(chunks)
            for c in chunks:
                all_chunks[c.id] = c

            new_hashes[f.rel_path] = f.hash

        # Add to graph
        entities_added = 0
        relations_added = 0
        for ent in all_entities:
            graph.add_entity(ent)
            entities_added += 1
        for rel in all_relations:
            if graph.has_entity(rel.src_id) or graph.has_entity(rel.tgt_id):
                graph.add_relation(rel)
                relations_added += 1

        # Embed
        embedder = EmbeddingClient(base_url=settings.embedding_url)
        try:
            if all_entities:
                ent_vecs = await embedder.embed([e.description for e in all_entities])
                vec_index.add_entity_vectors([e.id for e in all_entities], ent_vecs)
            if new_chunks:
                chunk_vecs = await embedder.embed([c.content[:1000] for c in new_chunks])
                vec_index.add_chunk_vectors([c.id for c in new_chunks], chunk_vecs)
        finally:
            await embedder.close()

        # Save
        graph.save(kg_path / GRAPH_FILE)
        vec_index.save(kg_path / VECTORS_FILE)
        _save_chunks(kg_path, all_chunks)
        meta.update({
            "version": 1, "entity_count": graph.entity_count,
            "relation_count": graph.relation_count, "chunk_count": len(all_chunks),
            "file_count": len(new_hashes), "embedding_url": settings.embedding_url,
            "hashes": new_hashes,
        })
        _save_meta(kg_path, meta)
        invalidate_kg_cache(kg_path)  # force reload on next query

        stats = {
            "files_synced": len(body.files),
            "files_deleted": len(body.deleted_files),
            "entities_added": entities_added,
            "relations_added": relations_added,
            "chunks_added": len(new_chunks),
            "total_entities": graph.entity_count,
            "total_relations": graph.relation_count,
            "total_chunks": len(all_chunks),
            "total_files": len(new_hashes),
        }
        await db.execute(
            "UPDATE repos SET index_status = 'done', index_stats = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(stats), repo_id),
        )
        await db.commit()
        logger.info("sync-ast done for %s: %s", repo_id, stats)

    except Exception as exc:
        logger.exception("sync-ast failed for %s", repo_id)
        await db.execute(
            "UPDATE repos SET index_status = 'error', index_stats = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps({"error": str(exc)[:500]}), repo_id),
        )
        await db.commit()


@router.post("/sync-ast", status_code=200)
async def sync_ast(
    repo_id: str,
    body: SyncAstRequest,
    ctx: TenantContext = Depends(require_tenant),
):
    """Receive pre-parsed AST from MCP client and rebuild graph/vectors.

    Runs synchronously so batched uploads process sequentially —
    each batch sees the previous batch's saved state.
    """
    row = await _get_repo_row(repo_id, ctx.tenant_id)
    repo_name = row["name"]
    await _run_ast_sync(repo_id, ctx.tenant_id, repo_name, body)
    await record_usage(ctx.tenant_id, "indexing.sync_ast", repo_id)
    return {"repo_id": repo_id, "status": "done"}
