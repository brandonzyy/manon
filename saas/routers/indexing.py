"""Indexing endpoints — sync-ast, index-status, merge-dynamic."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import TenantContext, require_tenant
from ..db import get_db
from ..metering import record_usage
from ..models import IndexStatus, SyncAstRequest, MergeDynamicRequest
from ..config import settings

# Ensure matrixone_graph is importable
_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from codeindex.parser import Symbol, Call, CallType, Import, Inheritance, ParseResult

from matrixone_graph.pipeline import (
    _map_parse_result, _module_from_rel_path, _resolve_import_by_filepath,
    GRAPH_FILE, VECTORS_FILE, CHUNKS_FILE, META_FILE,
    _load_meta, _save_meta, _load_chunks, _save_chunks,
    invalidate_kg_cache,
)
from matrixone_graph.store import CodeGraph, VectorIndex, Entity, Chunk
from matrixone_graph.embed import EmbeddingClient

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



@router.get("/index-status")
async def index_status(repo_id: str, ctx: TenantContext = Depends(require_tenant)):
    row = await _get_repo_row(repo_id, ctx.tenant_id)
    stats = json.loads(row["index_stats"]) if row["index_stats"] else None
    # 从 meta.json 读取实际入图的文件哈希
    repo_name = row["name"]
    kg_path = Path(settings.index_dir) / ctx.tenant_id / repo_name / "kg"
    meta = _load_meta(kg_path)
    file_hashes = meta.get("hashes", {})
    if stats:
        stats["file_hashes"] = file_hashes
    return IndexStatus(repo_id=repo_id, status=row["index_status"], stats=stats)


# ---------------------------------------------------------------------------
# sync-ast — receive pre-parsed AST from MCP client
# ---------------------------------------------------------------------------

def _reconstruct_parse_result(d: dict, file_path: str) -> ParseResult:
    """Rebuild a ParseResult from the dict sent by MCP client."""
    symbols = [Symbol(name=s.get("name", ""), kind=s.get("kind", ""),
                      signature=s.get("signature", ""), docstring=s.get("docstring", ""),
                      line_start=s.get("line_start", 0), line_end=s.get("line_end", 0),
                      annotations=s.get("annotations", []))
               for s in d.get("symbols", [])]
    calls = [Call(caller=c.get("caller", ""), callee=c.get("callee"),
                  line_number=c.get("line_number", 0),
                  call_type=CallType(c.get("call_type", "function")))
             for c in d.get("calls", [])]
    inheritances = [Inheritance(child=i.get("child", ""), parent=i.get("parent", ""))
                    for i in d.get("inheritances", [])]
    imports = [Import(module=im.get("module", ""), names=im.get("names", []))
               for im in d.get("imports", [])]
    return ParseResult(path=Path(file_path), symbols=symbols, calls=calls,
                       inheritances=inheritances, imports=imports,
                       error=d.get("error") or None)


def _remove_deleted_files(body, graph, vec_index, all_chunks, meta):
    """Remove deleted files from graph, vectors, and chunks."""
    for rel_path in body.deleted_files:
        old_entity_ids = {n for n, d in graph._g.nodes(data=True) if d.get("file_path") == rel_path}
        graph.remove_by_file(rel_path)
        old_cids = {cid for cid, c in all_chunks.items() if c.file_path == rel_path}
        vec_index.remove_by_ids(old_cids | old_entity_ids)
        for cid in old_cids:
            del all_chunks[cid]
        meta.get("hashes", {}).pop(rel_path, None)


def _process_ast_files(body, graph, all_chunks, vec_index):
    """Process each file's AST data. Returns (entities, relations, chunks, hashes)."""
    # Compute local package set from this batch + already-indexed graph entities.
    # Used to distinguish project-internal absolute imports (e.g. 'from matrixone_graph.store
    # import X') from genuinely external ones (fastapi, httpx, etc.).
    batch_top = {_module_from_rel_path(f.rel_path).split(".")[0] for f in body.files}
    graph_top = {
        eid.split(".")[0]
        for eid, d in graph._g.nodes(data=True)
        if d.get("kind") == "module" and "." not in eid
    }
    local_packages = frozenset(batch_top | graph_top)

    all_entities = []
    all_relations = []
    new_chunks = []
    new_hashes = {}
    new_reexports = {}
    for f in body.files:
        old_entity_ids = {n for n, d in graph._g.nodes(data=True) if d.get("file_path") == f.rel_path}
        graph.remove_by_file(f.rel_path)
        old_cids = {cid for cid, c in all_chunks.items() if c.file_path == f.rel_path}
        vec_index.remove_by_ids(old_cids | old_entity_ids)
        for cid in old_cids:
            del all_chunks[cid]
        pr = _reconstruct_parse_result(f.parse_result, f.rel_path)
        if pr.error:
            logger.warning("Skipping %s: parse error %s", f.rel_path, pr.error)
            continue
        module = _module_from_rel_path(f.rel_path)
        entities, relations = _map_parse_result(pr, module, local_packages)
        all_entities.extend(entities)
        all_relations.extend(relations)
        if not f.chunks:
            logger.warning("File %s has no pre-chunked data, skipping chunks", f.rel_path)
        chunks = [Chunk.from_dict(cd) for cd in f.chunks]
        new_chunks.extend(chunks)
        for c in chunks:
            all_chunks[c.id] = c
        new_hashes[f.rel_path] = f.hash

        # Extract re-export map from __init__.py files:
        # 'from .submod import name' → {pkg.name: pkg.submod.name}
        # Stored in meta and applied at final batch to redirect phantom edges.
        if Path(f.rel_path).name == "__init__.py":
            pkg_module = module  # e.g. "core.ast"
            for imp in pr.imports:
                if not imp.module.startswith("."):
                    continue
                submod = _resolve_import_by_filepath(f.rel_path, imp.module)
                for name in imp.names:
                    phantom_id = f"{pkg_module}.{name}"
                    real_id = f"{submod}.{name}"
                    if phantom_id != real_id:
                        new_reexports[phantom_id] = real_id

    return all_entities, all_relations, new_chunks, new_hashes, new_reexports


