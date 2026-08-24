"""Shared result types for contract audit."""

from __future__ import annotations

from dataclasses import dataclass, field

# Verdicts, strongest first. Only DEAD is reported as a defect by default;
# SUSPECT needs a human because the evidence is indirect.
DEAD = "dead"
SUSPECT = "suspect"


@dataclass
class Finding:
    """One dead or suspect surface.

    ``id`` must be stable across runs: it is the key for both the per-repo
    exemption list and the push-hook baseline diff. Anything derived from line
    numbers or file ordering is unusable here.
    """

    table: str
    id: str
    verdict: str
    summary: str
    where: str = ""
    evidence: dict = field(default_factory=dict)
    exempt_reason: str | None = None

    def to_dict(self) -> dict:
        data = {
            "table": self.table,
            "id": self.id,
            "verdict": self.verdict,
            "summary": self.summary,
            "where": self.where,
            "evidence": self.evidence,
        }
        if self.exempt_reason is not None:
            data["exempt_reason"] = self.exempt_reason
        return data


@dataclass
class TableResult:
    """Findings for one table plus the denominators that make them readable."""

    name: str
    title: str
    total: int = 0
    ok: int = 0
    findings: list[Finding] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "total": self.total,
            "ok": self.ok,
            "findings": [f.to_dict() for f in self.findings],
            "note": self.note,
        }
