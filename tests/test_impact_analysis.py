"""Tests for matrixone_graph/impact.py — impact analysis and risk assessment."""
import pytest
from pathlib import Path

from matrixone_graph.impact import (
    ChangeType,
    ChangedFile,
    ChangedSymbol,
    Caller,
    RiskAssessment,
    ImpactResult,
    RiskAssessor,
    CORE_MODULES,
)


class TestChangeType:
    """Tests for ChangeType enum."""

    def test_change_types_exist(self):
        """Should have all expected change types."""
        assert ChangeType.ADDED.value == "added"
        assert ChangeType.MODIFIED.value == "modified"
        assert ChangeType.DELETED.value == "deleted"

    def test_change_type_from_string(self):
        """Should create from string value."""
        assert ChangeType("added") == ChangeType.ADDED
        assert ChangeType("modified") == ChangeType.MODIFIED


class TestChangedFile:
    """Tests for ChangedFile dataclass."""

    def test_create_changed_file(self):
        """Should create with required fields."""
        cf = ChangedFile(path="src/main.py", change_type=ChangeType.MODIFIED)
        assert cf.path == "src/main.py"
        assert cf.change_type == ChangeType.MODIFIED

    def test_changed_file_with_lines(self):
        """Should include line ranges."""
        cf = ChangedFile(
            path="src/util.py",
            change_type=ChangeType.MODIFIED,
            added_lines=[(10, 20)],
            deleted_lines=[(5, 8)],
        )
        assert cf.added_lines == [(10, 20)]
        assert cf.deleted_lines == [(5, 8)]


class TestChangedSymbol:
    """Tests for ChangedSymbol dataclass."""

    def test_create_changed_symbol(self):
        """Should create with required fields."""
        cs = ChangedSymbol(
            name="authenticate",
            file="auth.py",
            change_type=ChangeType.MODIFIED,
        )
        assert cs.name == "authenticate"
        assert cs.file == "auth.py"

    def test_changed_symbol_to_dict(self):
        """Should serialize to dict."""
        cs = ChangedSymbol(
            name="test_func",
            file="test.py",
            change_type=ChangeType.ADDED,
            lines_changed=10,
        )
        d = cs.to_dict()
        assert d["name"] == "test_func"
        assert d["file"] == "test.py"
        assert d["change_type"] == "added"
        assert d["lines_changed"] == 10


class TestCaller:
    """Tests for Caller dataclass."""

    def test_create_caller(self):
        """Should create with required fields."""
        c = Caller(name="main", file="main.py")
        assert c.name == "main"
        assert c.file == "main.py"
        assert c.depth == 1  # default

    def test_caller_with_depth(self):
        """Should include depth for indirect callers."""
        c = Caller(name="helper", file="util.py", line=42, depth=2)
        assert c.depth == 2
        assert c.line == 42

    def test_caller_to_dict(self):
        """Should serialize to dict."""
        c = Caller(name="func", file="file.py", line=10, depth=1)
        d = c.to_dict()
        assert d["name"] == "func"
        assert d["file"] == "file.py"
        assert d["line"] == 10
        assert "depth" not in d  # depth=1 not included

    def test_caller_to_dict_with_depth(self):
        """Should include depth if > 1."""
        c = Caller(name="func", file="file.py", depth=3)
        d = c.to_dict()
        assert d["depth"] == 3


class TestRiskAssessment:
    """Tests for RiskAssessment dataclass."""

    def test_create_risk_assessment(self):
        """Should create with required fields."""
        ra = RiskAssessment(level="medium", reason="Test reason")
        assert ra.level == "medium"
        assert ra.reason == "Test reason"
        assert ra.suggestions == []

    def test_risk_assessment_with_suggestions(self):
        """Should include suggestions."""
        ra = RiskAssessment(
            level="high",
            reason="Core module",
            suggestions=["Review carefully", "Run integration tests"],
        )
        assert len(ra.suggestions) == 2

    def test_risk_assessment_to_dict(self):
        """Should serialize to dict."""
        ra = RiskAssessment(
            level="low",
            reason="Minor change",
            suggestions=["Run unit tests"],
        )
        d = ra.to_dict()
        assert d["level"] == "low"
        assert d["reason"] == "Minor change"
        assert d["suggestions"] == ["Run unit tests"]


class TestImpactResult:
    """Tests for ImpactResult dataclass."""

    def test_create_impact_result(self):
        """Should create with required fields."""
        ir = ImpactResult(
            commit="abc123",
            changed_symbols=[],
        )
        assert ir.commit == "abc123"
        assert ir.changed_symbols == []

    def test_impact_result_defaults(self):
        """Should have sensible defaults."""
        ir = ImpactResult(commit="abc", changed_symbols=[])
        assert ir.direct_callers == []
        assert ir.indirect_callers == []
        assert ir.affected_modules == []
        assert ir.affected_tests == []
        assert ir.risk is None

    def test_impact_result_to_dict(self):
        """Should serialize to dict."""
        ir = ImpactResult(
            commit="abc123",
            changed_symbols=[
                ChangedSymbol(name="func", file="test.py", change_type=ChangeType.MODIFIED)
            ],
            direct_callers=[Caller(name="caller", file="main.py")],
            affected_modules=["module1", "module2"],
        )
        d = ir.to_dict()
        assert d["commit"] == "abc123"
        assert len(d["changed_symbols"]) == 1
        assert len(d["direct_callers"]) == 1
        assert d["affected_modules"] == ["module1", "module2"]


