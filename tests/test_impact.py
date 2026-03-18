"""Tests for matrixone_graph.impact — data models, diff parser, risk assessment."""
import pytest

from matrixone_graph.impact import (
    ChangeType, ChangedFile, ChangedSymbol, Caller,
    RiskAssessment, ImpactResult, GitDiffParser,
    ChangedSymbolExtractor, RiskAssessor, ImpactAnalyzer,
)


# ── Data model tests ──────────────────────────────────

class TestChangeType:
    def test_values(self):
        assert ChangeType.ADDED.value == "added"
        assert ChangeType.MODIFIED.value == "modified"
        assert ChangeType.DELETED.value == "deleted"


class TestChangedFile:
    def test_create(self):
        cf = ChangedFile(path="foo.py", change_type=ChangeType.MODIFIED)
        assert cf.path == "foo.py"
        assert cf.added_lines == []


class TestChangedSymbol:
    def test_to_dict(self):
        cs = ChangedSymbol(name="foo", file="a.py", change_type=ChangeType.MODIFIED, lines_changed=5)
        d = cs.to_dict()
        assert d["name"] == "foo"
        assert d["change_type"] == "modified"
        assert d["lines_changed"] == 5


class TestCaller:
    def test_to_dict_depth1(self):
        c = Caller(name="bar", file="b.py", line=10, depth=1)
        d = c.to_dict()
        assert "depth" not in d  # depth=1 is omitted

    def test_to_dict_depth2(self):
        c = Caller(name="baz", file="c.py", line=20, depth=2)
        d = c.to_dict()
        assert d["depth"] == 2


class TestRiskAssessment:
    def test_to_dict(self):
        r = RiskAssessment(level="high", reason="many callers", suggestions=["add tests"])
        d = r.to_dict()
        assert d["level"] == "high"
        assert len(d["suggestions"]) == 1


class TestImpactResult:
    def test_to_dict(self):
        sym = ChangedSymbol(name="f", file="a.py", change_type=ChangeType.ADDED)
        caller = Caller(name="g", file="b.py")
        risk = RiskAssessment(level="low", reason="ok")
        ir = ImpactResult(
            commit="abc123", changed_symbols=[sym],
            direct_callers=[caller], affected_modules=["mod"],
            affected_tests=["test_a"], risk=risk,
        )
        d = ir.to_dict()
        assert d["commit"] == "abc123"
        assert len(d["changed_symbols"]) == 1
        assert d["risk"]["level"] == "low"

    def test_to_dict_no_risk(self):
        ir = ImpactResult(commit="x", changed_symbols=[])
        d = ir.to_dict()
        assert "risk" not in d


# ── GitDiffParser ─────────────────────────────────────

class TestGitDiffParser:
    def test_parse_empty(self):
        parser = GitDiffParser()
        files = parser._parse("")
        assert files == []

    def test_parse_simple_diff(self):
        diff = """diff --git a/foo.py b/foo.py
@@ -1,3 +1,4 @@
+new line
"""
        parser = GitDiffParser()
        files = parser._parse(diff)
        assert len(files) == 1
        assert files[0].path == "foo.py"

    def test_parse_new_file(self):
        diff = """diff --git a/new.py b/new.py
new file mode 100644
@@ -0,0 +1,5 @@
+content
"""
        parser = GitDiffParser()
        files = parser._parse(diff)
        assert len(files) == 1
        assert files[0].change_type == ChangeType.ADDED


# ── ChangedSymbolExtractor ────────────────────────────

class TestChangedSymbolExtractor:
    def test_deleted_file(self, tmp_path):
        ext = ChangedSymbolExtractor(repo_path=tmp_path)
        cf = ChangedFile(path="gone.py", change_type=ChangeType.DELETED)
        symbols = ext.extract([cf])
        assert len(symbols) == 1
        assert symbols[0].change_type == ChangeType.DELETED
        assert "gone" in symbols[0].name

    def test_missing_file_returns_empty(self, tmp_path):
        ext = ChangedSymbolExtractor(repo_path=tmp_path)
        cf = ChangedFile(path="noexist.py", change_type=ChangeType.MODIFIED, added_lines=[(1, 1)])
        symbols = ext.extract([cf])
        assert symbols == []

    def test_empty_input(self, tmp_path):
        ext = ChangedSymbolExtractor(repo_path=tmp_path)
        assert ext.extract([]) == []


# ── RiskAssessor ──────────────────────────────────────

class TestRiskAssessor:
    def test_low_risk(self):
        assessor = RiskAssessor()
        ir = ImpactResult(commit="x", changed_symbols=[], direct_callers=[], affected_modules=[])
        risk = assessor.assess(ir)
        assert risk.level == "low"

    def test_high_risk(self):
        assessor = RiskAssessor()
        syms = [ChangedSymbol(name=f"s{i}", file="a.py", change_type=ChangeType.MODIFIED) for i in range(10)]
        callers = [Caller(name=f"c{i}", file="b.py") for i in range(20)]
        ir = ImpactResult(
            commit="x", changed_symbols=syms,
            direct_callers=callers,
            affected_modules=[f"m{i}" for i in range(6)],
        )
        risk = assessor.assess(ir)
        assert risk.level in ("medium", "high")

    def test_test_only_low_risk(self):
        """All symbols in test files → level must be 'low'."""
        assessor = RiskAssessor()
        syms = [
            ChangedSymbol(name="test_foo", file="tests/test_foo.py", change_type=ChangeType.MODIFIED, lines_changed=50),
            ChangedSymbol(name="test_bar", file="tests/test_bar.py", change_type=ChangeType.MODIFIED, lines_changed=30),
        ]
        callers = [Caller(name=f"c{i}", file="b.py") for i in range(15)]
        ir = ImpactResult(
            commit="x", changed_symbols=syms,
            direct_callers=callers,
            affected_modules=[f"m{i}" for i in range(6)],
        )
        risk = assessor.assess(ir)
        assert risk.level == "low"
        assert "测试" in risk.reason

    def test_mixed_test_and_core(self):
        """Mixed test + core files → normal scoring (not capped)."""
        assessor = RiskAssessor()
        syms = [
            ChangedSymbol(name="test_foo", file="tests/test_foo.py", change_type=ChangeType.MODIFIED),
            ChangedSymbol(name="auth_check", file="auth/login.py", change_type=ChangeType.MODIFIED, lines_changed=25),
        ]
        callers = [Caller(name=f"c{i}", file="b.py") for i in range(12)]
        ir = ImpactResult(
            commit="x", changed_symbols=syms,
            direct_callers=callers,
            affected_modules=["auth", "tests"],
        )
        risk = assessor.assess(ir)
        assert risk.level in ("medium", "high")
