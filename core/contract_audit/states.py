"""Table 3 — state values: the enum a column may hold vs the values code uses.

``DC 死代码`` finds functions nobody calls. This is the same question one level
down, at the value: a state a schema allows but nothing ever writes is a
*phantom state* — every reader branching on it is dead code that looks live, and
every dashboard counting it will read zero forever without anyone noticing.

The inverse is worse. A terminal state written but never read back means the
closure evidence was never wired: the row says ``disbursed`` and nothing checks
whether the money moved.

Worse still, and the only verdict here that is a certainty rather than a
suspicion: a value the code *writes* that the column's ``CHECK`` forbids. That
statement cannot ever succeed. It is usually wrapped in a ``try`` that logs and
moves on, so the failure is invisible exactly where it matters.
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
_DROP_TABLE = re.compile(
    r"""DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?["`]?([\w.]+)["`]?""", re.I
)
_DROP_COLUMN = re.compile(r"""DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?["`]?(\w+)["`]?""", re.I)
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


def _line_span(text: str, start: int, end: int) -> range:
    first = text.count("\n", 0, start) + 1
    last = text.count("\n", 0, end) + 1
    return range(first, last + 1)


def collect_domains(
    files: list[SourceFile], state_columns: tuple[str, ...]
) -> tuple[dict[str, dict], dict[str, set[int]]]:
    """(table.column) -> domain, plus the schema lines to ignore when counting use.

    The second return value is the point of the split. A migration file *is* the
    declaration; counting the value inside its own ``CHECK`` as a use would make
    every column look alive. But everything else in a ``.sql`` file — seed rows,
    backfills, a ``DELETE ... WHERE status = 'x'`` — is a real writer or reader,
    and dropping whole SQL files to avoid the first problem silently creates the
    opposite one.
    """
    domains: dict[str, dict] = {}
    decl_lines: dict[str, set[int]] = {}

    def _record(
        owner: str, column: str, values: list[str], source: SourceFile,
        at: tuple[int, int], default: str = "", checked: bool = False,
    ):
        if not any(marker in column.lower() for marker in state_columns):
            return
        key = f"{owner}.{column}" if owner else column
        line = source.text.count("\n", 0, at[1]) + 1
        entry = domains.setdefault(
            key,
            {"values": set(), "defaults": set(), "checked": set(),
             "where": f"{source.rel}:{line}", "at": at},
        )
        keep = [v for v in values if _is_state_like(v)]
        entry["values"].update(keep)
        if checked:
            entry["checked"].update(keep)
        if default:
            entry["defaults"].add(default)
        if at > entry["at"]:
            entry["at"] = at

    sql_order = {
        source.rel: order
        for order, source in enumerate(f for f in files if f.kind == "sql")
    }
    for source in files:
        if source.kind != "sql":
            continue
        order = sql_order[source.rel]
        text = source.text
        spans = decl_lines.setdefault(source.rel, set())
        for match in _CHECK_IN.finditer(text):
            values = _LITERAL.findall(match.group(2))
            _record(
                _table_at(text, match.start()), match.group(1), values, source,
                (order, match.start()), checked=True,
            )
            spans.update(_line_span(text, match.start(), match.end()))
        for match in _DEFAULT_LIT.finditer(text):
            _record(
                _table_at(text, match.start()), match.group(1), [match.group(2)],
                source, (order, match.start()), default=match.group(2),
            )
            spans.update(_line_span(text, match.start(), match.end()))
        for match in _ENUM_TYPE.finditer(text):
            values = _LITERAL.findall(match.group(2))
            _record("", match.group(1), values, source, (order, match.start()))
            spans.update(_line_span(text, match.start(), match.end()))
    return domains, decl_lines


