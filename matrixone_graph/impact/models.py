"""Data models for impact analysis."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
