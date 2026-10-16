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
import time
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

    mg = MatrixoneGraph(repo, embedding_url=EMBED_URL, embedding_model=EMBED_MODEL,
                        embedding_api_key=_embedding_api_key())
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


@click.group("kg")
def kg():
    """Knowledge graph: index, query, status, clear."""
    pass


# ── 网络重试：连接类失败与瞬态 HTTP（429/5xx，嵌入服务限流最常见）按 2s/4s 退避重试 ──
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


def _with_retry(coro_factory, attempts: int = 4):
    """coro_factory 每次尝试调用时返回新协程（内部自建连接与客户端）。"""
    import asyncio as _asyncio
    import httpx as _httpx

    async def _run():
        last: Exception | None = None
        for i in range(attempts):
            try:
                return await coro_factory()
            except (_httpx.ConnectError, _httpx.RemoteProtocolError, _httpx.ReadTimeout) as exc:
                last = exc
                if i < attempts - 1:
                    await _asyncio.sleep(2 ** i * 2)
            except _httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _TRANSIENT_STATUS:
                    raise
                last = exc
                if i < attempts - 1:
                    await _asyncio.sleep(2 ** i * 2)
        raise last if last else RuntimeError("retry exhausted")

    return asyncio.run(_run())


# ── deep-query：LLM 引导的多轮图谱问答（移植自 saas/services/query.py，LLM 硬编码 verispring deepseek-flash）──
DEEPQUERY_LLM_URL = "https://api.verispring.cn/v1/chat/completions"
DEEPQUERY_LLM_MODEL = "deepseek-flash"

_DEEPQUERY_SYSTEM = """You are a code-graph query planner.
Return JSON with:
- sub_questions
- covered
- missing
- queries
- reason

If the current context is already enough, return an empty queries list.
"""


async def _llm_chat(messages: list[dict], *, max_tokens: int = 4096, timeout: float = 120.0) -> str:
    """Chat completion → content 字符串（OpenAI 兼容）。key 经 VERISPRING_API_KEY 注入。连接类与瞬态 HTTP（429/5xx）失败重试 3 次。"""
    import asyncio as _asyncio
    import httpx

    key = os.environ.get("VERISPRING_API_KEY", "")
    if not key:
        raise RuntimeError("deep-query 需要 VERISPRING_API_KEY（LLM=verispring deepseek-flash）")
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as c:
                resp = await c.post(
                    DEEPQUERY_LLM_URL,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    # deepseek-flash 接受 thinking disabled；GLM-5.x 会报错——换模型需移除
                    json={"model": DEEPQUERY_LLM_MODEL, "max_tokens": max_tokens, "messages": messages,
                          "thinking": {"type": "disabled"}},
                )
            resp.raise_for_status()
            data = resp.json()
            msg = data.get("choices", [{}])[0].get("message", {})
            return msg.get("content") or msg.get("reasoning_content") or ""
        except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt < 2:
                await _asyncio.sleep(2 * (attempt + 1))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in _TRANSIENT_STATUS:
                raise
            last_exc = exc
            if attempt < 2:
                await _asyncio.sleep(2 * (attempt + 1))
    raise last_exc if last_exc else RuntimeError("LLM 调用失败")


