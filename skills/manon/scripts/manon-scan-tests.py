#!/usr/bin/env python3
"""manon-scan-tests.py — side-channel test coverage scanner.

Usage:
    python <MANON_DIR>/scripts/manon-scan-tests.py <repo_id>

Priority:
  1. Dynamic  — reads coverage.xml from `pytest --cov --cov-report=xml`
  2. Static   — AST import/call analysis (fallback when no coverage.xml)

Output (stdout): JSON { covered, test_files, test_functions, source }
Cache:  ~/.manon/scan_cache/<repo_id>_coverage.json
"""
from __future__ import annotations

import json
import os
import site
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCAN_CACHE_DIR = Path.home() / ".manon" / "scan_cache"

_TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__"})


def _find_project_root() -> Path:
    """Locate repo root. Priority: MANON_DIR env var → upward search → fallback."""
    env_dir = os.environ.get("MANON_DIR")
    if env_dir and (Path(env_dir) / "manon_mcp").exists():
        return Path(env_dir)
    candidate = SCRIPT_DIR
    for _ in range(6):
        if (candidate / "manon_mcp").exists():
            return candidate
        candidate = candidate.parent
    return SCRIPT_DIR.parent  # fallback


PROJECT_ROOT = _find_project_root()
VENV_DIR = PROJECT_ROOT / ".venv"
REQ_FILE = PROJECT_ROOT / "manon_mcp" / "requirements.txt"


# ── Runtime bootstrap (mirrors manon-scan.py) ─────────────────────────────────

def _venv_site_packages() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Lib" / "site-packages"
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return VENV_DIR / "lib" / version / "site-packages"


def _scan_runtime_ready() -> bool:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    try:
        import httpx  # noqa: F401
        import yaml   # noqa: F401
        return True
    except ImportError:
        pass
    site_packages = _venv_site_packages()
    if not site_packages.exists():
        return False
    site.addsitedir(str(site_packages))
    try:
        import httpx  # noqa: F401
        import yaml   # noqa: F401
    except Exception:
        return False
    return True


def _bootstrap_scan_runtime() -> None:
    if _scan_runtime_ready():
        return
    subprocess.run(
        [sys.executable, "-m", "venv", str(VENV_DIR), "--clear"],
        check=True, cwd=str(PROJECT_ROOT),
    )
    site_packages = _venv_site_packages()
    site_packages.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q",
         "--disable-pip-version-check", "--upgrade",
         "--target", str(site_packages), "-r", str(REQ_FILE)],
        check=True, cwd=str(PROJECT_ROOT),
    )
    if not _scan_runtime_ready():
        raise RuntimeError("failed to bootstrap scan runtime")


# ── Dynamic coverage (coverage.xml) ───────────────────────────────────────────

