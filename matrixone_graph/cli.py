"""CLI commands for MatrixoneGraph.

Usage:
    codeindex kg sync [--full] [-j]      增量索引当前项目到 .manon/
    codeindex kg query TEXT [--top-k N --depth N -j]
    codeindex kg status
    codeindex kg clear
    codeindex kg merge-dynamic [DEPS_PATH]

存储布局（项目级，由会话常驻 scout 维护）：
    <项目>/.manon/graph/   graph.json + vectors.npz + chunks.json + meta.json
embedding：硬编码硅基流动 BAAI/bge-m3；API key 经 MANON_EMBEDDING_API_KEY 注入，
未配置时跳过向量，query 走词法兜底（实体名/限定名/chunk 文本的词重叠评分）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import click
from rich.console import Console

console = Console()
DEFAULT_EMBEDDING_URL = "http://localhost:8080"

# 本地索引：项目级 .manon/ 目录 + 硬编码 embedding 供应商
MANON_DIR = ".manon"
EMBED_URL = "https://api.siliconflow.cn/v1"
EMBED_MODEL = "BAAI/bge-m3"


def _get_embedding_url(url: str | None) -> str:
    return url or os.environ.get("CODEINDEX_EMBEDDING_URL", DEFAULT_EMBEDDING_URL)


def _kg_path(repo: Path) -> Path:
    return repo / MANON_DIR / "graph"


def _mg(repo: Path):
    from matrixone_graph import MatrixoneGraph

    mg = MatrixoneGraph(repo)
    mg.kg_path = _kg_path(repo)
    return mg


def _embedding_api_key() -> str:
    return os.environ.get("MANON_EMBEDDING_API_KEY", "")


def _reconstruct_parse_result(d: dict, file_path: str):
    """Rebuild a ParseResult from its dict form（移植自 saas/routers/indexing.py:69，本地 CLI 无 saas 依赖）."""
    from codeindex.parser import Symbol, Call, CallType, Import, Inheritance, ParseResult

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


def _apply_reexport_map(graph, reexport_map: dict) -> int:
    """Redirect phantom re-export edges to canonical entities（移植自 saas/routers/indexing.py:174）."""
    redirected = 0
    for phantom_id, real_id in reexport_map.items():
        if phantom_id not in graph._g:
            continue
        if graph.get_entity(real_id) is None:
            continue
        in_edges = list(graph._g.in_edges(phantom_id, data=True))
        out_edges = list(graph._g.out_edges(phantom_id, data=True))
        graph._g.remove_node(phantom_id)
        for src, _, data in in_edges:
            if graph._g.has_node(src) and not graph._g.has_edge(src, real_id):
                graph._g.add_edge(src, real_id, **data)
                redirected += 1
        for _, tgt, data in out_edges:
            if graph._g.has_node(real_id) and not graph._g.has_edge(real_id, tgt):
                graph._g.add_edge(real_id, tgt, **data)
                redirected += 1
    return redirected


def _lexical_query(mg, text: str, top_k: int, depth: int):
    """无向量时的词法兜底：实体限定名 / chunk 文本的词重叠评分 + 一跳关系。"""
    import networkx as nx

    from matrixone_graph.pipeline import QueryResult, _get_cached_kg
    from matrixone_graph.store import Chunk

    graph, _vec, chunks = _get_cached_kg(mg.kg_path)
    toks = [t for t in re.split(r"[^a-z0-9_\u4e00-\u9fff]+", text.lower()) if len(t) >= 2]

    def _score(s: str) -> float:
        ls = s.lower()
        return float(sum(t in ls for t in toks))

    hits: list[tuple[float, str, dict]] = []
    for eid, d in graph._g.nodes(data=True):
        s = 2 * _score(eid) + 2 * _score(str(d.get("name", ""))) + _score(str(d.get("description", "")))
        if s > 0:
            hits.append((s, eid, d))
    hits.sort(key=lambda x: (-x[0], x[1]))
    top = hits[:top_k]

    entities = [{
        "id": eid, "kind": d.get("kind", "?"),
        "name": d.get("name", str(eid).split(".")[-1]),
        "file_path": d.get("file_path", "?"), "line_start": d.get("line_start", 0),
        "description": d.get("description", ""), "score": round(s, 3),
    } for s, eid, d in top]

    top_ids = {eid for _, eid, _ in top}
    neighborhood = set(top_ids)
    if depth > 1:
        for eid in top_ids:
            if eid in graph._g:  # type: ignore[attr-defined]
                neighborhood.update(nx.ego_graph(graph._g, eid, radius=depth, undirected=True))
    relations: list[dict] = []
    seen: set[tuple] = set()
    for eid, _d in graph._g.nodes(data=True):
        if eid not in neighborhood:
            continue
        for src, tgt, data in list(graph._g.out_edges(eid, data=True)) + list(graph._g.in_edges(eid, data=True)):
            key = (src, data.get("kind", "call"), tgt)
            if key in seen:
                continue
            seen.add(key)
            relations.append({"src_id": src, "kind": data.get("kind", "call"), "tgt_id": tgt})
    relations = relations[:20]

    chits: list[tuple[float, Chunk]] = []
    for c in chunks.values():
        s = _score(c.content) + _score(str(getattr(c, "symbol_name", "") or ""))
        if s > 0:
            chits.append((s, c))
    chits.sort(key=lambda x: -x[0])
    chunk_list = [{
        "file_path": c.file_path, "line_start": c.line_start, "line_end": c.line_end,
        "symbol_name": getattr(c, "symbol_name", ""), "content": c.content,
        "score": round(s, 3),
    } for s, c in chits[:5]]

    context = f"词法匹配（无向量模式）: {len(entities)} 实体 / {len(relations)} 关系 / {len(chunk_list)} 片段"
    return QueryResult(entities=entities, relations=relations, chunks=chunk_list, context=context)


@click.group("kg")
def kg():
    """Knowledge graph: index, query, status, clear."""
    pass


@kg.command()
@click.option("--full", is_flag=True, help="忽略已有哈希，全量重建")
@click.option("-j", "--json-output", is_flag=True, help="Output raw JSON summary")
def sync(full: bool, json_output: bool):
    """增量索引当前项目到 .manon/（SHA256 内容增量，scout 每次任务后调用）。"""
    from core.ast.scanner import scan_and_parse
    from matrixone_graph.pipeline import (
        GRAPH_FILE, VECTORS_FILE, _load_chunks, _load_meta,
        _map_parse_result, _module_from_rel_path, _resolve_import_by_filepath,
        _save_chunks, _save_meta, invalidate_kg_cache,
    )
    from matrixone_graph.store import Chunk, CodeGraph, VectorIndex

    repo = Path(".").resolve()
    kg_path = _kg_path(repo)

    meta = {} if full else _load_meta(kg_path)
    meta.setdefault("version", 1)
    meta.setdefault("hashes", {})
    meta.setdefault("reexport_map", {})
    old_hashes: dict[str, str] = dict(meta.get("hashes", {}))

    graph, vec_index = CodeGraph(), VectorIndex()
    all_chunks: dict = {}
    if not full:
        graph.load(kg_path / GRAPH_FILE)
        vec_index.load(kg_path / VECTORS_FILE)
        all_chunks = _load_chunks(kg_path)

    file_results, deleted_files, scan_hashes = scan_and_parse(
        str(repo), {} if full else old_hashes)

    # 删除文件：从图/向量/chunks 移除（对齐 saas _remove_deleted_files）
    for rel_path in deleted_files:
        old_entity_ids = {n for n, d in graph._g.nodes(data=True) if d.get("file_path") == rel_path}  # type: ignore[attr-defined]
        graph.remove_by_file(rel_path)
        old_cids = {cid for cid, c in all_chunks.items() if c.file_path == rel_path}
        vec_index.remove_by_ids(old_cids | old_entity_ids)
        for cid in old_cids:
            all_chunks.pop(cid, None)

    # 变更文件：按文件移除后重建（对齐 saas _process_ast_files）
    batch_top = {_module_from_rel_path(f["rel_path"]).split(".")[0] for f in file_results}
    graph_top = {eid.split(".")[0] for eid, d in graph._g.nodes(data=True)  # type: ignore[attr-defined]
                 if d.get("kind") == "module" and "." not in eid}
    local_packages = frozenset(batch_top | graph_top)

    all_entities, all_relations, new_chunks, file_hashes, new_reexports = [], [], [], {}, {}
    for f in file_results:
        rel = f["rel_path"]
        old_entity_ids = {n for n, d in graph._g.nodes(data=True) if d.get("file_path") == rel}  # type: ignore[attr-defined]
        graph.remove_by_file(rel)
        old_cids = {cid for cid, c in all_chunks.items() if c.file_path == rel}
        vec_index.remove_by_ids(old_cids | old_entity_ids)
        for cid in old_cids:
            all_chunks.pop(cid, None)
        pr = _reconstruct_parse_result(f["parse_result"], rel)
        if pr.error:
            continue
        module = _module_from_rel_path(rel)
        entities, relations = _map_parse_result(pr, module, local_packages)
        all_entities.extend(entities)
        all_relations.extend(relations)
        chunks = [Chunk.from_dict(cd) for cd in f["chunks"]]
        new_chunks.extend(chunks)
        for c in chunks:
            all_chunks[c.id] = c
        file_hashes[rel] = f["hash"]
        if Path(rel).name == "__init__.py":
            pkg = module
            for imp in pr.imports:
                if not imp.module.startswith("."):
                    continue
                submod = _resolve_import_by_filepath(rel, imp.module)
                for name in imp.names:
                    phantom = f"{pkg}.{name}"
                    real = f"{submod}.{name}"
                    if phantom != real:
                        new_reexports[phantom] = real

    new_hashes = {**old_hashes, **file_hashes}

    # reconcile：清理图中已不在 tracked 集的陈旧文件实体（本地单批次，恒为 final）
    tracked = set(new_hashes.keys())
    stale_files = {d["file_path"] for _, d in graph._g.nodes(data=True)  # type: ignore[attr-defined]
                   if d.get("file_path") and d["file_path"] not in tracked}
    for fp in stale_files:
        old_cids = {cid for cid, c in all_chunks.items() if c.file_path == fp}
        vec_index.remove_by_ids(old_cids)
        for cid in old_cids:
            all_chunks.pop(cid, None)
        graph.remove_by_file(fp)

    for e in all_entities:
        graph.add_entity(e)
    relations_added = 0
    for r in all_relations:
        if graph.get_entity(r.src_id) is not None or graph.get_entity(r.tgt_id) is not None:
            graph.add_relation(r)
            relations_added += 1

    pruned = graph.prune_phantoms()
    meta.setdefault("reexport_map", {}).update(new_reexports)
    redirected = _apply_reexport_map(graph, meta.get("reexport_map", {}))

    # embedding：硬编码硅基流动 bge-m3；key 未配置则跳过向量
    api_key = _embedding_api_key()
    vectors_mode = "embedded"
    if api_key:
        from matrixone_graph.embed import EmbeddingClient

        async def _embed_and_add():
            embedder = EmbeddingClient(base_url=EMBED_URL, model=EMBED_MODEL, api_key=api_key)
            try:
                if all_entities:
                    ent_vecs = await embedder.embed([e.description for e in all_entities])
                    vec_index.add_entity_vectors([e.id for e in all_entities], ent_vecs)
                if new_chunks:
                    chunk_vecs = await embedder.embed([c.content[:1000] for c in new_chunks])
                    vec_index.add_chunk_vectors([c.id for c in new_chunks], chunk_vecs)
            finally:
                await embedder.close()

        asyncio.run(_embed_and_add())
    else:
        vectors_mode = "skipped"

    _save_chunks(kg_path, all_chunks)
    graph.save(kg_path / GRAPH_FILE)
    vec_index.save(kg_path / VECTORS_FILE)
    meta.update({
        "entity_count": graph.entity_count, "relation_count": graph.relation_count,
        "chunk_count": len(all_chunks), "file_count": len(new_hashes),
        "hashes": new_hashes, "vectors": vectors_mode,
    })
    _save_meta(kg_path, meta)
    invalidate_kg_cache(kg_path)

    summary = {
        "kg_path": str(kg_path),
        "files_synced": len(file_results), "files_deleted": len(deleted_files),
        "entities_added": len(all_entities), "relations_added": relations_added,
        "chunks_added": len(new_chunks),
        "total_entities": graph.entity_count, "total_relations": graph.relation_count,
        "total_files": len(new_hashes), "phantoms_pruned": pruned,
        "reexports_redirected": redirected, "vectors": vectors_mode,
        "stale_files_removed": len(stale_files),
    }
    if json_output:
        click.echo(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        console.print("[green]sync 完成[/green]  " + "  ".join(f"{k}={v}" for k, v in summary.items()))


@kg.command()
@click.argument("text")
@click.option("--embedding-url", default=None, help="Embedding endpoint URL")
@click.option("--top-k", default=10, type=int, help="Number of results")
@click.option("--depth", default=1, type=int, help="Graph traversal depth")
@click.option("-j", "--json-output", is_flag=True, help="Output raw JSON")
def query(text: str, embedding_url: str | None, top_k: int, depth: int, json_output: bool):
    """Query the knowledge graph（有向量走语义，无向量走词法兜底）."""
    from matrixone_graph.pipeline import _load_meta

    repo = Path(".").resolve()
    kg_path = _kg_path(repo)
    meta = _load_meta(kg_path)
    api_key = _embedding_api_key()

    if api_key and meta.get("vectors") == "embedded":
        from matrixone_graph import MatrixoneGraph

        inst = MatrixoneGraph(repo, embedding_url=_get_embedding_url(embedding_url) if embedding_url else EMBED_URL,
                              embedding_model=EMBED_MODEL, embedding_api_key=api_key)
        inst.kg_path = kg_path

        async def _do():
            try:
                return await inst.query(text, top_k=top_k, depth=depth)
            finally:
                await inst.close()

        result = asyncio.run(_do())
    else:
        result = _lexical_query(_mg(repo), text, top_k, depth)

    if json_output:
        click.echo(json.dumps({
            "entities": result.entities, "relations": result.relations,
            "chunks": result.chunks,
        }, ensure_ascii=False, indent=2))
    elif result.context:
        console.print(result.context)
    else:
        console.print("[yellow]No results found.[/yellow]")


@kg.command()
def status():
    """Show knowledge graph index status."""
    from matrixone_graph.pipeline import _load_meta

    repo = Path(".").resolve()
    kg_path = _kg_path(repo)
    meta = _load_meta(kg_path)
    if not meta.get("hashes"):
        console.print("[yellow]No knowledge graph index found（先跑 kg sync）。[/yellow]")
        return
    console.print("[bold]Knowledge Graph Status[/bold]")
    console.print(f"  Path:      {kg_path}")
    console.print(f"  Entities:  {meta.get('entity_count', '?')}")
    console.print(f"  Relations: {meta.get('relation_count', '?')}")
    console.print(f"  Chunks:    {meta.get('chunk_count', '?')}")
    console.print(f"  Files:     {meta.get('file_count', '?')}")
    console.print(f"  Vectors:   {meta.get('vectors', '?')}")


@kg.command()
def clear():
    """Clear the knowledge graph index."""
    import shutil

    repo = Path(".").resolve()
    shutil.rmtree(_kg_path(repo), ignore_errors=True)
    (repo / MANON_DIR / "hashes.json").unlink(missing_ok=True)
    console.print("[green]Knowledge graph index cleared.[/green]")


@kg.command("merge-dynamic")
@click.argument("deps_path", default="dynamic-deps.json", type=click.Path(exists=True))
def merge_dynamic(deps_path: str):
    """Merge runtime-traced dynamic call edges into the knowledge graph."""
    from matrixone_graph.merge_dynamic import load_dynamic_deps, merge_dynamic_edges
    from matrixone_graph.pipeline import GRAPH_FILE, invalidate_kg_cache

    repo = Path(".").resolve()
    mg = _mg(repo)
    graph = mg._load_graph()

    edges = load_dynamic_deps(deps_path)
    if not edges:
        console.print("[yellow]No dynamic edges found in file.[/yellow]")
        return

    stats = merge_dynamic_edges(graph, edges, replace=True)
    graph.save(mg.kg_path / GRAPH_FILE)
    invalidate_kg_cache(mg.kg_path)

    console.print(f"[green]Done.[/green]  added={stats['added']}  "
                  f"removed={stats['removed']}  skipped={stats['skipped']}")
    console.print(f"  Source: {deps_path} ({len(edges)} edges)")


if __name__ == "__main__":
    kg()
