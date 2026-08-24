"""Table 1 — endpoints: routes defined by the backend vs URLs anyone calls.

An AST graph stops at the process boundary: the edge from a frontend ``fetch``
to a FastAPI handler is carried by a *string*, so no call edge exists and the
handler looks alive because it is registered. This table rebuilds that edge by
matching path literals.

Matching is prefix-agnostic on purpose. A route declared as ``/versions`` on a
router mounted at ``/api/v1/control/workflows`` is matched by its own literal
segments appearing contiguously inside a caller's URL, so unresolved router
prefixes cannot manufacture dead endpoints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .files import SourceFile
from .model import DEAD, SUSPECT, Finding, TableResult
from .policy import Policy

_PY_PREFIX = re.compile(
    r"""(?P<var>\w+)\s*=\s*(?:\w+\.)?APIRouter\((?P<args>[^)]*)\)""", re.S
)
_PREFIX_KW = re.compile(r"""prefix\s*=\s*['"]([^'"]*)['"]""")

_PY_ROUTE = re.compile(
    r"""@(?P<var>\w+)\.(?P<method>get|post|put|patch|delete|head|options|route)\(\s*"""
    r"""(?:path\s*=\s*)?['"](?P<path>[^'"]*)['"]""",
    re.I,
)
_PY_METHODS_KW = re.compile(r"""methods\s*=\s*\[([^\]]*)\]""", re.I)

_JS_ROUTE = re.compile(
    r"""\b(?P<var>\w+)\.(?P<method>get|post|put|patch|delete|head|options|all)\(\s*"""
    r"""['"`](?P<path>/[^'"`]*)['"`]""",
    re.I,
)
_NEST_ROUTE = re.compile(
    r"""@(?P<method>Get|Post|Put|Patch|Delete|Head|Options|All)\(\s*['"`](?P<path>[^'"`]*)['"`]?"""
)
_NEST_CONTROLLER = re.compile(r"""@Controller\(\s*['"`]([^'"`]*)['"`]""")

# Only these receivers register routes. `client.get('/x')` is a *call*, and
# treating it as a registration would delete the one caller that proves the
# route alive.
_JS_ROUTER_VARS = frozenset({"app", "router", "server", "fastify", "api", "r"})

# Call sites are found by scanning for path-shaped runs anywhere in the text,
# not by requiring a whole quoted string to be a URL. Real clients interpolate:
#   `/api/v1/employee/cases/${encodeURIComponent(id)}/tasks${x ? `/${x}` : ''}`
# never closes as a clean literal, and `${base(id)}/thread` does not even start
# with a slash. Both must still count as calls.
_PATH_RUN = re.compile(r"""(?:/[A-Za-z0-9_\-.{}$%*][A-Za-z0-9_\-.{}$%*()]*){2,}""")
# A one-segment tail glued onto an interpolated or concatenated base.
_TAIL_RUN = re.compile(r"""[}\)+]\s*['"`]?(/[A-Za-z0-9_\-.{}$%*]+)""")
# A whole quoted string that is a single-segment path: client.get(`/pilot-access`).
_SOLO_PATH = re.compile(r"""['"`](/[A-Za-z0-9_\-.{}$%*]+)['"`]""")

_PARAM_SEG = re.compile(r"""^(\{.*\}|<.*>|:.+|\$.+|%[sd]|\*|\d+|\[.*\])$""")

# Segments too common to identify a route on their own.
_GENERIC_SEGMENTS = frozenset({
    "api", "v1", "v2", "v3", "admin", "internal", "public", "web", "app",
    "get", "post", "put", "list", "new", "edit", "id", "index", "health",
    "static", "assets", "auth", "login", "logout", "user", "users", "me",
})

# Route definitions live in these; a route path echoed inside another route
# definition is not a caller.
_DEFINITION_RE = (_PY_ROUTE, _JS_ROUTE, _NEST_ROUTE)


@dataclass
class RouteDef:
    method: str
    path: str
    handler_var: str
    rel: str
    line: int

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


def _segments(path: str) -> list[str]:
    path = path.split("?")[0].split("#")[0]
    out: list[str] = []
    for part in path.split("/"):
        if not part:
            continue
        if _PARAM_SEG.match(part):
            out.append("*")
            continue
        # An interpolation glued to a literal keeps the literal: the segment
        # `tasks${suffix}` still identifies the `tasks` route.
        head = re.split(r"""\$?\{""", part, maxsplit=1)[0].rstrip("-_.")
        out.append(head if head else "*")
    return out


def _contains(haystack: list[str], needle: list[str]) -> bool:
    """Contiguous subsequence match."""
    if not needle or len(needle) > len(haystack):
        return False
    span = len(needle)
    for start in range(len(haystack) - span + 1):
        if haystack[start:start + span] == needle:
            return True
    return False


def _literals(segments: list[str]) -> list[str]:
    """Drop parameters: their names differ between declaration and call site."""
    return [s for s in segments if s != "*"]


def _matches(route_lits: list[str], caller_lits: list[str]) -> bool:
    """Does this call site reach this route?

    Two shapes count, and only two:

    * the caller carries the whole declared path (possibly with more in front,
      e.g. a host or a gateway prefix) — ``route ⊆ caller``;
    * the caller carries only the tail because its client has a ``baseURL``,
      so what it holds is a *suffix* of the declared path.

    A caller that holds some middle fragment of the path does not count: that
    is how ``/cases`` would be read as calling ``/cases/{id}/archive``.
    """
    if not route_lits or not caller_lits:
        return False
    if _contains(caller_lits, route_lits):
        return True
    return route_lits[-len(caller_lits):] == caller_lits


def _distinctive(segments: list[str]) -> str:
    """The segment most likely to identify this route in free text.

    The tail identifies a route (``pilot-access``); the head is shared by
    hundreds of them (``api``, ``v1``, ``employee``).
    """
    literals = [s for s in _literals(segments) if s.lower() not in _GENERIC_SEGMENTS]
    if not literals:
        return ""
    return literals[-1]


def _py_prefixes(text: str) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for match in _PY_PREFIX.finditer(text):
        kw = _PREFIX_KW.search(match.group("args") or "")
        prefixes[match.group("var")] = kw.group(1) if kw else ""
    return prefixes


def _py_methods(line: str, default: str) -> list[str]:
    kw = _PY_METHODS_KW.search(line)
    if not kw:
        return [default]
    found = re.findall(r"""['"](\w+)['"]""", kw.group(1))
    return [m.upper() for m in found] or [default]


def collect_routes(files: list[SourceFile]) -> list[RouteDef]:
    """Extract backend route registrations across Python and TS/JS frameworks."""
    routes: list[RouteDef] = []
    for source in files:
        if source.kind not in ("code", "web"):
            continue
        text = source.text
        if source.rel.endswith(".py"):
            prefixes = _py_prefixes(text)
            for match in _PY_ROUTE.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                line = text.splitlines()[line_no - 1] if line_no <= text.count("\n") + 1 else ""
                raw_method = match.group("method").upper()
                methods = _py_methods(line, "GET" if raw_method == "ROUTE" else raw_method)
                prefix = prefixes.get(match.group("var"), "")
                full = (prefix + match.group("path")) or "/"
                for method in methods:
                    routes.append(RouteDef(method, full, match.group("var"), source.rel, line_no))
        elif source.rel.endswith((".ts", ".js", ".mjs", ".cjs", ".tsx")):
            controller = _NEST_CONTROLLER.search(text)
            base = controller.group(1) if controller else ""
            for match in _NEST_ROUTE.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                path = match.group("path") or ""
                full = "/" + "/".join(p for p in (base + "/" + path).split("/") if p)
                routes.append(RouteDef(match.group("method").upper(), full, "", source.rel, line_no))
            for match in _JS_ROUTE.finditer(text):
                var = match.group("var").lower()
                if var not in _JS_ROUTER_VARS:
                    continue
                line_no = text.count("\n", 0, match.start()) + 1
                method = match.group("method").upper()
                routes.append(
                    RouteDef("*" if method == "ALL" else method, match.group("path"),
                             match.group("var"), source.rel, line_no)
                )
    return routes


def _definition_spans(source: SourceFile) -> set[int]:
    """Line numbers holding a route registration, so they don't count as calls."""
    spans: set[int] = set()
    for pattern in _DEFINITION_RE:
        for match in pattern.finditer(source.text):
            if pattern is _JS_ROUTE and match.group("var").lower() not in _JS_ROUTER_VARS:
                continue
            spans.add(source.text.count("\n", 0, match.start()) + 1)
    return spans


def collect_call_sites(files: list[SourceFile]) -> list[tuple[list[str], SourceFile]]:
    """Every URL-looking literal that is not itself a route registration."""
    sites: list[tuple[list[str], SourceFile]] = []
    for source in files:
        skip_lines = _definition_spans(source)
        for pattern, group in ((_PATH_RUN, 0), (_TAIL_RUN, 1), (_SOLO_PATH, 1)):
            for match in pattern.finditer(source.text):
                line_no = source.text.count("\n", 0, match.start()) + 1
                if line_no in skip_lines:
                    continue
                segments = _segments(match.group(group))
                if segments:
                    sites.append((segments, source))
    return sites


# A file that mentions nearly every route is an inventory of the API — a
# generated OpenAPI spec, a route table, a Postman collection. It is derived
# *from* the backend, so it is not evidence that anything calls the backend.
_INVENTORY_RATIO = 0.75
# The ratio alone misfires on small surfaces: with three routes, the one file
# that calls all three is "100% of the API" and would be discarded as a spec.
_INVENTORY_MIN_ROUTES = 10
_INVENTORY_MIN_HITS = 8
_INVENTORY_NAMES = ("openapi", "swagger", "api-docs", "apidocs", "postman", "insomnia")

# Evidence that a real caller exists, as opposed to a mention.
_STRONG_KINDS = frozenset({"web", "code", "shell"})


def _match_map(
    routes: dict[str, RouteDef], sites: list[tuple[list[str], SourceFile]]
) -> dict[str, set[tuple[str, str]]]:
    """route key -> {(kind, rel)} of every file that references it."""
    matches: dict[str, set[tuple[str, str]]] = {key: set() for key in routes}
    site_lits = [(_literals(segments), source) for segments, source in sites]
    for key, route in routes.items():
        route_lits = _literals(_segments(route.path))
        if not route_lits:
            continue
        for caller_lits, source in site_lits:
            if source.rel == route.rel:
                continue
            if _matches(route_lits, caller_lits):
                kind = "test" if source.is_test else source.kind
                matches[key].add((kind, source.rel))
    return matches


def _inventory_files(matches: dict[str, set[tuple[str, str]]], total: int) -> set[str]:
    """Files that mention so much of the API they can only be an inventory."""
    per_file: dict[str, int] = {}
    for hits in matches.values():
        for _kind, rel in hits:
            per_file[rel] = per_file.get(rel, 0) + 1
    inventory = set()
    for rel, count in per_file.items():
        name = rel.rsplit("/", 1)[-1].lower()
        if any(marker in name for marker in _INVENTORY_NAMES):
            inventory.add(rel)
        elif (
            total >= _INVENTORY_MIN_ROUTES
            and count >= _INVENTORY_MIN_HITS
            and count / total >= _INVENTORY_RATIO
        ):
            inventory.add(rel)
    return inventory


def audit_endpoints(files: list[SourceFile], policy: Policy) -> TableResult:
    """Pair every declared route with whatever calls it."""
    routes = collect_routes(files)
    table = TableResult(name="endpoints", title="端点表：后端声明 ↔ 谁在调")
    if not routes:
        table.note = "未发现路由声明（框架不支持或此仓无 HTTP 面）"
        return table

    sites = collect_call_sites(files)
    seen: dict[str, RouteDef] = {}
    for route in routes:
        seen.setdefault(route.key, route)
    table.total = len(seen)

    matches = _match_map(seen, sites)
    inventory = _inventory_files(matches, table.total)
    if inventory:
        table.note = "已排除 API 清单文件（派生自后端，非调用方）: " + ", ".join(sorted(inventory))

    # Index only the tokens that could identify a route, and remember which file
    # each came from: the route's own definition file is not evidence of a caller.
    wanted = {_distinctive(_segments(route.path)) for route in seen.values()}
    wanted.discard("")
    token_files: dict[str, set[tuple[str, str]]] = {}
    for source in files:
        if source.rel in inventory:
            continue
        kind = "test" if source.is_test else source.kind
        for token in wanted:
            if token in source.text:
                token_files.setdefault(token, set()).add((kind, source.rel))

    for key, route in sorted(seen.items()):
        hits = {(kind, rel) for kind, rel in matches[key] if rel not in inventory}
        strong = {kind for kind, _rel in hits if kind in _STRONG_KINDS}
        weak = {kind for kind, _rel in hits if kind not in _STRONG_KINDS}
        segments = _segments(route.path)
        if strong or not _literals(segments):
            table.ok += 1  # a bare "/" cannot be matched; never call it dead
            continue

        token = _distinctive(segments)
        reason = policy.exemption_for("endpoints", key)
        if weak:
            verdict = SUSPECT
            summary = f"无真实调用方，仅 {'/'.join(sorted(weak))} 提及"
        else:
            token_hits = {
                kind for kind, rel in token_files.get(token, set())
                if rel != route.rel and kind != "sql"
            }
            if token_hits:
                verdict = SUSPECT
                summary = f"无 URL 调用点，仅在 {'/'.join(sorted(token_hits))} 出现同名标识 {token!r}"
            else:
                verdict = DEAD
                summary = "仓内零消费者：无前端、无脚本、无测试、无文档"
        table.findings.append(
            Finding(
                table="endpoints",
                id=key,
                verdict=verdict,
                summary=summary,
                where=f"{route.rel}:{route.line}",
                evidence={"mentions": sorted(f"{k}:{r}" for k, r in hits)[:5], "token": token},
                exempt_reason=reason,
            )
        )
    return table
