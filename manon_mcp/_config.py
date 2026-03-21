"""Manon MCP — configuration, version detection, geo-routing."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
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

# ── Geo-routing ──────────────────────────────────────
# Default: official SaaS service. Override with MANON_API_URL env var.
# For self-hosted deployment, set MANON_API_URL=http://localhost:3700
API_URL_CN = os.environ.get("MANON_API_URL_CN", "http://saas.matrixone.online:3700")
API_URL_INTL = os.environ.get("MANON_API_URL_INTL", "http://203.208.134.27:3700")
API_KEY = os.environ.get("MANON_API_KEY", "")
_explicit_url = os.environ.get("MANON_API_URL", "")
_REGION_CACHE = Path.home() / ".manon" / "region.json"
GIT_REMOTE = "https://github.com/brandonzyy/manon.git"
GIT_BRANCH = "master"


_CN_TZ_KEYWORDS = ("China", "Beijing", "Shanghai", "Asia/Shanghai",
                   "Asia/Chongqing", "Asia/Harbin", "Asia/Urumqi", "CST-8", "UTC+8", "UTC+08")


def _detect_cn_timezone() -> bool:
    """Check if system timezone suggests CN region."""
    import platform
    import subprocess as _sp
    sys_platform = platform.system()
    if sys_platform == "Windows":
        try:
            tz = _sp.run(["powershell", "-c", "(Get-TimeZone).Id"],
                         capture_output=True, text=True, stdin=_sp.DEVNULL, timeout=3).stdout.strip()
            return any(k in tz for k in _CN_TZ_KEYWORDS)
        except Exception:
            return False
    # macOS / Linux
    tz = os.environ.get("TZ", "")
    if not tz:
        try:
            tz = Path("/etc/timezone").read_text().strip()
        except Exception:
            pass
    if not tz:
        try:
            link = os.readlink("/etc/localtime")
            tz = link.split("zoneinfo/")[-1] if "zoneinfo/" in link else ""
        except Exception:
            pass
    return any(k in tz for k in _CN_TZ_KEYWORDS)


def _detect_region_via_ip() -> str | None:
    """Try IP-based region detection. Returns 'CN', 'INTL', or None on failure."""
    for endpoint, country_key in [("https://api.country.is/", "country"), ("https://ipinfo.io/json", "country")]:
        try:
            country = httpx.get(endpoint, timeout=3).json().get(country_key, "")
            if country.upper() == "CN":
                return "CN"
            if country:
                return "INTL"
        except Exception as e:
            log.debug("Region detect via %s failed: %s", endpoint, e)
    return None


def _detect_region() -> str:
    """Detect user region. Returns 'CN' or 'INTL'."""
    import locale
    try:
        loc = locale.getdefaultlocale()[0] or ""
        if loc.startswith("zh_CN") or loc.startswith("zh_Hans"):
            return "CN"
    except Exception:
        pass

    if _detect_cn_timezone():
        return "CN"

    return _detect_region_via_ip() or "CN"


def _get_cached_region() -> str:
    """Read cached region, or default to CN and detect in background."""
    try:
        if _REGION_CACHE.exists():
            data = json.loads(_REGION_CACHE.read_text(encoding="utf-8"))
            return data.get("region", "CN")
    except Exception:
        pass
    def _bg_detect():
        try:
            region = _detect_region()
            _REGION_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _REGION_CACHE.write_text(
                json.dumps({"region": region}), encoding="utf-8",
            )
            log.info("Region detected and cached: %s", region)
        except Exception:
            pass
    threading.Thread(target=_bg_detect, daemon=True).start()
    return "CN"


def _resolve_api_url() -> str:
    if _explicit_url:
        return _explicit_url
    region = _get_cached_region()
    if region == "CN":
        url = API_URL_CN
    else:
        url = API_URL_INTL or API_URL_CN
    log.info("Geo-routing: region=%s, api_url=%s", region, url)
    return url


REGION = _get_cached_region()
API_URL = _resolve_api_url()

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
