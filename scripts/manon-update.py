#!/usr/bin/env python3
"""Manon update script — run git pull + pip install outside MCP process.

Usage:
    python <MANON_DIR>/scripts/manon-update.py

Executes git pull + pip install, writes result to
~/.manon/update_status.json, prints JSON summary to stdout.
"""
from __future__ import annotations

import datetime
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
INSTALL_DIR = SCRIPT_DIR.parent
UPDATE_STATUS_FILE = Path.home() / ".manon" / "update_status.json"

# The repo has master and dev; there is no main. Region-based branch selection
# sent anyone with a stale INTL region cache to `git pull origin main`.
GIT_BRANCH = "master"


def _write_status(ok: bool, message: str):
    """Write update result to status file."""
    try:
        UPDATE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        UPDATE_STATUS_FILE.write_text(json.dumps({
            "ok": ok,
            "message": message,
            "timestamp": datetime.datetime.now().isoformat(),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def main():
    branch = GIT_BRANCH
    lines: list[str] = []
    ok = False

    # Step 1: git pull
    try:
        result = subprocess.run(
            ["git", "pull", "--quiet", "origin", branch],
            cwd=str(INSTALL_DIR),
            capture_output=True, text=True, encoding="utf-8",
            stdin=subprocess.DEVNULL, timeout=15,
        )
        git_out = result.stdout.strip()
        if "Already up to date" in git_out or "Already up-to-date" in git_out or not git_out:
            lines.append("代码已是最新，无需更新。")
            ok = True
            _write_status(ok, "\n".join(lines))
            print(json.dumps({"ok": ok, "message": "\n".join(lines)}, ensure_ascii=False))
            return
        lines.append(f"代码已更新:\n{git_out}")
    except subprocess.TimeoutExpired:
        lines.append("git pull 超时（15s），请手动执行: cd manon && git pull")
        _write_status(False, "\n".join(lines))
        print(json.dumps({"ok": False, "message": "\n".join(lines)}, ensure_ascii=False))
        return
    except Exception as e:
        lines.append(f"git pull 失败: {e}")
        _write_status(False, "\n".join(lines))
        print(json.dumps({"ok": False, "message": "\n".join(lines)}, ensure_ascii=False))
        return

    # Step 2: pip install
    req_file = INSTALL_DIR / "manon_mcp" / "requirements.txt"
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(req_file)],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
        )
        lines.append("依赖已更新。")
        ok = True
    except subprocess.TimeoutExpired:
        lines.append("pip install 超时，请手动执行: pip install -r manon_mcp/requirements.txt")
    except Exception as e:
        lines.append(f"依赖安装失败: {e}")

    lines.append("请重启 Claude Code 使新版本生效。")
    _write_status(ok, "\n".join(lines))
    print(json.dumps({"ok": ok, "message": "\n".join(lines)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
