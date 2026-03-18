"""Chunking, caching, and query pipelines for MatrixoneGraph.

chunk_file_from_dict() — client-side chunking (MCP → sync-ast)
query()               — embed → vector search → graph BFS → assemble context
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.ast.chunking import _make_entity_id, _module_from_rel_path

from .embed import EmbeddingClient
from .store import Chunk, CodeGraph, Entity, Relation, VectorIndex

logger = logging.getLogger(__name__)

KG_DIR = ".codeindex/kg"
GRAPH_FILE = "graph.json"
VECTORS_FILE = "vectors.npz"
CHUNKS_FILE = "chunks.json"
META_FILE = "meta.json"


@dataclass
class QueryResult:
    entities: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    context: str = ""


def _resolve_import_by_filepath(file_path: str, import_path: str) -> str:
    """Resolve a relative import using the actual file path.

    Uses the file path with '/' separators so that filenames
    containing dots (e.g., 'intent-detector.test.ts') are treated as
    a single path component.

    Args:
        file_path: Relative file path, e.g. "tests/intent-detector.test.ts"
        import_path: Import path, e.g. "../electron/orchestrator/intent-detector"

    Returns:
        Dot-separated module ID, e.g. "electron.orchestrator.intent-detector"
    """
    if not import_path.startswith("."):
        return import_path
    import posixpath
    file_dir = posixpath.dirname(file_path.replace("\\", "/"))
    resolved = posixpath.normpath(posixpath.join(file_dir, import_path))
    return resolved.replace("/", ".")


def _build_description(symbol) -> str:
    parts = [f"{symbol.kind}: {symbol.name}"]
    if symbol.signature:
        parts.append(symbol.signature)
    if symbol.docstring:
        parts.append(symbol.docstring[:200])
    return " | ".join(parts)


def _resolve_callee(
    call_callee: str, local_names: set[str], imported_names: dict[str, str],
    module: str, fp: str,
) -> str:
    """Resolve a callee reference to a fully qualified entity ID."""
    if call_callee in local_names:
        return _make_entity_id(module, call_callee)
    if call_callee in imported_names:
        return imported_names[call_callee]
    if call_callee.startswith(("./", "../")):
        return _resolve_import_by_filepath(fp, call_callee)
    if "." in call_callee:
        prefix, rest = call_callee.split(".", 1)
        if prefix in imported_names:
            return f"{imported_names[prefix]}.{rest}"
        parent_module = module.rsplit(".", 1)[0] if "." in module else ""
        if parent_module:
            return f"{parent_module}.{prefix}.{rest}"
    return call_callee


def _map_parse_result(pr: ParseResult, module: str) -> tuple[list[Entity], list[Relation]]:
    entities: list[Entity] = []
    relations: list[Relation] = []
    fp = str(pr.path)
    # Add a module-level entity so import relations have a valid source
    entities.append(Entity(
        id=module, kind="module", name=module,
        description=f"module: {module}",
        file_path=fp, line_start=0, line_end=0,
    ))
    local_names: set[str] = set()
    for sym in pr.symbols:
        eid = _make_entity_id(module, sym.name)
        local_names.add(sym.name)
        decorators = [
            a["name"] if isinstance(a, dict) else (a if isinstance(a, str) else a.name)
            for a in sym.annotations
        ] if sym.annotations else []
        entities.append(Entity(
            id=eid, kind=sym.kind, name=sym.name,
            description=_build_description(sym),
            file_path=fp, line_start=sym.line_start, line_end=sym.line_end,
            decorators=decorators,
        ))
    # Build imported_names: short name → fully qualified entity ID
    imported_names: dict[str, str] = {}
    for imp in pr.imports:
        resolved_module = _resolve_import_by_filepath(fp, imp.module)
        for name in imp.names:
            imported_names[name] = f"{resolved_module}.{name}"
        if not imp.names:
            short = resolved_module.rsplit(".", 1)[-1]
            imported_names[short] = resolved_module
    # Resolve calls
    for call in pr.calls:
        if call.callee is None:
            continue
        caller_id = _make_entity_id(module, call.caller) if call.caller in local_names else call.caller
        callee_id = _resolve_callee(call.callee, local_names, imported_names, module, fp)
        relations.append(Relation(
            src_id=caller_id, tgt_id=callee_id,
            kind="calls", description=f"{call.caller} -> {call.callee}",
            file_path=fp, weight=1.0,
        ))
    for inh in pr.inheritances:
        child_id = _make_entity_id(module, inh.child)
        relations.append(Relation(
            src_id=child_id, tgt_id=inh.parent,
            kind="inherits", description=f"{inh.child} extends {inh.parent}",
            file_path=fp, weight=1.0,
        ))
    for imp in pr.imports:
        resolved_module = _resolve_import_by_filepath(fp, imp.module)
        for name in imp.names:
            relations.append(Relation(
                src_id=module, tgt_id=f"{resolved_module}.{name}",
                kind="imports", description=f"imports {resolved_module}.{name}",
                file_path=fp, weight=0.5,
            ))
        if not imp.names:
            relations.append(Relation(
                src_id=module, tgt_id=resolved_module,
                kind="imports", description=f"imports {resolved_module}",
                file_path=fp, weight=0.5,
            ))
    return entities, relations


def _load_meta(kg_path: Path) -> dict[str, Any]:
    meta_file = kg_path / META_FILE
    if meta_file.exists():
        return json.loads(meta_file.read_text(encoding="utf-8"))
    return {"version": 1, "hashes": {}}

def _save_meta(kg_path: Path, meta: dict[str, Any]) -> None:
    kg_path.mkdir(parents=True, exist_ok=True)
    (kg_path / META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

def _load_chunks(kg_path: Path) -> dict[str, Chunk]:
    p = kg_path / CHUNKS_FILE
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {k: Chunk.from_dict(v) for k, v in raw.items()}

def _save_chunks(kg_path: Path, chunks: dict[str, Chunk]) -> None:
    kg_path.mkdir(parents=True, exist_ok=True)
    (kg_path / CHUNKS_FILE).write_text(
        json.dumps({k: v.to_dict() for k, v in chunks.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# In-memory graph cache (30-min TTL)
# ---------------------------------------------------------------------------

_KG_CACHE_TTL = 30 * 60  # 30 minutes

@dataclass
class _CachedKG:
    graph: CodeGraph
    vec_index: VectorIndex
    chunks: dict[str, Chunk]
    last_access: float

_kg_cache: dict[str, _CachedKG] = {}


def _get_cached_kg(kg_path: Path) -> tuple[CodeGraph, VectorIndex, dict[str, Chunk]]:
    """Load graph/vectors/chunks from cache or disk. Resets TTL on access."""
    key = str(kg_path)
    now = time.monotonic()
    # Evict stale entries
    stale = [k for k, v in _kg_cache.items() if now - v.last_access > _KG_CACHE_TTL]
    for k in stale:
        del _kg_cache[k]
    if key in _kg_cache:
        _kg_cache[key].last_access = now
        c = _kg_cache[key]
        return c.graph, c.vec_index, c.chunks
    # Load from disk
    graph = CodeGraph()
    vec_index = VectorIndex()
    graph.load(kg_path / GRAPH_FILE)
    vec_index.load(kg_path / VECTORS_FILE)
    chunks = _load_chunks(kg_path)
    _kg_cache[key] = _CachedKG(graph=graph, vec_index=vec_index, chunks=chunks, last_access=now)
    return graph, vec_index, chunks


def invalidate_kg_cache(kg_path: Path) -> None:
    """Remove a specific kg_path from cache (call after re-indexing)."""
    _kg_cache.pop(str(kg_path), None)



# ---------------------------------------------------------------------------
# Query pipeline
# ---------------------------------------------------------------------------

def _traverse_neighbors(
    graph: CodeGraph, ent_hits: list[tuple[str, float]],
    matched_entities: list[dict[str, Any]], depth: int, direction: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Traverse graph neighbors for matched entities. Returns (relations, neighbor_entities)."""
    all_rels: list[dict[str, Any]] = []
    neighbor_entities: list[dict[str, Any]] = []
    seen_rels: set[str] = set()
    matched_ids = {e["id"] for e in matched_entities}
    for eid, _ in ent_hits:
        traverse_ids = [eid]
        if not list(graph._g.edges(eid)) and not list(graph._g.in_edges(eid)):
            prefix = eid + "."
            traverse_ids.extend(
                nid for nid in graph._g.nodes() if nid.startswith(prefix)
            )
        for tid in traverse_ids:
            for ent, rels in graph.neighbors(tid, depth, direction=direction):
                for r in rels:
                    rkey = f"{r.src_id}->{r.tgt_id}:{r.kind}"
                    if rkey not in seen_rels:
                        seen_rels.add(rkey)
                        all_rels.append(r.to_dict())
                if ent.id not in matched_ids:
                    d = ent.to_dict()
                    d["score"] = 0.0
                    neighbor_entities.append(d)
                    matched_ids.add(ent.id)
    return all_rels, neighbor_entities


