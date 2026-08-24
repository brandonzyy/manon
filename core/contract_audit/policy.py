"""Per-repo audit policy — facts are global, verdicts are local.

The audit computes facts (who defines, who consumes). Whether a given dead
surface is *acceptable* is a per-repo decision: a route with no in-repo caller
may be a legitimate ops-only curl endpoint. Those decisions live in
``.manon-contract.yaml`` at the repo root, not in the report.

Without this file every run re-reports the same known-and-accepted surfaces,
the report gets ignored, and an ignored gate is the same as no gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

POLICY_FILENAMES = (".manon-contract.yaml", ".manon-contract.yml", ".manon-contract.json")

# A gate is anything that must run before the handler body: a decorator, or a
# FastAPI/Nest dependency. These are name fragments, matched case-insensitively.
DEFAULT_GATE_PATTERNS = (
    "auth", "admin", "require", "requires", "permission", "permissions",
    "guard", "verify", "current_user", "token", "login", "session",
    "authorize", "authenticate", "acl", "rbac", "scope",
)

# Sinks worth protecting. Deliberately narrow: a broad list turns the envelope
# table into noise, and a noisy table is an ignored table.
DEFAULT_SINK_PATTERNS = (
    "delete", "drop", "truncate", "purge",
    "pay", "payout", "transfer", "refund", "withdraw", "disburse",
    "grant", "revoke", "promote", "impersonate",
    "exec", "eval", "raw_sql", "execute_sql", "shell",
)

# Columns whose values form a state machine. Values of other columns are data,
# not states, and pairing their reads/writes says nothing.
DEFAULT_STATE_COLUMNS = (
    "status", "state", "stage", "phase", "outcome", "result",
    "kind", "type", "mode", "verdict", "decision", "lifecycle",
)


@dataclass
class Policy:
    """Loaded audit policy for one repo."""

    exempt: dict[str, dict[str, str]] = field(default_factory=dict)
    gate_patterns: tuple[str, ...] = DEFAULT_GATE_PATTERNS
    sink_patterns: tuple[str, ...] = DEFAULT_SINK_PATTERNS
    state_columns: tuple[str, ...] = DEFAULT_STATE_COLUMNS
    source: str = ""
    _hit: set[str] = field(default_factory=set, repr=False)

    def exemption_for(self, table: str, finding_id: str) -> str | None:
        """Return the recorded reason if this finding is exempted, else None."""
        reason = self.exempt.get(table, {}).get(finding_id)
        if reason is not None:
            self._hit.add(f"{table} {finding_id}")
        return reason

    def stale_exemptions(self) -> list[tuple[str, str, str]]:
        """Exemptions that matched nothing this run — the list has rotted."""
        stale = []
        for table, entries in self.exempt.items():
            for finding_id, reason in entries.items():
                if f"{table} {finding_id}" not in self._hit:
                    stale.append((table, finding_id, reason))
        return sorted(stale)


def _normalize_entries(raw) -> dict[str, str]:
    """Accept either a list of ids, a list of {id, reason}, or a mapping."""
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            out[str(key)] = str(value) if value is not None else ""
        return out
    for item in raw or []:
        if isinstance(item, str):
            out[item] = ""
        elif isinstance(item, dict):
            finding_id = item.get("id") or item.get("name")
            if finding_id:
                out[str(finding_id)] = str(item.get("reason", ""))
    return out


def _read_policy_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        import json

        return json.loads(text) or {}
    import yaml

    return yaml.safe_load(text) or {}


def load_policy(root: Path) -> Policy:
    """Load ``.manon-contract.yaml`` from the repo root; defaults if absent."""
    for name in POLICY_FILENAMES:
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            data = _read_policy_file(candidate)
        except Exception as exc:  # a broken policy must not silently disable the audit
            return Policy(source=f"{name} (解析失败: {exc})")
        exempt_raw = data.get("exempt") or {}
        exempt = {table: _normalize_entries(entries) for table, entries in exempt_raw.items()}
        envelope = data.get("envelope") or {}
        return Policy(
            exempt=exempt,
            gate_patterns=tuple(envelope.get("gate_patterns") or DEFAULT_GATE_PATTERNS),
            sink_patterns=tuple(envelope.get("sink_patterns") or DEFAULT_SINK_PATTERNS),
            state_columns=tuple(data.get("state_columns") or DEFAULT_STATE_COLUMNS),
            source=name,
        )
    return Policy(source="")
