"""Indexing endpoints — sync-ast, index-status, merge-dynamic."""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
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

from matrixone_graph.pipeline import (
    _map_parse_result, _module_from_rel_path,
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


def _remove_deleted_files(body, graph, vec_index, all_chunks, meta):
    """Remove deleted files from graph, vectors, and chunks."""
    for rel_path in body.deleted_files:
        graph.remove_by_file(rel_path)
        old_cids = {cid for cid, c in all_chunks.items() if c.file_path == rel_path}
        vec_index.remove_by_ids(old_cids)
        for cid in old_cids:
            del all_chunks[cid]
        meta.get("hashes", {}).pop(rel_path, None)


def _process_ast_files(body, graph, all_chunks, vec_index):
    """Process each file's AST data. Returns (entities, relations, chunks, hashes)."""
    all_entities = []
    all_relations = []
    new_chunks = []
    new_hashes = {}
    for f in body.files:
        graph.remove_by_file(f.rel_path)
        old_cids = {cid for cid, c in all_chunks.items() if c.file_path == f.rel_path}
        vec_index.remove_by_ids(old_cids)
        for cid in old_cids:
            del all_chunks[cid]
        pr = _reconstruct_parse_result(f.parse_result, f.rel_path)
        if pr.error:
            logger.warning("Skipping %s: parse error %s", f.rel_path, pr.error)
            continue
        module = _module_from_rel_path(f.rel_path)
        entities, relations = _map_parse_result(pr, module)
        all_entities.extend(entities)
        all_relations.extend(relations)
        if not f.chunks:
            logger.warning("File %s has no pre-chunked data, skipping chunks", f.rel_path)
        chunks = [Chunk.from_dict(cd) for cd in f.chunks]
        new_chunks.extend(chunks)
        for c in chunks:
            all_chunks[c.id] = c
        new_hashes[f.rel_path] = f.hash
    return all_entities, all_relations, new_chunks, new_hashes


async def _run_ast_sync(repo_id: str, tenant_id: str, repo_name: str, body: SyncAstRequest):
    """Background task: process pre-parsed AST data from MCP client."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE repos SET index_status = 'indexing', updated_at = datetime('now') WHERE id = ?",
            (repo_id,),
        )
        await db.commit()

        kg_path = Path(settings.index_dir) / tenant_id / repo_name / "kg"
        kg_path.mkdir(parents=True, exist_ok=True)

        graph = CodeGraph()
        vec_index = VectorIndex()
        graph.load(kg_path / GRAPH_FILE)
        vec_index.load(kg_path / VECTORS_FILE)
        all_chunks = _load_chunks(kg_path)
        meta = _load_meta(kg_path)

        if body.full_reindex:
            graph = CodeGraph()
            vec_index = VectorIndex()
            all_chunks = {}
            meta = {"version": 1, "hashes": {}}

        _remove_deleted_files(body, graph, vec_index, all_chunks, meta)
        all_entities, all_relations, new_chunks, file_hashes = _process_ast_files(
            body, graph, all_chunks, vec_index,
        )
        new_hashes = dict(meta.get("hashes", {}))
        new_hashes.update(file_hashes)

        entities_added = 0
        relations_added = 0
        for ent in all_entities:
            graph.add_entity(ent)
            entities_added += 1
        for rel in all_relations:
            if graph.has_entity(rel.src_id) or graph.has_entity(rel.tgt_id):
                graph.add_relation(rel)
                relations_added += 1

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

        graph.save(kg_path / GRAPH_FILE)
        vec_index.save(kg_path / VECTORS_FILE)
        _save_chunks(kg_path, all_chunks)
        meta.update({
            "version": 1, "entity_count": graph.entity_count,
            "relation_count": graph.relation_count, "chunk_count": len(all_chunks),
            "file_count": len(new_hashes),
            "hashes": new_hashes,
        })
        _save_meta(kg_path, meta)
        invalidate_kg_cache(kg_path)

        stats = {
            "files_synced": len(body.files), "files_deleted": len(body.deleted_files),
            "entities_added": entities_added, "relations_added": relations_added,
            "chunks_added": len(new_chunks), "total_entities": graph.entity_count,
            "total_relations": graph.relation_count, "total_chunks": len(all_chunks),
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
