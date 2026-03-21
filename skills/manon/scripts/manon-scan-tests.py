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
import re
import site
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCAN_CACHE_DIR = Path.home() / ".manon" / "scan_cache"

_TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__"})

# TypeScript/JavaScript test support
_TS_TEST_PATTERNS = (
    "*.test.ts", "*.test.tsx", "*.spec.ts", "*.spec.tsx",
    "*.test.js", "*.test.jsx", "*.spec.js", "*.spec.jsx",
)
_TS_SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", ".turbo", ".next",
    "coverage", "__pycache__", ".opencode", ".cache",
})
_TS_TEST_CALL_RE = re.compile(
    r"""(?:^|[;\s])(?:it|test)\s*(?:\.(?:only|skip|each|todo))?\s*\(""",
    re.MULTILINE,
)
_TS_IMPORT_RE = re.compile(
    r"""^(?:import|export)\s+(?:[^'";\n]*?\s+from\s+)?['"](\.[^'"]+)['"]""",
    re.MULTILINE,
)
_TS_EXPORT_FN_RE = re.compile(
    r"""export\s+(?:async\s+)?function\s+(\w+)|export\s+const\s+(\w+)\s*[=:][^=]"""
)



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


# ── TypeScript/JavaScript static coverage ─────────────────────────────────────

def _find_ts_test_files(project_path: Path) -> list[Path]:
    """Find TS/JS test files, excluding node_modules and build dirs."""
    files: list[Path] = []
    for pat in _TS_TEST_PATTERNS:
        for f in project_path.rglob(pat):
            try:
                parts = f.relative_to(project_path).parts
            except ValueError:
                continue
            if not any(p in _TS_SKIP_DIRS for p in parts):
                files.append(f)
    return list(dict.fromkeys(files))


def _count_ts_test_functions(test_files: list[Path]) -> int:
    total = 0
    for tf in test_files:
        try:
            content = tf.read_text(encoding="utf-8", errors="ignore")
            total += len(_TS_TEST_CALL_RE.findall(content))
        except Exception:
            pass
    return total


