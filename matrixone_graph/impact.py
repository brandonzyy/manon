"""Impact analysis — git diff → changed symbols → graph caller traversal → risk.

DEPRECATED: This module has been split into matrixone_graph/impact/ submodules.
This file now serves as a compatibility shim, re-exporting all classes.

New code should import from matrixone_graph.impact directly:
    from matrixone_graph.impact import ImpactAnalyzer, ImpactResult, etc.
"""
from __future__ import annotations

# Re-export everything from the new modular structure
from .impact import *  # noqa: F401, F403

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