def _format_query_context(
    matched_entities: list[dict], all_rels: list[dict], matched_chunks: list[dict],
) -> str:
    """Build human-readable context string from query results."""
    parts: list[str] = []
    if matched_entities:
        parts.append("=== Matched Entities ===")
        for e in matched_entities:
            parts.append(f"[{e['kind']}] {e['name']} ({e['file_path']}:{e['line_start']}) score={e['score']}")
            if e.get("description"):
                parts.append(f"  {e['description']}")
    if all_rels:
        parts.append("\n=== Relations ===")
        for r in all_rels[:20]:
            parts.append(f"  {r['src_id']} --{r['kind']}--> {r['tgt_id']}")
    if matched_chunks:
        parts.append("\n=== Code Snippets ===")
        for c in matched_chunks[:5]:
            parts.append(f"--- {c['file_path']}:{c['line_start']}-{c['line_end']} ({c.get('symbol_name','')}) score={c['score']} ---")
            parts.append(c["content"][:500])
    return "\n".join(parts)


async def query(
    repo_path: Path, text: str, embedder: EmbeddingClient,
    *, top_k: int = 10, depth: int = 1, direction: str = "both",
    kg_path: Path | None = None,
) -> QueryResult:
    repo_path = repo_path.resolve()
    if kg_path is None:
        kg_path = repo_path / KG_DIR

    graph, vec_index, all_chunks = _get_cached_kg(kg_path)

    q_vec = await embedder.embed_single(text)
    ent_hits = vec_index.search_entities(q_vec, top_k)
    chunk_hits = vec_index.search_chunks(q_vec, top_k)

    # Deduplicate entity hits — keep highest score per entity ID
    _seen_eids: dict[str, float] = {}
    deduped_ent_hits: list[tuple[str, float]] = []
    for eid, score in ent_hits:
        if eid not in _seen_eids or score > _seen_eids[eid]:
            if eid not in _seen_eids:
                deduped_ent_hits.append((eid, score))
            else:
                deduped_ent_hits = [(e, s if e != eid else score) for e, s in deduped_ent_hits]
            _seen_eids[eid] = score
    ent_hits = deduped_ent_hits

    matched_entities: list[dict[str, Any]] = []
    for eid, score in ent_hits:
        ent = graph.get_entity(eid)
        if ent:
            d = ent.to_dict()
            d["score"] = round(score, 4)
            matched_entities.append(d)

    all_rels, neighbor_entities = _traverse_neighbors(
        graph, ent_hits, matched_entities, depth, direction,
    )

    matched_chunks: list[dict[str, Any]] = []
    for cid, score in chunk_hits:
        if cid in all_chunks:
            d = all_chunks[cid].to_dict()
            d["score"] = round(score, 4)
            matched_chunks.append(d)

    return QueryResult(
        entities=matched_entities + neighbor_entities,
        relations=all_rels, chunks=matched_chunks,
        context=_format_query_context(matched_entities, all_rels, matched_chunks),
    )
