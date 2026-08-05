"""Tests for manon_mcp/_config.py — version, API endpoint, version check."""
import importlib

from manon_mcp._config import (
    _get_client_version, _git_branch, _check_version,
    API_URL, CLIENT_VERSION, API_URL_CN,
    GIT_REMOTE, GIT_BRANCH,
)


class TestGetClientVersion:
    def test_returns_string(self):
        assert isinstance(CLIENT_VERSION, str)
        assert len(CLIENT_VERSION) > 0

    def test_version_format(self):
        # Should be semver-like: x.y.z
        parts = CLIENT_VERSION.split(".")
        assert len(parts) >= 2








class TestGitBranch:
    def test_returns_master(self):
        assert _git_branch() == "master"


class TestConstants:
    def test_git_remote(self):
        assert "github" in GIT_REMOTE

    def test_branch(self):
        assert GIT_BRANCH == "master"

    def test_api_url_cn_set(self):
        assert API_URL_CN != ""


class TestApiUrl:
    """Geo-routing was removed — one endpoint, overridable by env."""

    def test_defaults_to_cn_endpoint(self, monkeypatch):
        monkeypatch.delenv("MANON_API_URL", raising=False)
        monkeypatch.delenv("MANON_API_URL_CN", raising=False)
        import manon_mcp._config as cfg
        assert importlib.reload(cfg).API_URL == "http://saas.matrixone.online:3700"

    def test_explicit_override_wins(self, monkeypatch):
        monkeypatch.setenv("MANON_API_URL", "http://localhost:3700")
        import manon_mcp._config as cfg
        assert importlib.reload(cfg).API_URL == "http://localhost:3700"

    def test_empty_override_falls_back(self, monkeypatch):
        """Windows installer used to write an empty MANON_API_URL."""
        monkeypatch.setenv("MANON_API_URL", "")
        monkeypatch.delenv("MANON_API_URL_CN", raising=False)
        import manon_mcp._config as cfg
        assert importlib.reload(cfg).API_URL == "http://saas.matrixone.online:3700"

    def test_no_intl_symbols_remain(self):
        import manon_mcp._config as cfg
        leftovers = [n for n in dir(cfg) if "INTL" in n or "REGION" in n.upper()]
        assert leftovers == []


class TestCheckVersion:
    def test_returns_string(self):
        # May return empty string or update notice
        result = _check_version()
        assert isinstance(result, str)
