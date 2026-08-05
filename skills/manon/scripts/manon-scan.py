#!/usr/bin/env python3
"""Manon scan script — run AST extraction outside MCP process.

Usage:
    python <MANON_DIR>/scripts/manon-scan.py <repo_id>

Reads project_path and old_hashes from ~/.manon/projects.json,
runs ensure_parsers + scan_and_parse, writes results to
~/.manon/scan_cache/<repo_id>.json, prints JSON summary to stdout.
"""
from __future__ import annotations

import json
import math
import os
import site
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCAN_CACHE_DIR = Path.home() / ".manon" / "scan_cache"
_MANON_CONFIG = Path.home() / ".manon" / "config.json"


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


def _venv_site_packages() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Lib" / "site-packages"
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return VENV_DIR / "lib" / version / "site-packages"


def _scan_runtime_ready() -> bool:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    # Try direct import first — succeeds when run via MANON_PYTHON (venv already has deps)
    try:
        import httpx  # noqa: F401
        import yaml  # noqa: F401
        return True
    except ImportError:
        pass

    # Fallback: activate local .venv
    site_packages = _venv_site_packages()
    if not site_packages.exists():
        return False
    site.addsitedir(str(site_packages))
    try:
        import httpx  # noqa: F401
        import yaml  # noqa: F401
    except Exception:
        return False
    return True


def _repair_scan_runtime() -> None:
    subprocess.run(
        [sys.executable, "-m", "venv", str(VENV_DIR), "--clear"],
        check=True,
        cwd=str(PROJECT_ROOT),
    )

    site_packages = _venv_site_packages()
    site_packages.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-q",
            "--disable-pip-version-check",
            "--upgrade",
            "--target",
            str(site_packages),
            "-r",
            str(REQ_FILE),
        ],
        check=True,
        cwd=str(PROJECT_ROOT),
    )


def _bootstrap_scan_runtime() -> None:
    if _scan_runtime_ready():
        return
    _repair_scan_runtime()
    if not _scan_runtime_ready():
        raise RuntimeError("failed to bootstrap scan runtime")


# ── Script classification ─────────────────────────────────────────────────────

def _load_manon_config() -> dict:
    try:
        return json.loads(_MANON_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _classify_uncertain_via_api(
    uncertain: list[dict], api_url: str, api_key: str,
) -> dict[str, str]:
    """Call /api/v1/classify-scripts with uncertain file summaries.

    Returns {rel_path: "source_code" | "tool_script"}.
    Falls back to empty dict on any error (uncertain files are kept).
    """
    import httpx

    from core.script_classifier import ScriptClassifier, ScriptSignals

    # project_packages: deduce from top-level dirs containing __init__.py
    classifier = ScriptClassifier([])  # packages not needed for make_summary

    summaries = []
    for f in uncertain:
        signals = ScriptSignals(f["rel_path"], parse_result=f.get("parse_result"))
        summaries.append(classifier.make_summary(signals))

    payload = {"files": summaries}
    url = api_url.rstrip("/") + "/api/v1/classify-scripts"

    try:
        # trust_env=False: the Manon API is plain HTTP, and a CONNECT-only
        # proxy rejects it with 405. run_mcp.py clears the proxy vars for the
        # same reason; this script runs standalone, so scope it to this call.
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
            trust_env=False,
        )
        resp.raise_for_status()
        return resp.json().get("results", {})
    except Exception as e:
        print(f"[classify] API call failed (skipping LLM tiebreaker): {e}", file=sys.stderr)
        return {}