def _is_test_file(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    if parts[-1].startswith("test_") or parts[-1].endswith("_test.py"):
        return True
    return any(p in _TEST_DIR_NAMES for p in parts[:-1])


def _find_coverage_xml(project_path: Path) -> Path | None:
    for candidate in (
        project_path / "coverage.xml",
        project_path / "htmlcov" / "coverage.xml",
        project_path / ".coverage.xml",
        project_path / "reports" / "coverage.xml",
    ):
        if candidate.exists():
            return candidate
    return None


def _parse_covered_lines(coverage_xml: Path) -> dict[str, set[int]]:
    """Parse coverage.xml → {rel_path: {covered_line_numbers}}"""
    try:
        tree = ET.parse(coverage_xml)
    except ET.ParseError:
        return {}
    result: dict[str, set[int]] = {}
    for cls in tree.getroot().iter("class"):
        filename = cls.get("filename", "").replace("\\", "/").lstrip("./")
        covered: set[int] = set()
        for line in cls.iter("line"):
            if line.get("hits", "0") != "0":
                covered.add(int(line.get("number", 0)))
        if covered:
            result[filename] = covered
    return result


def _module_from_rel(rel_path: str) -> str:
    """saas/routers/query.py → saas.routers.query"""
    s = rel_path.replace("\\", "/")
    if s.endswith(".py"):
        s = s[:-3]
    if s.endswith("/__init__"):
        s = s[:-9]
    return s.replace("/", ".")


def _resolve_abs(project_path: Path, rel_path: str) -> tuple[Path, str] | None:
    """Resolve rel_path to absolute, stripping leading path segments if needed."""
    direct = project_path / rel_path
    if direct.exists():
        return direct, rel_path
    parts = rel_path.split("/")
    for skip in range(1, min(4, len(parts))):
        trimmed = "/".join(parts[skip:])
        candidate = project_path / trimmed
        if candidate.exists():
            return candidate, trimmed
    return None


def _dynamic_coverage(project_path: Path) -> tuple[set[str], int, int, str] | None:
    """Build covered-symbol set from coverage.xml.
    Returns (covered_symbols, test_file_count, test_func_count, xml_path_str) or None.
    """
    xml_path = _find_coverage_xml(project_path)
    if not xml_path:
        return None

    from codeindex.parser import parse_file

    covered_lines = _parse_covered_lines(xml_path)
    covered_symbols: set[str] = set()

    for raw_rel, lines in covered_lines.items():
        if _is_test_file(raw_rel):
            continue
        resolved = _resolve_abs(project_path, raw_rel)
        if not resolved:
            continue
        abs_path, rel_path = resolved
        try:
            pr = parse_file(abs_path)
        except Exception:
            continue
        module = _module_from_rel(rel_path)
        for sym in pr.symbols or []:
            if getattr(sym, "kind", "") not in ("function", "method"):
                continue
            name = getattr(sym, "name", "") or ""
            ls = getattr(sym, "line_start", 0) or 0
            le = max(getattr(sym, "line_end", 0) or 0, ls)
            if set(range(ls, le + 1)) & lines:
                covered_symbols.add(f"{module}.{name}")

    test_files = _find_test_files(project_path)
    test_func_total = _count_test_functions(test_files)
    return covered_symbols, len(test_files), test_func_total, str(xml_path)


# ── Static coverage (AST import/call analysis) ────────────────────────────────

def _build_import_map(imports) -> dict[str, str]:
    result: dict[str, str] = {}
    for imp in imports:
        module  = getattr(imp, "module", "") or ""
        names   = getattr(imp, "names",  []) or []
        alias   = getattr(imp, "alias",  "") or ""
        is_from = getattr(imp, "is_from", False)
        if is_from:
            for name in names:
                full = f"{module}.{name}" if module else name
                result[name] = full
            if alias and len(names) == 1:
                result[alias] = f"{module}.{names[0]}" if module else names[0]
        else:
            if alias:
                result[alias] = module
            elif module:
                parts = module.split(".")
                for i in range(len(parts)):
                    prefix = ".".join(parts[: i + 1])
                    result[prefix] = prefix
    return result


def _resolve_callee(callee: str, import_map: dict[str, str]) -> str | None:
    if not callee:
        return None
    parts = callee.split(".")
    base = import_map.get(parts[0])
    if base is None:
        return None
    return base if len(parts) == 1 else f"{base}.{'.'.join(parts[1:])}"


def _scan_test_file(path: Path) -> tuple[set[str], int]:
    from codeindex.parser import parse_file
    try:
        pr = parse_file(path)
    except Exception:
        return set(), 0
    import_map = _build_import_map(pr.imports or [])
    test_func_names: set[str] = {
        getattr(sym, "name", "") for sym in (pr.symbols or [])
        if (getattr(sym, "name", "") or "").split(".")[-1].startswith("test_")
    }
    covered: set[str] = set()
    for call in pr.calls or []:
        caller = getattr(call, "caller", "") or ""
        callee = getattr(call, "callee", "") or ""
        caller_short = caller.split(".")[-1]
        if not (caller_short.startswith("test_") or
                any(tf in caller for tf in test_func_names)):
            continue
        resolved = _resolve_callee(callee, import_map)
        if resolved:
            covered.add(resolved)
    return covered, len(test_func_names)


def _find_test_files(project_path: Path) -> list[Path]:
    files: list[Path] = []
    for name in ("tests", "test"):
        d = project_path / name
        if d.is_dir():
            for pat in ("test_*.py", "*_test.py"):
                files.extend(d.rglob(pat))
    for pat in ("test_*.py", "*_test.py"):
        files.extend(f for f in project_path.glob(pat) if f not in files)
    return list(dict.fromkeys(files))


def _count_test_functions(test_files: list[Path]) -> int:
    from codeindex.parser import parse_file
    total = 0
    for tf in test_files:
        try:
            pr = parse_file(tf)
            for sym in pr.symbols or []:
                if (getattr(sym, "name", "") or "").split(".")[-1].startswith("test_"):
                    total += 1
        except Exception:
            pass
    return total


def _static_coverage(project_path: Path) -> tuple[set[str], int, int]:
    test_files = _find_test_files(project_path)
    covered_all: set[str] = set()
    test_func_total = 0
    for tf in test_files:
        covered, tf_count = _scan_test_file(tf)
        covered_all |= covered
        test_func_total += tf_count
    return covered_all, len(test_files), test_func_total


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: manon-scan-tests.py <repo_id>"}))
        sys.exit(1)

    _bootstrap_scan_runtime()

    from core.ast.project import find_project_by_repo_id
    from core.ast.parser_utils import ensure_parsers

    repo_id = sys.argv[1]
    found = find_project_by_repo_id(repo_id)
    if not found:
        print(json.dumps({"error": f"repo_id {repo_id} not found"}))
        sys.exit(1)

    project_path_str, _ = found
    project_path = Path(project_path_str)
    ensure_parsers(project_path_str)

    # Priority 1: dynamic coverage from coverage.xml
    source = "static"
    dynamic = _dynamic_coverage(project_path)
    if dynamic:
        covered_all, n_files, n_funcs, xml_path = dynamic
        source = f"dynamic:{Path(xml_path).name}"
    else:
        covered_all, n_files, n_funcs = _static_coverage(project_path)

    coverage_data = {
        "version": 1,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "summary": {
            "covered": len(covered_all),
            "test_files": n_files,
            "test_functions": n_funcs,
        },
        "covered": sorted(covered_all),
    }

    SCAN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = SCAN_CACHE_DIR / f"{repo_id}_coverage.json"
    cache_file.write_text(
        json.dumps(coverage_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {**coverage_data["summary"], "source": source}
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