async def _embed_and_index_vectors(all_entities, new_chunks, vec_index, embedding_url: str) -> None:
    """Embed entities and chunks, add vectors to index."""
    embedder = EmbeddingClient(base_url=embedding_url)
    try:
        if all_entities:
            ent_vecs = await embedder.embed([e.description for e in all_entities])
            vec_index.add_entity_vectors([e.id for e in all_entities], ent_vecs)
        if new_chunks:
            chunk_vecs = await embedder.embed([c.content[:1000] for c in new_chunks])
            vec_index.add_chunk_vectors([c.id for c in new_chunks], chunk_vecs)
    finally:
        await embedder.close()


def _apply_reexport_map(graph, reexport_map: dict) -> int:
    """Redirect edges from re-export phantom IDs to their canonical real entity IDs.

    When core/ast/__init__.py re-exports 'find_project_by_repo_id' from '.project',
    callers import it as 'core.ast.find_project_by_repo_id' (phantom), but the real
    entity is 'core.ast.project.find_project_by_repo_id'. This function redirects
    all edges and removes the phantom.

    Returns count of redirected edges.
    """
    redirected = 0
    for phantom_id, real_id in reexport_map.items():
        if phantom_id not in graph._g:
            continue
        if graph.get_entity(real_id) is None:
            continue  # real entity not indexed — leave phantom as-is
        in_edges  = list(graph._g.in_edges(phantom_id, data=True))
        out_edges = list(graph._g.out_edges(phantom_id, data=True))
        graph._g.remove_node(phantom_id)
        for src, _, data in in_edges:
            if graph._g.has_node(src) and not graph._g.has_edge(src, real_id):
                graph._g.add_edge(src, real_id, **data)
                redirected += 1
        for _, tgt, data in out_edges:
            if graph._g.has_node(tgt) and not graph._g.has_edge(real_id, tgt):
                graph._g.add_edge(real_id, tgt, **data)
                redirected += 1
    return redirected


