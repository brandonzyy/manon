"""CLI commands for MatrixoneGraph.

Usage:
    codeindex kg index [PATH] --embedding-url URL --full
    codeindex kg query TEXT --top-k N --depth N -j
    codeindex kg status
    codeindex kg clear
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import click
from rich.console import Console

console = Console()
DEFAULT_EMBEDDING_URL = "http://localhost:8080"


def _get_embedding_url(url: str | None) -> str:
    return url or os.environ.get("CODEINDEX_EMBEDDING_URL", DEFAULT_EMBEDDING_URL)


@click.group("kg")
def kg():
    """Knowledge graph: index, query, status, clear."""
    pass


@kg.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--embedding-url", default=None, help="Embedding endpoint URL")
@click.option("--full", is_flag=True, help="Force full re-index")
def index(path: str, embedding_url: str | None, full: bool):
    """Index a repository into the knowledge graph."""
    from matrixone_graph import MatrixoneGraph

    url = _get_embedding_url(embedding_url)
    repo = Path(path).resolve()

    def progress(msg: str):
        console.print(f"  [dim]{msg}[/dim]")

    console.print(f"[bold]Indexing[/bold] {repo}")
    console.print(f"  Embedding endpoint: {url}")
    kg_inst = MatrixoneGraph(repo, embedding_url=url)

    async def _do():
        try:
            return await kg_inst.index(incremental=not full, on_progress=progress)
        finally:
            await kg_inst.close()

    result = asyncio.run(_do())
    console.print(f"\n[green]Done.[/green]  "
                  f"scanned={result.files_scanned}  indexed={result.files_indexed}  "
                  f"skipped={result.files_skipped}")
    console.print(f"  entities={result.entities_added}  "
                  f"relations={result.relations_added}  "
                  f"chunks={result.chunks_added}")


@kg.command()
@click.argument("text")
@click.option("--embedding-url", default=None, help="Embedding endpoint URL")
@click.option("--top-k", default=10, type=int, help="Number of results")
@click.option("--depth", default=1, type=int, help="Graph traversal depth")
@click.option("-j", "--json-output", is_flag=True, help="Output raw JSON")
def query(text: str, embedding_url: str | None, top_k: int, depth: int, json_output: bool):
    """Query the knowledge graph."""
    from matrixone_graph import MatrixoneGraph

    url = _get_embedding_url(embedding_url)
    repo = Path(".").resolve()
    kg_inst = MatrixoneGraph(repo, embedding_url=url)

    async def _do():
        try:
            return await kg_inst.query(text, top_k=top_k, depth=depth)
        finally:
            await kg_inst.close()

    result = asyncio.run(_do())
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
    from matrixone_graph import MatrixoneGraph
    repo = Path(".").resolve()
    st = MatrixoneGraph(repo).status()
    if not st.get("indexed"):
        console.print("[yellow]No knowledge graph index found.[/yellow]")
        console.print("Run: codeindex kg index .")
        return
    console.print("[bold]Knowledge Graph Status[/bold]")
    console.print(f"  Entities:  {st['entity_count']}")
    console.print(f"  Relations: {st['relation_count']}")
    console.print(f"  Chunks:    {st['chunk_count']}")
    console.print(f"  Files:     {st['file_count']}")
    console.print(f"  Embedding: {st['embedding_url']}")


@kg.command()
def clear():
    """Clear the knowledge graph index."""
    from matrixone_graph import MatrixoneGraph
    MatrixoneGraph(Path(".").resolve()).clear()
    console.print("[green]Knowledge graph index cleared.[/green]")