def retired_after(files: list[SourceFile]) -> tuple[dict[str, tuple[int, int]], dict[str, tuple[int, int]]]:
    """Where each table / table.column was last dropped, in migration order.

    A schema is a sequence, not a snapshot. ``003`` creating a table and ``058``
    dropping it means the table does not exist — yet the ``CHECK`` literals sit
    in ``003`` forever, so a snapshot reading reports every value of every
    retired table as dead, permanently, and the loudest rows in the table are
    the ones somebody already cleaned up.
    """
    tables: dict[str, tuple[int, int]] = {}
    columns: dict[str, tuple[int, int]] = {}
    order = 0
    for source in files:
        if source.kind != "sql":
            continue
        position, order = order, order + 1
        if source.is_test:
            continue
        text = source.text
        for match in _DROP_TABLE.finditer(text):
            tables[match.group(1)] = (position, match.start())
        for match in _DROP_COLUMN.finditer(text):
            owner = _table_at(text, match.start())
            columns[f"{owner}.{match.group(1)}"] = (position, match.start())
    return tables, columns


_CONTEXT_RADIUS = 2


def _usage_index(
    files: list[SourceFile], wanted: set[str], decl_lines: dict[str, set[int]]
) -> dict[str, list[tuple[str, str, str, bool]]]:
    """value -> [(rel, line_text, context, is_test)] across every source.

    ``context`` is a small window around the hit. It exists for attribution: the
    literal ``'case'`` appears in a hundred unrelated places, and counting those
    as writes to ``artifacts.scope_type`` would make the table lie confidently.
    """
    index: dict[str, list[tuple[str, str, str, bool]]] = {}
    quoted = re.compile(r"""['"]([A-Za-z][A-Za-z0-9_.\-]{2,})['"]""")
    for source in files:
        if source.kind == "doc":
            continue
        skip = decl_lines.get(source.rel, frozenset())
        lines = source.text.splitlines()
        for number, line in enumerate(lines):
            if number + 1 in skip:
                continue
            for match in quoted.finditer(line):
                value = match.group(1)
                if value not in wanted:
                    continue
                low = max(0, number - _CONTEXT_RADIUS)
                context = "\n".join(lines[low:number + _CONTEXT_RADIUS + 1])
                index.setdefault(value, []).append((source.rel, line, context, source.is_test))
    return index


_INSERT_HEAD = re.compile(
    r"""INSERT\s+INTO\s+["`]?([\w.]+)["`]?\s*\(([^)]*)\)""", re.I | re.S
)
_UPDATE_HEAD = re.compile(r"""UPDATE\s+["`]?([\w.]+)["`]?\s+SET\b""", re.I)
_DO_UPDATE_SET = re.compile(r"""DO\s+UPDATE\s+SET\b""", re.I)
_VALUES_HEAD = re.compile(r"""\bVALUES\s*\(""", re.I)
_SET_LITERAL = re.compile(r"""["`]?(\w+)["`]?\s*=\s*'([^']*)'""")
_PLAIN_LITERAL = re.compile(r"""^'([^']*)'$""")

# A statement written inside a host-language string has no terminating ``;`` —
# the quote ends it. Without a bound the "statement" runs to end of file and
# every later assignment in the file gets attributed to the first table named,
# which is how one real defect became three hundred and thirty-three.
_CLAUSE_END = re.compile(r"""(\bWHERE\b|\bRETURNING\b|\bFROM\b|;|\"\"\"|''')""", re.I)
_CLAUSE_MAX = 400
_STATEMENT_MAX = 800


def _clause(text: str, start: int, limit: int = _CLAUSE_MAX) -> str:
    """One SQL clause: from ``start`` to the first terminator, hard-capped."""
    window = text[start:start + limit]
    stop = _CLAUSE_END.search(window)
    return window[:stop.start()] if stop else window


def _balanced(text: str, open_at: int) -> tuple[str, int]:
    """Body of the parenthesised group starting at ``open_at`` (index of ``(``)."""
    depth, quote, index = 0, "", open_at
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:index], index
        index += 1
    return "", len(text)


def _split_items(body: str) -> list[str]:
    items, depth, quote, current = [], 0, "", []
    for char in body:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    items.append("".join(current).strip())
    return [item for item in items if item]


