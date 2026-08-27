"""Contract audit — the facts an AST graph cannot hold.

The knowledge graph answers "who calls whom". It cannot answer "who *should*
prove what", and it breaks entirely at the edges that are carried by strings
rather than by call edges: an HTTP path, a config key, a state literal in a
column. Those edges cross languages, processes and deployments, which is exactly
where dead surfaces accumulate unseen.

Four tables, all set arithmetic over definitions and consumers:

======== ================================================================
endpoints routes the backend declares vs URLs anything calls
configs   knobs declared vs knobs read
states    state values a schema allows vs values code writes and reads
envelope  routed entry points that reach a sensitive sink with no gate
======== ================================================================

These are *facts*, not judgements, so they carry no score and belong in a gate:
they run without a model, in a hook or in CI. Whether a given dead surface is
acceptable is a per-repo decision recorded in ``.manon-contract.yaml``.
"""

from __future__ import annotations

import time
from pathlib import Path

from .configs import audit_configs
from .endpoints import audit_endpoints
from .envelope import audit_envelope
from .files import enumerate_files, project_excludes
from .model import DEAD, SUSPECT, Finding, TableResult
from .policy import POLICY_FILENAMES, Policy, load_policy
from .states import audit_states

TABLES = ("endpoints", "configs", "states", "envelope")

_AUDITORS = {
    "endpoints": audit_endpoints,
    "configs": audit_configs,
    "states": audit_states,
    "envelope": audit_envelope,
}


def audit_project(local_path: str, tables: tuple[str, ...] = TABLES,
                  extra_excludes: list[str] | None = None,
                  use_project_excludes: bool = True) -> dict:
    """Run the requested tables against a project tree and return raw facts."""
    root = Path(local_path).resolve()
    started = time.monotonic()
    policy = load_policy(root)
    # 运行时的 custom_excludes 是**用户为本机索引**配的取舍，不是仓库的事实：
    # 棘轮（check_l1）必须传 use_project_excludes=False，否则本机配过索引与
    # CI 干净克隆读出两套死面（判例 2026-08-27：scripts/ 被本机排除后，
    # launch_mcp.sh 对 /tunnel-url 的引用蒸发，两条 dead 凭空出现）。
    runtime_excludes = project_excludes(str(root)) if use_project_excludes else []
    files = [
        source for source in enumerate_files(
            root, runtime_excludes + list(extra_excludes or []))
        if source.rel not in POLICY_FILENAMES
    ]
    # The policy file names every finding it exempts. Left in the corpus it
    # reads as a consumer of every surface it mentions, and the whole table
    # silently goes to zero the moment someone writes one. The ratchet's own
    # baseline files are the same disease one layer out (--exclude exists so
    # the caller can keep its outputs out of the evidence).

    results: list[TableResult] = []
    for name in tables:
        auditor = _AUDITORS.get(name)
        if auditor is None:
            continue
        results.append(auditor(files, policy))

    findings = [f for table in results for f in table.findings]
    active = [f for f in findings if f.exempt_reason is None]
    return {
        "root": str(root),
        "policy_source": policy.source,
        "files_scanned": len(files),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "tables": [table.to_dict() for table in results],
        "findings": [f.to_dict() for f in findings],
        "dead": sum(1 for f in active if f.verdict == DEAD),
        "suspect": sum(1 for f in active if f.verdict == SUSPECT),
        "exempted": sum(1 for f in findings if f.exempt_reason is not None),
        "stale_exemptions": [
            {"table": table, "id": finding_id, "reason": reason}
            for table, finding_id, reason in policy.stale_exemptions()
        ],
    }


__all__ = [
    "TABLES",
    "DEAD",
    "SUSPECT",
    "Finding",
    "Policy",
    "TableResult",
    "audit_project",
    "load_policy",
]
