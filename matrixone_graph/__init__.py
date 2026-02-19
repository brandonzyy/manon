"""MatrixoneGraph — built-in graph + vector engine.

Usage::

    from matrixone_graph import MatrixoneGraph

    kg = MatrixoneGraph("/path/to/repo", embedding_url="http://localhost:8080")
    result = await kg.index()
    answer = await kg.query("authentication flow")
    impact = kg.impact_commit()          # git diff → callers → risk
    health = await kg.health()           # 8-dimension code health
    await kg.close()
"""

from __future__ import annotations

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

    # -- Impact analysis (graph-based, no LLM calls) --

    def _load_graph(self) -> CodeGraph:
        g = CodeGraph()
        g.load(self.repo_path / KG_DIR / GRAPH_FILE)
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
        """Compute 8-dimension code health score for the repo."""
        from .health import scan_file, compute_score
        from codeindex.scanner import scan_directory
        from codeindex.config import Config

        config = Config.load(self.repo_path / ".codeindex.yaml")
        scan_result = scan_directory(self.repo_path, config, self.repo_path)

        max_lines = any_count = mt_issues = legacy_marks = 0
        for f in scan_result.files:
            h = scan_file(f)
            if h.get("lines", 0) > max_lines:
                max_lines = h["lines"]
            any_count += h.get("any_count", 0)
            mt_issues += h.get("todos", 0) + h.get("hardcoded_urls", 0)
            legacy_marks += h.get("legacy_marks", 0)

        return compute_score({
            "max_lines": max_lines, "any_count": any_count,
            "mt_issues": mt_issues, "legacy_marks": legacy_marks,
        })

    # -- Status / lifecycle --

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
