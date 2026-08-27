#!/usr/bin/env python3
"""dao-commit.py — atomic commit + issue close + graph sync for dao sessions.

Usage:
    python dao-commit.py <project_path> <issue_id> <skill_dir> <repo_id>

Reads commit message from stdin or prompts Claude to pass -m via git env.
Actually: runs `git commit` with whatever staged changes exist, using the
MANON_DAO_MSG env var as the message (set by Claude before calling this).

Sequence:
  1. git commit (message from MANON_DAO_MSG env var)
  2. dao-report.py done <project_path> <issue_id> <commit_hash>
  3. Print graph sync instructions for Claude to execute via MCP
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run(cmd: list, cwd: str | None = None) -> tuple[int, str, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def main():
    if len(sys.argv) < 5:
        print("Usage: dao-commit.py <project_path> <issue_id> <skill_dir> <repo_id>",
              file=sys.stderr)
        sys.exit(1)

    project_path = sys.argv[1]
    issue_id     = sys.argv[2]
    skill_dir    = sys.argv[3]
    repo_id      = sys.argv[4]
    msg          = os.environ.get("MANON_DAO_MSG", "").strip()

    if not msg:
        print("Error: set MANON_DAO_MSG env var to the commit message", file=sys.stderr)
        sys.exit(1)

    # 1. git commit
    code, out, err = run(["git", "commit", "-m", msg], cwd=project_path)
    if code != 0:
        print(f"git commit failed:\n{err}", file=sys.stderr)
        sys.exit(code)
    print(out)

    # 2. Extract commit hash
    _, commit_hash, _ = run(["git", "rev-parse", "--short", "HEAD"], cwd=project_path)

    # 3. Close dao issue
    report_script = Path(skill_dir) / "scripts" / "dao-report.py"
    code2, out2, err2 = run(
        [sys.executable, str(report_script), "done", project_path, issue_id, commit_hash]
    )
    if code2 == 0:
        print(out2)
    else:
        print(f"dao-report done failed: {err2}", file=sys.stderr)

    # 4. Emit graph sync instruction for Claude (MCP calls cannot be made from script)
    print(json.dumps({
        "commit": commit_hash,
        "issue_closed": issue_id,
        "next": f"manon_impact(repo_id='{repo_id}', commit='HEAD') then sync graph",
    }))


if __name__ == "__main__":
    main()
