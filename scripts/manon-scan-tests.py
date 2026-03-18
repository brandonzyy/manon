#!/usr/bin/env python3
"""manon-scan-tests.py — side-channel test coverage scanner.

Usage:
    python <MANON_DIR>/scripts/manon-scan-tests.py <repo_id>

Scans tests/ (and test/) directory using AST, extracts which production
symbols are called from test functions, writes coverage_map.json to scan
cache. Does NOT index test files into the knowledge graph.

Output (stdout): JSON summary { covered, test_files, test_functions }
Cache: ~/.manon/scan_cache/<repo_id>_coverage.json
"""
from __future__ import annotations

import json
import os
import site
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
VENV_DIR = PROJECT_ROOT / ".venv"
REQ_FILE = PROJECT_ROOT / "manon_mcp" / "requirements.txt"
SCAN_CACHE_DIR = Path.home() / ".manon" / "scan_cache"


# ── Runtime bootstrap (mirrors manon-scan.py) ─────────────────────────────────

def _venv_site_packages() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Lib" / "site-packages"
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return VENV_DIR / "lib" / version / "site-packages"


def _scan_runtime_ready() -> bool:
    site_packages = _venv_site_packages()
    if not site_packages.exists():
        return False
    site.addsitedir(str(site_packages))
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
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


# ── Import resolution ──────────────────────────────────────────────────────────

def _build_import_map(imports) -> dict[str, str]:
    """Build local_name → full_qualified_path from a ParseResult's imports."""
    result: dict[str, str] = {}
    for imp in imports:
        module = getattr(imp, "module", "") or ""
        names  = getattr(imp, "names",  []) or []
        alias  = getattr(imp, "alias",  "") or ""
        is_from = getattr(imp, "is_from", False)

        if is_from:
            # from module import name1, name2 [as alias]
            for name in names:
                full = f"{module}.{name}" if module else name
                result[name] = full
            # single-name alias: from module import name as alias
            if alias and len(names) == 1:
                result[alias] = f"{module}.{names[0]}" if module else names[0]
        else:
            if alias:
                # import module as alias
                result[alias] = module
            elif module:
                # import a.b.c  → register all prefix lengths
                parts = module.split(".")
                for i in range(len(parts)):
                    prefix = ".".join(parts[: i + 1])
                    result[prefix] = prefix
    return result


def _resolve(callee: str, import_map: dict[str, str]) -> str | None:
    """Resolve a callee local name to its full qualified path, or None."""
    if not callee:
        return None
    parts = callee.split(".")
    base = import_map.get(parts[0])
    if base is None:
        return None
    if len(parts) == 1:
        return base
    return f"{base}.{'.'.join(parts[1:])}"


# ── Per-file scanner ───────────────────────────────────────────────────────────

def _scan_test_file(path: Path) -> tuple[set[str], int]:
    """Return (covered_full_paths, test_function_count) for one test file."""
    from codeindex.parser import parse_file

    try:
        pr = parse_file(path)
    except Exception:
        return set(), 0

    import_map = _build_import_map(pr.imports or [])

    # Collect test function names (handles plain functions and class methods)
    test_func_names: set[str] = set()
    for sym in pr.symbols or []:
        name = getattr(sym, "name", "") or ""
        # match test_* or *::test_* style
        short = name.split(".")[-1]
        if short.startswith("test_") or short.startswith("Test"):
            test_func_names.add(name)

    covered: set[str] = set()
    for call in pr.calls or []:
        caller = getattr(call, "caller", "") or ""
        callee = getattr(call, "callee", "") or ""

        # Only care about calls originating inside test functions
        caller_short = caller.split(".")[-1]
        if not (caller_short.startswith("test_") or
                any(tf in caller for tf in test_func_names)):
            continue

        resolved = _resolve(callee, import_map)
        if resolved:
            covered.add(resolved)

    return covered, len(test_func_names)


# ── Directory discovery ────────────────────────────────────────────────────────

def _find_test_files(project_path: Path) -> list[Path]:
    """Find all test .py files under standard test directories."""
    test_dirs = [d for name in ("tests", "test")
                 if (d := project_path / name).is_dir()]

    files: list[Path] = []
    for td in test_dirs:
        for pat in ("test_*.py", "*_test.py"):
            files.extend(td.rglob(pat))

    # Also pick up top-level test files
    for pat in ("test_*.py", "*_test.py"):
        files.extend(f for f in project_path.glob(pat) if f not in files)

    return list(dict.fromkeys(files))  # deduplicate, preserve order


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

    test_files = _find_test_files(project_path)

    covered_all: set[str] = set()
    test_func_total = 0

    for tf in test_files:
        covered, tf_count = _scan_test_file(tf)
        covered_all |= covered
        test_func_total += tf_count

    coverage_data = {
        "version": 1,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "covered": len(covered_all),
            "test_files": len(test_files),
            "test_functions": test_func_total,
        },
        "covered": sorted(covered_all),
    }

    SCAN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = SCAN_CACHE_DIR / f"{repo_id}_coverage.json"
    cache_file.write_text(
        json.dumps(coverage_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(coverage_data["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