def _persist_kg_state(kg_path: Path, graph, vec_index, all_chunks: dict, new_hashes: dict, meta: dict) -> None:
    """Save graph, vectors, chunks, and metadata to disk."""
    graph.save(kg_path / GRAPH_FILE)
    vec_index.save(kg_path / VECTORS_FILE)
    _save_chunks(kg_path, all_chunks)
    meta.update({
        "version": 1, "entity_count": graph.entity_count, "relation_count": graph.relation_count,
        "chunk_count": len(all_chunks), "file_count": len(new_hashes), "hashes": new_hashes,
    })
    _save_meta(kg_path, meta)
    invalidate_kg_cache(kg_path)


async def _run_ast_sync(repo_id: str, tenant_id: str, repo_name: str, body: SyncAstRequest):
    """Background task: process pre-parsed AST data from MCP client."""
    db = await get_db()
    try:
        await db.execute("UPDATE repos SET index_status = 'indexing', updated_at = datetime('now') WHERE id = ?", (repo_id,))
        await db.commit()

        kg_path = Path(settings.index_dir) / tenant_id / repo_name / "kg"
        kg_path.mkdir(parents=True, exist_ok=True)

        graph, vec_index = CodeGraph(), VectorIndex()
        graph.load(kg_path / GRAPH_FILE)
        vec_index.load(kg_path / VECTORS_FILE)
        all_chunks = _load_chunks(kg_path)
        meta = _load_meta(kg_path)

        if body.full_reindex:
            graph, vec_index, all_chunks, meta = CodeGraph(), VectorIndex(), {}, {"version": 1, "hashes": {}}

        _remove_deleted_files(body, graph, vec_index, all_chunks, meta)
        all_entities, all_relations, new_chunks, file_hashes, new_reexports = _process_ast_files(body, graph, all_chunks, vec_index)
        new_hashes = {**meta.get("hashes", {}), **file_hashes}
        # Accumulate re-export map across batches (stored in meta for persistence)
        meta.setdefault("reexport_map", {}).update(new_reexports)

        # Final-batch reconcile: remove any entities whose file is no longer tracked.
        # Catches stale entities from interrupted previous syncs where deleted_files
        # was never sent.
        if body.is_final_batch and not body.full_reindex:
            tracked = set(new_hashes.keys())
            stale_files = {
                d["file_path"]
                for _, d in graph._g.nodes(data=True)
                if d.get("file_path") and d["file_path"] not in tracked
            }
            for fp in stale_files:
                old_cids = {cid for cid, c in all_chunks.items() if c.file_path == fp}
                vec_index.remove_by_ids(old_cids)
                for cid in old_cids:
                    del all_chunks[cid]
                graph.remove_by_file(fp)
                meta.get("hashes", {}).pop(fp, None)
            if stale_files:
                logger.info("reconcile: removed stale entities from %d files: %s",
                            len(stale_files), list(stale_files)[:10])
            pruned = graph.prune_phantoms()
            if pruned:
                logger.info("prune_phantoms: removed %d dead phantom nodes", pruned)
            reexport_map = meta.get("reexport_map", {})
            if reexport_map:
                redirected = _apply_reexport_map(graph, reexport_map)
                if redirected:
                    logger.info("reexport normalization: redirected %d edges to canonical entities", redirected)

        entities_added = len(all_entities)
        for e in all_entities:
            graph.add_entity(e)
        relations_added = 0
        for r in all_relations:
            # Require at least one *real* entity (has kind) to avoid
            # chaining phantom nodes for fully-unresolved references.
            src_real = graph.get_entity(r.src_id) is not None
            tgt_real = graph.get_entity(r.tgt_id) is not None
            if src_real or tgt_real:
                graph.add_relation(r)
                relations_added += 1

        await _embed_and_index_vectors(all_entities, new_chunks, vec_index, settings.embedding_url)
        _persist_kg_state(kg_path, graph, vec_index, all_chunks, new_hashes, meta)

        phantom_ratio = graph.phantom_count / max(graph.entity_count, 1)
        stats = {
            "files_synced": len(body.files), "files_deleted": len(body.deleted_files),
            "entities_added": entities_added, "relations_added": relations_added,
            "chunks_added": len(new_chunks), "total_entities": graph.entity_count,
            "total_relations": graph.relation_count, "total_chunks": len(all_chunks),
            "total_files": len(new_hashes), "phantom_nodes": graph.phantom_count,
            "phantom_ratio": round(phantom_ratio, 3),
        }
        if phantom_ratio > 0.25:
            stats["recommend_rebuild"] = True
            logger.warning(
                "repo %s: phantom ratio %.2f (phantoms=%d entities=%d) exceeds threshold 0.25 — recommend full reindex",
                repo_id, phantom_ratio, graph.phantom_count, graph.entity_count,
            )
        await db.execute("UPDATE repos SET index_status = 'done', index_stats = ?, updated_at = datetime('now') WHERE id = ?", (json.dumps(stats), repo_id))
        await db.commit()
        logger.info("sync-ast done for %s: %s", repo_id, stats)

    except Exception as exc:
        logger.exception("sync-ast failed for %s", repo_id)
        await db.execute("UPDATE repos SET index_status = 'error', index_stats = ?, updated_at = datetime('now') WHERE id = ?", (json.dumps({"error": str(exc)[:500]}), repo_id))
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