def _parse_json(text: str) -> dict | list:
    """Extract JSON from LLM output（移植自 saas/services/llm.py:58）."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = cleaned.find(start_char)
        if start == -1:
            continue
        end = cleaned.rfind(end_char)
        if end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"No valid JSON found in LLM output: {text[:200]}")


def _parse_follow_ups(raw: list) -> list[str]:
    follow_ups: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            follow_ups.append(item.strip())
        elif isinstance(item, dict):
            val = item.get("query") or item.get("q") or item.get("text") or ""
            if isinstance(val, str) and val.strip():
                follow_ups.append(val.strip())
    return follow_ups


def _hits_to_log(result) -> dict:
    return {
        "entities": [{"id": e.get("id", ""), "name": e.get("name", ""),
                      "file_path": e.get("file_path", ""), "score": e.get("score", 0)}
                     for e in result.entities[:10]],
        "chunks": [{"id": c.get("id", ""), "file_path": c.get("file_path", "")}
                   for c in result.chunks[:10]],
    }


@kg.command("deep-query")
@click.argument("question")
@click.option("--max-rounds", default=3, type=int, help="LLM 引导的追加查询轮数上限")
@click.option("-j", "--json-output", is_flag=True, help="Output raw JSON")
def deep_query(question: str, max_rounds: int, json_output: bool):
    """LLM 引导的多轮图谱问答（需要 VERISPRING_API_KEY 与已建向量索引）."""
    from matrixone_graph.pipeline import _load_meta

    repo = Path(".").resolve()
    kg_path = _kg_path(repo)
    if not _embedding_api_key():
        raise click.ClickException("deep-query 依赖向量检索：先配置 MANON_EMBEDDING_API_KEY 并 kg sync")
    if _load_meta(kg_path).get("vectors") != "embedded":
        raise click.ClickException("索引无向量：配置 key 后重新 kg sync")
    if not os.environ.get("VERISPRING_API_KEY", ""):
        raise click.ClickException("deep-query 需要 VERISPRING_API_KEY（LLM=verispring deepseek-flash）")

    async def _run():
        mg = _mg(repo)
        result = await mg.query(question, top_k=10, depth=1)
        accumulated = result.context or ""
        rounds_detail = [{"round": 0, "query": question,
                          "entities": [e.get("id", e.get("name", "")) for e in result.entities],
                          "covered": False}]
        training = [{"round": 0, "query": question, **_hits_to_log(result)}]
        rounds = [{"round": 0, "query": question, "context_chars": len(accumulated)}]
        parsed: dict = {}
        for i in range(max_rounds):
            analysis = None
            try:
                analysis = await _llm_chat([
                    {"role": "system", "content": _DEEPQUERY_SYSTEM},
                    {"role": "user", "content": f"Question:\n{question}\n\nContext:\n{accumulated[:12000]}"},
                ])
                parsed = _parse_json(analysis)
                if isinstance(parsed, list):
                    parsed = {}
            except Exception as exc:
                console.print(f"[yellow]deep-query LLM 第 {i + 1} 轮失败: {exc}[/yellow]")
                break
            follow_ups = _parse_follow_ups(parsed.get("queries", []))
            if not follow_ups:
                if len(rounds_detail) == 1:
                    rounds_detail[0]["covered"] = True
                break
            async def _run_query(q: str):
                sub_mg = _mg(repo)
                return q, await sub_mg.query(q, top_k=5, depth=1)
            results = await asyncio.gather(*[_run_query(q) for q in follow_ups[:3]], return_exceptions=True)
            for q, fr in (r for r in results if isinstance(r, tuple)):
                if fr.context:
                    accumulated += f"\n\n## Follow-up: {q}\n{fr.context}"
                rounds_detail.append({"round": i + 1, "query": q, "covered": True})
            rounds.append({"round": i + 1, "queries": follow_ups, "context_chars": len(accumulated)})

        sub_questions = parsed.get("sub_questions", []) if isinstance(parsed, dict) else []
        covered = parsed.get("covered", []) if isinstance(parsed, dict) else []
        coverage = len(covered) / len(sub_questions) if sub_questions else 1.0
        return {"context": accumulated, "rounds": rounds, "sub_questions": sub_questions,
                "covered": covered, "missing": parsed.get("missing", []) if isinstance(parsed, dict) else [],
                "coverage_ratio": round(coverage, 3), "llm_analysis": parsed if isinstance(parsed, dict) else {}}, training

    payload, training = _with_retry(lambda: _run())
    try:
        (repo / MANON_DIR / "deep-query-last.json").write_text(
            json.dumps({"question": question, "timestamp": time.time(),
                        "rounds": training, "llm_analysis": payload.get("llm_analysis", {})},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    if json_output:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        console.print(payload["context"] or "[yellow]No context accumulated.[/yellow]")
        if payload["sub_questions"]:
            console.print(f"\n覆盖 {len(payload['covered'])}/{len(payload['sub_questions'])} 个子问题"
                          f"（缺失: {', '.join(payload['missing']) or '无'}）")


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
    if not _embedding_api_key():
        raise click.ClickException("sync 需要 MANON_EMBEDDING_API_KEY（硅基流动 bge-m3，向量为查询必需）")

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

    # embedding：硬编码硅基流动 bge-m3（key 在命令入口已校验）
    from matrixone_graph.embed import EmbeddingClient

    api_key = _embedding_api_key()

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

    _with_retry(lambda: _embed_and_add())
    vectors_mode = "embedded"

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
@click.option("--top-k", default=10, type=int, help="Number of results")
@click.option("--depth", default=1, type=int, help="Graph traversal depth")
@click.option("-j", "--json-output", is_flag=True, help="Output raw JSON")
def query(text: str, top_k: int, depth: int, json_output: bool):
    """语义查询知识图谱（需要 MANON_EMBEDDING_API_KEY，先 kg sync 建向量）."""
    import click

    from matrixone_graph.pipeline import _load_meta

    repo = Path(".").resolve()
    kg_path = _kg_path(repo)
    meta = _load_meta(kg_path)
    api_key = _embedding_api_key()
    if not api_key:
        raise click.ClickException("语义查询需要 MANON_EMBEDDING_API_KEY（硅基流动 bge-m3）；配置后重新 kg sync")
    if meta.get("vectors") != "embedded":
        raise click.ClickException("索引无向量：配置 key 后重新 kg sync（换过 embedding 模型用 --full 全量重建）")

    from matrixone_graph.embed import EmbeddingClient
    from matrixone_graph.pipeline import query as kg_query

    embedder = EmbeddingClient(base_url=EMBED_URL, model=EMBED_MODEL, api_key=api_key)

    async def _do():
        try:
            return await kg_query(repo, text, embedder, top_k=top_k, depth=depth, kg_path=kg_path)
        finally:
            await embedder.close()

    result = _with_retry(lambda: _do())

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


@kg.command()
@click.argument("symbol")
@click.option("--direction", type=click.Choice(["both", "callers", "callees"]), default="both")
@click.option("--depth", default=1, type=int, help="遍历跳数")
@click.option("-j", "--json-output", is_flag=True)
def graph(symbol: str, direction: str, depth: int, json_output: bool):
    """按符号遍历调用关系（callers/callees/both）。"""
    from collections import deque

    repo = Path(".").resolve()
    mg = _mg(repo)
    g = mg._load_graph()

    matched = [eid for eid in g._g.nodes  # type: ignore[attr-defined]
               if eid == symbol or eid.endswith("." + symbol) or symbol in str(g._g.nodes[eid].get("name", ""))]
    if not matched:
        console.print(f"[yellow]图谱中未找到符号: {symbol}[/yellow]")
        return

    edges: list[tuple[str, str, dict]] = []
    if direction in ("both", "callees"):
        edges.extend((src, tgt, d) for src, tgt, d in g._g.edges(data=True))
    if direction in ("both", "callers"):
        edges.extend((tgt, src, d) for src, tgt, d in g._g.edges(data=True))

    start = set(matched)
    seen = set(start)
    queue = deque((eid, 0) for eid in matched)
    while queue:
        eid, lv = queue.popleft()
        if lv >= depth:
            continue
        for src, tgt, _d in edges:
            nxt = tgt if src == eid else (src if tgt == eid else None)
            if nxt is not None and nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, lv + 1))

    out_edges = [(src, tgt, d.get("kind", "call")) for src, tgt, d in edges if src in seen and tgt in seen]
    entities = [{"id": eid,
                 "kind": g._g.nodes[eid].get("kind", "?"),  # type: ignore[attr-defined]
                 "name": g._g.nodes[eid].get("name", eid.split(".")[-1]),  # type: ignore[attr-defined]
                 "file_path": g._g.nodes[eid].get("file_path", "?"),  # type: ignore[attr-defined]
                 "line_start": g._g.nodes[eid].get("line_start", 0)}  # type: ignore[attr-defined]
                for eid in sorted(seen)]
    payload = {"root": matched, "entities": entities, "relations": out_edges}
    if json_output:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        console.print(f"根: {', '.join(matched)}  邻域 {len(entities)} 实体 / {len(out_edges)} 关系（depth={depth}, {direction}）")
        for e in entities:
            console.print(f"  [{e['kind']}] {e['name']} ({e['file_path']}:{e['line_start']})")


@kg.command()
@click.option("--commit", default="HEAD", help="分析目标提交")
@click.option("--max-depth", default=2, type=int, help="传播追踪深度")
@click.option("-j", "--json-output", is_flag=True)
def impact(commit: str, max_depth: int, json_output: bool):
    """commit 级影响传播分析（无 LLM 调用）。"""
    repo = Path(".").resolve()
    mg = _mg(repo)
    result = mg.impact_commit(commit=commit, max_depth=max_depth)
    if json_output:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        console.print(f"影响分析: commit {commit}")
        console.print(f"  变更文件: {len(result.get('changed_files', []))}")
        console.print(f"  变更符号: {len(result.get('changed_symbols', []))}")
        risk = result.get("risk", {})
        if risk:
            console.print(f"  风险: {risk.get('level', '?')} — {risk.get('reason', '')}")


@kg.command("health")
@click.option("-j", "--json-output", is_flag=True)
def health(json_output: bool):
    """图谱健康度评分（8 维度 + 仓库债务扫描）。"""
    repo = Path(".").resolve()
    mg = _mg(repo)
    result = asyncio.run(mg.health())
    if json_output:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        console.print(f"健康度总分: {result.get('total_score', result)}")


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
