#!/usr/bin/env python3
"""tc-commit.py — Commit test files and update coverage.

Usage:
  python tc-commit.py <project_path> <test_file> <source_file>

Actions:
  1. git add <test_file>
  2. git commit -m "test(<module>): add tests for <source>"
  3. Re-run bun test --coverage to update lcov.info
  4. Print coverage delta

Output JSON:
  {
    "committed": true,
    "commit_hash": "abc1234",
    "message": "test(util): add tests for format.ts",
    "coverage_before": {"line_pct": 69.9, "function_pct": 79.7},
    "coverage_after": {"line_pct": 70.5, "function_pct": 80.1}
  }
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], cwd: str, timeout: int = 60) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def _parse_lcov_summary(project_path: str) -> dict:
    """Quick summary from lcov.info."""
    root = Path(project_path)
    candidates = [
        root / "coverage" / "lcov.info",
        root / "packages" / "yourcoder" / "coverage" / "lcov.info",
    ]
    for lcov_path in candidates:
        if not lcov_path.exists():
            continue
        try:
            raw = lcov_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lh = lf = fnh = fnf = 0
        for line in raw.splitlines():
            if line.startswith("LH:"): lh += int(line[3:].strip() or 0)
            elif line.startswith("LF:"): lf += int(line[3:].strip() or 0)
            elif line.startswith("FNH:"): fnh += int(line[4:].strip() or 0)
            elif line.startswith("FNF:"): fnf += int(line[4:].strip() or 0)
        return {
            "line_pct": round(lh / max(lf, 1) * 100, 1),
            "function_pct": round(fnh / max(fnf, 1) * 100, 1),
        }
    return {"line_pct": 0.0, "function_pct": 0.0}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    project_path = sys.argv[1]
    test_file = sys.argv[2]
    source_file = sys.argv[3]

    # Coverage before
    before = _parse_lcov_summary(project_path)

    # Derive module name from source_file path
    parts = Path(source_file).with_suffix("").parts
    # Take last 2 meaningful parts: e.g. "src/util/format" → "util/format"
    module = "/".join(p for p in parts[-2:] if p not in ("src", "index"))
    source_name = Path(source_file).name

    # Git add + commit
    rc, out, err = _run(["git", "add", test_file], project_path)
    if rc != 0:
        print(json.dumps({"error": f"git add failed: {err}"}))
        sys.exit(1)

    msg = f"test({module}): add tests for {source_name}"
    rc, out, err = _run(["git", "commit", "-m", msg], project_path)
    if rc != 0:
        print(json.dumps({"error": f"git commit failed: {err}"}))
        sys.exit(1)

    # Extract commit hash
    rc, out, _ = _run(["git", "rev-parse", "--short", "HEAD"], project_path)
    commit_hash = out.strip() if rc == 0 else "unknown"

    # Re-run coverage (find bun work dir)
    bun = shutil.which("bun")
    if bun:
        # Find the sub-package that has the test
        test_abs = (Path(project_path) / test_file).resolve()
        work_dir = project_path
        packages = Path(project_path) / "packages"
        if packages.is_dir():
            for sub in packages.iterdir():
                if sub.is_dir() and test_abs.is_relative_to(sub):
                    work_dir = str(sub)
                    break
        cov_dir = Path(work_dir) / "coverage"
        args = [bun, "test", "--coverage", "--coverage-reporter=lcov",
                f"--coverage-dir={cov_dir}"]
        use_shell = os.name == "nt" and bun.lower().endswith(".cmd")
        cmd_str = " ".join(f'"{a}"' if " " in str(a) else str(a) for a in args)
        try:
            subprocess.run(
                cmd_str if use_shell else args,
                cwd=work_dir, capture_output=True, text=True,
                timeout=60, shell=use_shell,
            )
        except Exception:
            pass

    # Coverage after
    after = _parse_lcov_summary(project_path)

    result = {
        "committed": True,
        "commit_hash": commit_hash,
        "message": msg,
        "coverage_before": before,
        "coverage_after": after,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
