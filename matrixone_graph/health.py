"""Code health scoring — 8-dimension static analysis.

Dimensions (weight):
  CQ (15) — Code Quality: max production file lines
  TS (10) — Type Safety: `: any` count in TS/TSX
  MT (10) — Maintainability: TODO/HACK/FIXME + hardcoded URLs
  DC (10) — Dead Code: DEPRECATED/REMOVED/LEGACY marks
  IR (15) — Impact Risk (graph-based, default 10)
  MC (15) — Module Coupling (graph-based, default 10)
  TC (15) — Test Coverage of Change (default 10)
  CS (10) — Change Scope (default 10)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def scan_file(file_path: Path) -> dict[str, int]:
    """Scan a single file for health metrics."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    lines = content.split("\n")
    suffix = file_path.suffix.lower()
    is_ts = suffix in (".ts", ".tsx")
    is_test = (
        ".test." in file_path.name or ".spec." in file_path.name
        or "/tests/" in str(file_path).replace("\\", "/")
    )
    result = {"lines": len(lines), "any_count": 0, "todos": 0,
              "hardcoded_urls": 0, "legacy_marks": 0}
    if is_test:
        return result
    if is_ts:
        result["any_count"] = len(re.findall(r":\s*any\b", content))
    result["todos"] = len(re.findall(r"\bTODO\b|\bHACK\b|\bFIXME\b", content))
    if suffix not in (".md", ".txt", ".json"):
        result["hardcoded_urls"] = len(re.findall(r"http://", content))
    result["legacy_marks"] = len(
        re.findall(r"//\s*DEPRECATED|//\s*REMOVED|//\s*LEGACY", content)
    )
    return result


def compute_score(metrics: dict[str, Any]) -> dict[str, Any]:
    """Compute 8-dimension health score (0-100)."""
    max_lines = metrics.get("max_lines", 0)
    any_count = metrics.get("any_count", 0)
    mt_issues = metrics.get("mt_issues", 0)
    legacy_marks = metrics.get("legacy_marks", 0)

    cq = 10 if max_lines <= 300 else 9 if max_lines <= 500 else 7 if max_lines <= 800 else 5
    ts = 10 if any_count == 0 else 9 if any_count <= 5 else 7 if any_count <= 15 else 5
    mt = 10 if mt_issues == 0 else 9 if mt_issues <= 5 else 7
    dc = 10 if legacy_marks == 0 else 9 if legacy_marks <= 3 else 7 if legacy_marks <= 8 else 5

    ir = metrics.get("ir", 10)
    mc = metrics.get("mc", 10)
    tc = metrics.get("tc", 10)
    cs = metrics.get("cs", 10)

    raw = cq * 15 + ts * 10 + mt * 10 + dc * 10 + ir * 15 + mc * 15 + tc * 15 + cs * 10
    return {
        "score": round(raw / 10, 1),
        "cq": cq, "ts": ts, "mt": mt, "dc": dc,
        "ir": ir, "mc": mc, "tc": tc, "cs": cs,
    }
