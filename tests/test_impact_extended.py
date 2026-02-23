"""Extended tests for matrixone_graph/impact.py — ImpactAnalyzer, edge cases."""
import pytest
from pathlib import Path

from matrixone_graph.impact import (
    ChangeType, ChangedFile, ChangedSymbol, Caller,
    RiskAssessment, ImpactResult, GitDiffParser,
    ChangedSymbolExtractor, RiskAssessor, ImpactAnalyzer,
    CORE_MODULES,
)
from matrixone_graph.store import CodeGraph, Entity, Relation


class TestChangeTypeValues:
    def test_all_values(self):
        assert set(ct.value for ct in ChangeType) == {"added", "modified", "deleted"}


class TestChangedFileEdgeCases:
    def test_defaults(self):
        cf = ChangedFile(path="a.py", change_type=ChangeType.ADDED)
        assert cf.added_lines == []
        assert cf.deleted_lines == []

    def test_with_lines(self):
        cf = ChangedFile(
            path="a.py", change_type=ChangeType.MODIFIED,
            added_lines=[(1, 5), (10, 15)],
            deleted_lines=[(20, 25)],
        )
        assert len(cf.added_lines) == 2
        assert len(cf.deleted_lines) == 1


class TestChangedSymbolEdgeCases:
    def test_defaults(self):
        cs = ChangedSymbol(name="foo", file="a.py", change_type=ChangeType.ADDED)
        assert cs.lines_changed == 0
        assert cs.line_start == 0
        assert cs.line_end == 0

    def test_to_dict_keys(self):
        cs = ChangedSymbol(name="foo", file="a.py", change_type=ChangeType.MODIFIED, lines_changed=10)
        d = cs.to_dict()
        assert set(d.keys()) == {"name", "file", "change_type", "lines_changed"}


class TestCallerEdgeCases:
    def test_defaults(self):
        c = Caller(name="foo", file="a.py")
        assert c.line == 0
        assert c.depth == 1

    def test_to_dict_depth1_omits(self):
        c = Caller(name="foo", file="a.py", line=5, depth=1)
        d = c.to_dict()
        assert "depth" not in d
        assert d["line"] == 5

    def test_to_dict_depth_gt1_includes(self):
        c = Caller(name="foo", file="a.py", depth=3)
        d = c.to_dict()
        assert d["depth"] == 3


class TestRiskAssessmentEdgeCases:
    def test_empty_suggestions(self):
        r = RiskAssessment(level="low", reason="ok")
        assert r.suggestions == []

    def test_to_dict_complete(self):
        r = RiskAssessment(level="high", reason="bad", suggestions=["fix", "test"])
        d = r.to_dict()
        assert d["level"] == "high"
        assert len(d["suggestions"]) == 2


class TestImpactResultEdgeCases:
    def test_minimal(self):
        ir = ImpactResult(commit="abc", changed_symbols=[])
        assert ir.direct_callers == []
        assert ir.indirect_callers == []
        assert ir.affected_modules == []
        assert ir.affected_tests == []
        assert ir.risk is None

    def test_to_dict_with_all_fields(self):
        sym = ChangedSymbol(name="f", file="a.py", change_type=ChangeType.MODIFIED)
        caller = Caller(name="g", file="b.py", depth=2)
        risk = RiskAssessment(level="medium", reason="some callers")
        ir = ImpactResult(
            commit="abc", changed_symbols=[sym],
            direct_callers=[caller], indirect_callers=[],
            affected_modules=["mod_a", "mod_b"],
            affected_tests=["test_a"], risk=risk,
        )
        d = ir.to_dict()
        assert len(d["affected_modules"]) == 2
        assert d["risk"]["level"] == "medium"

    def test_directly_changed_modules(self):
        """to_dict should include directly_changed_modules derived from changed_files."""
        sym = ChangedSymbol(name="f", file="src/core.py", change_type=ChangeType.MODIFIED)
        files = [
            ChangedFile(path="src/core.py", change_type=ChangeType.MODIFIED),
            ChangedFile(path="src/utils.py", change_type=ChangeType.MODIFIED),
        ]
        ir = ImpactResult(
            commit="abc", changed_symbols=[sym],
            changed_files=files,
            affected_modules=["src.core", "src.utils", "src.api"],
        )
        d = ir.to_dict()
        assert "directly_changed_modules" in d
        assert "src.core" in d["directly_changed_modules"]
        assert "src.utils" in d["directly_changed_modules"]
        # src.api is not in changed_files, so not in directly_changed_modules
        assert "src.api" not in d["directly_changed_modules"]

    def test_boundary_callers_count_in_dict(self):
        """boundary_callers_count > 0 should appear in to_dict."""
        ir = ImpactResult(commit="abc", changed_symbols=[], boundary_callers_count=5)
        d = ir.to_dict()
        assert d["boundary_callers_count"] == 5

    def test_boundary_callers_count_zero_omitted(self):
        """boundary_callers_count == 0 should not appear in to_dict."""
        ir = ImpactResult(commit="abc", changed_symbols=[], boundary_callers_count=0)
        d = ir.to_dict()
        assert "boundary_callers_count" not in d
    def test_parse_multiple_files(self):
        diff = """diff --git a/foo.py b/foo.py
@@ -1,3 +1,4 @@
+new line
diff --git a/bar.py b/bar.py
@@ -5,2 +5,3 @@
+another line
"""
        parser = GitDiffParser()
        files = parser._parse(diff)
        assert len(files) == 2
        assert files[0].path == "foo.py"
        assert files[1].path == "bar.py"

    def test_parse_deleted_file(self):
        diff = """diff --git a/old.py b/old.py
deleted file mode 100644
@@ -1,5 +0,0 @@
-content
"""
        parser = GitDiffParser()
        files = parser._parse(diff)
        assert len(files) == 1
        assert files[0].change_type == ChangeType.DELETED

    def test_parse_no_hunks(self):
        diff = """diff --git a/foo.py b/foo.py
"""
        parser = GitDiffParser()
        files = parser._parse(diff)
        assert len(files) == 1
        assert files[0].added_lines == []