def _ts_module_from_path(project_path: Path, file_path: Path) -> str:
    """Convert absolute TS path to dotted module name."""
    try:
        rel = file_path.relative_to(project_path)
    except ValueError:
        return file_path.stem
    s = str(rel).replace("\\", "/")
    for ext in (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"):
        if s.endswith(ext):
            s = s[: -len(ext)]
            break
    if s.endswith("/index"):
        s = s[:-6]
    return s.replace("/", ".")


def _scan_ts_test_file_static(test_path: Path, project_path: Path) -> set[str]:
    """Regex-based: trace relative imports → exported symbols in source files."""
    try:
        content = test_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()

    covered: set[str] = set()
    for match in _TS_IMPORT_RE.finditer(content):
        imp = match.group(1)
        base = (test_path.parent / imp).resolve()
        for ext in (".ts", ".tsx", ".js", ".jsx", ""):
            cand = base.with_suffix(ext) if ext else base
            if not cand.is_file():
                # try index file
                for idx_ext in (".ts", ".tsx", ".js"):
                    idx = base / f"index{idx_ext}"
                    if idx.is_file():
                        cand = idx
                        break
                else:
                    continue
            try:
                parts = cand.relative_to(project_path).parts
            except ValueError:
                continue
            if any(p in _TS_SKIP_DIRS for p in parts):
                continue
            module = _ts_module_from_path(project_path, cand)
            try:
                src = cand.read_text(encoding="utf-8", errors="ignore")
                found_any = False
                for m in _TS_EXPORT_FN_RE.finditer(src):
                    fn_name = m.group(1) or m.group(2)
                    if fn_name:
                        covered.add(f"{module}.{fn_name}")
                        found_any = True
                if not found_any:
                    covered.add(f"{module}.__module__")
            except Exception:
                covered.add(f"{module}.__module__")
            break
    return covered


def _static_coverage_ts(project_path: Path) -> tuple[set[str], int, int]:
    """TypeScript static coverage: co-located tests + import tracing."""
    test_files = _find_ts_test_files(project_path)
    covered_all: set[str] = set()
    for tf in test_files:
        covered_all |= _scan_ts_test_file_static(tf, project_path)
    n_funcs = _count_ts_test_functions(test_files)
    return covered_all, len(test_files), n_funcs


# ── Istanbul/nyc JSON coverage ─────────────────────────────────────────────────

def _find_istanbul_coverage(project_path: Path) -> Path | None:
    for candidate in (
        project_path / "coverage" / "coverage-summary.json",
        project_path / "coverage-summary.json",
        project_path / ".nyc_output" / "coverage-summary.json",
    ):
        if candidate.exists():
            return candidate
    return None


def _parse_istanbul_coverage(
    coverage_json: Path, project_path: Path
) -> tuple[set[str], int, int]:
    try:
        data = json.loads(coverage_json.read_text(encoding="utf-8"))
    except Exception:
        return set(), 0, 0
    covered: set[str] = set()
    for file_path_str, stats in data.items():
        if file_path_str == "total":
            continue
        fn_pct = stats.get("functions", {}).get("pct", 0)
        if fn_pct == 0:
            continue
        module = _ts_module_from_path(project_path, Path(file_path_str))
        covered.add(f"{module}.__istanbul__")
    test_files = _find_ts_test_files(project_path)
    n_funcs = _count_ts_test_functions(test_files)
    return covered, len(test_files), n_funcs


# ── lcov coverage parser (bun / jest / vitest) ────────────────────────────────

def _find_lcov(project_path: Path) -> Path | None:
    # Direct candidates at root and common sub-package locations
    candidates = [
        project_path / "coverage" / "lcov.info",
        project_path / "lcov.info",
        project_path / "coverage" / "lcov" / "lcov.info",
    ]
    # Also search one level of sub-packages (monorepo pattern)
    packages_dir = project_path / "packages"
    if packages_dir.is_dir():
        for sub in packages_dir.iterdir():
            if sub.is_dir():
                candidates.append(sub / "coverage" / "lcov.info")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


_LCOV_FN_RE = re.compile(
    r"""^(?:export\s+)?(?:async\s+)?function\s+(\w+)|"""
    r"""^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(""",
    re.MULTILINE,
)


def _parse_lcov(lcov_path: Path, project_path: Path) -> tuple[set[str], int, int]:
    """
    Parse lcov.info → covered function symbols.
    Bun emits DA:<line>,<count> (line-level) but no FN:/FNDA: (function names).
    Strategy: collect covered lines per file, then regex-match function definitions
    whose start line falls in the covered set.
    """
    covered: set[str] = set()
    # Bun writes lcov to <work_dir>/coverage/lcov.info but SF paths are relative to <work_dir>
    lcov_work_dir = lcov_path.parent.parent  # packages/yourcoder/coverage/ → packages/yourcoder/

    def _flush_file(sf_raw: str, clines: set[int]) -> None:
        if not sf_raw or not clines:
            return
        fp = Path(sf_raw)
        if not fp.is_absolute():
            # Try: work_dir (where bun ran) → project root → give up
            for base in (lcov_work_dir, project_path):
                candidate = (base / sf_raw).resolve()
                if candidate.exists():
                    fp = candidate
                    break
            else:
                return  # not found under any base
        if not fp.exists():
            return
        try:
            parts = fp.relative_to(project_path).parts
        except ValueError:
            return
        if any(p in _TS_SKIP_DIRS for p in parts):
            return
        module = _ts_module_from_path(project_path, fp)
        try:
            src = fp.read_text(encoding="utf-8", errors="ignore")
            found_any = False
            for m in _LCOV_FN_RE.finditer(src):
                fn_name = m.group(1) or m.group(2)
                if not fn_name:
                    continue
                line_no = src[: m.start()].count("\n") + 1
                if line_no in clines:
                    covered.add(f"{module}.{fn_name}")
                    found_any = True
            # Fallback: file has covered lines but no regex-matched functions → mark module
            if not found_any and len(clines) >= 3:
                covered.add(f"{module}.__module__")
        except Exception:
            pass

    try:
        raw = lcov_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return set(), 0, 0

    current_sf: str | None = None
    covered_lines: set[int] = set()

    for line in raw:
        if line.startswith("SF:"):
            if current_sf is not None:
                _flush_file(current_sf, covered_lines)
            current_sf = line[3:].strip()
            covered_lines = set()
        elif line.startswith("DA:") and current_sf is not None:
            # DA:<line_number>,<execution_count>
            parts = line[3:].split(",", 1)
            if len(parts) == 2 and parts[1].strip() not in ("0", ""):
                try:
                    covered_lines.add(int(parts[0]))
                except ValueError:
                    pass
        elif line == "end_of_record":
            if current_sf is not None:
                _flush_file(current_sf, covered_lines)
            current_sf = None
            covered_lines = set()

    if current_sf is not None:
        _flush_file(current_sf, covered_lines)

    test_files = _find_ts_test_files(project_path)
    n_funcs = _count_ts_test_functions(test_files)
    return covered, len(test_files), n_funcs


# ── Auto test runner ───────────────────────────────────────────────────────────

def _detect_runner(project_path: Path) -> tuple[str, Path] | None:
    """Return (runner_type, work_dir) or None. Prefers sub-package with test script."""
    import shutil

    # Walk sub-packages first (monorepo pattern)
    packages_dir = project_path / "packages"
    candidates = list(packages_dir.glob("*/")) if packages_dir.is_dir() else []
    candidates.insert(0, project_path)

    for work_dir in candidates:
        pkg = work_dir / "package.json"
        if pkg.exists():
            try:
                d = json.loads(pkg.read_text(encoding="utf-8"))
                if not d.get("scripts", {}).get("test"):
                    continue
            except Exception:
                continue
            # Prefer bun if lockfile or bunfig present
            if (work_dir / "bun.lockb").exists() or (work_dir / "bunfig.toml").exists():
                if shutil.which("bun"):
                    return "bun", work_dir
            if shutil.which("bun"):
                return "bun", work_dir
            if shutil.which("npm"):
                return "npm", work_dir

    # Python fallback
    for marker in ("pytest.ini", "conftest.py", "pyproject.toml", "setup.cfg"):
        if (project_path / marker).exists():
            if shutil.which("pytest") or True:  # sys.executable always available
                return "pytest", project_path

    return None


def _run_tests_for_coverage(
    project_path: Path,
) -> tuple[str | None, Path | None]:
    """
    Run full test suite with coverage. Returns (cov_type, cov_path) or (None, None).
    Per-test timeout kills hanging tests individually; process cap is 60s.
    """
    detected = _detect_runner(project_path)
    if detected is None:
        return None, None

    runner, work_dir = detected
    cov_dir = work_dir / "coverage"

    if runner in ("bun", "npm"):
        import shutil
        bun_path = shutil.which("bun")
        if bun_path is None:
            return None, None

        lcov_out = cov_dir / "lcov.info"
        # 30s hard cap; bun writes lcov on normal exit (pass or fail).
        # Errors/failures are bun's problem — we just use whatever lcov appears.
        args = [
            bun_path, "test",
            "--coverage",
            "--coverage-reporter=lcov",
            f"--coverage-dir={cov_dir}",
        ]
        use_shell = os.name == "nt" and bun_path.lower().endswith(".cmd")
        cmd_str = " ".join(f'"{a}"' if " " in str(a) else str(a) for a in args)
        try:
            subprocess.run(
                cmd_str if use_shell else args,
                cwd=str(work_dir),
                capture_output=True, text=True,
                timeout=30,
                shell=use_shell,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
        if lcov_out.exists():
            return "lcov", lcov_out

    elif runner == "pytest":
        xml_out = project_path / "coverage.xml"
        cmd = [sys.executable, "-m", "pytest",
               "--cov", "--cov-report=xml", "-q", "--tb=no"]
        try:
            subprocess.run(
                cmd, cwd=str(project_path),
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
        if xml_out.exists():
            return "xml", xml_out

    return None, None


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

    run_full = "--run-tests" in sys.argv

    source = "static"
    covered_all: set[str] = set()
    n_files = n_funcs = 0

    def _apply_lcov(path: Path, label: str) -> bool:
        nonlocal covered_all, n_files, n_funcs, source
        c, f, fn = _parse_lcov(path, project_path)
        if f > 0:
            covered_all, n_files, n_funcs, source = c, f, fn, label
            return True
        return False

    def _apply_xml(label: str) -> bool:
        nonlocal covered_all, n_files, n_funcs, source
        d = _dynamic_coverage(project_path)
        if d:
            covered_all, n_files, n_funcs, _ = d
            source = label
            return True
        return False

    if run_full:
        # ── Explicit full run (--run-tests): force refresh, skip cached lcov ─
        cov_type, cov_path = _run_tests_for_coverage(project_path)
        if cov_type == "lcov" and cov_path:
            _apply_lcov(cov_path, "dynamic:lcov(full)")
        elif cov_type == "xml":
            _apply_xml("dynamic:xml(full)")

    else:
        # ── Normal init path: read cached → run if missing ───────────────────
        # P1: coverage.xml (pytest)
        d = _dynamic_coverage(project_path)
        if d:
            covered_all, n_files, n_funcs, xml_path = d
            source = f"dynamic:{Path(xml_path).name}"

        # P2: Istanbul/nyc JSON
        if n_files == 0:
            istanbul = _find_istanbul_coverage(project_path)
            if istanbul:
                covered_all, n_files, n_funcs = _parse_istanbul_coverage(istanbul, project_path)
                source = "dynamic:istanbul"

        # P3: lcov.info (whatever exists on disk — unit or full)
        if n_files == 0:
            lcov = _find_lcov(project_path)
            if lcov:
                _apply_lcov(lcov, "dynamic:lcov")

        # P4: no coverage on disk → run tests (full suite, 60s cap, 10s per-test timeout)
        if n_files == 0:
            cov_type, cov_path = _run_tests_for_coverage(project_path)
            if cov_type == "lcov" and cov_path:
                _apply_lcov(cov_path, "dynamic:lcov(auto)")
            elif cov_type == "xml":
                _apply_xml("dynamic:xml(auto)")

    # ── Static fallback (both paths) ─────────────────────────────────────────
    # P5: TypeScript static import tracing
    if n_files == 0:
        ts_covered, ts_files, ts_funcs = _static_coverage_ts(project_path)
        if ts_files > 0:
            covered_all, n_files, n_funcs, source = ts_covered, ts_files, ts_funcs, "static:ts"

    # P6: Python static AST analysis
    if n_files == 0:
        covered_all, n_files, n_funcs = _static_coverage(project_path)
        source = "static:py"

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

