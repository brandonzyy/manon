"""Tests for saas/ infrastructure — config, auth, db schema, metering, quota."""
from dataclasses import fields
from pathlib import Path

import pytest

from saas.auth import TenantContext
from saas.config import SaasSettings, settings
from saas.db import SCHEMA
from saas.config import RUNTIME_ROOT


class TestSaasSettings:
    def test_defaults(self):
        s = SaasSettings()
        assert s.port == 3700
        assert s.db_path == str(RUNTIME_ROOT / "saas.db")
        assert s.repos_dir == str(RUNTIME_ROOT / "repos")
        assert s.index_dir == str(RUNTIME_ROOT / "indexes")
        assert s.llm_model == "glm-4.5-air"


    def test_quota_repos(self):
        s = SaasSettings()
        assert s.quota_repos("free") == 1
        assert s.quota_repos("pro") == 5
        assert s.quota_repos("enterprise") == 9999


    def test_ensure_dirs(self, tmp_path, monkeypatch):
        s = SaasSettings()
        s.db_path = str(tmp_path / "saas.db")
        s.repos_dir = str(tmp_path / "repos")
        s.index_dir = str(tmp_path / "indexes")
        s.data_dir = str(tmp_path / "data")
        s.ensure_dirs()
        assert Path(s.db_path).parent.exists()
        assert (tmp_path / "repos").exists()
        assert (tmp_path / "indexes").exists()
        assert (tmp_path / "data").exists()

    def test_singleton(self):
        assert isinstance(settings, SaasSettings)


class TestTenantContext:
    def test_create(self):
        tc = TenantContext(tenant_id="t1", tier="pro")
        assert tc.tenant_id == "t1"
        assert tc.tier == "pro"

    def test_fields(self):
        field_names = {f.name for f in fields(TenantContext)}
        assert "tenant_id" in field_names
        assert "tier" in field_names
        assert "rate_limit" not in field_names  # dropped with subscription billing


class TestDBSchema:
    def test_schema_has_tables(self):
        assert "CREATE TABLE IF NOT EXISTS tenants" in SCHEMA
        assert "CREATE TABLE IF NOT EXISTS api_keys" in SCHEMA
        assert "CREATE TABLE IF NOT EXISTS repos" in SCHEMA

    def test_schema_has_usage_log(self):
        assert "usage_log" in SCHEMA
