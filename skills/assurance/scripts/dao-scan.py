#!/usr/bin/env python3
"""Fetch code health and context for the dao skill — replaces MCP tool calls.

Commands:
  context <project_path> <repo_id>   → JSON with health scores, scan checklist,
                                        changed files, and report state

Output JSON:
  {
    "health": {
      "score": 94.0,
      "grade": "A",
      "dimensions": [{"abbr": "MC", "name": "...", "value": 8.0, ...}]
    },
    "scan_checklist": {
      "A": [{"code": "A1", "name": "Unnecessary layers", "priority": "high",
             "signal": "FI=7.2 < 9 — fan-in hotspot"}, ...],
      "M": [...],
      "C": [...]
    },
    "report_exists": true,
    "open_issues": 3,
    "changed_files": ["saas/foo.py", ...]
  }

All 19 principles are always present. Health dimensions elevate specific ones
to "high" priority — guiding where to focus the deep_query, not restricting it.
"""
import json
import subprocess
import sys
from pathlib import Path
from urllib import request, error as url_error


# ── 19 principles (complete taxonomy) ─────────────────────────────────────────

PRINCIPLES = {
    "A": [
        ("A1", "Unnecessary layers"),
        ("A2", "Over-modularization"),
        ("A3", "Premature generalization"),
        ("A4", "Over-decoupling"),
        ("A5", "Config complexity"),
        ("A6", "Event system overkill"),
        ("A7", "Over-patterning"),
    ],
    "M": [
        ("M1", "Feature bloat"),
        ("M2", "Unclear boundaries"),
        ("M3", "Duplication"),
        ("M4", "Excessive dependencies"),
    ],
    "C": [
        ("C1", "Indirection / barrel exports"),
        ("C2", "Over-fragmentation"),
        ("C3", "Deep directory nesting"),
        ("C4", "Dead code"),
        ("C5", "Split by tech layer"),
        ("C6", "Unnecessary abstraction"),
        ("C7", "Circular dependencies"),
        ("C8", "Low cohesion"),
    ],
}

# Health dimension → principles it signals (score threshold, [principle codes])
DIMENSION_SIGNALS = {
    "DC": (10, ["C4"]),
    "CD": (10, ["C7", "A1"]),
    "MC": (9,  ["M4", "M2", "A4"]),
    "FI": (9,  ["M1", "A1"]),
    "FS": (9,  ["C8"]),
    "TD": (9,  ["C6", "C1"]),
    "MF": (9,  ["A2", "C2", "C3"]),
    "RE": (9,  ["C1", "C6", "A3"]),
}


# ── config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg_path = Path.home() / ".manon" / "config.json"
    if not cfg_path.exists():
        return {}
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def api_headers(cfg: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    return headers


# ── API call ──────────────────────────────────────────────────────────────────

def fetch_code_health(api_url: str, repo_id: str, headers: dict) -> dict:
    url = f"{api_url.rstrip('/')}/api/v1/repos/{repo_id}/code-health"
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except url_error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from e
    except url_error.URLError as e:
        raise RuntimeError(f"Cannot reach {url}: {e.reason}") from e


# ── scan checklist ────────────────────────────────────────────────────────────

def build_scan_checklist(dimensions: list[dict]) -> dict:
    """Return all 19 principles with health-derived priority signals."""
    dim_map = {d["abbr"]: d for d in dimensions}

    signals: dict[str, list[str]] = {}
    for abbr, (threshold, codes) in DIMENSION_SIGNALS.items():
        dim = dim_map.get(abbr)
        if dim and dim.get("value", 10) < threshold:
            msg = f"{abbr}={dim['value']:.1f} < {threshold} — {dim.get('name', abbr)}"
            for code in codes:
                signals.setdefault(code, []).append(msg)

    checklist: dict[str, list[dict]] = {}
    for layer, items in PRINCIPLES.items():
        checklist[layer] = []
        for code, name in items:
            entry: dict = {"code": code, "name": name, "priority": "normal"}
            if code in signals:
                entry["priority"] = "high"
                entry["signal"] = "; ".join(signals[code])
            checklist[layer].append(entry)

    return checklist


# ── report state ──────────────────────────────────────────────────────────────

def read_report_state(project_path: str) -> tuple[bool, int]:
    issues_file = Path(project_path) / ".dao" / "issues.json"
    if not issues_file.exists():
        return False, 0
    issues = json.loads(issues_file.read_text(encoding="utf-8"))
    open_count = sum(1 for i in issues if i.get("status") != "✅ 已完成")
    return True, open_count


# ── changed files ─────────────────────────────────────────────────────────────

def _git(project_path: str, *args: str) -> str:
    """Run git command, return stdout; return '' on error (cross-platform)."""
    r = subprocess.run(
        ["git", "-C", project_path, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.stdout if r.returncode == 0 else ""


def get_changed_files(project_path: str) -> list[str]:
    last_run_file = Path(project_path) / ".dao" / ".last-run"
    if last_run_file.exists():
        since = last_run_file.read_text().strip()
        out = _git(project_path, "log", "--name-only", "--pretty=format:", f"--after={since}")
    else:
        out = _git(project_path, "diff", "--name-only", "HEAD~5..HEAD")
    return sorted({f for f in out.strip().split("\n") if f and not f.startswith(".")})


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_context(project_path: str, repo_id: str):
    cfg = load_config()
    api_url = cfg.get("api_url", "http://saas.matrixone.online:3700")
    headers = api_headers(cfg)

    health_raw = fetch_code_health(api_url, repo_id, headers)
    dimensions = health_raw.get("dimensions", [])
    scan_checklist = build_scan_checklist(dimensions)

    report_exists, open_issues = read_report_state(project_path)
    changed_files = get_changed_files(project_path)

    result = {
        "health": {
            "score": health_raw.get("score"),
            "grade": health_raw.get("grade"),
            "dimensions": dimensions,
        },
        "scan_checklist": scan_checklist,
        "report_exists": report_exists,
        "open_issues": open_issues,
        "changed_files": changed_files,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ── main ──────────────────────────────────────────────────────────────────────

COMMANDS = {
    "context": lambda a: cmd_context(a[0], a[1]),
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
        print("Usage error. Run without args to see help.")
        sys.exit(1)
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