def _classify_file_results(
    file_results: list[dict], project_path: str,
) -> tuple[list[dict], int]:
    """Filter tool scripts from file_results.

    Returns (filtered_results, dropped_count).
    """
    from core.script_classifier import (
        ScriptClassifier, ScriptSignals,
        build_imported_paths,
    )

    # Detect project packages across all languages
    # Python: __init__.py, TS/JS: package.json, Go: go.mod, Rust: Cargo.toml, etc.
    project_root = Path(project_path)
    _PACKAGE_MARKERS = ("__init__.py", "package.json", "Cargo.toml", "go.mod",
                        "build.gradle", "pom.xml", "mix.exs", "pubspec.yaml")
    project_packages = []
    for d in project_root.iterdir():
        if not d.is_dir():
            continue
        if any((d / m).exists() for m in _PACKAGE_MARKERS):
            project_packages.append(d.name)
    # Also detect from root package manifests
    for manifest, key in [("package.json", "name"), ("Cargo.toml", None), ("go.mod", None)]:
        mf = project_root / manifest
        if not mf.exists():
            continue
        try:
            import json as _json
            if manifest == "package.json":
                pkg = _json.loads(mf.read_text(encoding="utf-8"))
                pkg_name = pkg.get("name", "")
                if pkg_name and pkg_name not in project_packages:
                    project_packages.append(pkg_name)
            elif manifest == "go.mod":
                # "module github.com/org/repo" → "repo"
                for line in mf.read_text(encoding="utf-8").splitlines():
                    if line.startswith("module "):
                        mod_name = line.split()[-1].split("/")[-1]
                        if mod_name and mod_name not in project_packages:
                            project_packages.append(mod_name)
                        break
        except Exception:
            pass

    classifier = ScriptClassifier(project_packages)
    imported_paths = build_imported_paths(file_results, project_root)

    # First pass: definitive classification
    keep, uncertain = classifier.classify_batch(file_results, imported_paths)

    if not uncertain:
        return keep, 0

    # Second pass: LLM tiebreaker for uncertain files
    cfg = _load_manon_config()
    api_url = (
        os.environ.get("MANON_API_URL")
        or cfg.get("api_url")
        or "http://saas.matrixone.online:3700"
    )
    api_key = os.environ.get("MANON_API_KEY") or cfg.get("api_key", "")

    dropped = 0
    if api_key:
        llm_results = _classify_uncertain_via_api(uncertain, api_url, api_key)
        for f in uncertain:
            label = llm_results.get(f["rel_path"])
            if label == "tool_script":
                dropped += 1
            else:
                # source_code or unknown → keep (conservative)
                keep.append(f)
    else:
        # No API key → keep all uncertain (conservative)
        keep.extend(uncertain)

    return keep, dropped


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: manon-scan.py <repo_id>"}))
        sys.exit(1)

    _bootstrap_scan_runtime()

    from core.ast.project import find_project_by_repo_id
    from core.ast.parser_utils import ensure_parsers
    from core.ast.scanner import SYNC_BATCH_SIZE, scan_and_parse

    repo_id = sys.argv[1]

    # Look up project
    found = find_project_by_repo_id(repo_id)
    if not found:
        print(json.dumps({"error": f"repo_id {repo_id} not found in projects.json"}))
        sys.exit(1)

    project_path, info = found
    old_hashes = info.get("file_hashes", {})

    # Ensure tree-sitter parsers are installed
    ensure_parsers(project_path)

    # Load mtime stat cache for fast-path (skip SHA256 on unchanged files)
    SCAN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stats_cache_file = SCAN_CACHE_DIR / f"{repo_id}_stats.json"
    try:
        stat_cache: dict = json.loads(stats_cache_file.read_text(encoding="utf-8"))
    except Exception:
        stat_cache = {}

    # Scan and parse all changed files
    file_results, deleted, new_hashes = scan_and_parse(
        project_path, old_hashes, max_files=0, stat_cache=stat_cache,
    )

    # Save updated stat cache
    try:
        stats_cache_file.write_text(json.dumps(stat_cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    # Classify: filter tool scripts from source files
    file_results, dropped = _classify_file_results(file_results, project_path)

    total_files = len(file_results)
    deleted_files = len(deleted)
    total_batches = max(math.ceil(total_files / SYNC_BATCH_SIZE), 1) if (total_files or deleted) else 0

    # Write cache to disk
    cache_file = SCAN_CACHE_DIR / f"{repo_id}.json"
    cache_data = {
        "file_results": file_results,
        "deleted": deleted,
        "new_hashes": new_hashes,
        "old_hashes": dict(old_hashes),
        "total_batches": total_batches,
        "project_path": project_path,
    }
    cache_file.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")

    # Output summary to stdout
    summary = {
        "total_files": total_files,
        "deleted_files": deleted_files,
        "total_batches": total_batches,
        "tool_scripts_dropped": dropped,
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
