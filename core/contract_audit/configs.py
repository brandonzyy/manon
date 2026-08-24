"""Table 2 — config: knobs that are declared vs knobs that are read.

Two shapes, both seen in the wild:

* an env key declared in ``.env.example`` / compose / Dockerfile that no code
  reads — a decoy knob. Someone will set it during an incident and conclude the
  system ignored them, which it did;
* a module-level constant defined in a config module that nothing references —
  the same defect one layer in, and the layer where dead credentials keep being
  propagated to customer deployments long after the code stopped reading them.
"""

from __future__ import annotations

import re

from .files import SourceFile
from .model import DEAD, SUSPECT, Finding, TableResult
from .policy import Policy

_ENV_DECL = re.compile(r"""^\s*(?:export\s+|-\s+)?([A-Z][A-Z0-9_]{2,})\s*=""", re.M)
_COMPOSE_DECL = re.compile(r"""^\s+([A-Z][A-Z0-9_]{2,})\s*:\s""", re.M)
_DOCKER_ENV = re.compile(r"""^\s*(?:ENV|ARG)\s+([A-Z][A-Z0-9_]{2,})""", re.M)

_PY_CONST = re.compile(r"""^([A-Z][A-Z0-9_]{2,})\s*(?::[^=]+)?=""", re.M)
_TS_CONST = re.compile(r"""^\s*(?:export\s+)?const\s+([A-Z][A-Z0-9_]{2,})\s*[:=]""", re.M)

# Files whose whole job is declaring values. A key appearing only here is
# declared, not read.
_DECL_ONLY_NAMES = (".env", "docker-compose", "compose.y", "dockerfile", ".env.example")

_CONFIG_MODULE = re.compile(r"""(^|/)(config|settings|constants|env|conf)(_\w+)?\.(py|ts|js|mjs)$""")

# Deploy-side consumers: a key read only here is not wired to any behaviour, it
# is just being forwarded to somewhere else that may not read it either.
_DEPLOY_HINTS = ("deploy/", "infra/", "ops/", "charts/", "helm/", "k8s/", "terraform/")


def _is_declaration_file(rel: str) -> bool:
    lowered = rel.rsplit("/", 1)[-1].lower()
    return any(marker in lowered for marker in _DECL_ONLY_NAMES)


def _is_deploy(rel: str) -> bool:
    return any(hint in rel for hint in _DEPLOY_HINTS)


def _collect_env_declarations(files: list[SourceFile]) -> dict[str, str]:
    """env key -> where it is declared."""
    declared: dict[str, str] = {}
    for source in files:
        name = source.rel.rsplit("/", 1)[-1].lower()
        patterns = []
        if name.startswith(".env") or ".env." in name or name.endswith(".env"):
            patterns.append(_ENV_DECL)
        elif "compose" in name and source.kind == "config":
            patterns.extend((_ENV_DECL, _COMPOSE_DECL))
        elif name.startswith("dockerfile"):
            patterns.append(_DOCKER_ENV)
        for pattern in patterns:
            for match in pattern.finditer(source.text):
                declared.setdefault(match.group(1), source.rel)
    return declared


def _collect_constants(files: list[SourceFile]) -> dict[str, tuple[str, int]]:
    """CONSTANT defined in a config module -> (file, line)."""
    constants: dict[str, tuple[str, int]] = {}
    for source in files:
        if source.is_test or not _CONFIG_MODULE.search(source.rel):
            continue
        pattern = _PY_CONST if source.rel.endswith(".py") else _TS_CONST
        for match in pattern.finditer(source.text):
            line_no = source.text.count("\n", 0, match.start()) + 1
            constants.setdefault(match.group(1), (source.rel, line_no))
    return constants


_INTERPOLATION = re.compile(r"""\$\{?([A-Z][A-Z0-9_]{2,})\}?""")


def _reader_index(files: list[SourceFile]) -> dict[str, set[str]]:
    """token -> {rel} of every file mentioning it. One pass, reused by both halves."""
    index: dict[str, set[str]] = {}
    for source in files:
        for token in set(re.findall(r"""[A-Z][A-Z0-9_]{2,}""", source.text)):
            index.setdefault(token, set()).add(source.rel)
    return index


def _interpolated_in(files: list[SourceFile]) -> dict[str, set[str]]:
    """key -> files consuming it as ``${KEY}``.

    Where the interpolation happens decides the verdict. A Dockerfile's
    ``ARG NODE_IMAGE`` is read by its own ``FROM ${NODE_IMAGE}`` and is fully
    alive; the same syntax in a deploy script is the opposite case — the key is
    being forwarded downstream by a system that does not itself read it.
    """
    used: dict[str, set[str]] = {}
    for source in files:
        for key in set(_INTERPOLATION.findall(source.text)):
            used.setdefault(key, set()).add(source.rel)
    return used


def audit_configs(files: list[SourceFile], policy: Policy) -> TableResult:
    """Pair every declared knob with whatever reads it."""
    table = TableResult(name="configs", title="配置表：声明 ↔ 谁在读")
    declared = _collect_env_declarations(files)
    constants = _collect_constants(files)
    index = _reader_index(files)
    interpolated = _interpolated_in(files)
    kind_by_rel = {source.rel: ("test" if source.is_test else source.kind) for source in files}
    table.total = len(declared) + len(constants)
    if not table.total:
        table.note = "未发现配置声明"
        return table

    for key in sorted(declared):
        where = declared[key]
        interp = interpolated.get(key, set())
        if where in interp:
            table.ok += 1  # declared and consumed in the same file
            continue
        readers = {
            rel for rel in index.get(key, set())
            if not _is_declaration_file(rel) and kind_by_rel.get(rel) != "doc"
        } | {rel for rel in interp if kind_by_rel.get(rel) != "doc"}
        code_readers = {rel for rel in readers if kind_by_rel.get(rel) in ("code", "web")}
        finding_id = f"env:{key}"
        reason = policy.exemption_for("configs", finding_id)
        if code_readers - {r for r in code_readers if _is_deploy(r)}:
            table.ok += 1
            continue
        if not readers:
            verdict = DEAD
            summary = "诱饵旋钮：全仓除声明处外零引用，设置它不会改变任何行为"
        elif code_readers:
            verdict = SUSPECT
            summary = "仅部署链引用：向下游传播，但本仓代码不读"
        else:
            verdict = SUSPECT
            summary = f"无代码读取点，仅 {', '.join(sorted(readers)[:3])} 提及"
        table.findings.append(
            Finding(
                table="configs", id=finding_id, verdict=verdict, summary=summary,
                where=where, evidence={"readers": sorted(readers)[:8]},
                exempt_reason=reason,
            )
        )

    for name in sorted(constants):
        rel, line = constants[name]
        readers = {other for other in index.get(name, set()) if other != rel}
        finding_id = f"const:{rel}:{name}"
        reason = policy.exemption_for("configs", finding_id)
        if readers:
            table.ok += 1
            continue
        table.findings.append(
            Finding(
                table="configs", id=finding_id, verdict=DEAD,
                summary="配置模块定义了它，全仓无第二处引用",
                where=f"{rel}:{line}", evidence={}, exempt_reason=reason,
            )
        )
    return table
