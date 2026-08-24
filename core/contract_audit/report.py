"""Rendering and baseline diffing for contract audit.

The push hook prints *deltas*, never the full table. A gate that reprints
seventy known-and-accepted lines on every push is a gate people learn to scroll
past, and a gate people scroll past is not a gate.
"""

from __future__ import annotations

import json
from pathlib import Path

BASELINE_DIR = Path.home() / ".manon" / "contract_audit"

_VERDICT_MARK = {"dead": "✗", "suspect": "?"}


def _lines_for_table(table: dict, limit: int) -> list[str]:
    lines: list[str] = []
    total, ok = table["total"], table["ok"]
    active = [f for f in table["findings"] if "exempt_reason" not in f]
    exempted = len(table["findings"]) - len(active)
    head = f"  {table['title']}  {ok}/{total} 干净"
    if active:
        dead = sum(1 for f in active if f["verdict"] == "dead")
        head += f"，{dead} 死面"
        suspect = len(active) - dead
        if suspect:
            head += f" + {suspect} 待确认"
    if exempted:
        head += f"（已豁免 {exempted}）"
    lines.append(head)
    if table.get("note"):
        lines.append(f"      note: {table['note']}")
    for finding in active[:limit]:
        mark = _VERDICT_MARK.get(finding["verdict"], "-")
        lines.append(f"    {mark} {finding['id']}")
        lines.append(f"        {finding['summary']}  @{finding['where']}")
    if len(active) > limit:
        lines.append(f"    … 另有 {len(active) - limit} 条，用 --json 取全量")
    return lines


def render(result: dict, limit: int = 8) -> str:
    """Full human-readable report."""
    lines = [
        f"契约对账  {result['dead']} 死面 / {result['suspect']} 待确认"
        f"（{result['files_scanned']} 文件，{result['elapsed_seconds']}s）"
    ]
    lines.append(
        f"  策略: {result['policy_source'] or '未配置 .manon-contract.yaml —— 豁免清单为空'}"
    )
    lines.append("")
    for table in result["tables"]:
        lines.extend(_lines_for_table(table, limit))
        lines.append("")
    stale = result.get("stale_exemptions") or []
    if stale:
        lines.append(f"  豁免清单已腐坏：{len(stale)} 条豁免今轮没匹配到任何东西")
        for entry in stale[:5]:
            lines.append(f"    - {entry['table']} {entry['id']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def load_baseline(repo_id: str) -> dict:
    path = BASELINE_DIR / f"{repo_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_baseline(repo_id: str, result: dict) -> None:
    try:
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "ids": sorted(
                f["id"] for f in result["findings"] if "exempt_reason" not in f
            ),
            "dead": result["dead"],
            "suspect": result["suspect"],
        }
        (BASELINE_DIR / f"{repo_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def diff_baseline(result: dict, baseline: dict) -> tuple[list[dict], list[str]]:
    """Return (findings that are new since the baseline, ids that are gone)."""
    known = set(baseline.get("ids") or [])
    active = [f for f in result["findings"] if "exempt_reason" not in f]
    new = [f for f in active if f["id"] not in known]
    fixed = sorted(known - {f["id"] for f in active})
    return new, fixed


def render_delta(result: dict, baseline: dict, limit: int = 6) -> str:
    """What the push hook prints: only what changed."""
    if not baseline:
        return (
            f"[manon] 契约对账基线已建立：{result['dead']} 死面 / "
            f"{result['suspect']} 待确认（后续只报新增）"
        )
    new, fixed = diff_baseline(result, baseline)
    if not new and not fixed:
        return ""
    lines = []
    if new:
        lines.append(f"[manon] 契约对账：本次新增 {len(new)} 个死面/待确认")
        for finding in new[:limit]:
            lines.append(f"          {_VERDICT_MARK.get(finding['verdict'], '-')} "
                         f"{finding['id']}  @{finding['where']}")
        if len(new) > limit:
            lines.append(f"          … 另有 {len(new) - limit} 条")
    if fixed:
        lines.append(f"[manon] 契约对账：{len(fixed)} 个旧死面已消失")
    return "\n".join(lines)
