"""Impact analysis — git diff → changed symbols → graph caller traversal → risk.

Replaces loomgraph's ImpactAnalyzer which queried LightRAG with NL ("What calls X?").
Now uses CodeGraph predecessor traversal directly — no LLM calls, instant results.

Designed for LLM consumers: returns structured ImpactResult with to_dict() for JSON.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from mcp.codeindex.parser import parse_file

from .store import CodeGraph, Entity, Relation


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class ChangeType(Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class ChangedFile:
    path: str
    change_type: ChangeType
    added_lines: list[tuple[int, int]] = field(default_factory=list)
    deleted_lines: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class ChangedSymbol:
    name: str
    file: str
    change_type: ChangeType
    lines_changed: int = 0
    line_start: int = 0
    line_end: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "file": self.file,
                "change_type": self.change_type.value,
                "lines_changed": self.lines_changed}

@dataclass
class Caller:
    name: str
    file: str
    line: int = 0
    depth: int = 1

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "file": self.file, "line": self.line}
        if self.depth > 1:
            d["depth"] = self.depth
        return d


@dataclass
class RiskAssessment:
    level: str  # "low", "medium", "high"
    reason: str
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "reason": self.reason,
                "suggestions": self.suggestions}


@dataclass
class ImpactResult:
    commit: str
    changed_symbols: list[ChangedSymbol]
    changed_files: list[ChangedFile] = field(default_factory=list)
    direct_callers: list[Caller] = field(default_factory=list)
    indirect_callers: list[Caller] = field(default_factory=list)
    affected_modules: list[str] = field(default_factory=list)
    affected_tests: list[str] = field(default_factory=list)
    propagation_chains: list[str] = field(default_factory=list)
    risk: RiskAssessment | None = None
    boundary_callers_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        # Derive directly_changed_modules from changed_files
        direct_modules: set[str] = set()
        for f in self.changed_files:
            m = f.path
            if m.endswith(".py"):
                m = m[:-3].replace("/", ".").replace("\\", ".").lstrip(".")
                if m:
                    direct_modules.add(m)

        d: dict[str, Any] = {
            "commit": self.commit,
            "changed_files": [
                {"path": f.path, "change_type": f.change_type.value}
                for f in self.changed_files
            ],
            "changed_symbols": [s.to_dict() for s in self.changed_symbols],
            "direct_callers": [c.to_dict() for c in self.direct_callers],
            "indirect_callers": [c.to_dict() for c in self.indirect_callers],
            "affected_modules": self.affected_modules,
            "directly_changed_modules": sorted(direct_modules),
            "affected_tests": self.affected_tests,
        }
        if self.propagation_chains:
            d["propagation_chains"] = self.propagation_chains
        if self.risk:
            d["risk"] = self.risk.to_dict()
        if self.boundary_callers_count > 0:
            d["boundary_callers_count"] = self.boundary_callers_count
        return d


# ---------------------------------------------------------------------------
# Git diff parser
# ---------------------------------------------------------------------------

_FILE_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class GitDiffParser:
    """Parse git diff output into ChangedFile objects."""

    def __init__(self, repo_path: Path = Path(".")) -> None:
        self.repo_path = repo_path

    def _git(self, *args: str) -> str:
        r = subprocess.run(
            ["git", "-C", str(self.repo_path), *args],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout

    def for_commit(self, commit: str = "HEAD") -> list[ChangedFile]:
        ref = f"{commit}~1..{commit}"
        return self._parse(self._git("diff", ref, "--unified=0"))

    def staged(self) -> list[ChangedFile]:
        return self._parse(self._git("diff", "--cached", "--unified=0"))

    def branch_diff(self, base: str, head: str = "HEAD") -> list[ChangedFile]:
        return self._parse(self._git("diff", f"{base}..{head}", "--unified=0"))

    def current_commit(self) -> str:
        return self._git("rev-parse", "--short", "HEAD").strip()

    def _parse(self, diff: str) -> list[ChangedFile]:
        files: list[ChangedFile] = []
        cur: ChangedFile | None = None
        is_new = is_del = False
        for line in diff.split("\n"):
            m = _FILE_RE.match(line)
            if m:
                if cur:
                    files.append(cur)
                cur = ChangedFile(path=m.group(2), change_type=ChangeType.MODIFIED)
                is_new = is_del = False
                continue
            if cur:
                if line.startswith("new file"):
                    is_new = True
                    cur.change_type = ChangeType.ADDED
                    continue
                if line.startswith("deleted file"):
                    is_del = True
                    cur.change_type = ChangeType.DELETED
                    continue
            hm = _HUNK_RE.match(line)
            if hm and cur:
                os, oc = int(hm.group(1)), int(hm.group(2) or 1)
                ns, nc = int(hm.group(3)), int(hm.group(4) or 1)
                if oc > 0 and not is_new:
                    cur.deleted_lines.append((os, os + oc - 1))
                if nc > 0 and not is_del:
                    cur.added_lines.append((ns, ns + nc - 1))
        if cur:
            files.append(cur)
        return files


# ---------------------------------------------------------------------------
# Symbol extractor — uses codeindex Python API (not subprocess)
# ---------------------------------------------------------------------------

class ChangedSymbolExtractor:
    """Extract code symbols from changed files using codeindex parser."""

    def __init__(self, repo_path: Path = Path(".")) -> None:
        self.repo_path = repo_path

    def extract(self, files: list[ChangedFile]) -> list[ChangedSymbol]:
        symbols: list[ChangedSymbol] = []
        for f in files:
            symbols.extend(self._from_file(f))
        return symbols

    def _from_file(self, cf: ChangedFile) -> list[ChangedSymbol]:
        if cf.change_type == ChangeType.DELETED:
            return [ChangedSymbol(
                name=f"<deleted:{Path(cf.path).stem}>",
                file=cf.path, change_type=ChangeType.DELETED,
            )]
        fp = self.repo_path / cf.path
        if not fp.exists():
            return []
        try:
            pr = parse_file(fp)
        except Exception:
            return []
        if pr.error:
            return []
        if cf.change_type == ChangeType.ADDED:
            return [
                ChangedSymbol(
                    name=s.name, file=cf.path, change_type=ChangeType.ADDED,
                    line_start=s.line_start, line_end=s.line_end,
                    lines_changed=s.line_end - s.line_start + 1,
                )
                for s in pr.symbols
            ]
        # MODIFIED — find symbols overlapping changed lines
        ranges = cf.added_lines + cf.deleted_lines
        result: list[ChangedSymbol] = []
        for s in pr.symbols:
            for rs, re_ in ranges:
                if s.line_start <= re_ and rs <= s.line_end:
                    changed = sum(
                        min(s.line_end, e) - max(s.line_start, st) + 1
                        for st, e in ranges if s.line_start <= e and st <= s.line_end
                    )
                    result.append(ChangedSymbol(
                        name=s.name, file=cf.path, change_type=ChangeType.MODIFIED,
                        line_start=s.line_start, line_end=s.line_end,
                        lines_changed=changed,
                    ))
                    break
        return result


# ---------------------------------------------------------------------------
# Risk assessor
# ---------------------------------------------------------------------------

CORE_MODULES = {
    "auth", "authentication", "security", "payment", "billing",
    "database", "db", "core", "config", "settings",
}


class RiskAssessor:
    """Assess risk level from impact analysis results.

    Considers: caller count, module count, core module changes,
    public vs private symbols, and change severity.
    """

    def __init__(self, low: int = 3, high: int = 10) -> None:
        self.low = low
        self.high = high

    @staticmethod
    def _is_test_file(fp: str) -> bool:
        """Check if a file path is a test file."""
        if not fp:
            return False
        fp_lower = fp.replace("\\", "/")
        return (
            fp_lower.startswith("tests/") or fp_lower.startswith("test/")
            or "/tests/" in fp_lower or "/test/" in fp_lower
            or fp_lower.endswith("_test.py")
            or "test_" in Path(fp_lower).name
        )

    def assess(self, result: ImpactResult) -> RiskAssessment:
        # Check if all changed symbols are in test files
        test_only = (
            len(result.changed_symbols) > 0
            and all(self._is_test_file(s.file) for s in result.changed_symbols)
        )

        total = len(result.direct_callers) + len(result.indirect_callers)
        is_core = any(
            any(c in s.file.lower() for c in CORE_MODULES)
            for s in result.changed_symbols
        )
        many_modules = len(result.affected_modules) >= 5

        # For mixed commits, exclude test-file symbols from public_changed
        if test_only:
            public_changed: list[ChangedSymbol] = []
        else:
            public_changed = [
                s for s in result.changed_symbols
                if not s.name.startswith("_") and not self._is_test_file(s.file)
            ]
        has_heavy_public = any(s.lines_changed > 20 for s in public_changed)

        reasons: list[str] = []
        suggestions: list[str] = []

        # Determine base level using caller thresholds (backward-compatible)
        if is_core or total >= self.high or many_modules:
            level = "high"
            if is_core:
                reasons.append("涉及核心模块")
                suggestions.append("核心模块变更需 code review")
            if total >= self.high:
                reasons.append(f"{total} 个调用者受影响")
            if many_modules:
                reasons.append(f"波及 {len(result.affected_modules)} 个模块")
            suggestions.append("建议完整集成测试")
        elif total >= self.low:
            level = "medium"
            reasons.append(f"{total} 个调用者, {len(result.affected_modules)} 个模块")
        else:
            level = "low"
            reasons.append("变更范围有限")

        # Severity upgrade: heavy public API changes bump low→medium
        if has_heavy_public and level == "low":
            level = "medium"
            reasons.append(f"{len(public_changed)} 个公共符号有大幅改动")

        if has_heavy_public:
            suggestions.append("检查公共 API 向后兼容性")

        # Specific test suggestions
        if result.affected_tests:
            test_list = ", ".join(result.affected_tests[:5])
            suggestions.append(f"运行受影响测试: {test_list}")
        elif total > 0:
            suggestions.append("未发现直接关联测试，建议补充测试覆盖")

        if not suggestions:
            suggestions.append("运行变更代码的单元测试")

        # Test-only commit: cap risk to low
        if test_only:
            level = "low"
            reasons = ["仅测试变更，风险有限"]
            suggestions = ["运行变更代码的单元测试"]

        return RiskAssessment(
            level=level,
            reason=f"{level.capitalize()} risk: " + "; ".join(reasons),
            suggestions=suggestions,
        )


# ---------------------------------------------------------------------------
# Impact analyzer — uses CodeGraph traversal (no LLM calls)
# ---------------------------------------------------------------------------

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

    def _find_callers(
        self, symbols: list[ChangedSymbol],
    ) -> tuple[list[Caller], list[Caller], list[str], int]:
        """Find callers by traversing CodeGraph predecessors (CALLS edges).

        Returns (direct_callers, indirect_callers, propagation_chains, boundary_callers_count).
        Chains are formatted as "A → B → C" showing the call propagation path.
        boundary_callers_count is the number of callers found at max_depth that may have more upstream callers.
        """
        direct: list[Caller] = []
        indirect: list[Caller] = []
        chains: list[str] = []
        seen: set[str] = set()
        boundary_count = 0

        for sym in symbols:
            # Find entity IDs matching this symbol name
            for eid in self._find_entity_ids(sym.name):
                # Direct callers: predecessors with "calls" edge
                for neighbor_ent, rels in self.graph.neighbors(eid, depth=1):
                    has_call = any(
                        r.kind == "calls" and r.tgt_id == eid for r in rels
                    )
                    if not has_call:
                        continue
                    key = f"{neighbor_ent.file_path}:{neighbor_ent.name}"
                    if key in seen:
                        continue
                    seen.add(key)
                    direct.append(Caller(
                        name=neighbor_ent.name, file=neighbor_ent.file_path,
                        line=neighbor_ent.line_start, depth=1,
                    ))

                    # Indirect callers (depth 2+) — track chains
                    if self.max_depth > 1:
                        for d_eid in self._find_entity_ids(neighbor_ent.name):
                            for n_ent, n_rels in self.graph.neighbors(d_eid, depth=1):
                                has_call2 = any(
                                    r.kind == "calls" and r.tgt_id == d_eid
                                    for r in n_rels
                                )
                                if not has_call2:
                                    continue
                                key2 = f"{n_ent.file_path}:{n_ent.name}"
                                if key2 in seen:
                                    continue
                                seen.add(key2)
                                indirect.append(Caller(
                                    name=n_ent.name, file=n_ent.file_path,
                                    line=n_ent.line_start, depth=2,
                                ))
                                chains.append(
                                    f"{sym.name} → {neighbor_ent.name} → {n_ent.name}"
                                )
                                # Probe next hop to count boundary callers
                                for probe_eid in self._find_entity_ids(n_ent.name):
                                    for b_ent, b_rels in self.graph.neighbors(probe_eid, depth=1):
                                        if any(r.kind == "calls" and r.tgt_id == probe_eid for r in b_rels):
                                            bkey = f"{b_ent.file_path}:{b_ent.name}"
                                            if bkey not in seen:
                                                boundary_count += 1

        return direct, indirect, chains, boundary_count

    def _find_lazy_import_callers(
        self, symbols: list[ChangedSymbol], seen: set[str],
    ) -> list[Caller]:
        """Find callers via lazy (function-body) imports not in the AST graph.

        Scans for indented 'from <module> import <symbol>' patterns which
        indicate imports inside function bodies — invisible to static AST.
        """
        extra: list[Caller] = []

        # Group symbols by module path
        mod_syms: dict[str, list[str]] = {}
        for sym in symbols:
            if not sym.file.endswith(".py"):
                continue
            mod = sym.file[:-3].replace("/", ".").replace("\\", ".")
            if sym.name.startswith("_") or sym.name.startswith("<") or "." in sym.name:
                continue
            mod_syms.setdefault(mod, []).append(sym.name)

        if not mod_syms:
            return extra

        for mod_path, sym_names in mod_syms.items():
            sym_alt = "|".join(re.escape(n) for n in sym_names)
            pattern = f"^\\s+from\\s+{re.escape(mod_path)}\\s+import\\s+.*\\b({sym_alt})\\b"
            try:
                result = subprocess.run(
                    ["git", "-C", str(self.repo_path), "grep", "-n", "-E",
                     pattern, "--", "*.py"],
                    capture_output=True, text=True, timeout=10,
                )
            except Exception:
                continue
            if result.returncode != 0 or not result.stdout.strip():
                continue

            for line in result.stdout.strip().split("\n"):
                parts = line.split(":", 2)
                if len(parts) < 3:
                    continue
                grep_file, line_no_str = parts[0], parts[1]
                try:
                    line_no = int(line_no_str)
                except ValueError:
                    continue

                func_name = self._find_containing_function(grep_file, line_no)
                if not func_name:
                    continue

                key = f"{grep_file}:{func_name}"
                if key in seen:
                    continue
                seen.add(key)
                extra.append(Caller(
                    name=func_name, file=grep_file, line=line_no, depth=1,
                ))
        return extra

    def _find_containing_function(self, file_path: str, line_no: int) -> str:
        """Find the function/method enclosing a given line number."""
        fp = self.repo_path / file_path
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").split("\n")
        except Exception:
            return ""
        if line_no < 1 or line_no > len(lines):
            return ""
        import_indent = len(lines[line_no - 1]) - len(lines[line_no - 1].lstrip())
        for i in range(line_no - 2, -1, -1):
            stripped = lines[i].lstrip()
            if not stripped.startswith("def ") and not stripped.startswith("async def "):
                continue
            func_indent = len(lines[i]) - len(lines[i].lstrip())
            if func_indent < import_indent:
                m = re.match(r"(?:async\s+)?def\s+(\w+)", stripped)
                return m.group(1) if m else ""
        return ""

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
