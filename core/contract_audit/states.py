"""Table 3 — state values: the enum a column may hold vs the values code uses.

``DC 死代码`` finds functions nobody calls. This is the same question one level
down, at the value: a state a schema allows but nothing ever writes is a
*phantom state* — every reader branching on it is dead code that looks live, and
every dashboard counting it will read zero forever without anyone noticing.

The inverse is worse. A terminal state written but never read back means the
closure evidence was never wired: the row says ``disbursed`` and nothing checks
whether the money moved.
"""

from __future__ import annotations

import re

from .files import SourceFile
from .model import DEAD, SUSPECT, Finding, TableResult
from .policy import Policy

_CREATE_TABLE = re.compile(
    r"""CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`]?([\w.]+)["`]?""", re.I
)
_ALTER_TABLE = re.compile(r"""ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?["`]?([\w.]+)["`]?""", re.I)
_CHECK_IN = re.compile(
    r"""["`]?(\w+)["`]?\s+IN\s*\(\s*((?:'[^']*'\s*,?\s*)+)\)""", re.I
)
_DEFAULT_LIT = re.compile(r"""["`]?(\w+)["`]?[^,;()\n]*?\bDEFAULT\s+'([^']+)'""", re.I)
_ENUM_TYPE = re.compile(
    r"""CREATE\s+TYPE\s+["`]?([\w.]+)["`]?\s+AS\s+ENUM\s*\(\s*((?:'[^']*'\s*,?\s*)+)\)""", re.I
)
_LITERAL = re.compile(r"""'([^']*)'""")

_READ_CTX = re.compile(
    r"""(==|!=|\bIN\b\s*\(|\bWHERE\b|\bcase\b|\bmatch\b|\belif\b|\bif\b|\.in_\(|"""
    r"""\bfilter\b|\bincludes\(|\bstartswith\(|\bendswith\()""",
    re.I,
)
_WRITE_CTX = re.compile(
    r"""(\bINSERT\b|\bVALUES\b|\bUPDATE\b|\bSET\b|\breturn\b|\byield\b|"""
    r"""(?<![=!<>])=\s*$|(?<![=!<>])=\s*f?['"]|:\s*f?['"])""",
    re.I,
)

# Values this short or this generic cannot be attributed to a column by text.
_MIN_VALUE_LEN = 3
_NOISE_VALUES = frozenset({"yes", "no", "true", "false", "null", "none", "n/a"})


def _is_state_like(value: str) -> bool:
    """Reject values that are data rather than states.

    ``state_columns`` matches on fragments, so ``type`` pulls in ``media_type``
    and ``content_type`` — whose values are MIME types. A MIME type has no
    writer and no reader in the state-machine sense, and pairing them produces
    rows that are always noise.
    """
    if len(value) < _MIN_VALUE_LEN or value.lower() in _NOISE_VALUES:
        return False
    return "/" not in value and " " not in value


def _table_at(text: str, position: int) -> str:
    """Nearest preceding CREATE/ALTER TABLE — the owner of this constraint."""
    head = text[:position]
    best_name, best_at = "", -1
    for pattern in (_CREATE_TABLE, _ALTER_TABLE):
        for match in pattern.finditer(head):
            if match.start() > best_at:
                best_at, best_name = match.start(), match.group(1)
    return best_name


def collect_domains(files: list[SourceFile], state_columns: tuple[str, ...]) -> dict[str, dict]:
    """(table.column) -> {values, defaults, where} for every state-ish column."""
    domains: dict[str, dict] = {}

    def _record(owner: str, column: str, values: list[str], rel: str, line: int, default: str = ""):
        if not any(marker in column.lower() for marker in state_columns):
            return
        key = f"{owner}.{column}" if owner else column
        entry = domains.setdefault(
            key, {"values": set(), "defaults": set(), "where": f"{rel}:{line}"}
        )
        entry["values"].update(v for v in values if _is_state_like(v))
        if default:
            entry["defaults"].add(default)

    for source in files:
        if source.kind != "sql":
            continue
        text = source.text
        for match in _CHECK_IN.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            values = _LITERAL.findall(match.group(2))
            _record(_table_at(text, match.start()), match.group(1), values, source.rel, line)
        for match in _DEFAULT_LIT.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            _record(
                _table_at(text, match.start()), match.group(1), [match.group(2)],
                source.rel, line, default=match.group(2),
            )
        for match in _ENUM_TYPE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            values = _LITERAL.findall(match.group(2))
            _record("", match.group(1), values, source.rel, line)
    return domains