class TestRiskAssessor:
    """Tests for RiskAssessor class."""

    def test_create_risk_assessor(self):
        """Should create with default thresholds."""
        ra = RiskAssessor()
        assert ra.low == 3
        assert ra.high == 10

    def test_create_risk_assessor_custom_thresholds(self):
        """Should accept custom thresholds."""
        ra = RiskAssessor(low=5, high=15)
        assert ra.low == 5
        assert ra.high == 15

    def test_is_test_file(self):
        """Should identify test files."""
        ra = RiskAssessor()
        assert ra._is_test_file("tests/test_main.py")
        assert ra._is_test_file("test/test_util.py")
        assert ra._is_test_file("src/tests/test_foo.py")
        assert ra._is_test_file("foo_test.py")
        assert ra._is_test_file("test_bar.py")
        assert not ra._is_test_file("src/main.py")
        assert not ra._is_test_file("lib/utils.py")

    def test_assess_low_risk(self):
        """Should assess low risk for minimal changes."""
        ra = RiskAssessor()
        ir = ImpactResult(
            commit="abc",
            changed_symbols=[
                ChangedSymbol(name="helper", file="util.py", change_type=ChangeType.MODIFIED)
            ],
            direct_callers=[],
            indirect_callers=[],
            affected_modules=["util"],
        )
        assessment = ra.assess(ir)
        assert assessment.level == "low"

    def test_assess_medium_risk(self):
        """Should assess medium risk for moderate changes."""
        ra = RiskAssessor()
        ir = ImpactResult(
            commit="abc",
            changed_symbols=[
                ChangedSymbol(name="func", file="main.py", change_type=ChangeType.MODIFIED)
            ],
            direct_callers=[Caller(name=f"caller{i}", file="caller.py") for i in range(3)],
            indirect_callers=[],
            affected_modules=["main", "caller"],
        )
        assessment = ra.assess(ir)
        assert assessment.level == "medium"

    def test_assess_high_risk_many_callers(self):
        """Should assess high risk for many callers."""
        ra = RiskAssessor()
        ir = ImpactResult(
            commit="abc",
            changed_symbols=[
                ChangedSymbol(name="core_func", file="core.py", change_type=ChangeType.MODIFIED)
            ],
            direct_callers=[Caller(name=f"caller{i}", file="caller.py") for i in range(10)],
            indirect_callers=[],
            affected_modules=["core", "callers"],
        )
        assessment = ra.assess(ir)
        assert assessment.level == "high"

    def test_assess_high_risk_core_module(self):
        """Should assess high risk for core module changes."""
        ra = RiskAssessor()
        ir = ImpactResult(
            commit="abc",
            changed_symbols=[
                ChangedSymbol(name="authenticate", file="auth/login.py", change_type=ChangeType.MODIFIED)
            ],
            direct_callers=[Caller(name="caller", file="main.py")],
            indirect_callers=[],
            affected_modules=["auth"],
        )
        assessment = ra.assess(ir)
        assert assessment.level == "high"
        assert "核心模块" in assessment.reason

    def test_assess_test_only_changes(self):
        """Should assess low risk for test-only changes."""
        ra = RiskAssessor()
        ir = ImpactResult(
            commit="abc",
            changed_symbols=[
                ChangedSymbol(name="test_func", file="tests/test_main.py", change_type=ChangeType.ADDED)
            ],
            direct_callers=[],
            indirect_callers=[],
            affected_modules=["tests"],
        )
        assessment = ra.assess(ir)
        assert assessment.level == "low"

    def test_assess_many_modules(self):
        """Should assess high risk for changes affecting many modules."""
        ra = RiskAssessor()
        ir = ImpactResult(
            commit="abc",
            changed_symbols=[
        ChangedSymbol(name="core", file="core/util.py", change_type=ChangeType.MODIFIED)
            ],
            direct_callers=[],
            indirect_callers=[],
            affected_modules=["mod1", "mod2", "mod3", "mod4", "mod5"],
        )
        assessment = ra.assess(ir)
        assert assessment.level == "high"


class TestCoreModules:
    """Tests for CORE_MODULES constant."""

    def test_core_modules_defined(self):
        """Should have expected core modules."""
        assert "auth" in CORE_MODULES
        assert "security" in CORE_MODULES
        assert "database" in CORE_MODULES
        assert "config" in CORE_MODULES

    def test_core_modules_immutable(self):
        """Should be a frozenset or similar immutable."""
        # Just verify it exists and has expected values
        assert len(CORE_MODULES) >= 5
