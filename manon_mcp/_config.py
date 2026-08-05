"""Manon MCP — configuration and version detection."""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import httpx

log = logging.getLogger("manon-mcp")

# ── Version ──────────────────────────────────────────

def _get_client_version() -> str:
    """Read version from VERSION file, or fall back to git commit count."""
    install_dir = Path(__file__).resolve().parent.parent
    version_file = install_dir / "VERSION"
    try:
        v = version_file.read_text(encoding="utf-8").strip()
        if v:
            return v
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(install_dir),
            capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=5,
        )
        if result.returncode == 0:
            count = result.stdout.strip()
            return f"1.0.{count}"
    except Exception:
        pass
    return "1.0.0"

CLIENT_VERSION = _get_client_version()

# ── API endpoint ─────────────────────────────────────
# Single service. Override with MANON_API_URL env var; for self-hosted
# deployment set MANON_API_URL=http://localhost:3700.
#
# Geo-routing (CN vs INTL) was removed: no INTL deployment ever existed, so
# detection could only mis-route. The INTL endpoint it pointed at is dead, and
# a stale region cache silently sent clients there.
API_URL_CN = os.environ.get("MANON_API_URL_CN", "http://saas.matrixone.online:3700")
API_KEY = os.environ.get("MANON_API_KEY", "")
GIT_REMOTE = "https://github.com/brandonzyy/manon.git"
GIT_BRANCH = "master"

API_URL = os.environ.get("MANON_API_URL", "") or API_URL_CN

# ── Version check ────────────────────────────────────
_version_checked = False
_update_notice: str = ""


def _check_version() -> str:
    """Compare local version with public repo via hosting API (no git ops)."""
    global _version_checked, _update_notice
    if _version_checked:
        return _update_notice
    _version_checked = True
    try:
        url = "https://api.github.com/repos/brandonzyy/manon"
        r = httpx.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            pushed = data.get("pushed_at", "")
            if pushed:
                import datetime
                remote_time = datetime.datetime.fromisoformat(pushed.replace("Z", "+00:00"))
                install_dir = Path(__file__).resolve().parent.parent
                result = subprocess.run(
                    ["git", "log", "-1", "--format=%cI"],
                    cwd=str(install_dir), capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=3,
                )
                if result.returncode == 0 and result.stdout.strip():
                    local_time = datetime.datetime.fromisoformat(result.stdout.strip())
                    if remote_time > local_time + datetime.timedelta(hours=1):
                        _update_notice = (
                            f"\n⚠ 有新版本可用（当前 {CLIENT_VERSION}），调用 manon_update 更新"
                        )
    except Exception:
        pass
    return _update_notice


def _git_branch() -> str:
    """Return git branch name."""
    return GIT_BRANCH