_CONTEXT_RADIUS = 2


def _usage_index(
    files: list[SourceFile], wanted: set[str]
) -> dict[str, list[tuple[str, str, str, bool]]]:
    """value -> [(rel, line_text, context, is_test)] across non-SQL sources.

    ``context`` is a small window around the hit. It exists for attribution: the
    literal ``'case'`` appears in a hundred unrelated places, and counting those
    as writes to ``artifacts.scope_type`` would make the table lie confidently.
    """
    index: dict[str, list[tuple[str, str, str, bool]]] = {}
    quoted = re.compile(r"""['"]([A-Za-z][A-Za-z0-9_.\-]{2,})['"]""")
    for source in files:
        if source.kind in ("sql", "doc"):
            continue
        lines = source.text.splitlines()
        for number, line in enumerate(lines):
            for match in quoted.finditer(line):
                value = match.group(1)
                if value not in wanted:
                    continue
                low = max(0, number - _CONTEXT_RADIUS)
                context = "\n".join(lines[low:number + _CONTEXT_RADIUS + 1])
                index.setdefault(value, []).append((source.rel, line, context, source.is_test))
    return index


def audit_states(files: list[SourceFile], policy: Policy) -> TableResult:
    """Pair every schema-declared state with its writers and readers."""
    table = TableResult(name="states", title="状态值表：schema 声明 ↔ 谁写谁读")
    domains = collect_domains(files, policy.state_columns)
    if not domains:
        table.note = "未发现状态枚举声明（无 SQL，或无 CHECK/ENUM/DEFAULT 约束）"
        return table

    wanted = {value for entry in domains.values() for value in entry["values"]}
    index = _usage_index(files, wanted)

    for column, entry in sorted(domains.items()):
        owner = column.split(".")[-1]
        table_name = column.split(".")[0] if "." in column else ""
        for value in sorted(entry["values"]):
            table.total += 1
            finding_id = f"state:{column}='{value}'"
            reason = policy.exemption_for("states", finding_id)
            uses = index.get(value, [])
            if not uses:
                # A column DEFAULT has a writer — the database. Zero code
                # references then means nothing *reads* it, which is a different
                # defect from nothing producing it, and a different fix.
                if value in entry["defaults"]:
                    table.findings.append(Finding(
                        table="states", id=finding_id, verdict=SUSPECT,
                        summary="只写不读：DB 默认值写入，代码无任何读取分支",
                        where=entry["where"], evidence={}, exempt_reason=reason,
                    ))
                else:
                    table.findings.append(Finding(
                        table="states", id=finding_id, verdict=DEAD,
                        summary="死状态值：schema 允许，代码零引用",
                        where=entry["where"], evidence={}, exempt_reason=reason,
                    ))
                continue

            production = [
                (rel, line, context) for rel, line, context, is_test in uses if not is_test
            ]
            if not production:
                table.findings.append(Finding(
                    table="states", id=finding_id, verdict=SUSPECT,
                    summary="仅测试引用：生产代码从不产生也不消费这个状态",
                    where=entry["where"],
                    evidence={"tests": sorted({rel for rel, _l, _c, _t in uses})[:4]},
                    exempt_reason=reason,
                ))
                continue

            # Phantom test. Writers are frequently invisible to text analysis:
            # `UPDATE t SET status = $1` carries no literal, so a value can be
            # written without ever appearing in a write-shaped line. The only
            # claim that survives that is the reverse one — every single
            # occurrence in the repo is a *comparison*. One non-comparison hit
            # anywhere is enough to disqualify, which is the safe direction.
            if value in entry["defaults"]:
                table.ok += 1  # the database itself writes the default
                continue
            non_reads = [rel for rel, line, _ctx in production if not _READ_CTX.search(line)]
            if non_reads:
                table.ok += 1
                continue
            attributed = [
                rel for rel, _line, context in production
                if owner in context or (table_name and table_name in context)
            ]
            if not attributed:
                table.ok += 1  # used somewhere, but not provably as this column
                continue
            table.findings.append(Finding(
                table="states", id=finding_id, verdict=SUSPECT,
                summary="幻想状态候选：全仓出现点全是比较分支，无任何产生它的地方",
                where=entry["where"],
                evidence={"readers": sorted(set(attributed))[:4]},
                exempt_reason=reason,
            ))
    return table
