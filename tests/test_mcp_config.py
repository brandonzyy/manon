"""Tests for manon_mcp/_config.py — version, geo-routing, version check."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from manon_mcp._config import (
    _get_client_version, _detect_region, _get_cached_region,
    _resolve_api_url, _git_branch, _check_version,
    CLIENT_VERSION, API_URL_CN, API_URL_INTL, REGION,
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


class TestDetectRegion:
    def test_returns_cn_or_intl(self):
        region = _detect_region()
        assert region in ("CN", "INTL")


class TestGetCachedRegion:
    def test_with_cache_file(self, tmp_path, monkeypatch):
        cache = tmp_path / "region.json"
        cache.write_text('{"region": "INTL"}', encoding="utf-8")
        monkeypatch.setattr("manon_mcp._config._REGION_CACHE", cache)
        assert _get_cached_region() == "INTL"

    def test_without_cache_defaults_cn(self, tmp_path, monkeypatch):
        monkeypatch.setattr("manon_mcp._config._REGION_CACHE", tmp_path / "nope.json")
        result = _get_cached_region()
        assert result == "CN"


class TestResolveApiUrl:
    def test_explicit_url(self, monkeypatch):
        monkeypatch.setattr("manon_mcp._config._explicit_url", "http://custom:3700")
        assert _resolve_api_url() == "http://custom:3700"

    def test_cn_region(self, monkeypatch):
        monkeypatch.setattr("manon_mcp._config._explicit_url", "")
        monkeypatch.setattr("manon_mcp._config._get_cached_region", lambda: "CN")
        url = _resolve_api_url()
        assert url == API_URL_CN


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


class TestCheckVersion:
    def test_returns_string(self):
        # May return empty string or update notice
        result = _check_version()
        assert isinstance(result, str)
