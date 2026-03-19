"""Impact analysis - modular structure."""
from __future__ import annotations

# Re-export all public classes for backward compatibility
from .models import (
    ChangeType,
    ChangedFile,
    ChangedSymbol,
    Caller,
    RiskAssessment,
    ImpactResult,
)

from .parsing import GitDiffParser, ChangedSymbolExtractor
from .risk_assessor import RiskAssessor, CORE_MODULES
from .analyzer import ImpactAnalyzer

__all__ = [
    # Models
    "ChangeType",
    "ChangedFile",
    "ChangedSymbol",
    "Caller",
    "RiskAssessment",
    "ImpactResult",
    # Components
    "GitDiffParser",
    "ChangedSymbolExtractor",
    "RiskAssessor",
    "ImpactAnalyzer",
    "CORE_MODULES",
]