# ---------------------------------------------------------------------------
# merge-dynamic — merge runtime-traced call edges into the graph
# ---------------------------------------------------------------------------

@router.post("/merge-dynamic")
async def merge_dynamic(
    repo_id: str,
    body: MergeDynamicRequest,
    ctx: TenantContext = Depends(require_tenant),
):
    """Merge dynamic call edges (from runtime tracing) into the knowledge graph.

    Accepts two formats:
    - edges: {"caller->callee": count} — pre-resolved entity IDs
    - raw_edges: [{"from": path, "to": path}] + project_root — file paths resolved server-side
    """
    from matrixone_graph.merge_dynamic import merge_dynamic_edges

    row = await _get_repo_row(repo_id, ctx.tenant_id)
    repo_name = row["name"]
    kg_path = Path(settings.index_dir) / ctx.tenant_id / repo_name / "kg"

    graph = CodeGraph()
    graph.load(kg_path / GRAPH_FILE)

    edges = dict(body.edges)

    # Resolve raw file-path edges if provided
    resolved_count = 0
    if body.raw_edges:
        if not body.project_root:
            raise HTTPException(400, "project_root is required when raw_edges is provided")
        from matrixone_graph.resolve_runtime import resolve_js_edges
        resolved = resolve_js_edges(body.raw_edges, body.project_root, graph=graph)
        resolved_count = len(resolved)
        # Merge resolved edges into the main edges dict
        for k, v in resolved.items():
            edges[k] = edges.get(k, 0) + v

    if not edges:
        return {"repo_id": repo_id, "status": "done", "removed": 0, "added": 0, "skipped": 0, "resolved": 0}

    stats = merge_dynamic_edges(graph, edges, replace=True)

    graph.save(kg_path / GRAPH_FILE)
    invalidate_kg_cache(kg_path)

    await record_usage(ctx.tenant_id, "indexing.merge_dynamic", repo_id)
    return {"repo_id": repo_id, "status": "done", "resolved_from_raw": resolved_count, **stats}