def collect_writes(files: list[SourceFile]) -> list[tuple[str, str, str, str, int]]:
    """(table, column, value, rel, line) for every literal a statement writes.

    Works on raw text rather than parsed SQL because the statements that matter
    most live inside host-language string literals — the ``INSERT`` that breaks
    a ``CHECK`` is in a Python file, not in the schema.
    """
    writes: list[tuple[str, str, str, str, int]] = []

    def _add(table: str, column: str, value: str, source: SourceFile, at: int):
        writes.append((table, column, value, source.rel, source.text.count("\n", 0, at) + 1))

    def _scan_set(table: str, source: SourceFile, at: int):
        clause = _clause(source.text, at)
        for assign in _SET_LITERAL.finditer(clause):
            _add(table, assign.group(1), assign.group(2), source, at + assign.start())

    for source in files:
        if source.is_test:
            continue
        text = source.text
        for match in _INSERT_HEAD.finditer(text):
            table = match.group(1)
            columns = [c.strip().strip('"`') for c in _split_items(match.group(2))]
            tail = text[match.end():match.end() + _STATEMENT_MAX]
            values = _VALUES_HEAD.search(tail)
            if values:
                cursor = tail.index("(", values.start())
                while True:
                    group, stop = _balanced(tail, cursor)
                    items = _split_items(group)
                    if len(items) == len(columns):
                        for column, item in zip(columns, items):
                            literal = _PLAIN_LITERAL.match(item)
                            if literal:
                                _add(table, column, literal.group(1), source, match.end() + cursor)
                    rest = tail[stop + 1:stop + 4].lstrip()
                    if not rest.startswith(","):
                        break
                    nxt = tail.find("(", stop + 1)
                    if nxt == -1:
                        break
                    cursor = nxt
            conflict = _DO_UPDATE_SET.search(tail)
            if conflict:
                _scan_set(table, source, match.end() + conflict.end())
        for match in _UPDATE_HEAD.finditer(text):
            _scan_set(match.group(1), source, match.end())
    return writes


def audit_states(files: list[SourceFile], policy: Policy) -> TableResult:
    """Pair every schema-declared state with its writers and readers."""
    table = TableResult(name="states", title="状态值表：schema 声明 ↔ 谁写谁读")
    domains, decl_lines = collect_domains(files, policy.state_columns)
    if not domains:
        table.note = "未发现状态枚举声明（无 SQL，或无 CHECK/ENUM/DEFAULT 约束）"
        return table

    dropped_tables, dropped_columns = retired_after(files)

    def _retired(column: str, entry: dict) -> bool:
        owner = column.split(".")[0] if "." in column else ""
        if owner and dropped_tables.get(owner, (-1, -1)) > entry["at"]:
            return True
        return dropped_columns.get(column, (-1, -1)) > entry["at"]

    live = {column: entry for column, entry in domains.items() if not _retired(column, entry)}
    wanted = {value for entry in live.values() for value in entry["values"]}
    index = _usage_index(files, wanted, decl_lines)

    for column, entry in sorted(live.items()):
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

    seen: set[str] = set()
    for target, column, value, rel, line in collect_writes(files):
        key = f"{target}.{column}"
        entry = live.get(key)
        if not entry or not entry["checked"] or value in entry["values"]:
            continue
        finding_id = f"write:{key}='{value}'"
        if finding_id in seen:
            continue
        seen.add(finding_id)
        table.findings.append(Finding(
            table="states", id=finding_id, verdict=DEAD,
            summary=(
                f"写入值不在 schema 允许集：CHECK 只收 "
                f"{'/'.join(sorted(entry['checked']))}，这条语句必然被拒"
            ),
            where=f"{rel}:{line}",
            evidence={"declared_at": entry["where"], "allowed": sorted(entry["checked"])},
            exempt_reason=policy.exemption_for("states", finding_id),
        ))
    return table
