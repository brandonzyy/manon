"""MatrixoneGraph — built-in graph + vector engine for LLM agents.

Usage::

    from matrixone_graph import MatrixoneGraph

    # Configure once at startup (data_dir = centralized index storage)
    MatrixoneGraph.configure(
        embedding_url="http://localhost:8080",
        data_dir="/path/to/manon/indexes",
    )

    # Get pooled instance (auto-created per repo)
    mg = MatrixoneGraph.get("/path/to/repo")
    result = await mg.index()
    answer = await mg.query("authentication flow")
    impact = mg.impact_commit()
    health = await mg.health()

    # Shutdown all instances
    await MatrixoneGraph.shutdown_all()
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .embed import EmbeddingClient
from .pipeline import (
    KG_DIR,
    GRAPH_FILE,
    META_FILE,
    IndexResult,
    QueryResult,
    index_repo,
    query,
)
from .store import CodeGraph

__all__ = ["MatrixoneGraph", "IndexResult", "QueryResult"]


class MatrixoneGraph:
    """Facade — single entry point for indexing, querying, impact, and health."""

    # Class-level instance pool
    _pool: dict[str, "MatrixoneGraph"] = {}
    _embedding_url: str = "http://localhost:8080"
    _data_dir: Path | None = None  # centralized index storage root

    @staticmethod
    def _repo_key(repo_path: Path) -> str:
        """Derive a unique, readable directory name from a repo path."""
        h = hashlib.sha256(str(repo_path).encode()).hexdigest()[:10]
        return f"{repo_path.name}-{h}"

    @classmethod
    def configure(cls, *, embedding_url: str = "", data_dir: str | Path = "") -> None:
        if embedding_url:
            cls._embedding_url = embedding_url
        if data_dir:
            cls._data_dir = Path(data_dir).resolve()
            cls._data_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get(cls, repo_path: str | Path) -> "MatrixoneGraph":
        """Get or create a pooled instance for a repo path."""
        key = str(Path(repo_path).resolve())
        if key not in cls._pool:
            cls._pool[key] = cls(key, embedding_url=cls._embedding_url)
        return cls._pool[key]

    @classmethod
    async def shutdown_all(cls) -> None:
        """Close all pooled instances (call from app lifespan shutdown)."""
        for mg in cls._pool.values():
            try:
                await mg.close()
            except Exception:
                pass
        cls._pool.clear()

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
        # Compute kg_path: centralized if data_dir is set, else in-repo fallback
        if self._data_dir:
            self.kg_path = self._data_dir / self._repo_key(self.repo_path) / "kg"
        else:
            self.kg_path = self.repo_path / KG_DIR

    # -- Indexing --

    async def index(self, *, incremental=True, on_progress=None) -> IndexResult:
        return await index_repo(
            self.repo_path, self._embedder,
            kg_path=self.kg_path,
            incremental=incremental, on_progress=on_progress,
        )

    async def index_report(self, *, incremental=True, on_progress=None) -> dict:
        """Index + health scan, return combined dict for API consumers."""
        result = await self.index(incremental=incremental, on_progress=on_progress)
        health = await self.health()
        # Compute entity type counts from graph
        g = self._load_graph()
        type_counts: dict[str, int] = {}
        for _, data in g._g.nodes(data=True):
            kind = data.get("kind", "")
            if kind:
                type_counts[kind] = type_counts.get(kind, 0) + 1
        return {
            "files": result.files_scanned,
            "entities": g.entity_count,
            "relations": g.relation_count,
            "chunks": result.chunks_added,
            "skipped": result.files_skipped,
            "errors": [],
            "entityTypes": type_counts,
            "health": health,
        }

    # -- Query --

    async def query(self, text: str, *, top_k=10, depth=1, direction="both") -> QueryResult:
        return await query(
            self.repo_path, text, self._embedder,
            top_k=top_k, depth=depth, direction=direction, kg_path=self.kg_path,
        )

    # -- Impact analysis (graph-based, no LLM calls) --

    def _load_graph(self) -> CodeGraph:
        g = CodeGraph()
        g.load(self.kg_path / GRAPH_FILE)
        return g

    def impact_commit(self, commit: str = "HEAD", *, max_depth: int = 2) -> dict:
        from .impact import ImpactAnalyzer
        analyzer = ImpactAnalyzer(self._load_graph(), self.repo_path, max_depth)
        return analyzer.analyze_commit(commit).to_dict()

    def impact_staged(self, *, max_depth: int = 2) -> dict:
        from .impact import ImpactAnalyzer
        analyzer = ImpactAnalyzer(self._load_graph(), self.repo_path, max_depth)
        return analyzer.analyze_staged().to_dict()

    def impact_branch(self, base: str, head: str = "HEAD", *, max_depth: int = 2) -> dict:
        from .impact import ImpactAnalyzer
        analyzer = ImpactAnalyzer(self._load_graph(), self.repo_path, max_depth)
        return analyzer.analyze_branch(base, head).to_dict()

    # -- Health scoring --

    async def health(self) -> dict:
        from .health import compute_graph_metrics, compute_score, scan_directory_debt

        g = self._load_graph()
        graph_metrics = compute_graph_metrics(g)
        debt_metrics = scan_directory_debt(self.repo_path)
        return compute_score(graph_metrics, debt_metrics)

    # -- Status / lifecycle --

    def status(self) -> dict[str, Any]:
        meta_file = self.kg_path / META_FILE
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
        if self.kg_path.exists():
            shutil.rmtree(self.kg_path)

    async def close(self) -> None:
        await self._embedder.close()
