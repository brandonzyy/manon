"""Risk assessment for impact analysis."""
from __future__ import annotations

from pathlib import Path

from .models import ChangedSymbol, ImpactResult, RiskAssessment

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

    def _compute_base_level(
        self, total: int, is_core: bool, many_modules: bool, n_modules: int,
    ) -> tuple[str, list[str], list[str]]:
        """Compute base risk level, reasons, and suggestions."""
        reasons: list[str] = []
        suggestions: list[str] = []
        if is_core or total >= self.high or many_modules:
            level = "high"
            if is_core:
                reasons.append("涉及核心模块")
                suggestions.append("核心模块变更需 code review")
            if total >= self.high:
                reasons.append(f"{total} 个调用者受影响")
            if many_modules:
                reasons.append(f"波及 {n_modules} 个模块")
            suggestions.append("建议完整集成测试")
        elif total >= self.low:
            level = "medium"
            reasons.append(f"{total} 个调用者, {n_modules} 个模块")
        else:
            level = "low"
            reasons.append("变更范围有限")
        return level, reasons, suggestions

    def assess(self, result: ImpactResult) -> RiskAssessment:
        test_only = bool(result.changed_symbols) and all(self._is_test_file(s.file) for s in result.changed_symbols)
        total = len(result.direct_callers) + len(result.indirect_callers)
        is_core = any(any(c in s.file.lower() for c in CORE_MODULES) for s in result.changed_symbols)
        many_modules = len(result.affected_modules) >= 5
        public_changed = [] if test_only else [
            s for s in result.changed_symbols if not s.name.startswith("_") and not self._is_test_file(s.file)
        ]
        has_heavy_public = any(s.lines_changed > 20 for s in public_changed)

        level, reasons, suggestions = self._compute_base_level(total, is_core, many_modules, len(result.affected_modules))

        if has_heavy_public:
            if level == "low":
                level = "medium"
                reasons.append(f"{len(public_changed)} 个公共符号有大幅改动")
            suggestions.append("检查公共 API 向后兼容性")

        if result.affected_tests:
            suggestions.append(f"运行受影响测试: {', '.join(result.affected_tests[:5])}")
        elif total > 0:
            suggestions.append("未发现直接关联测试，建议补充测试覆盖")
        if not suggestions:
            suggestions.append("运行变更代码的单元测试")

        if test_only:
            level, reasons, suggestions = "low", ["仅测试变更，风险有限"], ["运行变更代码的单元测试"]

        return RiskAssessment(level=level, reason=f"{level.capitalize()} risk: " + "; ".join(reasons), suggestions=suggestions)
