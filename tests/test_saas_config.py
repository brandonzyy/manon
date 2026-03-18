"""Tests for saas/config.py — configuration settings."""
from pathlib import Path
from unittest.mock import patch

import pytest

from saas.config import SaasSettings, settings
from saas.config import RUNTIME_ROOT


class TestSaasSettings:
    """Tests for SaasSettings class."""

    def test_default_port(self):
        """Should have default port."""
        s = SaasSettings()
        assert s.port == 3700

    def test_default_db_path(self):
        """Should have default db path."""
        s = SaasSettings()
        assert s.db_path == str(RUNTIME_ROOT / "saas.db")

    def test_default_embedding_url(self):
        """Should have default embedding URL."""
        s = SaasSettings()
        assert s.embedding_url.startswith("http")

    def test_default_llm_settings(self):
        """Should have default LLM settings."""
        s = SaasSettings()
        assert s.llm_model == "qwen2.5-coder:7b"
        assert s.llm_api_url != ""

    def test_default_rate_limits(self):
        """Should have tiered rate limits."""
        s = SaasSettings()
        assert s.rate_free == 30
        assert s.rate_pro == 300
        assert s.rate_enterprise == 3000
        # Enterprise should be highest
        assert s.rate_enterprise > s.rate_pro > s.rate_free

    def test_default_quotas(self):
        """Should have tiered quotas."""
        s = SaasSettings()
        assert s.quota_repos_free == 2
        assert s.quota_repos_pro == 20
        assert s.quota_repos_enterprise == 9999

    def test_rate_for_tiers(self):
        """rate_for should return correct limits."""
        s = SaasSettings()
        assert s.rate_for("free") == s.rate_free
        assert s.rate_for("pro") == s.rate_pro
        assert s.rate_for("enterprise") == s.rate_enterprise

    def test_rate_for_unknown_tier(self):
        """Unknown tier should default to free."""
        s = SaasSettings()
        assert s.rate_for("unknown") == s.rate_free
        assert s.rate_for("premium") == s.rate_free

    def test_quota_repos_for_tiers(self):
        """quota_repos should return correct limits."""
        s = SaasSettings()
        assert s.quota_repos("free") == s.quota_repos_free
        assert s.quota_repos("pro") == s.quota_repos_pro
        assert s.quota_repos("enterprise") == s.quota_repos_enterprise

    def test_quota_repos_unknown_tier(self):
        """Unknown tier should default to free quota."""
        s = SaasSettings()
        assert s.quota_repos("unknown") == s.quota_repos_free

    def test_quota_deep_query_for_tiers(self):
        """quota_deep_query should return correct limits."""
        s = SaasSettings()
        assert s.quota_deep_query("free") == s.quota_deep_query_free
        assert s.quota_deep_query("pro") == s.quota_deep_query_pro
        assert s.quota_deep_query("enterprise") == s.quota_deep_query_enterprise

    def test_quota_deep_query_unknown_tier(self):
        """Unknown tier should default to free quota."""
        s = SaasSettings()
        assert s.quota_deep_query("unknown") == s.quota_deep_query_free

    def test_ensure_dirs(self, tmp_path):
        """Should create directories."""
        s = SaasSettings(
            db_path=str(tmp_path / "saas.db"),
            repos_dir=str(tmp_path / "repos"),
            index_dir=str(tmp_path / "indexes"),
            data_dir=str(tmp_path / "data"),
        )
        s.ensure_dirs()
        assert Path(s.db_path).parent.exists()
        assert Path(s.repos_dir).exists()
        assert Path(s.index_dir).exists()
        assert Path(s.data_dir).exists()

    def test_ensure_dirs_existing(self, tmp_path):
        """Should not fail if directories exist."""
        repos_dir = tmp_path / "repos"
        index_dir = tmp_path / "indexes"
        repos_dir.mkdir()
        index_dir.mkdir()

        s = SaasSettings(repos_dir=str(repos_dir), index_dir=str(index_dir))
        s.ensure_dirs()  # Should not raise

    def test_env_prefix(self):
        """Should use SAAS_ env prefix."""
        with patch.dict("os.environ", {"SAAS_PORT": "8888"}):
            s = SaasSettings()
            assert s.port == 8888

    def test_legacy_manon_llm_env_names(self):
        """Legacy MANON_LLM_* env names should still work."""
        with patch.dict(
            "os.environ",
            {
                "MANON_LLM_API_URL": "http://legacy-llm.test/v1/chat/completions",
                "MANON_LLM_MODEL": "legacy-model",
                "MANON_LLM_API_KEY": "legacy-key",
            },
            clear=False,
        ):
            s = SaasSettings()
            assert s.llm_api_url == "http://legacy-llm.test/v1/chat/completions"
            assert s.llm_model == "legacy-model"
            assert s.llm_api_key == "legacy-key"

    def test_saas_llm_env_names_override_legacy(self):
        """SAAS_LLM_* env names should override legacy MANON_LLM_* values."""
        with patch.dict(
            "os.environ",
            {
                "SAAS_LLM_API_URL": "http://saas-llm.test/v1/chat/completions",
                "SAAS_LLM_MODEL": "saas-model",
                "SAAS_LLM_API_KEY": "saas-key",
                "MANON_LLM_API_URL": "http://legacy-llm.test/v1/chat/completions",
                "MANON_LLM_MODEL": "legacy-model",
                "MANON_LLM_API_KEY": "legacy-key",
            },
            clear=False,
        ):
            s = SaasSettings()
            assert s.llm_api_url == "http://saas-llm.test/v1/chat/completions"
            assert s.llm_model == "saas-model"
            assert s.llm_api_key == "saas-key"

    def test_custom_settings(self):
        """Should accept custom settings."""
        s = SaasSettings(
            port=9999,
            db_path="/custom/db.db",
            rate_free=100,
        )
        assert s.port == 9999
        assert s.db_path == "/custom/db.db"
        assert s.rate_free == 100


