"""Table 4 — guard envelope: entry points that reach a sink without a gate.

The other three tables are set arithmetic. This one needs the call graph: the
question is not "does this handler declare a guard" but "can this handler reach
something dangerous without passing through one".

That distinction is what catches the callback-shaped hole: a card action or
webhook handler is registered outside the framework path where the permission
decorator lives, so every ``@requires`` in the codebase is irrelevant to it, and
nothing about the handler itself looks wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

from .files import SourceFile
from .model import SUSPECT, Finding, TableResult
from .policy import Policy

_PY_ROUTE_LINE = re.compile(
    r"""^\s*@(\w+)\.(get|post|put|patch|delete|head|options|route)\(""", re.I
)
_PY_DEF = re.compile(r"""^\s*(?:async\s+)?def\s+(\w+)\s*\(""")
_JS_HANDLER = re.compile(
    r"""\b\w+\.(get|post|put|patch|delete|all)\(\s*['"`][^'"`]*['"`]\s*,(?P<rest>[^;]*)""",
    re.I,
)

# Below this share of guarded handlers the repo has no auth convention to
# violate, and every handler would be reported. Silence beats noise.
_CONVENTION_FLOOR = 0.25
_MAX_HOPS = 4


def _gate_text(lines: list[str], decorator_at: int, def_at: int) -> str:
    """Decorator stack plus signature — everything that runs before the body."""
    end = def_at
    depth = 0
    for index in range(def_at, min(def_at + 40, len(lines))):
        depth += lines[index].count("(") - lines[index].count(")")
        end = index
        if depth <= 0 and lines[index].rstrip().endswith(":"):
            break
    return "\n".join(lines[decorator_at:end + 1])


def _has_gate(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def _python_handlers(source: SourceFile, patterns: tuple[str, ...]) -> list[tuple[str, int, bool]]:
    """(handler name, line, guarded) for every routed function in this file."""
    lines = source.lines()
    handlers: list[tuple[str, int, bool]] = []
    for index, line in enumerate(lines):
        if not _PY_ROUTE_LINE.match(line):
            continue
        for forward in range(index + 1, min(index + 25, len(lines))):
            match = _PY_DEF.match(lines[forward])
            if match:
                text = _gate_text(lines, index, forward)
                handlers.append((match.group(1), forward + 1, _has_gate(text, patterns)))
                break
    return handlers


def _js_handlers(source: SourceFile, patterns: tuple[str, ...]) -> list[tuple[str, int, bool]]:
    handlers: list[tuple[str, int, bool]] = []
    for match in _JS_HANDLER.finditer(source.text):
        line_no = source.text.count("\n", 0, match.start()) + 1
        rest = match.group("rest")[:400]
        name_match = re.search(r"""(\w+)\s*[,)]""", rest)
        name = name_match.group(1) if name_match else f"handler@{line_no}"
        handlers.append((name, line_no, _has_gate(rest, patterns)))
    return handlers


def _call_graph(files: list[SourceFile]) -> dict[str, set[str]]:
    """caller -> callees, merged across the repo by bare symbol name."""
    graph: dict[str, set[str]] = {}
    try:
        from codeindex.parser import parse_file
    except Exception:
        return graph
    for source in files:
        if source.kind != "code" or source.is_test:
            continue
        try:
            result = parse_file(Path(source.path))
        except Exception:
            continue
        if result.error:
            continue
        for call in result.calls:
            if call.caller and call.callee:
                graph.setdefault(call.caller, set()).add(call.callee)
    return graph


_WORD_SPLIT = re.compile(r"""[^a-z0-9]+|(?<=[a-z0-9])(?=[A-Z])""")


def _is_sink(callee: str, sinks: tuple[str, ...]) -> bool:
    """Match sinks on whole words, never on substrings.

    Substring matching makes ``exec`` swallow ``connection.execute``, i.e. every
    database call in the repo, and a table where every row is a database call is
    a table nobody reads.
    """
    # Match the method, not the receiver: `run_grant.get` is a dict lookup on a
    # variable that happens to be named after a grant, not a grant operation.
    name = callee.rsplit(".", 1)[-1].lower()
    tokens = {token for token in _WORD_SPLIT.split(name) if token}
    for sink in sinks:
        lowered = sink.lower()
        if lowered in tokens:
            return True
        if "_" in lowered and lowered in name:
            return True
    return False


def _reaches_sink(start: str, graph: dict[str, set[str]], sinks: tuple[str, ...]) -> str:
    """BFS from a handler; return the first sink name reached, or ''."""
    seen = {start}
    frontier = [start]
    for _hop in range(_MAX_HOPS):
        nxt: list[str] = []
        for node in frontier:
            for callee in graph.get(node, ()):  # noqa: SIM118 - set default
                if callee in seen:
                    continue
                seen.add(callee)
                if _is_sink(callee, sinks):
                    return callee
                nxt.append(callee)
        if not nxt:
            break
        frontier = nxt
    return ""


def audit_envelope(files: list[SourceFile], policy: Policy) -> TableResult:
    """Find routed entry points that reach a sensitive sink with no gate."""
    table = TableResult(name="envelope", title="守卫包络表：入口 → 敏感汇点，中间有没有门")
    handlers: list[tuple[SourceFile, str, int, bool]] = []
    for source in files:
        if source.kind != "code" or source.is_test:
            continue
        if source.rel.endswith(".py"):
            found = _python_handlers(source, policy.gate_patterns)
        elif source.rel.endswith((".ts", ".js", ".mjs", ".cjs")):
            found = _js_handlers(source, policy.gate_patterns)
        else:
            continue
        handlers.extend((source, name, line, guarded) for name, line, guarded in found)

    table.total = len(handlers)
    if not handlers:
        table.note = "未发现路由处理器"
        return table

    guarded = sum(1 for _s, _n, _l, ok in handlers if ok)
    share = guarded / len(handlers)
    if share < _CONVENTION_FLOOR:
        table.ok = table.total
        table.note = (
            f"仅 {guarded}/{len(handlers)} 个处理器带门禁，此仓无可违反的鉴权约定，跳过判定"
        )
        return table

    graph = _call_graph(files)
    if not graph:
        table.note = "调用图不可用（解析器缺失），仅报告无门禁入口"

    for source, name, line, is_guarded in sorted(handlers, key=lambda h: (h[0].rel, h[2])):
        if is_guarded:
            table.ok += 1
            continue
        sink = _reaches_sink(name, graph, policy.sink_patterns) if graph else ""
        if graph and not sink:
            table.ok += 1  # ungated, but reaches nothing dangerous
            continue
        finding_id = f"envelope:{source.rel}:{name}"
        reason = policy.exemption_for("envelope", finding_id)
        summary = (
            f"入口无门禁，却可达敏感汇点 {sink}()"
            if sink
            else f"入口无门禁（本仓 {guarded}/{len(handlers)} 个处理器都有）"
        )
        table.findings.append(Finding(
            table="envelope", id=finding_id, verdict=SUSPECT, summary=summary,
            where=f"{source.rel}:{line}", evidence={"sink": sink}, exempt_reason=reason,
        ))
    return table
