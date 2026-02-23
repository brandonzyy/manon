"""Tests for saas/ infrastructure — config, auth, db schema, metering, quota."""
import pytest
from dataclasses import fields

from saas.config import SaasSettings, settings
from saas.auth import TenantContext
from saas.db import SCHEMA


class TestSaasSettings:
    def test_defaults(self):
        s = SaasSettings()
        assert s.port == 3700
        assert s.db_path == "./saas.db"
        assert s.repos_dir == "./saas_repos"
        assert s.index_dir == "./saas_indexes"
        assert s.llm_model == "glm-4.7-fp8"

    def test_rate_for(self):
        s = SaasSettings()
        assert s.rate_for("free") == 30
        assert s.rate_for("pro") == 300
        assert s.rate_for("enterprise") == 3000
        assert s.rate_for("unknown") == 30  # fallback to free

    def test_quota_repos(self):
        s = SaasSettings()
        assert s.quota_repos("free") == 2
        assert s.quota_repos("pro") == 20
        assert s.quota_repos("enterprise") == 9999

    def test_quota_deep_query(self):
        s = SaasSettings()
        assert s.quota_deep_query("free") == 10
        assert s.quota_deep_query("pro") == 9999

    def test_ensure_dirs(self, tmp_path, monkeypatch):
        s = SaasSettings()
        s.repos_dir = str(tmp_path / "repos")
        s.index_dir = str(tmp_path / "indexes")
        s.ensure_dirs()
        assert (tmp_path / "repos").exists()
        assert (tmp_path / "indexes").exists()

    def test_singleton(self):
        assert isinstance(settings, SaasSettings)


class TestTenantContext:
    def test_create(self):
        tc = TenantContext(tenant_id="t1", tier="pro", rate_limit=300)
        assert tc.tenant_id == "t1"
        assert tc.tier == "pro"
        assert tc.rate_limit == 300

    def test_fields(self):
        field_names = {f.name for f in fields(TenantContext)}
        assert "tenant_id" in field_names
        assert "tier" in field_names
        assert "rate_limit" in field_names


class TestDBSchema:
    def test_schema_has_tables(self):
        assert "CREATE TABLE IF NOT EXISTS tenants" in SCHEMA
        assert "CREATE TABLE IF NOT EXISTS api_keys" in SCHEMA
        assert "CREATE TABLE IF NOT EXISTS repos" in SCHEMA

    def test_schema_has_usage_log(self):
        assert "usage_log" in SCHEMA
