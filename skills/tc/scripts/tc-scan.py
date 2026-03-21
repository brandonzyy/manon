#!/usr/bin/env python3
"""tc-scan.py — Coverage scanner with Manon graph priority ranking.

Commands:
  scan <project_path> <repo_id>   → JSON with coverage summary + priority targets

Reads lcov.info (bun/jest/vitest) and cross-references with Manon graph
to rank uncovered functions by importance (fan-in from callers).

Output JSON:
  {
    "summary": {
      "lines_hit": 14233, "lines_total": 20344, "line_pct": 69.9,
      "functions_hit": 2021, "functions_total": 2534, "function_pct": 79.7,
      "files_covered": 161, "files_total": 277
    },
    "targets": [
      {"file": "src/foo.ts", "function": "bar", "line_pct": 30.0,
       "fan_in": 8, "priority": "critical", "score": 24.0}
    ]
  }
"""
import json
import os
import re
import sys
from pathlib import Path
from urllib import error as url_error
from urllib import request

# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg_path = Path.home() / ".manon" / "config.json"
    return json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

def api_headers(cfg: dict) -> dict:
    h = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        h["Authorization"] = f"Bearer {cfg['api_key']}"
    return h

def api_get(url: str, headers: dict) -> dict:
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


# ── lcov parser ───────────────────────────────────────────────────────────────

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", ".turbo", ".next",
    "coverage", "__pycache__", ".opencode", ".cache",
})

_EXPORT_FN_RE = re.compile(
    r"""^(?:export\s+)?(?:async\s+)?function\s+(\w+)"""
    r"""|^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*="""
    r"""|\s+(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*\S+\s*)?\{""",
    re.MULTILINE,
)


def find_lcov(project_path: Path) -> Path | None:
    """Find lcov.info in project or sub-packages."""
    candidates = [
        project_path / "coverage" / "lcov.info",
        project_path / "lcov.info",
    ]
    packages_dir = project_path / "packages"
    if packages_dir.is_dir():
        for sub in packages_dir.iterdir():
            if sub.is_dir():
                candidates.append(sub / "coverage" / "lcov.info")
    for c in candidates:
        if c.exists():
            return c
    return None


def parse_lcov(lcov_path: Path, project_path: Path) -> dict:
    """Parse lcov.info → per-file coverage data.

    Returns {
        "files": {rel_path: {"lh": N, "lf": N, "fnh": N, "fnf": N}},
        "summary": {lines_hit, lines_total, ...}
    }
    """
    lcov_work_dir = lcov_path.parent.parent  # coverage/ → package root

    try:
        raw = lcov_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return {"files": {}, "summary": _empty_summary()}

    files: dict[str, dict] = {}
    current_sf: str | None = None
    lh = lf = fnh = fnf = 0

    def _flush():
        nonlocal current_sf, lh, lf, fnh, fnf
        if current_sf is None:
            return
        # Resolve to relative path
        fp = Path(current_sf)
        if not fp.is_absolute():
            for base in (lcov_work_dir, project_path):
                candidate = (base / current_sf).resolve()
                if candidate.exists():
                    fp = candidate
                    break
        try:
            rel = str(fp.relative_to(project_path)).replace("\\", "/")
        except ValueError:
            current_sf = None
            lh = lf = fnh = fnf = 0
            return
        # Skip test files and build dirs
        parts = rel.split("/")
        if any(p in _SKIP_DIRS for p in parts):
            current_sf = None
            lh = lf = fnh = fnf = 0
            return
        basename = parts[-1]
        if ".test." in basename or ".spec." in basename or basename.startswith("test_"):
            current_sf = None
            lh = lf = fnh = fnf = 0
            return

        files[rel] = {"lh": lh, "lf": lf, "fnh": fnh, "fnf": fnf}
        current_sf = None
        lh = lf = fnh = fnf = 0

    for line in raw:
        if line.startswith("SF:"):
            _flush()
            current_sf = line[3:].strip()
        elif line.startswith("LH:"):
            try: lh = int(line[3:].strip())
            except ValueError: pass
        elif line.startswith("LF:"):
            try: lf = int(line[3:].strip())
            except ValueError: pass
        elif line.startswith("FNH:"):
            try: fnh = int(line[4:].strip())
            except ValueError: pass
        elif line.startswith("FNF:"):
            try: fnf = int(line[4:].strip())
            except ValueError: pass
        elif line == "end_of_record":
            _flush()

    _flush()

    # Build summary
    total_lh = sum(f["lh"] for f in files.values())
    total_lf = sum(f["lf"] for f in files.values())
    total_fnh = sum(f["fnh"] for f in files.values())
    total_fnf = sum(f["fnf"] for f in files.values())
    files_covered = sum(1 for f in files.values() if f["lh"] > 0)

    summary = {
        "lines_hit": total_lh, "lines_total": total_lf,
        "line_pct": round(total_lh / max(total_lf, 1) * 100, 1),
        "functions_hit": total_fnh, "functions_total": total_fnf,
        "function_pct": round(total_fnh / max(total_fnf, 1) * 100, 1),
        "files_covered": files_covered, "files_total": len(files),
    }
    return {"files": files, "summary": summary}


