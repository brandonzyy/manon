"""MatrixoneGraph — built-in graph + vector engine.

Usage::

    from matrixone_graph import MatrixoneGraph

    kg = MatrixoneGraph("/path/to/repo", embedding_url="http://localhost:8080")
    result = await kg.index()
    answer = await kg.query("authentication flow")
    await kg.close()
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .embed import EmbeddingClient
from .pipeline import (
    KG_DIR,
    META_FILE,
    IndexResult,
    QueryResult,
    index_repo,
    query,
)

__all__ = ["MatrixoneGraph", "IndexResult", "QueryResult"]


class MatrixoneGraph:
    """Facade — single entry point for indexing and querying."""

    def __init__(
        self,
        repo_path: str | Path,
        *,
        embedding_url: str = "http://localhost:8080",
        batch_size: int = 32,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self._embedder = EmbeddingClient(
            base_url=embedding_url, batch_size=batch_size
        )

    async def index(self, *, incremental=True, on_progress=None) -> IndexResult:
        return await index_repo(
            self.repo_path, self._embedder,
            incremental=incremental, on_progress=on_progress,
        )

    async def query(self, text: str, *, top_k=10, depth=1) -> QueryResult:
        return await query(
            self.repo_path, text, self._embedder,
            top_k=top_k, depth=depth,
        )

    def status(self) -> dict[str, Any]:
        kg_path = self.repo_path / KG_DIR
        meta_file = kg_path / META_FILE
        if not meta_file.exists():
            return {"indexed": False}
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        return {
            "indexed": True,
            "entity_count": meta.get("entity_count", 0),
            "relation_count": meta.get("relation_count", 0),
            "chunk_count": meta.get("chunk_count", 0),
            "file_count": meta.get("file_count", 0),
            "embedding_url": meta.get("embedding_url", ""),
        }

    def clear(self) -> None:
        import shutil
        kg_path = self.repo_path / KG_DIR
        if kg_path.exists():
            shutil.rmtree(kg_path)

    async def close(self) -> None:
        await self._embedder.close()

