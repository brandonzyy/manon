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
from .policy import Policy, load_policy
from .states import audit_states

TABLES = ("endpoints", "configs", "states", "envelope")

_AUDITORS = {
    "endpoints": audit_endpoints,
    "configs": audit_configs,
    "states": audit_states,
    "envelope": audit_envelope,
}


def audit_project(local_path: str, tables: tuple[str, ...] = TABLES) -> dict:
    """Run the requested tables against a project tree and return raw facts."""
    root = Path(local_path).resolve()
    started = time.monotonic()
    policy = load_policy(root)
    files = enumerate_files(root, project_excludes(str(root)))

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