def _empty_summary():
    return {
        "lines_hit": 0, "lines_total": 0, "line_pct": 0.0,
        "functions_hit": 0, "functions_total": 0, "function_pct": 0.0,
        "files_covered": 0, "files_total": 0,
    }


# ── Uncovered function extraction ─────────────────────────────────────────────

def extract_uncovered_functions(
    project_path: Path, file_coverage: dict[str, dict]
) -> list[dict]:
    """Find functions in poorly-covered files by regex-scanning source."""
    targets: list[dict] = []
    for rel_path, cov in file_coverage.items():
        line_pct = cov["lh"] / max(cov["lf"], 1) * 100
        if line_pct > 80:
            continue  # well-covered, skip
        abs_path = project_path / rel_path
        if not abs_path.exists():
            continue
        try:
            src = abs_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in _EXPORT_FN_RE.finditer(src):
            fn_name = m.group(1) or m.group(2) or m.group(3)
            if not fn_name or fn_name.startswith("_"):
                continue
            targets.append({
                "file": rel_path,
                "function": fn_name,
                "line_pct": round(line_pct, 1),
                "fan_in": 0,
                "priority": "normal",
                "score": 0.0,
            })
    return targets


# ── Graph enrichment ──────────────────────────────────────────────────────────

def enrich_with_graph(
    targets: list[dict], api_base: str, repo_id: str, headers: dict
) -> list[dict]:
    """Add fan-in data from Manon graph to rank targets by importance."""
    # Batch: query unique function names (limit API calls)
    seen_fns: dict[str, int] = {}  # fn_name → fan_in
    for t in targets[:50]:  # limit
        fn = t["function"]
        if fn in seen_fns:
            t["fan_in"] = seen_fns[fn]
            continue
        url = f"{api_base}/api/v1/repos/{repo_id}/graph?symbol={fn}&depth=1&direction=callers"
        result = api_get(url, headers)
        callers = len(result.get("relations", []))
        seen_fns[fn] = callers
        t["fan_in"] = callers

    # Score and rank
    for t in targets:
        fan_in = t["fan_in"]
        fan_weight = 3 if fan_in >= 5 else 2 if fan_in >= 2 else 1
        line_pct = t["line_pct"]
        cov_weight = 3 if line_pct == 0 else 2 if line_pct <= 50 else 1
        t["score"] = round(fan_weight * cov_weight, 1)
        t["priority"] = "critical" if t["score"] >= 6 else "important" if t["score"] >= 3 else "normal"

    # Deduplicate: keep highest-scoring entry per file
    best_per_file: dict[str, dict] = {}
    for t in targets:
        key = t["file"]
        if key not in best_per_file or t["score"] > best_per_file[key]["score"]:
            best_per_file[key] = t
    targets = sorted(best_per_file.values(), key=lambda t: -t["score"])

    return targets[:20]  # max 20


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_scan(project_path: str, repo_id: str):
    root = Path(project_path)
    cfg = load_config()
    api_base = cfg.get("api_url", "http://saas.matrixone.online:3700").rstrip("/")
    headers = api_headers(cfg)

    # Find and parse lcov
    lcov = find_lcov(root)
    if not lcov:
        print(json.dumps({
            "error": "no lcov.info found",
            "hint": "run: bun test --coverage --coverage-reporter=lcov",
            "summary": _empty_summary(),
            "targets": [],
        }, ensure_ascii=False))
        return

    parsed = parse_lcov(lcov, root)
    summary = parsed["summary"]

    # Extract uncovered functions
    targets = extract_uncovered_functions(root, parsed["files"])

    # Enrich with graph fan-in
    if targets:
        targets = enrich_with_graph(targets, api_base, repo_id, headers)

    result = {"summary": summary, "targets": targets, "lcov_path": str(lcov)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ── Main ──────────────────────────────────────────────────────────────────────

COMMANDS = {
    "scan": lambda a: cmd_scan(a[0], a[1]),
}

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    args = sys.argv[1:]
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    try:
        COMMANDS[args[0]](args[1:])
    except (IndexError, TypeError):
        print("Usage: tc-scan.py scan <project_path> <repo_id>")
        sys.exit(1)

if __name__ == "__main__":
    main()
