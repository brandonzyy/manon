"""Impact analyzer - main orchestrator using CodeGraph traversal."""
from __future__ import annotations

from pathlib import Path

from ..store import CodeGraph

from .models import ChangedSymbol, ChangedFile, Caller, ImpactResult
from .parsing import GitDiffParser, ChangedSymbolExtractor
from .risk_assessor import RiskAssessor


class ImpactAnalyzer:
    """Analyze impact of code changes using the knowledge graph.

    Key difference from loomgraph: finds callers via CodeGraph predecessor
    traversal instead of LightRAG NL queries. Zero LLM calls, instant results.
    """

    def __init__(self, graph: CodeGraph, repo_path: Path, max_depth: int = 2) -> None:
        self.graph = graph
        self.repo_path = repo_path
        self.max_depth = max_depth
        self._diff = GitDiffParser(repo_path)
        self._extractor = ChangedSymbolExtractor(repo_path)
        self._risk = RiskAssessor()

    def analyze_commit(self, commit: str = "HEAD") -> ImpactResult:
        files = self._diff.for_commit(commit)
        symbols = self._extractor.extract(files)
        commit_hash = self._diff.current_commit() if commit == "HEAD" else commit[:7]
        return self._build_result(commit_hash, symbols, files)

    def analyze_staged(self) -> ImpactResult:
        files = self._diff.staged()
        symbols = self._extractor.extract(files)
        return self._build_result("staged", symbols, files)

    def analyze_branch(self, base: str, head: str = "HEAD") -> ImpactResult:
        files = self._diff.branch_diff(base, head)
        symbols = self._extractor.extract(files)
        return self._build_result(f"{base}..{head}", symbols, files)

    def _build_result(
        self, commit: str, symbols: list[ChangedSymbol],
        files: list[ChangedFile] | None = None,
    ) -> ImpactResult:
        direct, indirect, chains, boundary_count = self._find_callers(symbols)

        # Supplement with lazy import callers (function-body imports)
        seen = {f"{c.file}:{c.name}" for c in direct + indirect}
        lazy_callers = self._find_lazy_import_callers(symbols, seen)
        direct.extend(lazy_callers)

        modules = self._affected_modules(symbols, direct, indirect)
        tests = [c.file for c in direct + indirect if self._is_test(c.file)]
        result = ImpactResult(
            commit=commit, changed_symbols=symbols,
            changed_files=files or [],
            direct_callers=direct, indirect_callers=indirect,
            affected_modules=sorted(set(modules)),
            affected_tests=sorted(set(tests)),
            propagation_chains=chains,
            boundary_callers_count=boundary_count,
        )
        result.risk = self._risk.assess(result)
        return result

    def _collect_direct_callers(self, eid: str, seen: set[str]) -> list[Caller]:
        """Collect direct (depth=1) callers of an entity from the graph."""
        result = []
        for neighbor_ent, rels in self.graph.neighbors(eid, depth=1):
            if not any(r.type == "calls" for r in rels):
                continue
            key = f"{neighbor_ent.file_path}:{neighbor_ent.name}"
            if key in seen:
                continue
            seen.add(key)
            result.append(Caller(name=neighbor_ent.name, file=neighbor_ent.file_path,
                                 line=neighbor_ent.line_start, depth=1))
        return result

    def _collect_indirect_callers(
        self, eid: str, sym_name: str, seen: set[str],
    ) -> tuple[list[Caller], list[str], int]:
        """Collect indirect (depth>1) callers of an entity. Returns (callers, chains, boundary_count)."""
        indirect, chains = [], []
        boundary_count = 0
        for neighbor_ent, rels in self.graph.neighbors(eid, depth=self.max_depth):
            if not any(r.type == "calls" for r in rels):
                continue
            key = f"{neighbor_ent.file_path}:{neighbor_ent.name}"
            if key in seen:
                continue
            seen.add(key)
            depth = len(rels)
            if depth == 1:
                continue
            indirect.append(Caller(name=neighbor_ent.name, file=neighbor_ent.file_path,
                                   line=neighbor_ent.line_start, depth=depth))
            if depth == self.max_depth:
                boundary_count += 1
            chain_parts = [sym_name] + [r.source_name for r in rels if r.source_name]
            chains.append(" → ".join(chain_parts))
        return indirect, chains, boundary_count

    def _find_callers(
        self, symbols: list[ChangedSymbol],
    ) -> tuple[list[Caller], list[Caller], list[str], int]:
        """Find callers by traversing CodeGraph predecessors (CALLS edges)."""
        direct: list[Caller] = []
        indirect: list[Caller] = []
        chains: list[str] = []
        seen: set[str] = set()
        boundary_count = 0

        for sym in symbols:
            for eid in self._find_entity_ids(sym.name):
                direct.extend(self._collect_direct_callers(eid, seen))
                if self.max_depth > 1:
                    ind, ch, bc = self._collect_indirect_callers(eid, sym.name, seen)
                    indirect.extend(ind)
                    chains.extend(ch)
                    boundary_count += bc

        return direct, indirect, chains[:10], boundary_count

    def _find_lazy_import_callers(
        self, symbols: list[ChangedSymbol], seen: set[str],
    ) -> list[Caller]:
        """Find callers via lazy imports (function-body imports).

        Scans for IMPORTS edges where the target is one of the changed symbols.
        These represent dynamic imports that may not show up in static call graphs.
        """
        lazy: list[Caller] = []
        for sym in symbols:
            for eid in self._find_entity_ids(sym.name):
                # Find entities that import this symbol
                for neighbor_ent, rels in self.graph.neighbors(eid, depth=1):
                    if not any(r.type == "imports" for r in rels):
                        continue
                    key = f"{neighbor_ent.file_path}:{neighbor_ent.name}"
                    if key in seen:
                        continue
                    seen.add(key)
                    lazy.append(Caller(
                        name=neighbor_ent.name,
                        file=neighbor_ent.file_path,
                        line=neighbor_ent.line_start,
                        depth=1,
                    ))
        return lazy

    def _find_entity_ids(self, symbol_name: str) -> list[str]:
        """Find entity IDs in the graph that match a symbol name."""
        results = []
        for nid, data in self.graph._g.nodes(data=True):
            if data.get("name") == symbol_name:
                results.append(nid)
        return results

    def _affected_modules(
        self, symbols: list[ChangedSymbol],
        direct: list[Caller], indirect: list[Caller],
    ) -> list[str]:
        modules: set[str] = set()
        for s in symbols:
            m = self._file_to_module(s.file)
            if m:
                modules.add(m)
        for c in direct + indirect:
            m = self._file_to_module(c.file)
            if m:
                modules.add(m)
        return sorted(modules)

    @staticmethod
    def _file_to_module(fp: str) -> str:
        if not fp.endswith(".py"):
            return ""
        return fp[:-3].replace("/", ".").replace("\\", ".").lstrip(".")

    @staticmethod
    def _is_test(fp: str) -> bool:
        if not fp:
            return False
        fp_lower = fp.replace("\\", "/")
        return (
            fp_lower.startswith("tests/") or fp_lower.startswith("test/")
            or "/tests/" in fp_lower or "/test/" in fp_lower
            or fp_lower.endswith("_test.py")
            or "test_" in Path(fp_lower).name
        )
