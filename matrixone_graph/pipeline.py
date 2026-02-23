"""Index and query pipelines for MatrixoneGraph.

index_repo()  — scan → parse → map → embed → store
query()       — embed → vector search → graph BFS → assemble context
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from codeindex.config import Config
from codeindex.parser import ParseResult, parse_file
from codeindex.scanner import scan_directory

from .embed import EmbeddingClient
from .store import Chunk, CodeGraph, Entity, Relation, VectorIndex

logger = logging.getLogger(__name__)

KG_DIR = ".codeindex/kg"
GRAPH_FILE = "graph.json"
VECTORS_FILE = "vectors.npz"
CHUNKS_FILE = "chunks.json"
META_FILE = "meta.json"


@dataclass
class IndexResult:
    files_scanned: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    entities_added: int = 0
    relations_added: int = 0
    chunks_added: int = 0

@dataclass
class QueryResult:
    entities: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    context: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _module_prefix(file_path: Path, repo_root: Path) -> str:
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:
        rel = file_path
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _make_entity_id(module: str, symbol_name: str) -> str:
    return f"{module}.{symbol_name}" if module else symbol_name


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


def _chunk_file(source: str, parse_result: ParseResult, module: str) -> list[Chunk]:
    lines = source.splitlines(keepends=True)
    chunks: list[Chunk] = []
    covered: set[int] = set()
    for sym in sorted(parse_result.symbols, key=lambda s: s.line_start):
        start = max(sym.line_start - 1, 0)
        end = min(sym.line_end, len(lines))
        if start >= end:
            continue
        content = "".join(lines[start:end])
        if not content.strip():
            continue
        cid = f"chunk:{_make_entity_id(module, sym.name)}"
        chunks.append(Chunk(
            id=cid, content=content,
            file_path=str(parse_result.path),
            line_start=sym.line_start, line_end=sym.line_end,
            symbol_name=sym.name,
        ))
        covered.update(range(start, end))
    uncovered = [i for i in range(len(lines)) if i not in covered]
    if uncovered:
        content = "".join(lines[i] for i in uncovered)
        if content.strip():
            chunks.append(Chunk(
                id=f"chunk:{module}.__file__", content=content[:2000],
                file_path=str(parse_result.path),
                line_start=uncovered[0] + 1, line_end=uncovered[-1] + 1,
                symbol_name="",
            ))
    return chunks


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
            a["name"] if isinstance(a, dict) else a.name
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
            # import of whole module — use last segment as short name
            short = resolved_module.rsplit(".", 1)[-1]
            imported_names[short] = resolved_module
    for call in pr.calls:
        if call.callee is None:
            continue
        caller_id = _make_entity_id(module, call.caller) if call.caller in local_names else call.caller
        if call.callee in local_names:
            callee_id = _make_entity_id(module, call.callee)
        elif call.callee in imported_names:
            callee_id = imported_names[call.callee]
        elif call.callee.startswith(("./", "../")):
            # TS parser resolved callee with relative module path — resolve to full entity ID
            callee_id = _resolve_import_by_filepath(fp, call.callee)
        else:
            callee_id = call.callee
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
# Index pipeline
# ---------------------------------------------------------------------------

async def index_repo(
    repo_path: Path,
    embedder: EmbeddingClient,
    *,
    kg_path: Path | None = None,
    incremental: bool = True,
    on_progress: Callable[[str], None] | None = None,
) -> IndexResult:
    repo_path = repo_path.resolve()
    if kg_path is None:
        kg_path = repo_path / KG_DIR
    result = IndexResult()

    graph = CodeGraph()
    vec_index = VectorIndex()
    graph.load(kg_path / GRAPH_FILE)
    vec_index.load(kg_path / VECTORS_FILE)
    all_chunks = _load_chunks(kg_path)
    meta = _load_meta(kg_path)
    old_hashes: dict[str, str] = meta.get("hashes", {})

    config = Config.load(repo_path / ".codeindex.yaml")
    scan_result = scan_directory(repo_path, config, repo_path)
    result.files_scanned = len(scan_result.files)
    if on_progress:
        on_progress(f"Scanned {result.files_scanned} files")

    files_to_index: list[Path] = []
    new_hashes: dict[str, str] = {}
    for f in scan_result.files:
        h = _file_hash(f)
        key = str(f.relative_to(repo_path))
        new_hashes[key] = h
        if incremental and old_hashes.get(key) == h:
            result.files_skipped += 1
            continue
        files_to_index.append(f)
    if on_progress:
        on_progress(f"{len(files_to_index)} files to index, {result.files_skipped} unchanged")

    all_entities: list[Entity] = []
    all_relations: list[Relation] = []
    new_chunks: list[Chunk] = []

    for f in files_to_index:
        module = _module_prefix(f, repo_path)
        fp_str = str(f)
        graph.remove_by_file(fp_str)
        old_chunk_ids = {cid for cid, c in all_chunks.items() if c.file_path == fp_str}
        vec_index.remove_by_ids(old_chunk_ids)
        for cid in old_chunk_ids:
            del all_chunks[cid]

        pr = parse_file(f)
        if pr.error:
            logger.warning("Parse error %s: %s", f, pr.error)
            continue
        entities, relations = _map_parse_result(pr, module)
        all_entities.extend(entities)
        all_relations.extend(relations)
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            source = ""
        chunks = _chunk_file(source, pr, module)
        new_chunks.extend(chunks)
        for c in chunks:
            all_chunks[c.id] = c
        result.files_indexed += 1
        if on_progress and result.files_indexed % 20 == 0:
            on_progress(f"Indexed {result.files_indexed}/{len(files_to_index)} files")

    for ent in all_entities:
        graph.add_entity(ent)
        result.entities_added += 1
    for rel in all_relations:
        if graph.has_entity(rel.src_id) or graph.has_entity(rel.tgt_id):
            graph.add_relation(rel)
            result.relations_added += 1

    if all_entities:
        if on_progress:
            on_progress(f"Embedding {len(all_entities)} entities...")
        ent_vecs = await embedder.embed([e.description for e in all_entities])
        vec_index.add_entity_vectors([e.id for e in all_entities], ent_vecs)

    if new_chunks:
        if on_progress:
            on_progress(f"Embedding {len(new_chunks)} chunks...")
        chunk_vecs = await embedder.embed([c.content[:1000] for c in new_chunks])
        vec_index.add_chunk_vectors([c.id for c in new_chunks], chunk_vecs)
        result.chunks_added = len(new_chunks)

    if on_progress:
        on_progress("Saving to disk...")
    graph.save(kg_path / GRAPH_FILE)
    vec_index.save(kg_path / VECTORS_FILE)
    _save_chunks(kg_path, all_chunks)
    invalidate_kg_cache(kg_path)  # force reload on next query
    meta.update({
        "version": 1, "entity_count": graph.entity_count,
        "relation_count": graph.relation_count, "chunk_count": len(all_chunks),
        "file_count": len(new_hashes), "embedding_url": embedder.base_url,
        "hashes": new_hashes,
    })
    _save_meta(kg_path, meta)
    return result


# ---------------------------------------------------------------------------
# Query pipeline
# ---------------------------------------------------------------------------

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

    all_rels: list[dict[str, Any]] = []
    neighbor_entities: list[dict[str, Any]] = []
    seen_rels: set[str] = set()
    matched_ids = {e["id"] for e in matched_entities}
    for eid, _ in ent_hits:
        # Collect entity IDs to traverse: the entity itself + its members (for class/module)
        traverse_ids = [eid]
        # If entity has no direct edges, also traverse its member entities
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

    matched_chunks: list[dict[str, Any]] = []
    for cid, score in chunk_hits:
        if cid in all_chunks:
            d = all_chunks[cid].to_dict()
            d["score"] = round(score, 4)
            matched_chunks.append(d)

    ctx_parts: list[str] = []
    if matched_entities:
        ctx_parts.append("=== Matched Entities ===")
        for e in matched_entities:
            ctx_parts.append(f"[{e['kind']}] {e['name']} ({e['file_path']}:{e['line_start']}) score={e['score']}")
            if e.get("description"):
                ctx_parts.append(f"  {e['description']}")
    if all_rels:
        ctx_parts.append("\n=== Relations ===")
        for r in all_rels[:20]:
            ctx_parts.append(f"  {r['src_id']} --{r['kind']}--> {r['tgt_id']}")
    if matched_chunks:
        ctx_parts.append("\n=== Code Snippets ===")
        for c in matched_chunks[:5]:
            ctx_parts.append(f"--- {c['file_path']}:{c['line_start']}-{c['line_end']} ({c.get('symbol_name','')}) score={c['score']} ---")
            ctx_parts.append(c["content"][:500])

    return QueryResult(
        entities=matched_entities + neighbor_entities,
        relations=all_rels, chunks=matched_chunks,
        context="\n".join(ctx_parts),
    )