class TestGlobalSettings:
    """Tests for global settings instance."""

    def test_global_settings_exists(self):
        """Global settings should exist."""
        assert settings is not None
        assert isinstance(settings, SaasSettings)

    def test_global_settings_has_required_fields(self):
        """Global settings should have all required fields."""
        assert settings.port > 0
        assert settings.db_path != ""
        assert settings.repos_dir != ""
        assert settings.index_dir != ""
        assert settings.embedding_url != ""

    def test_global_settings_tier_methods(self):
        """Global settings should have working tier methods."""
        # These should not raise
        settings.rate_for("free")
        settings.quota_repos("pro")
        settings.quota_deep_query("enterprise")


class TestTierHierarchy:
    """Tests for tier hierarchy."""

    def test_rate_limit_hierarchy(self):
        """Enterprise > Pro > Free for rate limits."""
        s = SaasSettings()
        assert s.rate_enterprise > s.rate_pro
        assert s.rate_pro > s.rate_free

    def test_repo_quota_hierarchy(self):
        """Enterprise > Pro > Free for repo quotas."""
        s = SaasSettings()
        assert s.quota_repos_enterprise > s.quota_repos_pro
        assert s.quota_repos_pro > s.quota_repos_free

    def test_deep_query_quota_hierarchy(self):
        """Enterprise >= Pro > Free for deep query quotas."""
        s = SaasSettings()
        assert s.quota_deep_query_pro >= s.quota_deep_query_free
        assert s.quota_deep_query_enterprise >= s.quota_deep_query_pro

    def test_enterprise_effectively_unlimited(self):
        """Enterprise should have very high limits."""
        s = SaasSettings()
        assert s.quota_repos_enterprise >= 9999
        assert s.quota_deep_query_enterprise >= 9999
        assert s.rate_enterprise >= 3000