class TestRiskAssessorEdgeCases:
    def test_medium_risk(self):
        assessor = RiskAssessor()
        callers = [Caller(name=f"c{i}", file="b.py") for i in range(5)]
        ir = ImpactResult(commit="x", changed_symbols=[], direct_callers=callers)
        risk = assessor.assess(ir)
        assert risk.level == "medium"

    def test_core_module_high_risk(self):
        assessor = RiskAssessor()
        sym = ChangedSymbol(name="auth_check", file="auth/login.py", change_type=ChangeType.MODIFIED)
        ir = ImpactResult(commit="x", changed_symbols=[sym])
        risk = assessor.assess(ir)
        assert risk.level == "high"

    def test_many_modules_high_risk(self):
        assessor = RiskAssessor()
        ir = ImpactResult(
            commit="x", changed_symbols=[],
            affected_modules=[f"m{i}" for i in range(6)],
        )
        risk = assessor.assess(ir)
        assert risk.level == "high"

    def test_custom_thresholds(self):
        assessor = RiskAssessor(low=1, high=3)
        callers = [Caller(name=f"c{i}", file="b.py") for i in range(2)]
        ir = ImpactResult(commit="x", changed_symbols=[], direct_callers=callers)
        risk = assessor.assess(ir)
        assert risk.level == "medium"


class TestRiskAssessorIsTestFile:
    def test_test_paths(self):
        assert RiskAssessor._is_test_file("tests/test_foo.py")
        assert RiskAssessor._is_test_file("test/test_bar.py")
        assert RiskAssessor._is_test_file("src/tests/test_baz.py")
        assert RiskAssessor._is_test_file("foo_test.py")
        assert RiskAssessor._is_test_file("src/test_utils.py")

    def test_non_test_paths(self):
        assert not RiskAssessor._is_test_file("src/core.py")
        assert not RiskAssessor._is_test_file("auth/login.py")
        assert not RiskAssessor._is_test_file("")


class TestCoreModules:
    def test_known_modules(self):
        assert "auth" in CORE_MODULES
        assert "database" in CORE_MODULES
        assert "config" in CORE_MODULES


class TestImpactAnalyzerHelpers:
    def _make_graph(self):
        g = CodeGraph()
        g.add_entity(Entity(id="mod.foo", kind="function", name="foo", file_path="mod.py"))
        g.add_entity(Entity(id="mod.bar", kind="function", name="bar", file_path="mod.py"))
        g.add_entity(Entity(id="util.baz", kind="function", name="baz", file_path="util.py"))
        g.add_relation(Relation(src_id="mod.bar", tgt_id="mod.foo", kind="calls"))
        g.add_relation(Relation(src_id="util.baz", tgt_id="mod.bar", kind="calls"))
        return g

    def test_file_to_module(self):
        assert ImpactAnalyzer._file_to_module("foo/bar.py") == "foo.bar"
        assert ImpactAnalyzer._file_to_module("simple.py") == "simple"
        assert ImpactAnalyzer._file_to_module("not_python.js") == ""

    def test_is_test(self):
        assert ImpactAnalyzer._is_test("tests/test_foo.py")
        assert ImpactAnalyzer._is_test("src/foo_test.py")
        assert not ImpactAnalyzer._is_test("src/foo.py")
        assert not ImpactAnalyzer._is_test("")

    def test_find_entity_ids(self, tmp_path):
        g = self._make_graph()
        analyzer = ImpactAnalyzer(g, tmp_path, max_depth=2)
        ids = analyzer._find_entity_ids("foo")
        assert "mod.foo" in ids

    def test_find_entity_ids_not_found(self, tmp_path):
        g = self._make_graph()
        analyzer = ImpactAnalyzer(g, tmp_path, max_depth=2)
        ids = analyzer._find_entity_ids("nonexistent")
        assert ids == []

    def test_affected_modules(self, tmp_path):
        g = self._make_graph()
        analyzer = ImpactAnalyzer(g, tmp_path, max_depth=2)
        syms = [ChangedSymbol(name="foo", file="mod.py", change_type=ChangeType.MODIFIED)]
        callers = [Caller(name="baz", file="util.py")]
        modules = analyzer._affected_modules(syms, callers, [])
        assert "mod" in modules
        assert "util" in modules
